"""audit_log storage. This branch is the only writer; perception writes
sessions, transcript_segments, and bugs. Both read all four."""

import os
import sqlite3
from datetime import datetime, timezone

DATABASE_PATH = os.environ.get("DATABASE_URL", "sqlite:///nightshift.db").removeprefix("sqlite:///")

SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_log (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id       TEXT,
  bug_id           TEXT,
  proposing_agent  TEXT,
  responding_agent TEXT,
  identity_used    TEXT,
  decision         TEXT,
  reason           TEXT,
  commit_url       TEXT,
  timestamp        TEXT
);
"""


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute(SCHEMA)
    conn.commit()
    return conn


def write_audit_log(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    bug_id: str,
    proposing_agent: str,
    responding_agent: str,
    identity_used: str,
    decision: str,
    reason: str,
    commit_url: str | None,
) -> None:
    conn.execute(
        """INSERT INTO audit_log
           (session_id, bug_id, proposing_agent, responding_agent, identity_used,
            decision, reason, commit_url, timestamp)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            session_id,
            bug_id,
            proposing_agent,
            responding_agent,
            identity_used,
            decision,
            reason,
            commit_url,
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    conn.commit()
