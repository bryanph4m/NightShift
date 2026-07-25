"""Unit tests for extraction.py. No network: the Groq client is always faked."""

from __future__ import annotations

import json
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import extraction  # noqa: E402


# ---------------------------------------------------------------------------
# Fake OpenAI-compatible client
# ---------------------------------------------------------------------------


class FakeCompletions:
    def __init__(self, replies, fail_on_schema=False):
        # ``replies`` is a list; each create() pops the next one.
        self._replies = list(replies)
        self.fail_on_schema = fail_on_schema
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        fmt = (kwargs.get("response_format") or {}).get("type")
        if self.fail_on_schema and fmt == "json_schema":
            raise RuntimeError("json_schema not supported by this model")
        content = self._replies.pop(0) if self._replies else self._replies_default()
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
        )

    @staticmethod
    def _replies_default():
        return '{"is_proposal": false, "description": "", "target_repo": "", ' \
               '"target_path": "", "proposing_agent": ""}'


def fake_client(*replies, fail_on_schema=False):
    completions = FakeCompletions(replies, fail_on_schema=fail_on_schema)
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    client.completions = completions  # test-side handle
    return client


PROPOSAL_JSON = json.dumps(
    {
        "is_proposal": True,
        "description": "dispatch loop uses notification.subject as the recipient",
        "target_repo": "notifications-service",
        "target_path": "src/dispatch.py",
        "proposing_agent": "agent_a",
    }
)

NOT_PROPOSAL_JSON = json.dumps(
    {
        "is_proposal": False,
        "description": "",
        "target_repo": "",
        "target_path": "",
        "proposing_agent": "",
    }
)

SEGMENTS = [
    {"speaker_label": "Agent B", "text": "Go ahead."},
    {
        "speaker_label": "Agent A",
        "text": (
            "Bug in notifications-service/src/dispatch.py. I don't have write "
            "access there. Bob, can you take it?"
        ),
    },
]


@pytest.fixture(autouse=True)
def _clear_latency():
    extraction.reset_latency_stats()
    yield
    extraction.reset_latency_stats()


# ---------------------------------------------------------------------------
# strip_code_fences
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        '{"a": 1}',
        '```json\n{"a": 1}\n```',
        '```\n{"a": 1}\n```',
        'Here you go:\n```json\n{"a": 1}\n```\nHope that helps!',
        'Sure! {"a": 1}',
        '```json\n{"a": 1}',  # unterminated opening fence
    ],
)
def test_strip_code_fences_recovers_object(raw):
    assert json.loads(extraction.strip_code_fences(raw)) == {"a": 1}


def test_strip_code_fences_handles_empty_and_none():
    assert extraction.strip_code_fences("") == ""
    assert extraction.strip_code_fences(None) == ""


def test_strip_code_fences_keeps_nested_braces():
    raw = '```json\n{"outer": {"inner": 2}}\n```'
    assert json.loads(extraction.strip_code_fences(raw)) == {"outer": {"inner": 2}}


# ---------------------------------------------------------------------------
# parse_json_response
# ---------------------------------------------------------------------------


def test_parse_json_response_ok():
    assert extraction.parse_json_response('```json\n{"x": true}\n```') == {"x": True}


def test_parse_json_response_rejects_garbage():
    with pytest.raises(extraction.ExtractionError, match="parseable JSON"):
        extraction.parse_json_response("the model felt chatty today")


def test_parse_json_response_rejects_empty():
    with pytest.raises(extraction.ExtractionError, match="empty"):
        extraction.parse_json_response("   ")


def test_parse_json_response_rejects_non_object():
    with pytest.raises(extraction.ExtractionError, match="expected a JSON object"):
        extraction.parse_json_response("[1, 2, 3]")


# ---------------------------------------------------------------------------
# window building / speaker normalisation
# ---------------------------------------------------------------------------


def test_build_window_limits_size_and_formats():
    segs = [{"speaker_label": f"S{i}", "text": f"line {i}"} for i in range(20)]
    window = extraction.build_window(segs, size=3)
    assert window.splitlines() == ["S17: line 17", "S18: line 18", "S19: line 19"]


