"""
VoiceCal API server.

Run from the CodeJam-Backend folder:

    uvicorn demo:app --reload

Flow: the browser records audio -> POST /api/transcribe returns the transcript
and the parsed events -> the user reviews/edits them -> POST /api/events adds
them to Google Calendar. POST /api/parse does the same parsing for typed text,
and the original one-shot POST /upload is kept for older clients.
"""

import importlib.util
import logging
import os
import shutil
import sys
import tempfile
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from main import DEFAULT_TIME_ZONE, MAX_EVENTS, MAX_TEXT_LENGTH, json_to_google_event, parse_schedule

logger = logging.getLogger("voicecal")

BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = BASE_DIR.parent / "CodeJam-Frontend"
TOKEN_PATH = BASE_DIR / "token.json"
SCOPES = ["https://www.googleapis.com/auth/calendar"]

WHISPER_MODEL_NAME = os.getenv("VOICECAL_WHISPER_MODEL", "small.en")
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MIN_UPLOAD_BYTES = 1024  # anything smaller is an aborted or silent recording
AUDIO_SUFFIXES = {".webm", ".ogg", ".oga", ".mp4", ".m4a", ".wav", ".mp3", ".mpeg", ".aac", ".flac"}
# Whisper's usual output for silence or background noise
SILENCE_TRANSCRIPTS = {"", "you", "thank you", "thanks for watching", "bye", "okay"}
MAX_EVENT_LENGTH = timedelta(days=7)

@asynccontextmanager
async def lifespan(_app):
    warm_up_whisper()
    yield


