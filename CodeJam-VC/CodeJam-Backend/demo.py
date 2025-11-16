from fastapi import FastAPI, UploadFile, File
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
import whisper
import os

from main import parse_schedule_to_events, json_to_google_event
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

app = FastAPI()

# Serve static files
app.mount("/static", StaticFiles(directory="/Users/walter/Desktop/CodeJam-VC/CodeJam-Frontend"), name="static")

# Redirect root to homepage
@app.get("/")
def home():
    return RedirectResponse("/static/HomePage.html")

# Load Whisper model
model = whisper.load_model("small.en")

# Google Calendar setup
SCOPES = ["https://www.googleapis.com/auth/calendar"]
creds = Credentials.from_authorized_user_file("token.json", SCOPES)
service = build("calendar", "v3", credentials=creds)

# Upload endpoint
@app.post("/upload")
async def upload_audio(file: UploadFile = File(...)):
    # Ensure uploads folder exists
    os.makedirs("uploads", exist_ok=True)
    file_path = f"uploads/{file.filename}"

    # Save audio file
    with open(file_path, "wb") as f:
        f.write(await file.read())

    # Transcribe audio
    result = model.transcribe(file_path)
    transcription = result["text"]

    # Parse transcription to events
    events = parse_schedule_to_events(transcription)
    event_dicts = [e.to_dict() for e in events]

    # Convert to Google Calendar event format
    google_events = [json_to_google_event(e) for e in event_dicts]

    # Insert events into Google Calendar
    inserted_links = []
    for g_event in google_events:
        created = service.events().insert(
            calendarId="primary",
            body=g_event
        ).execute()
        inserted_links.append(created.get("htmlLink"))

    return {
        "text": transcription,
        "events": event_dicts,
        "google_events": google_events,
        "calendar_links": inserted_links
    }
