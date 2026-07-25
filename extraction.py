"""Groq proposal extraction for Night Shift (latency-critical path, Phase 1.6).

Given the tail of a rolling transcript / meeting-chat window, decide whether an
agent just proposed a fix on the call and, if so, extract the proposal object
(see main's README, "Shared contracts" -> Proposal object).

This runs continuously while two agents are live on a Google Meet call, so the
prompt and the window are small on purpose - every millisecond here shows up on
stage as dead air or as two agents talking over each other.

Groq is OpenAI-compatible: point the ``openai`` client at
https://api.groq.com/openai/v1 with GROQ_API_KEY. Model comes from GROQ_MODEL,
default "openai/gpt-oss-20b", which supports strict ``json_schema`` structured
outputs. We fall back to ``json_object`` mode plus a hard "return only JSON"
instruction if the strict call errors.

GROQ_API_KEY is read lazily inside functions - never at import time - so this
module imports and unit-tests cleanly with no keys present.
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

load_dotenv()

DEFAULT_MODEL = "openai/gpt-oss-20b"
GROQ_BASE_URL = "https://api.groq.com/openai/v1"

# How many trailing segments we hand the model. Small on purpose: a proposal is
# a single utterance, and every extra segment is latency paid on every tick.
WINDOW_SIZE = 8

# Longest single segment we forward. A pasted stack trace must not blow up the
# prompt (and therefore the round-trip time).
MAX_SEGMENT_CHARS = 600

VALID_AGENTS = ("agent_a", "agent_b")

REQUEST_TIMEOUT_S = 20.0

# The SDK's default is 2 silent retries. On a live call a retry is worse than a
# miss - the next transcript window carries the same utterance a second later -
# and silent retries also make the measured latency a lie.
MAX_RETRIES = 1

# gpt-oss models reason before answering, and those reasoning tokens are pure
# latency for a task this mechanical. "low" cuts the round-trip roughly in half
# with no observed accuracy loss on the proposal/not-proposal call.
REASONING_EFFORT = "low"

# Reasoning tokens count against max_tokens. Too low and the model is cut off
# mid-reasoning, which surfaces as a schema-validation 400 rather than as
# truncated text - that was the single failure in the first 20-run check.
# reasoning_effort="low" keeps the actual completion well under this; the
# headroom only exists so a chatty reasoning trace cannot truncate the JSON.
# Kept modest because Groq reserves max_tokens against the per-minute budget.
MAX_TOKENS = 700

# ---------------------------------------------------------------------------
# Latency instrumentation - a dashboard reads these directly.
# ---------------------------------------------------------------------------

_last_latency_ms: Optional[float] = None
_latencies_ms: List[float] = []


def get_last_latency_ms() -> Optional[float]:
    """Round-trip latency (ms) of the most recent Groq call; None before any."""
    return _last_latency_ms


def get_latency_stats() -> Dict[str, Any]:
    """min / median / max / last over every Groq round-trip this process made."""
    if not _latencies_ms:
        return {
            "count": 0,
            "last_ms": None,
            "min_ms": None,
            "median_ms": None,
            "max_ms": None,
        }
    ordered = sorted(_latencies_ms)
    mid = len(ordered) // 2
    median = ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2
    return {
        "count": len(ordered),
        "last_ms": round(_last_latency_ms, 1) if _last_latency_ms is not None else None,
        "min_ms": round(ordered[0], 1),
        "median_ms": round(median, 1),
        "max_ms": round(ordered[-1], 1),
    }


def reset_latency_stats() -> None:
    """Clear collected latencies (used by the verification script)."""
    global _last_latency_ms
    _last_latency_ms = None
    _latencies_ms.clear()


def _record_latency(ms: float) -> None:
    global _last_latency_ms
    _last_latency_ms = ms
    _latencies_ms.append(ms)


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

PROPOSAL_JSON_SCHEMA = {
    "name": "proposal_extraction",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "is_proposal": {"type": "boolean"},
            "description": {"type": "string"},
            "target_repo": {"type": "string"},
            "target_path": {"type": "string"},
            "proposing_agent": {
                "type": "string",
                "enum": ["agent_a", "agent_b", ""],
            },
        },
        "required": [
            "is_proposal",
            "description",
            "target_repo",
            "target_path",
            "proposing_agent",
        ],
        "additionalProperties": False,
    },
}

SYSTEM_PROMPT = """You watch a live transcript between two AI agents: Agent A \
(labelled "Agent A" or "Alice") and Agent B (labelled "Agent B" or "Bob"), \
coordinating a bug-fix handoff on a call.

Decide whether the LAST line is an agent PROPOSING a fix and handing it to the \
other agent - naming a repo and/or file, saying it lacks write access, asking \
the other agent to take it.

