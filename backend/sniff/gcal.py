"""Google Calendar integration. Events are only created from POST /reminders, which is the user's approval click."""
import json
from datetime import date, timedelta

from . import config

SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
CLIENT_FILE = config.SECRETS_DIR / "google_client.json"
TOKEN_FILE = config.DATA_DIR / "google_token.json"  # only where no credential store works (and where older versions kept it)
KEYRING = ("Sniff", "google-calendar-token")  # service and user name in the system's credential store


def load_token() -> str | None:
    """The saved Google sign-in token, from the system's credential store (Windows Credential Manager, the macOS
    Keychain, or the Secret Service on Linux), else from TOKEN_FILE."""
    try:
        import keyring

        if text := keyring.get_password(*KEYRING):
            return text
    except Exception:  # no store, a locked one, or a broken backend: the file is the fallback
        pass
    return TOKEN_FILE.read_text() if TOKEN_FILE.exists() else None


def save_token(text: str) -> None:
    """Into the credential store, and the old file goes. Without a working store, into TOKEN_FILE."""
    try:
        import keyring

        keyring.set_password(*KEYRING, text)
        TOKEN_FILE.unlink(missing_ok=True)
        return
    except Exception:
        pass
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(text)


def _service():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    text = load_token()
    creds = Credentials.from_authorized_user_info(json.loads(text), SCOPES) if text else None
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not CLIENT_FILE.exists():
                raise RuntimeError(f"Google Calendar is not set up. Put the OAuth client JSON at {CLIENT_FILE}")
            creds = InstalledAppFlow.from_client_secrets_file(str(CLIENT_FILE), SCOPES).run_local_server(port=0)
        save_token(creds.to_json())
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def connected() -> bool:
    """True if a saved Google token exists and loads (an expired one with a refresh token still counts). Never returns the token."""
    try:
        from google.oauth2.credentials import Credentials
        creds = Credentials.from_authorized_user_info(json.loads(load_token() or "null"), SCOPES)
        return bool(creds.valid or creds.refresh_token)
    except Exception:
        return False


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
