"""Claude Sonnet 5 diagnosis module for Night Shift.

Given raw CI output from a failed workflow run, this module fetches the real
source file through the ScaleKit GitHub connector and asks Claude to produce a
*proposal object* (see main's README) containing a FULL replacement file body.

The two planted bugs in the demo repos, so a reader can follow along:

  1. bryanph4m/notifications-service - src/dispatch.py
     The dispatch loop appends ``notification.subject`` to the recipient list
     instead of ``notification.recipient``, so notifications are addressed to
     their own subject line.

  2. bryanph4m/payments-service - src/processor.py
     ``process_payment`` does ``payer.balance_cents += amount_cents`` instead of
     ``-=``, so charging a payer credits them instead of debiting them.

Nothing here reads environment variables at import time; ANTHROPIC_API_KEY is
read lazily inside ``diagnose`` so the module imports and unit-tests cleanly
without any keys present.
"""

from __future__ import annotations

import base64
import json
import os
import re
from typing import Any, Dict, Optional

from dotenv import load_dotenv

load_dotenv()

DEFAULT_MODEL = "claude-sonnet-5"

# Which ScaleKit identity can actually *read* each repo. Alice cannot see
# notifications-service at all (GitHub returns 404), Bob cannot see
# payments-service, so the reader identity has to match the repo.
REPO_READER_IDENTITY = {
    "notifications-service": "bob",
    "payments-service": "alice",
}

# Which agent proposes for a given repo. The proposing agent is the one that
# CANNOT write there - that is the whole point of the handoff.
REPO_PROPOSING_AGENT = {
    "notifications-service": "agent_a",
    "payments-service": "agent_b",
}

DEFAULT_PATH_FOR_REPO = {
    "notifications-service": "src/dispatch.py",
    "payments-service": "src/processor.py",
}

GITHUB_OWNER = os.environ.get("GITHUB_ORG_OR_OWNER", "bryanph4m")

SYSTEM_PROMPT = """You are a senior engineer triaging a failed CI run at 2am.

You are given the CI output and the FULL current contents of the source file
that the failure points at. Find the single real bug and fix it.

Rules:
- Make the minimal correct change. Do not refactor, rename, reformat, or add
  comments, logging, type hints, or error handling that was not there before.
- Return the ENTIRE fixed file, byte-for-byte identical to the original except
  for the bug fix. It will be committed verbatim as a full file replacement.
- Reply with a single JSON object and nothing else. No preamble, no explanation
  outside the JSON, no markdown code fences.

The JSON object must have exactly these keys:
  "description": one sentence naming the bug in plain English.
  "target_path": the repo-relative path of the file you fixed.
  "proposed_change": the full corrected file contents as a JSON string.
"""


class DiagnosisError(RuntimeError):
    """Raised when diagnosis cannot produce a usable proposal."""


def _short_repo(repo: str) -> str:
    """Accept either 'owner/name' or bare 'name' and return the bare name."""
    return repo.split("/")[-1]


def _full_repo(repo: str) -> tuple[str, str]:
    """Return (owner, name) for either 'owner/name' or bare 'name'."""
    if "/" in repo:
        owner, name = repo.split("/", 1)
        return owner, name
    return GITHUB_OWNER, repo


def strip_code_fences(text: str) -> str:
    """Remove markdown fences that models emit regardless of instructions.

    Handles ```json ... ```, bare ``` ... ```, and stray leading/trailing prose
    around a single JSON object.
    """
    if text is None:
        return ""
    cleaned = text.strip()

    # Fenced block anywhere in the response.
    fenced = re.search(r"```(?:[A-Za-z0-9_+-]*)\s*\n(.*?)```", cleaned, re.DOTALL)
    if fenced:
        cleaned = fenced.group(1).strip()
    else:
        # Unterminated opening fence, e.g. hit the token cap mid-stream.
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[-1]
            if cleaned.rstrip().endswith("```"):
                cleaned = cleaned.rstrip()[:-3]
            cleaned = cleaned.strip()

    # Trim anything outside the outermost JSON object.
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        cleaned = cleaned[start : end + 1]

    return cleaned.strip()


def guess_target_path(ci_output: str, repo: str) -> str:
    """Pull the most plausible source path out of the CI output."""
    short = _short_repo(repo)
    candidates = re.findall(r"[\w./-]*src/[\w./-]+\.py", ci_output or "")
    if candidates:
        # Last frame in a traceback is usually the guilty one.
        path = candidates[-1]
        path = path.split(f"{short}/")[-1]
        idx = path.find("src/")
        if idx != -1:
            path = path[idx:]
        return path
    return DEFAULT_PATH_FOR_REPO.get(short, "src/main.py")


