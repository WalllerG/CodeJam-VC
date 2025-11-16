# CodeJam-VC
A voice calendar generator
📅 VoiceCal – Voice-Powered Google Calendar Assistant
A simple and friendly web app that lets users speak their calendar events and automatically adds them to Google Calendar.
VoiceCal is a lightweight web application that lets users speak reminders or events, converts their speech into text, and automatically creates Google Calendar events through the Google Calendar API.
🌟 Overview
This project combines:

FastAPI backend
HTML/CSS/JavaScript frontend
OAuth2 Google Calendar integration
Browser microphone recording
Speech-to-text processing
Automated calendar event creation
Success animations and clean UI

1.Users simply tap the microphone, speak their event (like “Dinner with Alex tomorrow at 7”), and VoiceCal:
2.Records audio in the browser
3.Sends it to your FastAPI backend
4.Processes the speech with AI
5.Adds the event directly to the user’s Google Calendar
The goal is to offer a smooth, fast, and delightful experience — no typing required.


Project structure:
CodeJam-VC/
│
├── CodeJam-Backend/
│   ├── main.py                # FastAPI backend server
│   ├── credentials.json       # Google OAuth client credentials
│   ├── token.json             # User OAuth token (generated automatically)
|   └── uploads/ # Temp folder for audio files                    
├── CodeJam-Frontend/
|   ├── HomePage.html
│   ├── mic.html
│   ├── HomePage.css
│   ├── images...
└── README.md

TRY IT!
WE are using mainly Python for this project so make sure you have alreay installed Python 3.9+. but < Python 3.14
