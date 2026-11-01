"""
VoiceCal API server.

Run from the CodeJam-Backend folder:

    uvicorn demo:app --reload

Flow: the browser records audio -> POST /api/transcribe returns the transcript
and the parsed events -> the user reviews/edits them -> POST /api/events adds
them to the Google Calendar of whoever signed in on that browser
(GET /auth/login, POST /auth/logout). POST /api/parse does the same parsing
for typed text, and the original one-shot POST /upload is kept for older clients.
"""

import base64
import importlib.util
import json
import logging
import os
import re
import secrets
import shutil
import sys
import tempfile
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from main import DEFAULT_TIME_ZONE, MAX_EVENTS, MAX_TEXT_LENGTH, json_to_google_event, parse_schedule

logger = logging.getLogger("voicecal")

BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = BASE_DIR.parent / "CodeJam-Frontend"
CREDENTIALS_PATH = BASE_DIR / "credentials.json"  # the app's OAuth client from Google Cloud Console
SESSION_DIR = BASE_DIR / ".sessions"  # one file per signed-in browser; holds that user's Google tokens
SESSION_COOKIE = "voicecal_session"
STATE_COOKIE = "voicecal_oauth_state"
SESSION_MAX_AGE = 60 * 60 * 24 * 30
LOGIN_TIMEOUT_SECONDS = 10 * 60
CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.events"
USER_SCOPES = ["openid", "https://www.googleapis.com/auth/userinfo.email", CALENDAR_SCOPE]
# Google may return the scopes in a different order or with extras; don't treat that as an error
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

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
def home(request: Request, code: Optional[str] = None, state: Optional[str] = None, error: Optional[str] = None):
    # Google sends the user back here after sign-in: the root URL is a valid
    # redirect for both "Desktop app" (loopback) and "Web app" OAuth clients.
    if code or error:
        return finish_login(request, code, state, error)
    return RedirectResponse("/static/HomePage.html")


# ---------------------------------------------------------------------------
# Google sign-in (each browser signs in with its own Google account)
# ---------------------------------------------------------------------------

_pending_logins = {}  # OAuth state -> (PKCE code verifier, page to return to, started at)
_pending_lock = threading.Lock()


def safe_next(path):
    """Only send people back to our own pages after sign-in (no open redirects)."""
    if path and re.fullmatch(r"/static/[\w-]+\.html", path):
        return path
    return "/static/mic.html"


def set_cookie(response, request, name, value, max_age):
    response.set_cookie(name, value, max_age=max_age, httponly=True, samesite="lax",
                        secure=request.url.scheme == "https")


def granted_scopes(token):
    scope = token.get("scope") or []
    return set(scope.split() if isinstance(scope, str) else scope)


def email_from_id_token(id_token):
    """The ID token comes straight from Google's token endpoint over TLS, so reading its payload is enough."""
    try:
        payload = id_token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))).get("email")
    except (AttributeError, IndexError, ValueError):
        return None


@app.get("/auth/login")
def login(request: Request, next: str = "/static/mic.html"):
    if not CREDENTIALS_PATH.exists():
        raise HTTPException(503, "Google sign-in isn't set up: put credentials.json in the CodeJam-Backend folder.")
    try:
        from google_auth_oauthlib.flow import Flow
    except ImportError:
        raise HTTPException(503, "Google sign-in libraries aren't installed on the server.")

    flow = Flow.from_client_secrets_file(str(CREDENTIALS_PATH), scopes=USER_SCOPES,
                                         redirect_uri=str(request.base_url))
    auth_url, state = flow.authorization_url(access_type="offline", prompt="consent select_account")
    now = time.time()
    with _pending_lock:
        for old_state in [s for s, p in _pending_logins.items() if now - p[2] > LOGIN_TIMEOUT_SECONDS]:
            del _pending_logins[old_state]
        _pending_logins[state] = (flow.code_verifier, safe_next(next), now)

    response = RedirectResponse(auth_url)
    set_cookie(response, request, STATE_COOKIE, state, LOGIN_TIMEOUT_SECONDS)
    return response


def finish_login(request, code, state, error):
    with _pending_lock:
        pending = _pending_logins.pop(state, None) if state else None
    next_path = pending[1] if pending else "/static/mic.html"

    def fail(reason):
        response = RedirectResponse(f"{next_path}?auth_error={reason}")
        response.delete_cookie(STATE_COOKIE)
        return response

    if error:
        return fail("denied" if error == "access_denied" else "failed")
    # The state must match the one this browser started with (protects against forged callbacks)
    if (pending is None or request.cookies.get(STATE_COOKIE) != state
            or time.time() - pending[2] > LOGIN_TIMEOUT_SECONDS):
        return fail("expired")

    from google_auth_oauthlib.flow import Flow
    flow = Flow.from_client_secrets_file(str(CREDENTIALS_PATH), scopes=USER_SCOPES, state=state,
                                         redirect_uri=str(request.base_url), code_verifier=pending[0])
    try:
        token = flow.fetch_token(code=code)
    except Exception as exc:  # network problems, reused or expired code...
        logger.warning("Google sign-in failed: %s", exc)
        return fail("failed")
    if CALENDAR_SCOPE not in granted_scopes(token):
        return fail("calendar_scope")  # the user unticked calendar access on Google's consent screen

    credentials = flow.credentials
    old_session = request.cookies.get(SESSION_COOKIE)
    if old_session:
        delete_session(old_session)
    session_id = secrets.token_urlsafe(32)
    save_session(session_id, {
        "email": email_from_id_token(credentials.id_token),
        "credentials": json.loads(credentials.to_json()),
        "created": datetime.now().isoformat(timespec="seconds"),
    })

    response = RedirectResponse(next_path)
    set_cookie(response, request, SESSION_COOKIE, session_id, SESSION_MAX_AGE)
    response.delete_cookie(STATE_COOKIE)
    return response


