"""
Phone escalation: when both agents have been genuinely refused, ring a human.

This fires only after two real ScaleKit denials against the same bug_id — the
same rule the Slack path follows. The agents do not decide a change "looks
risky"; they exhaust their own authority and then wake someone.

Setup (Twilio):
  1. Sign up at twilio.com/try-twilio (free trial includes a number)
  2. From the console dashboard copy Account SID and Auth Token
  3. Copy your trial phone number (this is TWILIO_FROM_NUMBER)
  4. On a trial account you must verify the destination number first:
     Console -> Phone Numbers -> Verified Caller IDs -> add your mobile
  5. Add to .env:
       TWILIO_ACCOUNT_SID=AC...
       TWILIO_AUTH_TOKEN=...
       TWILIO_FROM_NUMBER=+1...
       ONCALL_PHONE_NUMBER=+1...      # the human to wake, E.164 format

Test it standalone:  python escalate_phone.py
"""

import os
import sys
from xml.sax.saxutils import escape

import httpx
from dotenv import load_dotenv

load_dotenv()

TWILIO_API = "https://api.twilio.com/2010-04-01"


class PhoneEscalationError(RuntimeError):
    pass


def build_twiml(bug_id: str, denials: list[tuple[str, str]]) -> str:
    """Spoken script for the call. Kept short — a woken human needs the reason
    and the ask, not a report."""
    lines = [
        "This is Night Shift, calling about a production issue.",
        f"Both agents were refused on {escape(bug_id.replace('-', ' '))}.",
    ]
    for name, reason in denials:
        lines.append(f"{escape(name)} was denied. {escape(reason)}.")
    lines.append(
        "The change has to land on a protected branch, so it needs a human. "
        "The agents have paused and are waiting for you."
    )
    say = " ".join(lines)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<Response><Pause length="1"/><Say voice="alice">{say}</Say>'
        f'<Pause length="1"/><Say voice="alice">{say}</Say></Response>'
    )


def place_call(bug_id: str, denials: list[tuple[str, str]]) -> str:
    """Ring the on-call human. Returns the Twilio call SID."""
    sid = os.environ.get("TWILIO_ACCOUNT_SID")
    token = os.environ.get("TWILIO_AUTH_TOKEN")
    from_number = os.environ.get("TWILIO_FROM_NUMBER")
    to_number = os.environ.get("ONCALL_PHONE_NUMBER")

    missing = [
        name
        for name, val in (
            ("TWILIO_ACCOUNT_SID", sid),
            ("TWILIO_AUTH_TOKEN", token),
            ("TWILIO_FROM_NUMBER", from_number),
            ("ONCALL_PHONE_NUMBER", to_number),
        )
        if not val
    ]
    if missing:
        raise PhoneEscalationError(f"missing env vars: {', '.join(missing)}")

    resp = httpx.post(
        f"{TWILIO_API}/Accounts/{sid}/Calls.json",
        auth=(sid, token),
        data={
            "To": to_number,
            "From": from_number,
            "Twiml": build_twiml(bug_id, denials),
        },
        timeout=30,
    )
    if resp.status_code >= 300:
        raise PhoneEscalationError(f"Twilio returned {resp.status_code}: {resp.text[:300]}")
    return resp.json().get("sid", "")


if __name__ == "__main__":
    demo_denials = [
        ("Alice", "branch protection on main refused the write"),
        ("Bob", "branch protection on main refused the write"),
    ]
    try:
        call_sid = place_call("bug-3", demo_denials)
        print(f"calling {os.environ.get('ONCALL_PHONE_NUMBER')} — call sid {call_sid}")
    except PhoneEscalationError as exc:
        sys.exit(f"phone escalation not configured: {exc}")
