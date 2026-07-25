import os
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
  session_id      TEXT PRIMARY KEY,
  meet_link       TEXT,
  triggered_by    TEXT,
  started_at      TIMESTAMP,
  ended_at        TIMESTAMP
);

CREATE TABLE IF NOT EXISTS transcript_segments (
  id              INTEGER PRIMARY KEY,
  session_id      TEXT,
  speaker_label   TEXT,
  text            TEXT,
  timestamp       TIMESTAMP
);

CREATE TABLE IF NOT EXISTS bugs (
  bug_id          TEXT PRIMARY KEY,
  session_id      TEXT,
  description     TEXT,
  target_repo     TEXT,
  target_path     TEXT,
  proposed_change TEXT,
  status          TEXT
);

CREATE TABLE IF NOT EXISTS audit_log (
  id               INTEGER PRIMARY KEY,
  session_id       TEXT,
  bug_id           TEXT,
  proposing_agent  TEXT,
  responding_agent TEXT,
  identity_used    TEXT,
  decision         TEXT,
  reason           TEXT,
  commit_url       TEXT,
  timestamp        TIMESTAMP
);
"""


def _db_path() -> str:
    url = os.environ.get("DATABASE_URL", "sqlite:///nightshift.db")
    prefix = "sqlite:///"
    if not url.startswith(prefix):
        raise ValueError(f"Only sqlite DATABASE_URL is supported right now, got: {url}")
    return url[len(prefix):]


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = get_connection()
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


def insert_session(session_id: str, meet_link: str, triggered_by: str, started_at: str) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO sessions (session_id, meet_link, triggered_by, started_at) "
            "VALUES (?, ?, ?, ?)",
            (session_id, meet_link, triggered_by, started_at),
        )
        conn.commit()
    finally:
        conn.close()


def insert_transcript_segment(
    session_id: str, speaker_label: str, text: str, timestamp: str
) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO transcript_segments (session_id, speaker_label, text, timestamp) "
            "VALUES (?, ?, ?, ?)",
            (session_id, speaker_label, text, timestamp),
        )
        conn.commit()
    finally:
        conn.close()


def insert_audit_log(
    session_id: str,
    bug_id: str,
    proposing_agent: str,
    responding_agent: str,
    identity_used: str,
    decision: str,
    reason: str,
    commit_url: str,
    timestamp: str,
) -> None:
    """Writes one audit_log row. Every orchestrator state transition calls
    this — see orchestrator.py. Schema is canonical (see SCHEMA above)."""
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO audit_log (session_id, bug_id, proposing_agent, responding_agent, "
            "identity_used, decision, reason, commit_url, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                session_id, bug_id, proposing_agent, responding_agent,
                identity_used, decision, reason, commit_url, timestamp,
            ),
        )
        conn.commit()
    finally:
        conn.close()