def test_build_window_truncates_huge_segments():
    segs = [{"speaker_label": "A", "text": "x" * 5000}]
    window = extraction.build_window(segs)
    assert "[truncated]" in window
    assert len(window) < 5000


def test_build_window_skips_empty_and_accepts_alt_keys():
    segs = [
        {"speaker": "Alice", "message": "hello"},
        {"speaker_label": "Bob", "text": "   "},
    ]
    assert extraction.build_window(segs) == "Alice: hello"


@pytest.mark.parametrize(
    "label,expected",
    [
        ("Agent A", "agent_a"),
        ("alice", "agent_a"),
        ("[Agent Alice] (host)", "agent_a"),
        ("Agent B", "agent_b"),
        ("Bob", "agent_b"),
        ("some human", ""),
        ("", ""),
    ],
)
def test_normalize_agent(label, expected):
    assert extraction.normalize_agent(label) == expected


# ---------------------------------------------------------------------------
# extract_proposal
# ---------------------------------------------------------------------------


def test_extract_proposal_returns_contract_shape():
    client = fake_client(PROPOSAL_JSON)
    result = extraction.extract_proposal(
        SEGMENTS, session_id="sess-1", bug_id="bug-1", client=client
    )
    assert result is not None
    for key in (
        "bug_id",
        "session_id",
        "description",
        "target_repo",
        "target_path",
        "proposed_change",
        "proposing_agent",
    ):
        assert key in result
    assert result["bug_id"] == "bug-1"
    assert result["session_id"] == "sess-1"
    assert result["target_repo"] == "notifications-service"
    assert result["target_path"] == "src/dispatch.py"
    assert result["proposing_agent"] == "agent_a"
    assert result["proposed_change"] == ""  # filled in by the diagnosis step


def test_extract_proposal_survives_fenced_output():
    client = fake_client(f"```json\n{PROPOSAL_JSON}\n```")
    assert extraction.extract_proposal(SEGMENTS, client=client) is not None


def test_extract_proposal_returns_none_when_not_a_proposal():
    client = fake_client(NOT_PROPOSAL_JSON)
    assert extraction.extract_proposal(SEGMENTS, client=client) is None


def test_extract_proposal_returns_none_on_unparseable_output():
    client = fake_client("I think Bob should probably take this one.")
    assert extraction.extract_proposal(SEGMENTS, client=client) is None


def test_extract_proposal_returns_none_on_empty_segments():
    client = fake_client(PROPOSAL_JSON)
    assert extraction.extract_proposal([], client=client) is None
    assert extraction.extract_proposal([{"text": "  "}], client=client) is None
    # No network call should have happened at all.
    assert client.completions.calls == []


def test_extract_proposal_falls_back_to_last_speaker_for_agent():
    payload = json.loads(PROPOSAL_JSON)
    payload["proposing_agent"] = ""
    client = fake_client(json.dumps(payload))
    result = extraction.extract_proposal(SEGMENTS, client=client)
    assert result is not None
    assert result["proposing_agent"] == "agent_a"  # last speaker was "Agent A"


def test_extract_proposal_returns_none_when_agent_unknowable():
    payload = json.loads(PROPOSAL_JSON)
    payload["proposing_agent"] = ""
    client = fake_client(json.dumps(payload))
    segs = [{"speaker_label": "Carol from ops", "text": "someone fix dispatch.py"}]
    assert extraction.extract_proposal(segs, client=client) is None


def test_extract_proposal_normalizes_owner_prefixed_repo():
    payload = json.loads(PROPOSAL_JSON)
    payload["target_repo"] = "bryanph4m/notifications-service"
    client = fake_client(json.dumps(payload))
    result = extraction.extract_proposal(SEGMENTS, client=client)
    assert result["target_repo"] == "notifications-service"


def test_extract_proposal_requires_repo_and_path():
    payload = json.loads(PROPOSAL_JSON)
    payload["target_path"] = ""
    client = fake_client(json.dumps(payload))
    assert extraction.extract_proposal(SEGMENTS, client=client) is None


