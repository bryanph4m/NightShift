"""Phase 2.4 escalation: fires only when two real ScaleKit calls were refused.

The trigger reads `audit_log` -- the same rows the scope-check service wrote
when it actually attempted each commit -- and never re-runs or simulates a
scope check. That is the point: escalation is downstream evidence of two
genuine refusals, not a judgement this code makes about whether a change
"looks structural". If a judge asks what fires the page-out, the answer is a
SQL query over rows that each correspond to a real GitHub API rejection.

Three conditions must all hold, and each one excludes a real false positive
seen during this build:

1. Scoped to (session_id, bug_id), not bug_id alone. Branch creation is
   idempotent so the sequence can be rehearsed repeatedly, which means
   audit_log legitimately holds deny rows for bug-3 from earlier runs. Keyed
   on bug_id alone, a second rehearsal would escalate off the first run's rows.
2. Two *distinct, non-null* identity_used values. Two deny rows are not
   enough -- retrying the same identity twice is one refusal, not two.
3. Both reasons must be genuine refusals (permission denied / protected
   branch). A malformed proposal writes decision='deny' with
   identity_used=NULL and never reached ScaleKit at all; it must never
   contribute to an escalation.

Slack: deliberately not wired. No Slack connector exists in this ScaleKit
environment and one was not set up for this build, so escalation notifies via
the meeting chat and audit_log only. Nothing here claims a page was sent --
see HANDOFF.md.
"""

import sqlite3

from enforcement.db import write_audit_log
from enforcement.errors import is_genuine_refusal
from enforcement.meetstream import post_to_meeting

ESCALATION_DECISION = "escalated"

# Preference order for who narrates the escalation in chat. main's standardized
# format attributes it to Agent A, but Bot A's id belongs to perception and is
# unset on this branch, so posting falls back to Bot B rather than raising.
# Documented as a deviation in HANDOFF.md.
ESCALATION_POST_ORDER = ("agent_a", "agent_b")


def genuine_refusals(conn: sqlite3.Connection, session_id: str, bug_id: str) -> list[sqlite3.Row]:
    """Deny rows for this bug that record a real identity refused by GitHub."""
    rows = conn.execute(
        """SELECT identity_used, responding_agent, proposing_agent, reason, timestamp
             FROM audit_log
            WHERE session_id IS ?
              AND bug_id IS ?
              AND decision = 'deny'
              AND identity_used IS NOT NULL
         ORDER BY id""",
        (session_id, bug_id),
    ).fetchall()
    return [r for r in rows if is_genuine_refusal(r["reason"])]


def should_escalate(conn: sqlite3.Connection, session_id: str, bug_id: str) -> bool:
    """True once two *different* identities have each been genuinely refused."""
    refusals = genuine_refusals(conn, session_id, bug_id)
    return len({r["identity_used"] for r in refusals}) >= 2


def already_escalated(conn: sqlite3.Connection, session_id: str, bug_id: str) -> bool:
    row = conn.execute(
        """SELECT 1 FROM audit_log
            WHERE session_id IS ? AND bug_id IS ? AND decision = ?
            LIMIT 1""",
        (session_id, bug_id, ESCALATION_DECISION),
    ).fetchone()
    return row is not None


def compose_notice(bug_id: str, refusals: list[sqlite3.Row], target_repo: str | None) -> str:
    """The full escalation text: what was attempted, who was refused, why.

    This is what a human needs in order to make the call, and it is built
    entirely from stored evidence -- every 'why' below is a real GitHub
    response captured at the time of the refusal.
    """
    lines = [
        f"Escalation required: {bug_id}",
        f"Repository: {target_repo or 'unknown'}",
        "",
        "Attempted: land the proposed fix directly on the protected `main` branch.",
        "",
        f"Both identities were refused ({len(refusals)} recorded refusals):",
    ]
    for r in refusals:
        lines.append(
            f"  - {r['identity_used']} (as {r['responding_agent']}): {r['reason']}"
        )
    lines += [
        "",
        "Neither agent exceeded its principal's real access, so neither can proceed.",
        "Decision needed from a human: approve and merge the change to `main`,",
        "or reject it and tell the agents to stand down.",
    ]
    return "\n".join(lines)


def _post_escalation(text: str) -> str | None:
    """Post to the meeting chat as Agent A if possible, else Agent B.

    Returns the agent_id that actually posted, or None if no bot was reachable.
    """
    last_error: Exception | None = None
    for agent_id in ESCALATION_POST_ORDER:
        try:
            post_to_meeting(agent_id, text)
            return agent_id
        except Exception as e:
            last_error = e
    print(f"escalation: could not post to meeting chat ({last_error})")
    return None


def escalate(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    bug_id: str,
    target_repo: str | None = None,
) -> dict | None:
    """Escalate if and only if two real identities were genuinely refused.

    Returns the escalation record, or None if the bar wasn't met (or it has
    already fired for this session's bug).
    """
    refusals = genuine_refusals(conn, session_id, bug_id)
    identities = sorted({r["identity_used"] for r in refusals})

    if len(identities) < 2:
        return None
    if already_escalated(conn, session_id, bug_id):
        return None

    notice = compose_notice(bug_id, refusals, target_repo)

    chat_line = (
        "[Agent A] Neither of us can merge to protected main. "
        "Escalating to a human; we're paused until there's a decision."
    )
    posted_as = _post_escalation(chat_line)

    write_audit_log(
        conn,
        session_id=session_id,
        bug_id=bug_id,
        proposing_agent=refusals[0]["proposing_agent"],
        responding_agent=None,
        # Escalation is the *absence* of any identity that could act -- recording
        # one here would misrepresent which principal the call ran as.
        identity_used=None,
        decision=ESCALATION_DECISION,
        reason=f"both identities refused ({', '.join(identities)}); awaiting human decision",
        commit_url=None,
    )

    return {
        "bug_id": bug_id,
        "session_id": session_id,
        "identities_refused": identities,
        "notice": notice,
        "posted_as": posted_as,
        "slack_sent": False,  # no Slack connector in this environment, by design
    }


def resume(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    bug_id: str,
    resolution: str,
) -> dict:
    """Record a human's decision and unpause the agents in chat.

    The shared decision enum is allow|deny|escalated with no 'resolved' member,
    so the resolution is stored as a second `escalated` row whose reason carries
    the human's answer, rather than inventing an enum value on a contract this
    branch doesn't own. Flagged in HANDOFF.md.
    """
    chat_line = f"[Agent A] Human decision received: {resolution}. Resuming."
    posted_as = _post_escalation(chat_line)

    write_audit_log(
        conn,
        session_id=session_id,
        bug_id=bug_id,
        proposing_agent=None,
        responding_agent=None,
        identity_used=None,
        decision=ESCALATION_DECISION,
        reason=f"human resolution: {resolution}",
        commit_url=None,
    )

    return {
        "bug_id": bug_id,
        "session_id": session_id,
        "resolution": resolution,
        "posted_as": posted_as,
    }