Confirmations, commit URLs, escalations, greetings and questions are NOT \
proposals. When in doubt, is_proposal is false.

Reply with ONLY a JSON object, no prose, no markdown:
{"is_proposal": bool, "description": str, "target_repo": str, "target_path": \
str, "proposing_agent": "agent_a"|"agent_b"|""}

target_repo is the bare repo name (e.g. "notifications-service"). If it is not \
a proposal, set is_proposal to false and leave the string fields as ""."""

FALLBACK_INSTRUCTION = (
    "\n\nReturn ONLY raw JSON matching that exact shape. "
    "No preamble. No explanation. No markdown fences."
)

_FENCE_RE = re.compile(r"```(?:[A-Za-z0-9_+-]*)\s*\n(.*?)```", re.DOTALL)

# Loose speaker labels MeetStream gives us -> the contract's agent ids.
_SPEAKER_TO_AGENT = {
    "agent_a": "agent_a",
    "agent a": "agent_a",
    "agent alice": "agent_a",
    "alice": "agent_a",
    "agent_b": "agent_b",
    "agent b": "agent_b",
    "agent bob": "agent_b",
    "bob": "agent_b",
}


class ExtractionError(RuntimeError):
    """Raised when the extractor cannot produce a usable answer."""


# ---------------------------------------------------------------------------
# Pure parsing helpers - no network, fully unit-tested
# ---------------------------------------------------------------------------


def strip_code_fences(text: str) -> str:
    """Pull a bare JSON object out of whatever the model actually emitted.

    Handles ```json ... ```, bare fences, an unterminated opening fence (token
    cap hit mid-stream), and prose on either side of the object.
    """
    if not text:
        return ""
    cleaned = text.strip()

    fenced = _FENCE_RE.search(cleaned)
    if fenced:
        cleaned = fenced.group(1).strip()
    elif cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1]
        if cleaned.rstrip().endswith("```"):
            cleaned = cleaned.rstrip()[:-3]
        cleaned = cleaned.strip()

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        cleaned = cleaned[start : end + 1]

    return cleaned.strip()


def parse_json_response(text: str) -> Dict[str, Any]:
    """Fence-strip and parse, raising ExtractionError with the raw text on failure."""
    cleaned = strip_code_fences(text)
    if not cleaned:
        raise ExtractionError("model returned an empty response")
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ExtractionError(
            f"model did not return parseable JSON: {exc}; got: {cleaned[:300]!r}"
        ) from exc
    if not isinstance(parsed, dict):
        raise ExtractionError(
            f"model returned {type(parsed).__name__}, expected a JSON object"
        )
    return parsed


def _speaker(segment: Dict[str, Any]) -> str:
    for key in ("speaker_label", "speakerName", "speaker", "agent_id", "author"):
        value = segment.get(key)
        if value:
            return str(value)
    return "unknown"


def _text(segment: Dict[str, Any]) -> str:
    for key in ("text", "message", "content", "transcript"):
        value = segment.get(key)
        if value:
            return str(value)
    return ""


def normalize_agent(speaker: str) -> str:
    """Best-effort map of a speaker label onto agent_a / agent_b ('' if unknown)."""
    if not speaker:
        return ""
    low = speaker.strip().lower()
    if low in _SPEAKER_TO_AGENT:
        return _SPEAKER_TO_AGENT[low]
    # Substring scan handles "[Agent A]", "Agent Alice (host)", etc.
    for needle, agent in _SPEAKER_TO_AGENT.items():
        if needle in low:
            return agent
    return ""


def build_window(
    segments: List[Dict[str, Any]], size: int = WINDOW_SIZE
) -> str:
    """Render the last ``size`` non-empty segments as compact 'Speaker: text' lines."""
    window = [s for s in (segments or []) if _text(s).strip()][-size:]
    lines = []
    for seg in window:
        body = _text(seg).strip()
        if len(body) > MAX_SEGMENT_CHARS:
            body = body[:MAX_SEGMENT_CHARS] + " ...[truncated]"
        lines.append(f"{_speaker(seg)}: {body}")
    return "\n".join(lines)


def _last_speaker(segments: List[Dict[str, Any]]) -> str:
    for seg in reversed(segments or []):
        if _text(seg).strip():
            return _speaker(seg)
    return ""


# ---------------------------------------------------------------------------
# Groq client
# ---------------------------------------------------------------------------


def _get_client() -> Any:
    """OpenAI-compatible client pointed at Groq. GROQ_API_KEY read lazily."""
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GROQ_API_KEY is not set. extraction.extract_proposal() needs it to "
            "call Groq; add GROQ_API_KEY to your .env."
        )
    import openai  # imported lazily so the module imports without the SDK

    return openai.OpenAI(
        api_key=api_key,
        base_url=GROQ_BASE_URL,
        timeout=REQUEST_TIMEOUT_S,
        max_retries=MAX_RETRIES,
    )


def _model() -> str:
    return os.environ.get("GROQ_MODEL", DEFAULT_MODEL)


# Failures that the json_object fallback cannot possibly fix. Retrying these
# with a second full request just burns the 8000 tokens/minute free-tier budget
# at exactly the moment it is already exhausted, and doubles the measured
# latency of the tick. Let them propagate instead.
_NON_SCHEMA_ERROR_MARKERS = (
    "rate_limit",
    "rate limit",
    "429",
    "too many requests",
    "timeout",
    "timed out",
    "connection",
    "authentication",
    "invalid_api_key",
    "401",
    "403",
    "insufficient_quota",
)


def _should_try_fallback(exc: Exception) -> bool:
    """True only when the error plausibly came from the strict-schema request.

    A model/endpoint that rejects ``json_schema`` or ``reasoning_effort`` is
    worth one retry in plain json_object mode. A 429, a timeout, or bad
    credentials is not - the second call fails the same way.
    """
    text = f"{type(exc).__name__}: {exc}".lower()
    return not any(marker in text for marker in _NON_SCHEMA_ERROR_MARKERS)


def _call_groq(client: Any, model: str, user_prompt: str) -> str:
    """One Groq round-trip. Strict json_schema first, json_object as fallback.

    The fallback also drops ``reasoning_effort``, which is a Groq/gpt-oss
    extension: if we are already talking to something that rejected the strict
    schema, it may well reject that too. It fires only for errors a plain
    json_object retry could actually fix - see ``_should_try_fallback``.

    Latency covers the whole path, fallback included - the caller should see
    what this tick actually cost, not a flattering subset of it.
    """
    start = time.perf_counter()
    try:
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": PROPOSAL_JSON_SCHEMA,
                },
                temperature=0,
                max_tokens=MAX_TOKENS,
                reasoning_effort=REASONING_EFFORT,
            )
        except Exception as exc:
            if not _should_try_fallback(exc):
                raise
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "system",
                        "content": SYSTEM_PROMPT + FALLBACK_INSTRUCTION,
                    },
                    {"role": "user", "content": user_prompt},
                ],
                response_format={"type": "json_object"},
                temperature=0,
                max_tokens=MAX_TOKENS,
            )
    finally:
        _record_latency((time.perf_counter() - start) * 1000.0)

    return resp.choices[0].message.content or ""


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def extract_proposal(
    segments: List[Dict[str, Any]],
    session_id: str = "",
    bug_id: str = "",
    _client: Any = None,
    client: Any = None,
) -> Optional[Dict[str, Any]]:
    """Given the last N transcript/chat segments, return a proposal or None.

    The returned dict follows the shared Proposal contract, except that
    ``proposed_change`` is empty: the full replacement file body comes from the
    Claude diagnosis step, not from the transcript. The orchestrator merges the
    two. ``latency_ms`` is attached so the dashboard can show the live number.

    ``client`` / ``_client`` exist so tests can inject a fake; production
    callers pass neither.
    """
    user_window = build_window(segments)
    if not user_window.strip():
        return None

    user_prompt = "Transcript window (oldest to newest):\n" + user_window

    active = client or _client or _get_client()
    raw = _call_groq(active, _model(), user_prompt)

    try:
        parsed = parse_json_response(raw)
    except ExtractionError:
        # A malformed tick is not fatal on a live call - the next window will
        # carry the same utterance. Latency was already recorded.
        return None

    if not parsed.get("is_proposal"):
        return None

    agent = str(parsed.get("proposing_agent") or "").strip().lower()
    if agent not in VALID_AGENTS:
        # Model left it blank; fall back to who actually spoke last.
        agent = normalize_agent(_last_speaker(segments))
    if agent not in VALID_AGENTS:
        return None

    # Accept "owner/repo" and normalise to the bare name the contract uses.
    target_repo = str(parsed.get("target_repo") or "").strip().split("/")[-1]
    target_path = str(parsed.get("target_path") or "").strip()
    if not target_repo or not target_path:
        return None

    return {
        "bug_id": bug_id,
        "session_id": session_id,
        "description": str(parsed.get("description") or "").strip(),
        "target_repo": target_repo,
        "target_path": target_path,
        "proposed_change": "",
        "proposing_agent": agent,
        "latency_ms": (
            round(_last_latency_ms, 1) if _last_latency_ms is not None else None
        ),
    }
