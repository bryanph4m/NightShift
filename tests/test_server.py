import hashlib
import hmac
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SECRET = "test-secret"


@pytest.fixture(autouse=True)
def env(monkeypatch):
    tmpdir = tempfile.mkdtemp()
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{Path(tmpdir, 'test.db').as_posix()}")
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("MEET_LINK", "https://meet.google.com/abc-defg-hij")
    monkeypatch.setenv("PUBLIC_WEBHOOK_BASE_URL", "https://example.test")
    yield


@pytest.fixture
def client(env):
    from fastapi.testclient import TestClient

    import server

    with TestClient(server.app) as c:
        yield c


def _sign(body: bytes) -> str:
    return "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


def _payload(conclusion: str) -> dict:
    return {
        "action": "completed",
        "workflow_run": {
            "conclusion": conclusion,
            "name": "tests",
            "id": 12345,
            "head_sha": "deadbeef",
            "html_url": "https://github.com/bryanph4m/notifications-service/actions/runs/12345",
        },
        "repository": {
            "name": "notifications-service",
            "owner": {"login": "bryanph4m"},
        },
    }


def _sessions() -> list:
    import db

    conn = db.get_connection()
    try:
        return conn.execute("SELECT * FROM sessions").fetchall()
    finally:
        conn.close()


def test_health(client):
    assert client.get("/health").json() == {"ok": True}


def test_bad_signature_returns_401(client):
    body = json.dumps(_payload("failure")).encode()
    resp = client.post(
        "/webhook/github",
        content=body,
        headers={"X-Hub-Signature-256": "sha256=" + "0" * 64},
    )
    assert resp.status_code == 401
    assert _sessions() == []


def test_missing_signature_returns_401(client):
    body = json.dumps(_payload("failure")).encode()
    resp = client.post("/webhook/github", content=body)
    assert resp.status_code == 401


def test_successful_run_is_ignored(client, monkeypatch):
    import server

    monkeypatch.setattr(
        server.meetstream_client, "create_bot",
        lambda *a, **k: pytest.fail("bot dispatched on a green build"),
    )
    body = json.dumps(_payload("success")).encode()
    resp = client.post(
        "/webhook/github", content=body, headers={"X-Hub-Signature-256": _sign(body)}
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ignored"
    assert _sessions() == []


def test_failure_writes_session_and_dispatches_bot(client, monkeypatch):
    import server

    calls = {}
    monkeypatch.setattr(
        server.github_events, "fetch_job_logs",
        lambda owner, repo, run_id: "AssertionError: boom",
    )

    def fake_create_bot(name, link, base):
        calls["bot"] = (name, link, base)
        return {"bot_id": "bot-a-123", "status": "joining"}

    monkeypatch.setattr(server.meetstream_client, "create_bot", fake_create_bot)

    body = json.dumps(_payload("failure")).encode()
    resp = client.post(
        "/webhook/github", content=body, headers={"X-Hub-Signature-256": _sign(body)}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "session_opened"
    assert data["bot_a_id"] == "bot-a-123"

    rows = _sessions()
    assert len(rows) == 1
    assert rows[0]["session_id"] == data["session_id"]
    assert rows[0]["meet_link"] == os.environ["MEET_LINK"]
    assert rows[0]["triggered_by"] == "bryanph4m/notifications-service#12345"

    assert calls["bot"] == (
        "Agent Alice", os.environ["MEET_LINK"], "https://example.test",
    )
    assert server.STATE["bot_a_id"] == "bot-a-123"


def test_transcript_interim_not_stored(client):
    import db

    resp = client.post(
        "/webhook/meetstream/transcript",
        json={"speakerName": "Agent Alice", "transcript": "I think the", "end_of_turn": False},
    )
    assert resp.json()["status"] == "interim"
    conn = db.get_connection()
    try:
        assert conn.execute("SELECT COUNT(*) c FROM transcript_segments").fetchone()["c"] == 0
    finally:
        conn.close()


def test_transcript_end_of_turn_stored(client):
    import db

    resp = client.post(
        "/webhook/meetstream/transcript",
        json={
            "speakerName": "Agent Alice",
            "transcript": "Bug in notifications-service dispatch.py",
            "end_of_turn": True,
            "timestamp": "2026-07-25T00:00:00+00:00",
        },
    )
    assert resp.json()["status"] == "stored"
    conn = db.get_connection()
    try:
        row = conn.execute("SELECT * FROM transcript_segments").fetchone()
    finally:
        conn.close()
    assert row["speaker_label"] == "Agent Alice"
    assert row["text"] == "Bug in notifications-service dispatch.py"


def test_lifecycle_events(client):
    for event in ["bot.joining", "bot.in_waiting_room", "bot.inmeeting", "bot.stopped",
                  "bot.denied", "bot.notallowed", "bot.kicked", "bot.failed"]:
        resp = client.post(
            "/webhook/meetstream/lifecycle",
            json={"bot_event": event, "bot_id": "bot-a-123", "message": "x"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}