def fetch_file_contents(
    repo: str,
    path: str,
    identifier: Optional[str] = None,
    scalekit: Any = None,
) -> str:
    """Read the current file body through the ScaleKit GitHub connector."""
    owner, name = _full_repo(repo)
    identity = identifier or REPO_READER_IDENTITY.get(_short_repo(repo), "bob")

    if scalekit is None:
        from scalekit_client import get_client  # imported lazily on purpose

        scalekit = get_client()

    result = scalekit.actions.execute_tool(
        tool_name="github_file_contents_get",
        identifier=identity,
        tool_input={"owner": owner, "repo": name, "path": path},
    )
    data = result.data or {}
    encoded = data.get("content")
    if not encoded:
        raise DiagnosisError(
            f"github_file_contents_get returned no content for {owner}/{name}:{path}"
        )
    return base64.b64decode(encoded).decode("utf-8")


def validate_proposal(proposal: Dict[str, Any]) -> Dict[str, Any]:
    """Assert the proposal matches the shared contract exactly."""
    required = (
        "bug_id",
        "session_id",
        "description",
        "target_repo",
        "target_path",
        "proposed_change",
        "proposing_agent",
    )
    missing = [k for k in required if k not in proposal]
    if missing:
        raise DiagnosisError(f"proposal missing keys: {missing}")

    for key in required:
        if not isinstance(proposal[key], str) or not proposal[key].strip():
            raise DiagnosisError(f"proposal field '{key}' must be a non-empty string")

    if proposal["proposing_agent"] not in ("agent_a", "agent_b"):
        raise DiagnosisError(
            f"proposing_agent must be 'agent_a' or 'agent_b', got "
            f"{proposal['proposing_agent']!r}"
        )
    return proposal


def _call_claude(system_prompt: str, user_prompt: str) -> str:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. diagnosis.diagnose() needs it to call "
            "the Claude API; add ANTHROPIC_API_KEY to your .env."
        )

    import anthropic  # imported lazily so the module imports without the SDK key

    client = anthropic.Anthropic(api_key=api_key)
    model = os.environ.get("ANTHROPIC_MODEL", DEFAULT_MODEL)

    response = client.messages.create(
        model=model,
        max_tokens=16000,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )

    # Sonnet 5 runs adaptive thinking whenever `thinking` is omitted, and
    # max_tokens caps thinking + visible text together. If we hit the cap the
    # JSON is truncated and json.loads fails with a confusing parse error, so
    # name the real cause here instead.
    if response.stop_reason == "max_tokens":
        raise DiagnosisError(
            "Claude hit the max_tokens cap before finishing the JSON object "
            "(model={}, max_tokens=16000). The file is probably too large; "
            "raise max_tokens or lower output_config effort.".format(model)
        )
    if response.stop_reason == "refusal":
        raise DiagnosisError(f"Claude refused the diagnosis request (model={model}).")

    text = "".join(
        b.text for b in response.content if getattr(b, "type", "") == "text"
    )
    if not text.strip():
        raise DiagnosisError(
            f"Claude returned no text content (stop_reason={response.stop_reason!r}). "
            "If thinking blocks are the only content, the token cap was likely consumed "
            "by reasoning."
        )
    return text


def diagnose(
    ci_output: str,
    repo: str,
    session_id: str,
    bug_id: str,
    target_path: Optional[str] = None,
    identifier: Optional[str] = None,
    scalekit: Any = None,
    _call: Any = None,
) -> Dict[str, Any]:
    """Diagnose a CI failure and return a proposal object.

    ``_call`` and ``scalekit`` exist so tests can inject fakes; production
    callers pass neither.
    """
    short = _short_repo(repo)
    path = target_path or guess_target_path(ci_output, repo)

    current_source = fetch_file_contents(
        repo, path, identifier=identifier, scalekit=scalekit
    )

    user_prompt = (
        f"Repository: {short}\n"
        f"File: {path}\n\n"
        "=== CI OUTPUT ===\n"
        f"{ci_output}\n\n"
        f"=== CURRENT CONTENTS OF {path} ===\n"
        f"{current_source}\n\n"
        "Return the JSON object now."
    )

    caller = _call or _call_claude
    raw = caller(SYSTEM_PROMPT, user_prompt)
    cleaned = strip_code_fences(raw)

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise DiagnosisError(
            f"model did not return parseable JSON: {exc}; got: {cleaned[:400]!r}"
        ) from exc

    if not isinstance(parsed, dict):
        raise DiagnosisError(f"model returned {type(parsed).__name__}, expected object")

    proposal = {
        "bug_id": bug_id,
        "session_id": session_id,
        "description": parsed.get("description", ""),
        "target_repo": short,
        "target_path": parsed.get("target_path") or path,
        "proposed_change": parsed.get("proposed_change", ""),
        "proposing_agent": REPO_PROPOSING_AGENT.get(short, "agent_a"),
    }
    return validate_proposal(proposal)