def test_extract_proposal_accepts_legacy_underscore_client_kwarg():
    client = fake_client(PROPOSAL_JSON)
    assert extraction.extract_proposal(SEGMENTS, _client=client) is not None


# ---------------------------------------------------------------------------
# structured-output path + fallback
# ---------------------------------------------------------------------------


def test_uses_strict_json_schema_first():
    client = fake_client(PROPOSAL_JSON)
    extraction.extract_proposal(SEGMENTS, client=client)
    fmt = client.completions.calls[0]["response_format"]
    assert fmt["type"] == "json_schema"
    assert fmt["json_schema"]["strict"] is True


def test_falls_back_to_json_object_mode_when_schema_rejected():
    client = fake_client(PROPOSAL_JSON, fail_on_schema=True)
    result = extraction.extract_proposal(SEGMENTS, client=client)
    assert result is not None
    assert len(client.completions.calls) == 2
    assert client.completions.calls[0]["response_format"]["type"] == "json_schema"
    assert client.completions.calls[1]["response_format"]["type"] == "json_object"
    # The fallback must carry the hard "return only JSON" instruction.
    system_msg = client.completions.calls[1]["messages"][0]["content"]
    assert "ONLY raw JSON" in system_msg


class _RateLimited(FakeCompletions):
    def create(self, **kwargs):
        self.calls.append(kwargs)
        raise RuntimeError(
            "Error code: 429 - rate_limit_exceeded: tokens per minute (TPM)"
        )


def test_rate_limit_does_not_trigger_a_second_request():
    """A 429 must propagate, not be retried in json_object mode.

    The free tier is 8000 tokens/minute; a fallback request fired on a 429
    burns the budget again at the moment it is already gone, and fails
    identically.
    """
    completions = _RateLimited([])
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    with pytest.raises(RuntimeError, match="429"):
        extraction.extract_proposal(SEGMENTS, client=client)
    assert len(completions.calls) == 1
    # Latency of the failed tick is still recorded.
    assert extraction.get_last_latency_ms() is not None


@pytest.mark.parametrize(
    "message,expect_fallback",
    [
        ("json_schema not supported by this model", True),
        ("Unrecognized request argument: reasoning_effort", True),
        ("Error code: 429 - rate_limit_exceeded", False),
        ("Request timed out", False),
        ("Error code: 401 - invalid_api_key", False),
    ],
)
def test_should_try_fallback_classification(message, expect_fallback):
    assert extraction._should_try_fallback(RuntimeError(message)) is expect_fallback


# ---------------------------------------------------------------------------
# latency instrumentation
# ---------------------------------------------------------------------------


def test_latency_recorded_on_every_call():
    client = fake_client(PROPOSAL_JSON, NOT_PROPOSAL_JSON)
    extraction.extract_proposal(SEGMENTS, client=client)
    first = extraction.get_last_latency_ms()
    assert first is not None and first >= 0

    extraction.extract_proposal(SEGMENTS, client=client)
    stats = extraction.get_latency_stats()
    assert stats["count"] == 2
    assert stats["min_ms"] <= stats["median_ms"] <= stats["max_ms"]


def test_latency_recorded_even_when_output_is_garbage():
    client = fake_client("not json at all")
    assert extraction.extract_proposal(SEGMENTS, client=client) is None
    assert extraction.get_last_latency_ms() is not None


def test_proposal_carries_latency_ms():
    client = fake_client(PROPOSAL_JSON)
    result = extraction.extract_proposal(SEGMENTS, client=client)
    assert isinstance(result["latency_ms"], float)


def test_latency_stats_empty_before_any_call():
    stats = extraction.get_latency_stats()
    assert stats == {
        "count": 0,
        "last_ms": None,
        "min_ms": None,
        "median_ms": None,
        "max_ms": None,
    }


# ---------------------------------------------------------------------------
# missing key
# ---------------------------------------------------------------------------


def test_missing_groq_api_key_raises_named_runtime_error(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="GROQ_API_KEY"):
        extraction.extract_proposal(SEGMENTS)