@app.post("/auth/logout")
def logout(request: Request):
    session_id = request.cookies.get(SESSION_COOKIE)
    session = load_session(session_id)
    if session:
        token = session.get("credentials", {}).get("refresh_token") or session.get("credentials", {}).get("token")
        try:  # tell Google to forget this app's access too; signing out still works if this fails
            import requests
            requests.post("https://oauth2.googleapis.com/revoke", params={"token": token}, timeout=5)
        except Exception as exc:
            logger.info("Could not revoke Google token: %s", exc)
        delete_session(session_id)
    response = JSONResponse({"signed_in": False})
    response.delete_cookie(SESSION_COOKIE)
    return response


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

def session_file(session_id):
    if not session_id or not re.fullmatch(r"[A-Za-z0-9_-]{32,64}", session_id):
        return None
    return SESSION_DIR / f"{session_id}.json"


def load_session(session_id):
    path = session_file(session_id)
    if path is None or not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def save_session(session_id, data):
    SESSION_DIR.mkdir(exist_ok=True)
    session_file(session_id).write_text(json.dumps(data))


def delete_session(session_id):
    path = session_file(session_id)
    if path is not None:
        path.unlink(missing_ok=True)


def get_calendar_service(session_id):
    """Calendar client for the Google account signed in on this browser."""
    try:
        from google.auth.exceptions import RefreshError
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
    except ImportError:
        raise HTTPException(503, "Google Calendar libraries aren't installed on the server.")

    session = load_session(session_id)
    if session is None:
        raise HTTPException(401, "Sign in with Google to add events to your calendar.")
    try:
        credentials = Credentials.from_authorized_user_info(session["credentials"])
    except (KeyError, ValueError):
        delete_session(session_id)
        raise HTTPException(401, "Your Google sign-in is no longer valid. Please sign in again.")

    if not credentials.valid:
        try:
            credentials.refresh(Request())
        except RefreshError:  # the user revoked access or the token expired for good
            delete_session(session_id)
            raise HTTPException(401, "Your Google sign-in expired. Please sign in again.")
        session["credentials"] = json.loads(credentials.to_json())
        save_session(session_id, session)

    return build("calendar", "v3", credentials=credentials, cache_discovery=False)


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
        return "Google refused the request. Sign out, sign in again and allow calendar access."
    if status == 429:
        return "Google is rate limiting us. Wait a moment and try again."
    return "Google Calendar couldn't save this event. Please try again."


def insert_events(session_id, events, tz_name, tz, skip_duplicates):
    service = get_calendar_service(session_id)  # raises a readable 503/401 before anything else can fail
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
def health(request: Request):
    session = load_session(request.cookies.get(SESSION_COOKIE))
    return {
        "speech_installed": importlib.util.find_spec("whisper") is not None,
        "speech_ready": _model is not None,
        "ffmpeg_installed": find_ffmpeg(),
        "calendar_configured": CREDENTIALS_PATH.exists(),
        "signed_in": session is not None,
        "email": session.get("email") if session else None,
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
async def create_events(payload: CreateEventsRequest, request: Request):
    if not payload.events:
        raise HTTPException(422, "There are no events to add.")
    if len(payload.events) > MAX_EVENTS:
        raise HTTPException(422, f"You can add up to {MAX_EVENTS} events at once.")

    tz_name, tz = resolve_time_zone(payload.timezone)
    events = []
    for event in payload.events:
        start, end = event.start, event.end
        if start.tzinfo is not None:  # convert "...Z" / "+02:00" to the user's wall clock
            start = start.astimezone(tz).replace(tzinfo=None) if tz else start.replace(tzinfo=None)
        if end.tzinfo is not None:
            end = end.astimezone(tz).replace(tzinfo=None) if tz else end.replace(tzinfo=None)
        events.append({"activity": event.activity, "location": event.location, "start": start, "end": end})

    results = await run_in_threadpool(insert_events, request.cookies.get(SESSION_COOKIE), events, tz_name, tz,
                                      payload.skip_duplicates)
    created = sum(1 for r in results if r["status"] == "created")
    return {"results": results, "created": created, "timezone": tz_name}


@app.post("/upload")
async def upload_audio(request: Request, file: UploadFile = File(...), timezone: str = Form(DEFAULT_TIME_ZONE)):
    """Original one-shot endpoint: transcribe and add everything straight away."""
    data, suffix = await read_upload(file)
    text = await run_in_threadpool(transcribe_audio, data, suffix)
    parsed = parse_response(text, timezone)
    if not parsed["events"]:
        raise HTTPException(422, (parsed["warnings"] or ["No events found."])[0])

    tz_name, tz = resolve_time_zone(timezone)
    results = await run_in_threadpool(insert_events, request.cookies.get(SESSION_COOKIE), parsed["events"],
                                      tz_name, tz, True)
    return {
        "text": text,
        "events": parsed["events"],
        "google_events": [json_to_google_event(e, tz_name) for e in parsed["events"]],
        "calendar_links": [r.get("link") for r in results if r["status"] == "created"],
        "results": results,
    }
