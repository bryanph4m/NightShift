"""Read-only check of the escalation trigger against the real audit_log.

Unit tests use synthetic rows. This runs the same trigger over the rows that
real ScaleKit calls actually produced, so the thing demonstrated on stage is
the thing that was verified. Writes nothing.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from enforcement.db import get_connection
from enforcement.escalation import genuine_refusals, should_escalate


def main() -> None:
    conn = get_connection()

    pairs = conn.execute(
        "SELECT DISTINCT session_id, bug_id FROM audit_log ORDER BY session_id, bug_id"
    ).fetchall()

    if not pairs:
        print("audit_log is empty -- run the Phase 2.3 fixtures first.")
        return

    for p in pairs:
        session_id, bug_id = p["session_id"], p["bug_id"]
        refusals = genuine_refusals(conn, session_id, bug_id)
        identities = sorted({r["identity_used"] for r in refusals})
        verdict = should_escalate(conn, session_id, bug_id)

        print(f"{session_id} / {bug_id}")
        print(f"  genuine refusals: {len(refusals)}  distinct identities: {identities}")
        print(f"  escalate: {verdict}")
        for r in refusals:
            print(f"    - {r['identity_used']}: {r['reason'][:88]}")
        print()

    conn.close()


if __name__ == "__main__":
    main()
