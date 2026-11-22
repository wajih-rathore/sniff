"""Google Calendar integration. Events are only created from POST /reminders, which is the user's approval click."""
from datetime import date, timedelta

from . import config

SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
CLIENT_FILE = config.SECRETS_DIR / "google_client.json"
TOKEN_FILE = config.DATA_DIR / "google_token.json"


def _service():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES) if TOKEN_FILE.exists() else None
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not CLIENT_FILE.exists():
                raise RuntimeError(f"Google Calendar is not set up. Put the OAuth client JSON at {CLIENT_FILE}")
            creds = InstalledAppFlow.from_client_secrets_file(str(CLIENT_FILE), SCOPES).run_local_server(port=0)
        TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        TOKEN_FILE.write_text(creds.to_json())
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def create_event(title: str, day: date, description: str) -> str:
    """All-day event on `day`, with pop-up reminders a week and a day before. Returns the event id."""
    body = {
        "summary": title,
        "description": description,
        "start": {"date": day.isoformat()},
        "end": {"date": (day + timedelta(days=1)).isoformat()},
        "reminders": {"useDefault": False, "overrides": [{"method": "popup", "minutes": 7 * 24 * 60}, {"method": "popup", "minutes": 24 * 60}]},
    }
    return _service().events().insert(calendarId="primary", body=body).execute()["id"]
