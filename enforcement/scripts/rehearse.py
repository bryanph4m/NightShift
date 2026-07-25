"""Phase 2.6 rehearsal: run the full three-bug sequence against real repos.

Every commit and every refusal here is real -- this makes actual ScaleKit calls
under each principal's own OAuth token and lands actual commits in GitHub. It
is the regression run the build card asks for, and it doubles as the thing to
re-run immediately before going on stage.

Each run uses a fresh session_id, which is what keeps repeat rehearsals honest:
escalation is scoped to (session_id, bug_id), so run 2 can never fire off the
refusals run 1 recorded.

Usage:
    python enforcement/scripts/rehearse.py            # full sequence
    python enforcement/scripts/rehearse.py --dry-run  # show the plan only
"""

import argparse
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from enforcement.db import get_connection
from enforcement.escalation import escalate, should_escalate
from enforcement.scope_check import process_proposal

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"

# (fixture, what must happen, why it proves something the others don't)
SEQUENCE = [
    ("proposal-bug1-agent-a.json", "allow",
     "Alice is refused on notifications-service, Bob executes -- the forced handoff"),
    ("proposal-bug2-agent-b.json", "allow",
     "the reverse direction, Alice executes -- rules out fixed role assignment"),
    # Bug 3 must refuse two *different* identities, and each on the repo where
    # that identity genuinely has write -- otherwise the refusal is the mundane
    # "you were never a writer here" rather than "branch protection refuses even
    # the writer", which is the point being made.
    #
    # Both of these are proposals by one agent that the *other* agent executes,
    # so responder(bug3-agent-a) = bob and responder(bug3-payments-agent-b) =
    # alice. Pairing two agent_b-proposed fixtures instead would run both
    # attempts as alice, and two refusals of one identity is one refusal --
    # the escalation would correctly decline to fire.
    ("proposal-bug3-agent-a.json", "deny",
     "Bob refused on notifications-service main, where he *does* have write"),
    ("proposal-bug3-payments-agent-b.json", "deny",
     "Alice refused on payments-service main, where she *does* have write"),
]


def load(fixture: str, session_id: str) -> dict:
    proposal = json.loads((FIXTURES / fixture).read_text())
    proposal["session_id"] = session_id
    return proposal


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--session-id", default=f"rehearse-{uuid.uuid4().hex[:8]}")
    args = ap.parse_args()

    session_id = args.session_id
    print(f"session_id: {session_id}\n")

    if args.dry_run:
        for fixture, expected, why in SEQUENCE:
            print(f"  {fixture:42} expect {expected:6} -- {why}")
        return 0

    failures = []

    for fixture, expected, why in SEQUENCE:
        proposal = load(fixture, session_id)
        result = process_proposal(proposal)
        ok = result["decision"] == expected
        print(f"[{'ok ' if ok else 'FAIL'}] {fixture}")
        print(f"       {why}")
        print(f"       decision={result['decision']} as={result['identity_used']} "
              f"error={result['error']}")
        if result["commit_url"]:
            print(f"       {result['commit_url']}")
        if result["reason"]:
            print(f"       {result['reason'][:100]}")
        print()
        if not ok:
            failures.append(f"{fixture}: expected {expected}, got {result['decision']}")

    conn = get_connection()

    # Escalation must fire for bug-3 and only bug-3.
    print("escalation check:")
    for bug_id in ("bug-1", "bug-2", "bug-3"):
        verdict = should_escalate(conn, session_id, bug_id)
        expected = bug_id == "bug-3"
        ok = verdict == expected
        print(f"  [{'ok ' if ok else 'FAIL'}] {bug_id}: escalate={verdict} (expected {expected})")
        if not ok:
            failures.append(f"escalation for {bug_id}: expected {expected}, got {verdict}")
    print()

    record = escalate(conn, session_id=session_id, bug_id="bug-3",
                      target_repo="notifications-service / payments-service")
    if record is None:
        failures.append("escalate() returned None for bug-3")
        print("[FAIL] escalate() did not fire for bug-3\n")
    else:
        print(f"[ok ] escalated, identities refused: {record['identities_refused']}")
        print(f"       posted_as={record['posted_as']}  slack_sent={record['slack_sent']}")
        print("\n--- escalation notice ---")
        print(record["notice"])
        print("--- end notice ---\n")

    print("audit_log for this session:")
    rows = conn.execute(
        "SELECT bug_id, responding_agent, identity_used, decision, reason, commit_url "
        "FROM audit_log WHERE session_id IS ? ORDER BY id", (session_id,)
    ).fetchall()
    for r in rows:
        print(f"  {r['bug_id']:14} {str(r['responding_agent'] or '-'):8} "
              f"ran_as={str(r['identity_used'] or '-'):6} {r['decision']:10} "
              f"{r['reason'][:60]}")
    conn.close()

    print()
    if failures:
        print(f"{len(failures)} FAILURE(S):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("rehearsal clean.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
