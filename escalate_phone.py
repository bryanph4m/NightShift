"""
Phone escalation: when both agents have been genuinely refused, ring a human.

This fires only after two real ScaleKit denials against the same bug_id — the
same rule the Slack path follows. The agents do not decide a change "looks
risky"; they exhaust their own authority and then wake someone.

Backed by AgentPhone (https://agentphone.ai). The call is placed by a
conversational agent rather than a recording, so the woken human can ask "which
repo?" or "who tried?" and get an answer instead of a voicemail.

Setup:
  Sign up once (issues an api_key and agent_id), then add to .env:
    AGENTPHONE_API_KEY=sk_live_...
    AGENTPHONE_AGENT_ID=agt_...
    ONCALL_PHONE_NUMBER=+1...        # the human to wake, E.164

Test standalone:  python escalate_phone.py
"""

import os
import sys

import httpx
from dotenv import load_dotenv

load_dotenv()

AGENTPHONE_API = "https://api.agentphone.ai"


class PhoneEscalationError(RuntimeError):
    pass


def build_system_prompt(bug_id: str, denials: list[tuple[str, str]]) -> str:
    """Instructions for the calling agent.

    Written so the agent can hold a short back-and-forth: a person woken at 2am
    asks questions, and a script that can only recite would be useless.
    """
    refusals = "\n".join(f"  - {name} was denied: {reason}" for name, reason in denials)
    return (
        "You are Night Shift, an automated on-call escalation. You are phoning a "
        "human engineer because two AI agents both hit the limits of their real "
        "GitHub permissions and cannot proceed without a person.\n\n"
        f"What happened:\n"
        f"  - CI failed and the agents diagnosed the bug ({bug_id}).\n"
        f"  - The fix has to land on the protected main branch.\n"
        f"{refusals}\n"
        "  - Neither agent has permission to merge to protected main, so they "
        "have stopped and are waiting.\n\n"
        "Your job: tell them clearly and briefly why they are being woken, that "
        "both agents were refused, and that a human merge is required. Keep it "
        "under about thirty seconds unless they ask questions. Answer follow-up "
        "questions using only the facts above — if you are asked something you "
        "do not know, say you do not have that detail rather than guessing. Do "
        "not offer to take any action yourself; you are only notifying them. "
        "Once they acknowledge, thank them and end the call."
    )


def place_call(bug_id: str, denials: list[tuple[str, str]]) -> str:
    """Ring the on-call human. Returns the call id."""
    api_key = os.environ.get("AGENTPHONE_API_KEY")
    agent_id = os.environ.get("AGENTPHONE_AGENT_ID")
    to_number = os.environ.get("ONCALL_PHONE_NUMBER")

    missing = [
        name
        for name, val in (
            ("AGENTPHONE_API_KEY", api_key),
            ("AGENTPHONE_AGENT_ID", agent_id),
            ("ONCALL_PHONE_NUMBER", to_number),
        )
        if not val
    ]
    if missing:
        raise PhoneEscalationError(f"missing env vars: {', '.join(missing)}")

    resp = httpx.post(
        f"{AGENTPHONE_API}/v1/calls",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "agentId": agent_id,
            "toNumber": to_number,
            "systemPrompt": build_system_prompt(bug_id, denials),
            "initialGreeting": (
                "Hi, this is Night Shift calling with an automated escalation. "
                "Do you have a moment?"
            ),
        },
        timeout=30,
    )
    if resp.status_code >= 300:
        raise PhoneEscalationError(f"AgentPhone returned {resp.status_code}: {resp.text[:300]}")
    return resp.json().get("id", "")


def get_call(call_id: str) -> dict:
    """Fetch a call's status and transcript. Poll until status is completed."""
    api_key = os.environ.get("AGENTPHONE_API_KEY")
    if not api_key:
        raise PhoneEscalationError("AGENTPHONE_API_KEY is not set")
    resp = httpx.get(
        f"{AGENTPHONE_API}/v1/calls/{call_id}",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


if __name__ == "__main__":
    demo_denials = [
        ("Alice", "branch protection on main refused the write"),
        ("Bob", "branch protection on main refused the write"),
    ]
    try:
        call_id = place_call("bug-3", demo_denials)
        print(f"calling {os.environ.get('ONCALL_PHONE_NUMBER')} — call id {call_id}")
    except PhoneEscalationError as exc:
        sys.exit(f"phone escalation not configured: {exc}")
