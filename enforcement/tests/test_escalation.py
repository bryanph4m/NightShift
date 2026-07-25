"""Escalation trigger tests.

The false-positive cases matter more than the happy path here. An escalation
that fires when it shouldn't is worse on stage than one that doesn't fire at
all: it turns the demo's most load-bearing claim -- that the page-out is driven
by two real refusals -- into something the code decided on its own.

Meeting posts are stubbed out; these tests are about the trigger, and the
posting layer was verified live against a real Meet call in Phase 2.2.
"""

import sqlite3

import pytest

from enforcement import escalation
from enforcement.db import SCHEMA, write_audit_log
from enforcement.errors import REASON_MALFORMED_PROPOSAL, REASON_PERMISSION_DENIED, REASON_PROTECTED_BRANCH

SESSION = "sess-test"
BUG = "bug-3"


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute(SCHEMA)
    c.commit()
    return c


@pytest.fixture(autouse=True)
def no_real_posts(monkeypatch):
    posted = []
    monkeypatch.setattr(escalation, "post_to_meeting", lambda agent_id, text: posted.append((agent_id, text)))
    return posted


def add(conn, *, decision, identity, reason, bug_id=BUG, session_id=SESSION, responding="agent_b"):
    write_audit_log(
        conn,
        session_id=session_id,
        bug_id=bug_id,
        proposing_agent="agent_a",
        responding_agent=responding,
        identity_used=identity,
        decision=decision,
        reason=reason,
        commit_url=None,
    )


def test_two_distinct_genuine_refusals_escalate(conn):
    add(conn, decision="deny", identity="bob", reason=f"{REASON_PROTECTED_BRANCH}: 409")
    add(conn, decision="deny", identity="alice", reason=f"{REASON_PROTECTED_BRANCH}: 409")
    assert escalation.should_escalate(conn, SESSION, BUG) is True


def test_permission_denied_also_counts_as_genuine(conn):
    add(conn, decision="deny", identity="bob", reason=f"{REASON_PROTECTED_BRANCH}: 409")
    add(conn, decision="deny", identity="alice", reason=f"{REASON_PERMISSION_DENIED}: Not Found")
    assert escalation.should_escalate(conn, SESSION, BUG) is True


def test_single_refusal_does_not_escalate(conn):
    add(conn, decision="deny", identity="bob", reason=f"{REASON_PROTECTED_BRANCH}: 409")
    assert escalation.should_escalate(conn, SESSION, BUG) is False


def test_same_identity_refused_twice_does_not_escalate(conn):
    # A retry is one refusal, not two. Without the DISTINCT check a flaky
    # network retry would look identical to both principals being refused.
    add(conn, decision="deny", identity="bob", reason=f"{REASON_PROTECTED_BRANCH}: 409")
    add(conn, decision="deny", identity="bob", reason=f"{REASON_PROTECTED_BRANCH}: 409")
    assert escalation.should_escalate(conn, SESSION, BUG) is False


def test_malformed_proposal_never_counts(conn):
    # The critical false positive: a malformed proposal writes decision='deny'
    # but never reached ScaleKit, so pairing it with one real refusal must not
    # clear the bar.
    add(conn, decision="deny", identity="bob", reason=f"{REASON_PROTECTED_BRANCH}: 409")
    add(conn, decision="deny", identity=None, reason=f"{REASON_MALFORMED_PROPOSAL}, missing target_repo")
    assert escalation.should_escalate(conn, SESSION, BUG) is False


def test_allows_do_not_escalate(conn):
    add(conn, decision="allow", identity="bob", reason="write access verified")
    add(conn, decision="allow", identity="alice", reason="write access verified")
    assert escalation.should_escalate(conn, SESSION, BUG) is False


def test_refusals_from_a_previous_rehearsal_do_not_leak(conn):
    # Branch creation is idempotent so the sequence can be rehearsed twice, as
    # the build card requires. Scoping on bug_id alone would make run 2 escalate
    # off run 1's rows before run 2 had attempted anything.
    add(conn, decision="deny", identity="bob", reason=f"{REASON_PROTECTED_BRANCH}: 409", session_id="sess-run-1")
    add(conn, decision="deny", identity="alice", reason=f"{REASON_PROTECTED_BRANCH}: 409", session_id="sess-run-1")
    assert escalation.should_escalate(conn, "sess-run-2", BUG) is False
    assert escalation.should_escalate(conn, "sess-run-1", BUG) is True


def test_escalate_writes_row_and_is_idempotent(conn, no_real_posts):
    add(conn, decision="deny", identity="bob", reason=f"{REASON_PROTECTED_BRANCH}: 409")
    add(conn, decision="deny", identity="alice", reason=f"{REASON_PROTECTED_BRANCH}: 409")

    result = escalation.escalate(conn, session_id=SESSION, bug_id=BUG, target_repo="notifications-service")
    assert result is not None
    assert result["identities_refused"] == ["alice", "bob"]
    assert result["slack_sent"] is False
    assert "notifications-service" in result["notice"]
    assert "bob" in result["notice"] and "alice" in result["notice"]

    rows = conn.execute(
        "SELECT * FROM audit_log WHERE decision = ?", (escalation.ESCALATION_DECISION,)
    ).fetchall()
    assert len(rows) == 1
    # Escalation means no identity could act -- recording one would misstate it.
    assert rows[0]["identity_used"] is None

    # Firing again for the same session+bug must not double-post or double-log.
    assert escalation.escalate(conn, session_id=SESSION, bug_id=BUG) is None
    rows = conn.execute(
        "SELECT * FROM audit_log WHERE decision = ?", (escalation.ESCALATION_DECISION,)
    ).fetchall()
    assert len(rows) == 1


def test_escalate_returns_none_when_bar_not_met(conn):
    add(conn, decision="deny", identity="bob", reason=f"{REASON_PROTECTED_BRANCH}: 409")
    assert escalation.escalate(conn, session_id=SESSION, bug_id=BUG) is None
    assert conn.execute("SELECT COUNT(*) c FROM audit_log WHERE decision='escalated'").fetchone()["c"] == 0


def test_post_falls_back_to_agent_b_when_agent_a_unavailable(conn, monkeypatch):
    attempts = []

    def fake_post(agent_id, text):
        attempts.append(agent_id)
        if agent_id == "agent_a":
            raise ValueError("no bot_id configured for 'agent_a'")

    monkeypatch.setattr(escalation, "post_to_meeting", fake_post)
    add(conn, decision="deny", identity="bob", reason=f"{REASON_PROTECTED_BRANCH}: 409")
    add(conn, decision="deny", identity="alice", reason=f"{REASON_PROTECTED_BRANCH}: 409")

    result = escalation.escalate(conn, session_id=SESSION, bug_id=BUG)
    assert attempts == ["agent_a", "agent_b"]
    assert result["posted_as"] == "agent_b"


def test_resume_records_human_resolution(conn, no_real_posts):
    result = escalation.resume(conn, session_id=SESSION, bug_id=BUG, resolution="approved, merging manually")
    assert result["resolution"] == "approved, merging manually"
    row = conn.execute(
        "SELECT * FROM audit_log WHERE reason LIKE 'human resolution:%'"
    ).fetchone()
    assert row is not None
    assert row["decision"] == escalation.ESCALATION_DECISION
    assert "approved, merging manually" in row["reason"]
