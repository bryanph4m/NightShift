"""
Phone escalation: when both agents have been genuinely refused, ring a human.

This fires only after two real ScaleKit denials against the same bug_id — the
same rule the Slack path follows. The agents do not decide a change "looks
risky"; they exhaust their own authority and then wake someone.

Backed by AgentPhone. The call is a live conversation rather than a recording,
because a person woken at 2am asks questions — which repo, who tried, what is
blocked — and a voicemail cannot answer them.

Setup — add to .env:
    AGENTPHONE_API_KEY=sk_live_...
    AGENTPHONE_AGENT_ID=agt_...
    ONCALL_PHONE_NUMBER=+1...        # the human to wake, E.164

Test standalone:  python escalate_phone.py
"""

import os
import sys
import time

from dotenv import load_dotenv

load_dotenv()


class PhoneEscalationError(RuntimeError):
    pass


def build_system_prompt(bug_id: str, denials: list[tuple[str, str]]) -> str:
    """Instructions for the calling agent.

    Deliberately bounded: it is given only the refusal facts and told to admit
    ignorance rather than improvise. An escalation that invents detail is worse
    than one that says "I don't have that" — the human is about to act on it.
    """
    refusals = "\n".join(f"  - {name} was denied: {reason}" for name, reason in denials)
    return (
        "You are Night Shift, an automated on-call escalation. You are phoning a "
        "human engineer because two AI agents both hit the limits of their real "
        "GitHub permissions and cannot proceed without a person.\n\n"
        "What happened:\n"
        f"  - CI failed and the agents diagnosed the bug ({bug_id}).\n"
        "  - The fix has to land on the protected main branch.\n"
        f"{refusals}\n"
        "  - Neither agent can merge to protected main, so both have stopped "
        "and are waiting.\n\n"
        "Tell them clearly and briefly why they are being woken: both agents "
        "were refused, and a human merge is required. Keep it under about "
        "thirty seconds unless they ask questions. Answer follow-ups using only "
        "the facts above; if asked something you do not know, say you do not "
        "have that detail rather than guessing. Do not offer to take any action "
        "yourself — you are only notifying them. Once they acknowledge, thank "
        "them and end the call."
    )


def _client():
    api_key = os.environ.get("AGENTPHONE_API_KEY")
    if not api_key:
        raise PhoneEscalationError("AGENTPHONE_API_KEY is not set")
    from agentphone import AgentPhone

    return AgentPhone(api_key=api_key)


def place_call(bug_id: str, denials: list[tuple[str, str]]) -> str:
    """Ring the on-call human. Returns the call id."""
    agent_id = os.environ.get("AGENTPHONE_AGENT_ID")
    to_number = os.environ.get("ONCALL_PHONE_NUMBER")

    missing = [
        name
        for name, val in (
            ("AGENTPHONE_API_KEY", os.environ.get("AGENTPHONE_API_KEY")),
            ("AGENTPHONE_AGENT_ID", agent_id),
            ("ONCALL_PHONE_NUMBER", to_number),
        )
        if not val
    ]
    if missing:
        raise PhoneEscalationError(f"missing env vars: {', '.join(missing)}")

    client = _client()
    call = client.calls.make(
        agent_id=agent_id,
        to_number=to_number,
        system_prompt=build_system_prompt(bug_id, denials),
        initial_greeting=(
            "Hi, this is Night Shift with an automated escalation. "
            "Do you have a moment?"
        ),
    )
    return getattr(call, "id", "")


def wait_for_transcript(call_id: str, timeout_s: int = 180) -> list:
    """Poll until the call ends, then return its transcript turns.

    Worth capturing for the audit trail: it is the record of what the human was
    actually told at the moment the system handed the decision over.
    """
    client = _client()
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        call = client.calls.get(call_id)
        if getattr(call, "status", "") in ("completed", "failed"):
            return list(getattr(call, "transcripts", []) or [])
        time.sleep(5)
    return []


if __name__ == "__main__":
    demo_denials = [
        ("Alice", "branch protection on main refused the write"),
        ("Bob", "branch protection on main refused the write"),
    ]
    try:
        call_id = place_call("bug-3", demo_denials)
    except PhoneEscalationError as exc:
        sys.exit(f"phone escalation not configured: {exc}")

    print(f"calling {os.environ.get('ONCALL_PHONE_NUMBER')} — call id {call_id}")
    print("waiting for the call to finish...")
    for turn in wait_for_transcript(call_id):
        role = getattr(turn, "role", "?")
        text = getattr(turn, "transcript", getattr(turn, "content", ""))
        print(f"  [{role}] {text}")
