"""Unit tests for orchestrator.py -- no network, no API keys required.

These drive the full state machine against a fake check_scope, including the
double-denial path that reaches ESCALATED. `check_scope_fn` is the only seam
used to avoid hitting real GitHub in tests -- the state machine itself
(orchestrator.py) is exercised unmodified, and the fake is keyed by
identity-per-call, not by (agent, repo), so it stands in for "what ScaleKit
would have said" rather than a permission table.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db  # noqa: E402
import orchestrator  # noqa: E402
from orchestrator import ESCALATED, RESOLVED, process_bug, run_session  # noqa: E402
import scope_check_stub  # noqa: E402


ALICE = {"label": "A", "name": "Alice", "identity": "alice"}
BOB = {"label": "B", "name": "Bob", "identity": "bob"}


def make_bug(bug_id="bug-1", diagnosing=ALICE, other=BOB, repo="notifications-service"):
    return {
        "bug_id": bug_id,
        "owner": "bryanph4m",
        "repo": repo,
        "path": "src/dispatch.py",
        "content": "# fixed\n",
        "description": "test bug",
        "diagnosing_agent": diagnosing,
        "other_agent": other,
    }


def fake_audit_log():
    """Captures audit rows in memory instead of touching sqlite."""
    rows = []

    def log_audit(**kwargs):
        rows.append(kwargs)

    return rows, log_audit


def fake_speak():
    """Captures every spoken line instead of printing."""
    lines = []

    def speak(label, text):
        lines.append(f"[Agent {label}] {text}")

    return lines, speak


def make_fake_check_scope(outcomes):
    """outcomes: dict mapping identity -> "allow" or "deny". This fake
    stands in for a real per-call ScaleKit response (see FakeToolException
    below for what a real denial looks like) -- it is not a table the
    orchestrator itself consults; process_bug() never sees `outcomes`."""

    def fake_check(scalekit, *, bug_id, identity, owner, repo, path, content, description, branch=None):
        decision = outcomes[identity]
        if decision == "allow":
            return {
                "bug_id": bug_id, "decision": "allow", "identity_used": identity,
                "reason": f"write access to {repo} verified",
                "commit_url": f"https://github.com/{owner}/{repo}/commit/fake",
                "error": None,
            }
        return {
            "bug_id": bug_id, "decision": "deny", "identity_used": identity,
            "reason": "no write access (simulated)", "commit_url": None,
            "error": "simulated denial",
        }

    return fake_check


def test_self_attempt_allowed_resolves_immediately():
    bug = make_bug()
    audit_rows, log_audit = fake_audit_log()
    lines, speak = fake_speak()
    fake_check = make_fake_check_scope({"alice": "allow"})

    result = process_bug(
        None, bug, speak=speak, log_audit=log_audit, check_scope_fn=fake_check,
    )

    assert result["final_state"] == RESOLVED
    assert len(result["trail"]) == 1
    assert len(audit_rows) == 1
    assert audit_rows[0]["decision"] == "allow"
    assert audit_rows[0]["identity_used"] == "alice"
    assert len(lines) == 1
    assert lines[0].startswith("[Agent A] Confirmed. Committed as Alice.")


def test_self_denied_handoff_allowed_resolves():
    bug = make_bug()
    audit_rows, log_audit = fake_audit_log()
    lines, speak = fake_speak()
    fake_check = make_fake_check_scope({"alice": "deny", "bob": "allow"})

    result = process_bug(
        None, bug, speak=speak, log_audit=log_audit, check_scope_fn=fake_check,
    )

    assert result["final_state"] == RESOLVED
    assert len(result["trail"]) == 2
    assert [r["decision"] for r in audit_rows] == ["deny", "allow"]

    # exact message contract
    assert lines[0] == (
        "[Agent A] Bug in notifications-service/src/dispatch.py. "
        "I don't have write access there. Bob, can you take it?"
    )
    assert lines[1].startswith("[Agent B] Confirmed. Committed as Bob.")


def test_double_denial_escalates():
    bug = make_bug(bug_id="bug-3", repo="payments-service")
    audit_rows, log_audit = fake_audit_log()
    lines, speak = fake_speak()
    slack_calls = []

    def fake_slack(bug_id, denials):
        slack_calls.append((bug_id, denials))

    fake_check = make_fake_check_scope({"alice": "deny", "bob": "deny"})

    result = process_bug(
        None, bug, speak=speak, log_audit=log_audit,
        notify_slack=fake_slack, check_scope_fn=fake_check,
    )

    assert result["final_state"] == ESCALATED
    assert len(result["trail"]) == 2
    assert [r["decision"] for r in audit_rows] == ["deny", "deny", "escalated"]
    assert audit_rows[-1]["identity_used"] == "-"

    assert lines[0] == (
        "[Agent A] Bug in payments-service/src/dispatch.py. "
        "I don't have write access there. Bob, can you take it?"
    )
    assert lines[1] == "[Agent B] I don't have write access to payments-service either."
    assert lines[2] == "[Agent A] Neither of us can merge to protected main. Paging on-call."

    # escalation only fires after both real refusals are in hand
    assert len(slack_calls) == 1
    bug_id, denials = slack_calls[0]
    assert bug_id == "bug-3"
    assert {d[0] for d in denials} == {"alice", "bob"}


def test_bugs_run_strictly_in_sequence():
    """Bug 2 must not start until bug 1 has reached a terminal state. We
    prove this by asserting all of bug 1's scope-check calls happen before
    bug 2's first call."""
    bug1 = make_bug(bug_id="bug-1", diagnosing=ALICE, other=BOB, repo="notifications-service")
    bug2 = make_bug(bug_id="bug-2", diagnosing=BOB, other=ALICE, repo="payments-service")

    audit_rows, log_audit = fake_audit_log()
    lines, speak = fake_speak()
    call_order = []

    def fake_check(scalekit, *, bug_id, identity, owner, repo, path, content, description, branch=None):
        call_order.append(bug_id)
        allow_map = {"bug-1": {"alice": False, "bob": True}, "bug-2": {"bob": False, "alice": True}}
        allowed = allow_map[bug_id][identity]
        if allowed:
            return {"bug_id": bug_id, "decision": "allow", "identity_used": identity,
                     "reason": "ok", "commit_url": "https://example.com/commit", "error": None}
        return {"bug_id": bug_id, "decision": "deny", "identity_used": identity,
                 "reason": "denied", "commit_url": None, "error": "denied"}

    results = run_session(
        None, [bug1, bug2], speak=speak, log_audit=log_audit, check_scope_fn=fake_check,
    )

    assert [r["final_state"] for r in results] == [RESOLVED, RESOLVED]
    first_bug2_index = call_order.index("bug-2")
    assert all(b == "bug-1" for b in call_order[:first_bug2_index])


