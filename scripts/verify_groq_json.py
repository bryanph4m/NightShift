"""Verification harness for extraction.py (Phase 1.6, latency-critical path).

Runs the real extraction prompt N times (default 20) against ONE fixed
transcript window and reports:

  - how many runs produced parseable JSON
  - how many of those produced a valid proposal object
  - min / median / p95 / max round-trip latency in ms

Extraction sits on the live critical path: it fires on every rolling transcript
window while two agents are talking on a call. A single flaky JSON parse is a
dropped handoff on stage, so "it worked when I tried it" is not evidence. This
is. The build card says: do not proceed until 20 clean parses are observed.

Run with the project venv:
    .venv/Scripts/python.exe scripts/verify_groq_json.py [runs]
"""

from __future__ import annotations

import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import extraction  # noqa: E402

DEFAULT_RUNS = 20
BAR_WIDTH = 32

# One fixed window: Agent A diagnosing a bug it cannot fix and handing it to
# Agent B. This is exactly the utterance shape the orchestrator must catch.
FIXTURE_SEGMENTS = [
    {
        "speaker_label": "Agent A",
        "text": "CI just went red on notifications-service, pulling the job logs now.",
    },
    {
        "speaker_label": "Agent B",
        "text": "Go ahead, I'm listening.",
    },
    {
        "speaker_label": "Agent A",
        "text": (
            "Bug in notifications-service/src/dispatch.py - the dispatch loop "
            "appends notification.subject to the recipient list instead of "
            "notification.recipient. I tried the commit and GitHub refused: I "
            "don't have write access there. Bob, can you take it?"
        ),
    },
]


def _bar(value: float, worst: float) -> str:
    if worst <= 0:
        return ""
    filled = max(1, min(BAR_WIDTH, round(BAR_WIDTH * value / worst)))
    return "#" * filled + "." * (BAR_WIDTH - filled)


def main() -> int:
    runs = DEFAULT_RUNS
    if len(sys.argv) > 1:
        try:
            runs = int(sys.argv[1])
        except ValueError:
            print(f"Not a number: {sys.argv[1]!r}")
            return 2

    if not os.environ.get("GROQ_API_KEY"):
        print("GROQ_API_KEY is not set.")
        print()
        print("This script calls the real Groq API and cannot run without it.")
        print("Add GROQ_API_KEY to your .env, then re-run:")
        print("    .venv/Scripts/python.exe scripts/verify_groq_json.py")
        return 1

    model = os.environ.get("GROQ_MODEL", extraction.DEFAULT_MODEL)
    window_chars = len(extraction.build_window(FIXTURE_SEGMENTS))

    print("=" * 62)
    print("  Night Shift - Groq extraction reliability check")
    print("=" * 62)
    print(f"  model     : {model}")
    print(f"  endpoint  : {extraction.GROQ_BASE_URL}")
    print(f"  runs      : {runs}")
    print(f"  window    : {len(FIXTURE_SEGMENTS)} segments, {window_chars} chars")
    print("=" * 62)
    print()

    extraction.reset_latency_stats()

    proposals = 0
    latencies: list[float] = []
    failures: list[tuple[int, str]] = []

    for i in range(1, runs + 1):
        try:
            result = extraction.extract_proposal(
                FIXTURE_SEGMENTS, session_id="verify", bug_id="bug_verify"
            )
            error = None
        except Exception as exc:  # network blips, rate limits, auth
            result = None
            error = f"{type(exc).__name__}: {exc}"

        ms = extraction.get_last_latency_ms()
        if ms is not None:
            latencies.append(ms)

        if error:
            status = "ERROR"
            failures.append((i, error))
        elif result is None:
            # Either unparseable JSON or the model judged it not a proposal.
            status = "no-proposal"
            failures.append(
                (i, "returned None (unparseable JSON or is_proposal=false)")
            )
        else:
            proposals += 1
            status = "OK"

        shown = f"{ms:>7.0f} ms" if ms is not None else "      - ms"
        print(f"  run {i:>2}/{runs}   {status:<12} {shown}")

    print()
    print("-" * 62)
    print("  RESULTS")
    print("-" * 62)

    pct = (proposals / runs * 100) if runs else 0.0
    print(f"  valid proposal extracted : {proposals}/{runs}  ({pct:.0f}%)")
    print(f"  failed / no proposal     : {runs - proposals}/{runs}")

    if latencies:
        ordered = sorted(latencies)
        p95 = ordered[min(len(ordered) - 1, round(0.95 * (len(ordered) - 1)))]
        median = statistics.median(ordered)
        worst = ordered[-1]
        print()
        print("  latency (round-trip, ms)")
        print(f"    min    {ordered[0]:>7.0f}  {_bar(ordered[0], worst)}")
        print(f"    median {median:>7.0f}  {_bar(median, worst)}")
        print(f"    p95    {p95:>7.0f}  {_bar(p95, worst)}")
        print(f"    max    {worst:>7.0f}  {_bar(worst, worst)}")
        print(f"    mean   {statistics.fmean(ordered):>7.0f}")

    if failures:
        print()
        print("  failures")
        for run_no, message in failures[:5]:
            print(f"    run {run_no}: {message[:150]}")
        if len(failures) > 5:
            print(f"    ... and {len(failures) - 5} more")

    print("-" * 62)
    if proposals == runs:
        print(f"  PASS - {runs}/{runs} runs returned a valid proposal object.")
    elif pct >= 90:
        print(f"  PASS (marginal) - {pct:.0f}% success. Acceptable, but watch it.")
    else:
        print(f"  FAIL - only {pct:.0f}% of runs produced a valid proposal.")
    print("-" * 62)

    return 0 if pct >= 90 else 1


if __name__ == "__main__":
    sys.exit(main())
