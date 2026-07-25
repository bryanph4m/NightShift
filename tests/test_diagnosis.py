"""Unit tests for diagnosis.py - no network, no API keys required."""

import base64
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import diagnosis  # noqa: E402
from diagnosis import (  # noqa: E402
    DiagnosisError,
    diagnose,
    guess_target_path,
    strip_code_fences,
    validate_proposal,
)

FIXED_SOURCE = (
    "def dispatch(notification, outbox):\n"
    "    outbox.append(notification.recipient)\n"
    "    return outbox\n"
)

CI_OUTPUT = """Run pytest -q
tests/test_dispatch.py::test_recipient FAILED
  File "/home/runner/work/notifications-service/notifications-service/src/dispatch.py", line 2, in dispatch
    outbox.append(notification.subject)
AssertionError: assert 'Server down' == 'ops@example.com'
"""


class _FakeActions:
    def __init__(self, content: str):
        self._content = content
        self.calls = []

    def execute_tool(self, tool_name, identifier, tool_input):
        self.calls.append((tool_name, identifier, tool_input))

        class _Result:
            data = {
                "content": base64.b64encode(self._content.encode("utf-8")).decode(),
            }

        return _Result()


class _FakeScalekit:
    def __init__(self, content: str):
        self.actions = _FakeActions(content)


def _model_reply(fence: str = "") -> str:
    payload = json.dumps(
        {
            "description": "dispatch appends notification.subject instead of recipient",
            "target_path": "src/dispatch.py",
            "proposed_change": FIXED_SOURCE,
        }
    )
    if fence == "json":
        return "```json\n" + payload + "\n```"
    if fence == "bare":
        return "```\n" + payload + "\n```"
    if fence == "chatty":
        return "Here's the fix:\n\n```json\n" + payload + "\n```\n\nHope that helps!"
    return payload


# --- fence stripping ---------------------------------------------------------


@pytest.mark.parametrize("fence", ["", "json", "bare", "chatty"])
def test_strip_code_fences_yields_parseable_json(fence):
    parsed = json.loads(strip_code_fences(_model_reply(fence)))
    assert parsed["target_path"] == "src/dispatch.py"
    assert parsed["proposed_change"] == FIXED_SOURCE


def test_strip_code_fences_handles_unterminated_fence():
    raw = '```json\n{"a": 1}'
    assert json.loads(strip_code_fences(raw)) == {"a": 1}


def test_strip_code_fences_on_empty_input():
    assert strip_code_fences("") == ""
    assert strip_code_fences(None) == ""


# --- proposal shape ----------------------------------------------------------


def _good_proposal():
    return {
        "bug_id": "bug-1",
        "session_id": "sess-abc",
        "description": "wrong attribute appended",
        "target_repo": "notifications-service",
        "target_path": "src/dispatch.py",
        "proposed_change": FIXED_SOURCE,
        "proposing_agent": "agent_a",
    }


def test_validate_proposal_accepts_contract_shape():
    assert validate_proposal(_good_proposal()) == _good_proposal()


def test_validate_proposal_rejects_missing_key():
    bad = _good_proposal()
    del bad["proposed_change"]
    with pytest.raises(DiagnosisError):
        validate_proposal(bad)


def test_validate_proposal_rejects_empty_field():
    bad = _good_proposal()
    bad["proposed_change"] = "   "
    with pytest.raises(DiagnosisError):
        validate_proposal(bad)


def test_validate_proposal_rejects_bad_agent():
    bad = _good_proposal()
    bad["proposing_agent"] = "agent_c"
    with pytest.raises(DiagnosisError):
        validate_proposal(bad)


# --- path inference ----------------------------------------------------------


def test_guess_target_path_from_traceback():
    assert guess_target_path(CI_OUTPUT, "notifications-service") == "src/dispatch.py"


def test_guess_target_path_falls_back_per_repo():
    assert guess_target_path("no paths here", "payments-service") == "src/processor.py"


# --- end-to-end with a mocked model + mocked connector -----------------------


@pytest.mark.parametrize("fence", ["", "json", "chatty"])
def test_diagnose_returns_valid_proposal(fence):
    fake = _FakeScalekit("def dispatch(n, outbox):\n    outbox.append(n.subject)\n")
    calls = {}

    def fake_call(system_prompt, user_prompt):
        calls["system"] = system_prompt
        calls["user"] = user_prompt
        return _model_reply(fence)

    proposal = diagnose(
        ci_output=CI_OUTPUT,
        repo="notifications-service",
        session_id="sess-abc",
        bug_id="bug-1",
        scalekit=fake,
        _call=fake_call,
    )

    assert validate_proposal(proposal) is proposal
    assert proposal["bug_id"] == "bug-1"
    assert proposal["session_id"] == "sess-abc"
    assert proposal["target_repo"] == "notifications-service"
    assert proposal["target_path"] == "src/dispatch.py"
    assert proposal["proposed_change"] == FIXED_SOURCE
    assert proposal["proposing_agent"] == "agent_a"
    # the model saw the real source, not just the stack trace
    assert "outbox.append(n.subject)" in calls["user"]
    # the connector was called with the identity that can actually read the repo
    tool_name, identity, tool_input = fake.actions.calls[0]
    assert tool_name == "github_file_contents_get"
    assert identity == "bob"
    assert tool_input == {
        "owner": diagnosis.GITHUB_OWNER,
        "repo": "notifications-service",
        "path": "src/dispatch.py",
    }


def test_diagnose_payments_repo_proposes_as_agent_b():
    fake = _FakeScalekit("payer.balance_cents += amount_cents\n")

    def fake_call(system_prompt, user_prompt):
        return json.dumps(
            {
                "description": "process_payment credits instead of debits",
                "target_path": "src/processor.py",
                "proposed_change": "payer.balance_cents -= amount_cents\n",
            }
        )

    proposal = diagnose(
        ci_output="assert balance == 500",
        repo="bryanph4m/payments-service",
        session_id="sess-xyz",
        bug_id="bug-2",
        scalekit=fake,
        _call=fake_call,
    )
    assert proposal["target_repo"] == "payments-service"
    assert proposal["proposing_agent"] == "agent_b"
    assert fake.actions.calls[0][1] == "alice"


def test_diagnose_raises_on_unparseable_model_output():
    fake = _FakeScalekit("whatever\n")
    with pytest.raises(DiagnosisError):
        diagnose(
            ci_output=CI_OUTPUT,
            repo="notifications-service",
            session_id="s",
            bug_id="b",
            scalekit=fake,
            _call=lambda s, u: "I could not find a bug, sorry.",
        )


def test_missing_api_key_raises_named_runtime_error(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        diagnosis._call_claude("sys", "user")