def test_scope_check_stub_requires_env_flag():
    """The stub refuses to run at all unless NIGHTSHIFT_USE_STUB=1 -- it must
    never silently fall back to a lookup table."""
    old = os.environ.pop("NIGHTSHIFT_USE_STUB", None)
    try:
        try:
            scope_check_stub.check_scope(
                None, bug_id="bug-x", identity="alice", owner="bryanph4m",
                repo="notifications-service", path="src/dispatch.py",
                content="x", description="test",
            )
            assert False, "expected NotImplementedError without NIGHTSHIFT_USE_STUB=1"
        except NotImplementedError:
            pass
    finally:
        if old is not None:
            os.environ["NIGHTSHIFT_USE_STUB"] = old


def test_no_agent_repo_permission_table_anywhere():
    """Static guard for the project's central claim: no function in
    orchestrator.py or scope_check_stub.py may be a hardcoded (agent, repo)
    -> bool lookup table."""
    import inspect

    for module in (orchestrator, scope_check_stub):
        source = inspect.getsource(module)
        assert "_STUB_WRITABLE" not in source
        assert "notifications-service" not in source
        assert "payments-service" not in source


def test_insert_audit_log_writes_a_row(tmp_path, monkeypatch):
    db_file = tmp_path / "test_nightshift.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file}")
    db.init_db()
    db.insert_audit_log(
        session_id="s1", bug_id="bug-1", proposing_agent="Alice",
        responding_agent="Bob", identity_used="bob", decision="allow",
        reason="write access verified", commit_url="https://example.com/commit",
        timestamp="2026-01-01T00:00:00Z",
    )
    conn = db.get_connection()
    try:
        row = conn.execute("SELECT * FROM audit_log").fetchone()
    finally:
        conn.close()
    assert row["bug_id"] == "bug-1"
    assert row["decision"] == "allow"
    assert row["identity_used"] == "bob"