app = FastAPI(title="VoiceCal", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.get("/")
def home():
    return RedirectResponse("/static/HomePage.html")


# ---------------------------------------------------------------------------
# Whisper (loaded lazily so the server starts instantly and survives without it)
# ---------------------------------------------------------------------------

_model = None
_model_lock = threading.Lock()
_transcribe_lock = threading.Lock()  # one Whisper model instance is not safe to share between threads


def get_whisper_model():
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:
                try:
                    import whisper
                except ImportError:
                    raise HTTPException(503, "Speech recognition isn't installed on the server "
                                             "(pip install openai-whisper). You can still type your events.")
                _model = whisper.load_model(WHISPER_MODEL_NAME)
    return _model


def warm_up_whisper():
    def load():
        try:
            get_whisper_model()
            logger.info("Whisper model %s loaded", WHISPER_MODEL_NAME)
        except Exception as exc:  # the API reports this again when someone records
            logger.warning("Whisper not available: %s", getattr(exc, "detail", exc))

    if importlib.util.find_spec("whisper") is not None:
        threading.Thread(target=load, daemon=True).start()


def find_ffmpeg():
    """Whisper shells out to ffmpeg. If it was installed after this terminal opened, reload PATH from Windows."""
    if shutil.which("ffmpeg"):
        return True
    if sys.platform == "win32":
        import winreg
        extra = []
        for root, key in ((winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
                          (winreg.HKEY_CURRENT_USER, "Environment")):
            try:
                with winreg.OpenKey(root, key) as handle:
                    extra.append(os.path.expandvars(winreg.QueryValueEx(handle, "Path")[0]))
            except OSError:
                continue
        os.environ["PATH"] = os.pathsep.join([os.environ.get("PATH", "")] + extra)
    return shutil.which("ffmpeg") is not None


def transcribe_audio(data, suffix):
    if not find_ffmpeg():
        raise HTTPException(503, "ffmpeg is missing on the server, so audio can't be decoded. "
                                 "Install it (winget install ffmpeg) and restart the server.")
    model = get_whisper_model()
    fd, path = tempfile.mkstemp(prefix="voicecal-", suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        with _transcribe_lock:
            result = model.transcribe(path, language="en", fp16=False)
    except FileNotFoundError:
        raise HTTPException(503, "ffmpeg is missing on the server, so audio can't be decoded.")
    except RuntimeError as exc:
        logger.warning("Could not decode audio: %s", exc)
        raise HTTPException(422, "That recording couldn't be read. Please try again.")
    finally:
        try:
            os.remove(path)
        except OSError:
            pass

    segments = result.get("segments") or []
    if segments and all(s.get("no_speech_prob", 0) > 0.6 for s in segments):
        return ""
    text = (result.get("text") or "").strip()
    if text.lower().strip(" .!?") in SILENCE_TRANSCRIPTS:
        return ""
    return text


async def read_upload(file):
    content_type = (file.content_type or "").lower()
    if content_type and not content_type.startswith(("audio/", "video/webm", "video/mp4",
                                                     "application/octet-stream")):
        raise HTTPException(415, "Please upload an audio recording.")

    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "That recording is too long. Try keeping it under a minute.")
    if len(data) < MIN_UPLOAD_BYTES:
        raise HTTPException(422, "That recording was too short. Hold on a little longer while speaking.")

    suffix = Path(file.filename or "").suffix.lower()
    return data, suffix if suffix in AUDIO_SUFFIXES else ".webm"


# ---------------------------------------------------------------------------
# Google Calendar
# ---------------------------------------------------------------------------

_service = None
_credentials = None
_service_lock = threading.Lock()


def get_calendar_service():
    global _service, _credentials
    with _service_lock:
        try:
            from google.auth.exceptions import RefreshError
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build
        except ImportError:
            raise HTTPException(503, "Google Calendar libraries aren't installed on the server.")

        if _credentials is None:
            if not TOKEN_PATH.exists():
                raise HTTPException(503, "Google Calendar isn't connected yet. Run quickstart.py once "
                                         "in the CodeJam-Backend folder, then restart the server.")
            _credentials = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)

        if not _credentials.valid:
            if not (_credentials.expired and _credentials.refresh_token):
                _credentials = _service = None
                raise HTTPException(401, "Your Google sign-in is no longer valid. Run quickstart.py again.")
            try:
                _credentials.refresh(Request())
                TOKEN_PATH.write_text(_credentials.to_json())
            except RefreshError:
                _credentials = _service = None
                raise HTTPException(401, "Your Google sign-in expired. Run quickstart.py again.")

        if _service is None:
            _service = build("calendar", "v3", credentials=_credentials, cache_discovery=False)
        return _service


def find_duplicate(service, body, tz):
    """Return the link of an existing event with the same title and start time, if any."""
    start = datetime.fromisoformat(body["start"]["dateTime"]).replace(tzinfo=tz)
    end = datetime.fromisoformat(body["end"]["dateTime"]).replace(tzinfo=tz)
    existing = service.events().list(
        calendarId="primary", timeMin=start.isoformat(), timeMax=end.isoformat(),
        q=body["summary"], singleEvents=True, maxResults=10,
    ).execute()
    for item in existing.get("items", []):
        item_start = (item.get("start") or {}).get("dateTime")
        if (item.get("summary", "").strip().lower() == body["summary"].lower()
                and item_start and datetime.fromisoformat(item_start.replace("Z", "+00:00")) == start):
            return item.get("htmlLink") or ""
    return None


def google_error_message(exc):
    status = getattr(getattr(exc, "resp", None), "status", None)
    if status in (401, 403):
        return "Google refused the request. Run quickstart.py again to reconnect your calendar."
    if status == 429:
        return "Google is rate limiting us. Wait a moment and try again."
    return "Google Calendar couldn't save this event. Please try again."


def insert_events(events, tz_name, tz, skip_duplicates):
    service = get_calendar_service()  # raises a readable 503/401 before anything else can fail
    from googleapiclient.errors import HttpError

    results = []
    created_keys = set()
    for index, event in enumerate(events):
        try:
            body = json_to_google_event(event, tz_name)
        except (ValueError, KeyError, TypeError) as exc:
            results.append({"index": index, "status": "error", "message": str(exc) or "Invalid event."})
            continue
        length = (datetime.fromisoformat(body["end"]["dateTime"])
                  - datetime.fromisoformat(body["start"]["dateTime"]))
        if length > MAX_EVENT_LENGTH:
            results.append({"index": index, "status": "error", "message": "Events can be at most 7 days long."})
            continue

        key =(body["summary"].lower(), body["start"]["dateTime"])
        if key in created_keys:
            results.append({"index": index, "status": "duplicate", "message": "Same event appears twice."})
            continue

        try:
            if skip_duplicates and tz is not None:
                link = find_duplicate(service, body, tz)
                if link is not None:
                    results.append({"index": index, "status": "duplicate", "link": link,
                                    "message": "Already on your calendar."})
                    continue
            created = service.events().insert(calendarId="primary", body=body).execute()
        except HttpError as exc:
            logger.warning("Google Calendar error: %s", exc)
            results.append({"index": index, "status": "error", "message": google_error_message(exc)})
            continue
        except OSError:
            results.append({"index": index, "status": "error",
                            "message": "Couldn't reach Google Calendar. Check your connection."})
            continue

        created_keys.add(key)
        results.append({"index": index, "status": "created", "link": created.get("htmlLink"),
                        "id": created.get("id")})
    return results


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def resolve_time_zone(name):
    """Use the browser's time zone when it's valid, otherwise the default."""
    for candidate in ((name or "").strip(), DEFAULT_TIME_ZONE):
        if not candidate:
            continue
        try:
            return candidate, ZoneInfo(candidate)
        except (ZoneInfoNotFoundError, ValueError):
            continue
    return DEFAULT_TIME_ZONE, None  # no tz database installed (pip install tzdata)


def now_in(tz):
    return datetime.now(tz).replace(tzinfo=None) if tz else datetime.now()


def parse_response(text, timezone):
    tz_name, tz = resolve_time_zone(timezone)
    events, warnings = parse_schedule(text, now_in(tz))
    return {"text": text, "events": [e.to_dict() for e in events], "warnings": warnings, "timezone": tz_name}


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

class ParseRequest(BaseModel):
    text: str
    timezone: Optional[str] = None


class EventIn(BaseModel):
    activity: str
    location: Optional[str] = None
    start: datetime
    end: datetime


class CreateEventsRequest(BaseModel):
    events: List[EventIn]
    timezone: Optional[str] = None
    skip_duplicates: bool = True


@app.get("/api/health")
def health():
    return {
        "speech_installed": importlib.util.find_spec("whisper") is not None,
        "speech_ready": _model is not None,
        "ffmpeg_installed": find_ffmpeg(),
        "calendar_connected": TOKEN_PATH.exists(),
        "max_events": MAX_EVENTS,
        "max_text_length": MAX_TEXT_LENGTH,
    }


@app.post("/api/transcribe")
async def transcribe(file: UploadFile = File(...), timezone: str = Form(DEFAULT_TIME_ZONE)):
    data, suffix = await read_upload(file)
    text = await run_in_threadpool(transcribe_audio, data, suffix)
    return parse_response(text, timezone)


@app.post("/api/parse")
def parse_text(request: ParseRequest):
    return parse_response(request.text[:MAX_TEXT_LENGTH], request.timezone)


@app.post("/api/events")
async def create_events(request: CreateEventsRequest):
    if not request.events:
        raise HTTPException(422, "There are no events to add.")
    if len(request.events) > MAX_EVENTS:
        raise HTTPException(422, f"You can add up to {MAX_EVENTS} events at once.")

    tz_name, tz = resolve_time_zone(request.timezone)
    events = []
    for event in request.events:
        start, end = event.start, event.end
        if start.tzinfo is not None:  # convert "...Z" / "+02:00" to the user's wall clock
            start = start.astimezone(tz).replace(tzinfo=None) if tz else start.replace(tzinfo=None)
        if end.tzinfo is not None:
            end = end.astimezone(tz).replace(tzinfo=None) if tz else end.replace(tzinfo=None)
        events.append({"activity": event.activity, "location": event.location, "start": start, "end": end})

    results = await run_in_threadpool(insert_events, events, tz_name, tz, request.skip_duplicates)
    created = sum(1 for r in results if r["status"] == "created")
    return {"results": results, "created": created, "timezone": tz_name}


@app.post("/upload")
async def upload_audio(file: UploadFile = File(...), timezone: str = Form(DEFAULT_TIME_ZONE)):
    """Original one-shot endpoint: transcribe and add everything straight away."""
    data, suffix = await read_upload(file)
    text = await run_in_threadpool(transcribe_audio, data, suffix)
    parsed = parse_response(text, timezone)
    if not parsed["events"]:
        raise HTTPException(422, (parsed["warnings"] or ["No events found."])[0])

    tz_name, tz = resolve_time_zone(timezone)
    results = await run_in_threadpool(insert_events, parsed["events"], tz_name, tz, True)
    return {
        "text": text,
        "events": parsed["events"],
        "google_events": [json_to_google_event(e, tz_name) for e in parsed["events"]],
        "calendar_links": [r.get("link") for r in results if r["status"] == "created"],
        "results": results,
    }
