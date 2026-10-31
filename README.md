# CodeJam-VC
A voice calendar generator

📅 VoiceCal – Voice-Powered Google Calendar Assistant

A simple and friendly web app that lets users speak their calendar events and automatically adds them to Google Calendar.

VoiceCal is a lightweight web application that lets users speak reminders or events, converts their speech into text, and automatically creates

Google Calendar events through the Google Calendar API.

<img width="1470" height="956" alt="Screenshot 2025-11-16 at 5 41 04 PM" src="https://github.com/user-attachments/assets/7ea25a60-fa44-4d5d-b14f-1e25b8d694cd" />

___________________________________________________________________________________________________________________________________________


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

<img width="739" height="517" alt="Screenshot 2025-11-16 at 5 52 51 PM" src="https://github.com/user-attachments/assets/1533d765-2491-42f9-9875-8877cd434fbc" />

_______________________________________________________________________________________________________________________________________________

TRY IT!

git clone https://github.com/WalllerG/CodeJam-VC

1.Install Python Dependencies
We used Python 3.9.9 and PyTorch 1.10.1 to train and test our models, but the codebase is expected to be compatible with Python 3.8-3.11 and
recent PyTorch versions. The codebase also depends on a few Python packages, most notably OpenAI's tiktoken for their fast tokenizer implementation. You can download and install (or update to) the latest release of Whisper with the following command:

pip install -U openai-whisper

pip install --upgrade --no-deps --force-reinstall git+https://github.com/openai/whisper.git

It also requires the command-line tool ffmpeg to be installed on your system, which is available from most package managers:
# on Ubuntu or Debian
sudo apt update && sudo apt install ffmpeg

# on Arch Linux
sudo pacman -S ffmpeg

# on MacOS using Homebrew (https://brew.sh/)
brew install ffmpeg

# on Windows using Chocolatey (https://chocolatey.org/)
choco install ffmpeg

# on Windows using Scoop (https://scoop.sh/)
scoop install ffmpeg

You may need rust installed as well, in case tiktoken does not provide a pre-built wheel for your platform. If you see installation errors during the pip install command above, please follow the Getting started page to install Rust development environment. Additionally, you may need to configure the PATH environment variable, e.g. export PATH="$HOME/.cargo/bin:$PATH". If the installation fails with No module named 'setuptools_rust', you need to install setuptools_rust, e.g. by running:

pip install setuptools-rust

Install the Google client library for Python:

pip install --upgrade google-api-python-client google-auth-httplib2 google-auth-oauthlib

________________________________________________________________________________________________________________________________________________


Now you are Half way there!
To be able to completely run this program, you have to create a google cloud account to reqeust API usage from google. DONT WORRY YOU CAN HAVE A 90 FREE TRIAL FOR THAT!

2. Set up Google Cloud OAuth

Go to Google Cloud Console

Create a new project

Enable Google Calendar API

Create OAuth Client ID → Desktop App

Choose the Download Json button and then rename the file to credentials.json

Place it in your backend folder

GO to the Scopes page

Add scopes and click the first free scopes that show on the page and search calendar then add the first one shows in the result as well

The one you search should be like this: https://www.googleapis.com/auth/calenda

BEFORE TO RUN THIS PROGRAM, WE NEED TO RUN the file quickstart.py once. The file is in your CodeJam-Backend Folder
It will take you to the google login page to make sure you authenticate the page
Once you successfully ran it you should see a page like this:

<img width="1470" height="956" alt="Screenshot 2025-11-16 at 5 21 06 PM" src="https://github.com/user-attachments/assets/a38fa7cf-87ca-4d3d-b970-9b29ca872da6" />

To make sure, there should be a token.json file in your Backend folder as well

________________________________________________________________________________________________________________________________________________

No client ID needs to be pasted into the frontend any more: the server uses token.json directly, and the
status pill at the top of the page tells you whether Google Calendar is connected.

credentials.json and token.json are listed in .gitignore - never commit them.

All Python dependencies can also be installed in one go:

pip install -r CodeJam-VC/CodeJam-Backend/requirements.txt

________________________________________________________________________________________________________________________________________________

HOW THE SCHEDULE BUILDER WORKS

1. Tap the microphone (or press Space) and say one or more events, e.g.
   "Gym tomorrow at 7 am and lunch with Sam at noon at Joe's, then study for 2 hours".
   You can also type the same thing in the "or type it" box.
2. VoiceCal turns it into events in "Your schedule". Anything it had to guess (no time given, AM vs PM,
   a date that doesn't exist...) is highlighted so you can fix it. You can edit, remove, or add events by hand.
3. Press "Add to Google Calendar". Events already on your calendar are skipped instead of duplicated.

Run the parser tests with:

cd CodeJam-VC/CodeJam-Backend

python -m pytest test_parser.py

________________________________________________________________________________________________________________________________________________

YOU ARE ALL SET RIGHT NOW

TO RUN THE PROGRAM

SIMPLY run these command in your terminal:

cd CodeJam-Backend
uvicorn demo:app --reload

Open your Browser, go to http://127.0.0.1:8000 (Or the url that uvicorn give you in the terminal)
_______________________________________________________________________________________________________________________________________________

Group Project Made By:

Walter Guo

Ismaël Péan

Joel Saputra

Ayoub Melliani
