from google_auth_oauthlib.flow import InstalledAppFlow
from google.oauth2.credentials import Credentials
import json

SCOPES = ["https://www.googleapis.com/auth/calendar"]

flow = InstalledAppFlow.from_client_secrets_file(
    'credentials.json', SCOPES)
creds = flow.run_local_server(port=0)

# Save the token for later
with open('token.json', 'w') as token_file:
    token_file.write(creds.to_json())