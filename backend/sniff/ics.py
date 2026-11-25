"""A standard .ics file (RFC 5545) for one all-day reminder, so no Google account is needed."""
from datetime import date, datetime, timedelta, timezone


def _text(s: str) -> str:
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _fold(line: str) -> str:
    """Lines longer than 75 bytes continue on the next line, which starts with a space."""
    out, cur = [], ""
    for ch in line:
        if len((cur + ch).encode()) > (75 if not out else 74):
            out.append(cur)
            cur = ""
        cur += ch
    return "\r\n ".join(out + [cur])


def make_ics(uid: str, title: str, day: date, description: str) -> str:
    """All-day event on `day`, with display alarms 7 days and 1 day before."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    alarms = [line for n in (7, 1) for line in ("BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{_text(title)}", f"TRIGGER:-P{n}D", "END:VALARM")]
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Sniff//Reminder//EN", "CALSCALE:GREGORIAN", "BEGIN:VEVENT", f"UID:{uid}", f"DTSTAMP:{stamp}",
             f"DTSTART;VALUE=DATE:{day:%Y%m%d}", f"DTEND;VALUE=DATE:{day + timedelta(days=1):%Y%m%d}",
             f"SUMMARY:{_text(title)}", f"DESCRIPTION:{_text(description)}", *alarms, "END:VEVENT", "END:VCALENDAR"]
    return "".join(_fold(line) + "\r\n" for line in lines)
