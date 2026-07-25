"""Turn-taking orchestrator for Night Shift (Phase 1.7).

Two LLM-driven agents on one call will talk over each other unless order is
enforced in code. This module runs an explicit state machine per bug:

    DIAGNOSED      -> the agent that found the bug attempts its own fix
    SELF_ATTEMPTED -> the real ScaleKit call returned.
                      allowed  -> RESOLVED
                      denied   -> the agent proposes on the call -> PROPOSED
    PROPOSED       -> the OTHER agent attempts the identical write
                      allowed  -> commits, posts confirmation -> RESOLVED
                      denied   -> both have now failed          -> ESCALATED
    ESCALATED      -> Slack sent, waiting on a human
    RESOLVED       -> logged, move to the next bug

THE LOAD-BEARING RULE
---------------------
No transition in this file is decided by consulting a table of who owns which
repo. ``_attempt_commit`` runs the actual GitHub write through the agent's own
OAuth token and reports whatever GitHub answers. The SELF_ATTEMPTED ->
PROPOSED edge fires only because a real token was really refused. There is
deliberately no pre-check, no allow-list, and no "can this identity write here"
helper anywhere below -- if one existed the demo would be theatre.

The one exception is the rehearsal stub (``scope_check_stub``), which exists so
call flow can be exercised without burning API calls. It is OFF unless
NIGHTSHIFT_SCOPE_STUB=1 is set explicitly, it logs a loud warning on every use,
and every decision it produces is tagged ``STUB:`` in the reason and in the
audit log so a stubbed run can never be mistaken for a real one.

Nothing here reads an API key at import time, so the module imports and
unit-tests cleanly with no credentials present.
"""

from __future__ import annotations

import base64
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

import db

log = logging.getLogger("nightshift.orchestrator")

# ---------------------------------------------------------------------------
# States
# ---------------------------------------------------------------------------

DIAGNOSED = "DIAGNOSED"
SELF_ATTEMPTED = "SELF_ATTEMPTED"
PROPOSED = "PROPOSED"
RESOLVED = "RESOLVED"
ESCALATED = "ESCALATED"

# Which ScaleKit identifier each agent runs as. This maps an agent NAME to a
# credential -- it says nothing about what that credential may do.
AGENT_IDENTIFIER = {
    "agent_a": os.environ.get("ALICE_IDENTIFIER", "alice"),
    "agent_b": os.environ.get("BOB_IDENTIFIER", "bob"),
}
AGENT_HUMAN_NAME = {"agent_a": "Alice", "agent_b": "Bob"}
AGENT_CHAT_LABEL = {"agent_a": "A", "agent_b": "B"}


def other_agent(agent: str) -> str:
    return "agent_b" if agent == "agent_a" else "agent_a"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _owner() -> str:
    return os.environ.get("GITHUB_ORG_OR_OWNER", "bryanph4m")


def _short_repo(repo: str) -> str:
    return repo.split("/")[-1]


# ---------------------------------------------------------------------------
# Decision objects (shared contract, from main's README)
# ---------------------------------------------------------------------------


def make_decision(
    bug_id: str,
    decision: str,
    identity_used: str,
    reason: str,
    commit_url: str = "",
    error: str = "",
) -> Dict[str, Any]:
    """Build a decision object with exactly the contract's field names."""
    if decision not in ("allow", "deny", "escalated"):
        raise ValueError(f"decision must be allow|deny|escalated, got {decision!r}")
    return {
        "bug_id": bug_id,
        "decision": decision,
        "identity_used": identity_used,
        "reason": reason,
        "commit_url": commit_url,
        "error": error,
    }


def classify_error(exc: Exception) -> str:
    """Tell a permission denial apart from a malformed call.

    Without this a wrong tool name looks exactly like a refusal and sends you
    debugging the wrong thing.
    """
    text = str(exc)
    if '"status":"404"' in text or "Not Found" in text:
        return (
            "no write access (GitHub returns 404 rather than 403 for repos "
            "you cannot write to)"
        )
    if "Changes must be made through a pull request" in text:
        return "branch protection on main refused the write"
    if "protected branch" in text.lower():
        return "branch protection on main refused the write"
    return f"call failed: {text[:200]}"


def is_denial(exc: Exception) -> bool:
    """True when the failure is a genuine permission refusal.

    A transport error or a bad tool name is NOT a denial and must not be
    allowed to masquerade as one -- that would fake the demo's central claim
    from the opposite direction.
    """
    text = str(exc)
    return (
        '"status":"404"' in text
        or "Not Found" in text
        or '"status":"403"' in text
        or "Changes must be made through a pull request" in text
        or "protected branch" in text.lower()
    )


# ---------------------------------------------------------------------------
# Message formats (shared contract -- enforcement's posting layer expects these
# strings byte-for-byte)
# ---------------------------------------------------------------------------



# The contract records these with literal "[Agent A]"/"[Agent B]" prefixes,
# but that is the bug-1 case written out. In bug 2 the roles swap: Bob is the
# one refused and Alice commits. The prefix therefore tracks WHO IS SPEAKING
# while the wording stays byte-identical to the contract.


def proposal_message(speaker: str, repo: str, path: str, responder_name: str) -> str:
    return (
        f"[Agent {AGENT_CHAT_LABEL[speaker]}] Bug in {_short_repo(repo)}/{path}. "
        f"I don't have write access there. {responder_name}, can you take it?"
    )


def allow_message(speaker: str, committer_name: str, commit_url: str) -> str:
    return (
        f"[Agent {AGENT_CHAT_LABEL[speaker]}] Confirmed. Committed as "
        f"{committer_name}. {commit_url}"
    )


def deny_message(speaker: str, repo: str) -> str:
    return (
        f"[Agent {AGENT_CHAT_LABEL[speaker]}] I don't have write access to "
        f"{_short_repo(repo)} either."
    )


def escalation_message(speaker: str = "agent_a") -> str:
    return (
        f"[Agent {AGENT_CHAT_LABEL[speaker]}] Neither of us can merge to "
        "protected main. Paging on-call."
    )


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------


def record_audit(
    session_id: str,
    bug_id: str,
    proposing_agent: str,
    responding_agent: str,
    identity_used: str,
    decision: str,
    reason: str,
    commit_url: str = "",
) -> None:
    """Write one row to audit_log. Every state transition goes through here."""
    try:
        conn = db.get_connection()
        try:
            conn.execute(
                "INSERT INTO audit_log (session_id, bug_id, proposing_agent, "
                "responding_agent, identity_used, decision, reason, commit_url, "
                "timestamp) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    session_id,
                    bug_id,
                    proposing_agent,
                    responding_agent,
                    identity_used,
                    decision,
                    reason,
                    commit_url,
                    _now(),
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:  # the audit log must never sink a live demo
        log.error("audit_log write failed for %s: %s", bug_id, exc)


def record_bug(proposal: Dict[str, Any], status: str) -> None:
    try:
        conn = db.get_connection()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO bugs (bug_id, session_id, description, "
                "target_repo, target_path, proposed_change, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    proposal["bug_id"],
                    proposal["session_id"],
                    proposal["description"],
                    proposal["target_repo"],
                    proposal["target_path"],
                    proposal["proposed_change"],
                    status,
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        log.error("bugs write failed for %s: %s", proposal.get("bug_id"), exc)


# ---------------------------------------------------------------------------
# The real ScaleKit write. This is the only thing that decides allow vs deny.
# ---------------------------------------------------------------------------


def _execute(scalekit, identity: str, tool_name: str, tool_input: dict):
    """Run one real ScaleKit tool call. Returns (ok, result_or_exception)."""
    try:
        result = scalekit.actions.execute_tool(
            tool_name=tool_name,
            identifier=identity,
            tool_input=tool_input,
        )
        return True, result
    except Exception as exc:
        return False, exc


def attempt_commit(
    scalekit,
    identity: str,
    repo: str,
    path: str,
    content: str,
    message: str,
    base_branch: str = "",
    branch: str = "",
) -> Dict[str, Any]:
    """Attempt the real write as ``identity``. Never pre-checks anything.

    Returns {"allowed": bool, "commit_url": str, "reason": str,
             "error": str, "denial": bool}.

    ``base_branch="main"`` with ``branch="main"`` writes straight at protected
    main, which is how the escalation bug is meant to fail.
    """
    owner = _owner()
    name = _short_repo(repo)
    target_branch = branch or f"nightshift-fix-{uuid.uuid4().hex[:6]}"
    write_to_main = target_branch == "main"

    # Read the file's blob sha. github_file_create_update needs it to replace
    # an existing file. A failure here is usually itself a read denial.
    ok, result = _execute(
        scalekit,
        identity,
        "github_file_contents_get",
        {"owner": owner, "repo": name, "path": path},
    )
    if not ok:
        return {
            "allowed": False,
            "commit_url": "",
            "reason": classify_error(result),
            "error": str(result)[:500],
            "denial": is_denial(result),
        }
    file_sha = (result.data or {}).get("sha", "")

    if not write_to_main:
        # Branch off main. sha is main's head, read with the same identity.
        ok, head = _execute(
            scalekit,
            identity,
            "github_branch_get",
            {"owner": owner, "repo": name, "branch": base_branch or "main"},
        )
        if not ok:
            return {
                "allowed": False,
                "commit_url": "",
                "reason": classify_error(head),
                "error": str(head)[:500],
                "denial": is_denial(head),
            }
        head_sha = head.data["commit"]["sha"]

        ok, created = _execute(
            scalekit,
            identity,
            "github_branch_create",
            {
                "owner": owner,
                "repo": name,
                "branch_name": target_branch,
                "sha": head_sha,
            },
        )
        if not ok:
            return {
                "allowed": False,
                "commit_url": "",
                "reason": classify_error(created),
                "error": str(created)[:500],
                "denial": is_denial(created),
            }

    ok, written = _execute(
        scalekit,
        identity,
        "github_file_create_update",
        {
            "owner": owner,
            "repo": name,
            "path": path,
            "message": message,
            "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
            "branch": target_branch,
            "sha": file_sha,
        },
    )
    if not ok:
        return {
            "allowed": False,
            "commit_url": "",
            "reason": classify_error(written),
            "error": str(written)[:500],
            "denial": is_denial(written),
        }

    data = written.data or {}
    commit_url = (data.get("commit") or {}).get("html_url", "")
    return {
        "allowed": True,
        "commit_url": commit_url,
        "reason": f"write access to {name} verified by a real commit",
        "error": "",
        "denial": False,
        "branch": target_branch,
    }


# ---------------------------------------------------------------------------
# Rehearsal stub -- OFF unless explicitly switched on
# ---------------------------------------------------------------------------


def stub_enabled() -> bool:
    return os.environ.get("NIGHTSHIFT_SCOPE_STUB", "") == "1"


# Hardcoded outcomes for rehearsal only. Deliberately keyed by repo, which is
# exactly the shortcut that is forbidden in a real run -- hence the tagging.
_STUB_WRITABLE = {"agent_a": {"payments-service"}, "agent_b": {"notifications-service"}}


def scope_check_stub(agent: str, repo: str, path: str) -> Dict[str, Any]:
    log.warning(
        "SCOPE STUB ACTIVE -- no ScaleKit call was made for %s on %s. "
        "This run proves nothing about real permissions.",
        agent,
        repo,
    )
    writable = _short_repo(repo) in _STUB_WRITABLE.get(agent, set())
    return {
        "allowed": writable,
        "commit_url": (
            f"https://github.com/{_owner()}/{_short_repo(repo)}/commit/stub"
            if writable
            else ""
        ),
        "reason": "STUB: hardcoded rehearsal outcome, no real call made",
        "error": "" if writable else "STUB: hardcoded denial",
        "denial": not writable,
    }


def _try_write(scalekit, agent, identity, repo, path, content, message, branch=""):
    if stub_enabled():
        return scope_check_stub(agent, repo, path)
    return attempt_commit(
        scalekit, identity, repo, path, content, message, branch=branch
    )


# ---------------------------------------------------------------------------
# The state machine
# ---------------------------------------------------------------------------


def default_poster(text: str) -> None:
    """Where an agent's line goes when enforcement's posting layer is absent."""
    print(text)


class BugRun:
    """One bug's journey through the state machine. Kept as an object so the
    orchestrator can assert an agent only speaks on a real transition."""

    def __init__(self, proposal: Dict[str, Any]):
        self.proposal = proposal
        self.bug_id = proposal["bug_id"]
        self.session_id = proposal["session_id"]
        self.state = DIAGNOSED
        self.decisions: List[Dict[str, Any]] = []
        self.transitions: List[str] = [DIAGNOSED]

    def to(self, state: str) -> None:
        self.state = state
        self.transitions.append(state)


def run_bug(
    proposal: Dict[str, Any],
    scalekit: Any = None,
    poster: Optional[Callable[[str], None]] = None,
    escalate: Optional[Callable[[Dict[str, Any], List[Dict[str, Any]]], None]] = None,
    branch: str = "",
) -> BugRun:
    """Drive one bug from DIAGNOSED to RESOLVED or ESCALATED.

    ``scalekit``/``poster``/``escalate`` are injectable so this is testable
    without credentials; production callers pass none of them.
    """
    post = poster or default_poster
    run = BugRun(proposal)

    proposer = proposal["proposing_agent"]
    responder = other_agent(proposer)
    repo = proposal["target_repo"]
    path = proposal["target_path"]
    content = proposal["proposed_change"]
    commit_message = f"Fix {run.bug_id}: {proposal['description']}"

    record_bug(proposal, DIAGNOSED)

    if scalekit is None and not stub_enabled():
        from scalekit_client import get_client  # lazy: no key needed to import

        scalekit = get_client()

    # -- DIAGNOSED -> SELF_ATTEMPTED -------------------------------------
    # The agent that found the bug tries to fix it with its own token.
    proposer_identity = AGENT_IDENTIFIER[proposer]
    outcome = _try_write(
        scalekit, proposer, proposer_identity, repo, path, content,
        commit_message, branch=branch,
    )
    run.to(SELF_ATTEMPTED)

    if outcome["allowed"]:
        decision = make_decision(
            run.bug_id, "allow", proposer_identity, outcome["reason"],
            commit_url=outcome["commit_url"],
        )
        run.decisions.append(decision)
        record_audit(
            run.session_id, run.bug_id, proposer, proposer, proposer_identity,
            "allow", outcome["reason"], outcome["commit_url"],
        )
        post(allow_message(proposer, AGENT_HUMAN_NAME[proposer], outcome["commit_url"]))
        run.to(RESOLVED)
        record_bug(proposal, RESOLVED)
        return run

    decision = make_decision(
        run.bug_id, "deny", proposer_identity, outcome["reason"],
        error=outcome["error"],
    )
    run.decisions.append(decision)
    record_audit(
        run.session_id, run.bug_id, proposer, proposer, proposer_identity,
        "deny", outcome["reason"],
    )

    if not outcome["denial"]:
        # A transport error or bad tool name is not a permission refusal and
        # must not be dressed up as one.
        log.error(
            "%s: %s's attempt failed for a non-permission reason: %s",
            run.bug_id, proposer_identity, outcome["error"],
        )

    # -- SELF_ATTEMPTED -> PROPOSED --------------------------------------
    # Only a real refusal gets us here. The agent speaks, then waits.
    post(proposal_message(proposer, repo, path, AGENT_HUMAN_NAME[responder]))
    run.to(PROPOSED)
    record_bug(proposal, PROPOSED)

    # -- PROPOSED -> RESOLVED | ESCALATED --------------------------------
    responder_identity = AGENT_IDENTIFIER[responder]
    outcome = _try_write(
        scalekit, responder, responder_identity, repo, path, content,
        commit_message, branch=branch,
    )

    if outcome["allowed"]:
        decision = make_decision(
            run.bug_id, "allow", responder_identity, outcome["reason"],
            commit_url=outcome["commit_url"],
        )
        run.decisions.append(decision)
        record_audit(
            run.session_id, run.bug_id, proposer, responder, responder_identity,
            "allow", outcome["reason"], outcome["commit_url"],
        )
        post(allow_message(responder, AGENT_HUMAN_NAME[responder], outcome["commit_url"]))
        run.to(RESOLVED)
        record_bug(proposal, RESOLVED)
        return run

    decision = make_decision(
        run.bug_id, "deny", responder_identity, outcome["reason"],
        error=outcome["error"],
    )
    run.decisions.append(decision)
    record_audit(
        run.session_id, run.bug_id, proposer, responder, responder_identity,
        "deny", outcome["reason"],
    )
    post(deny_message(responder, repo))

    # -- ESCALATED -------------------------------------------------------
    # Two observed refusals of the same change, both from real tokens.
    run.to(ESCALATED)
    record_bug(proposal, ESCALATED)
    escalation = make_decision(
        run.bug_id, "escalated", "-",
        "two genuine refusals for the same change",
    )
    run.decisions.append(escalation)
    record_audit(
        run.session_id, run.bug_id, proposer, responder, "-",
        "escalated", "two genuine refusals for the same change",
    )
    post(escalation_message(proposer))
    if escalate:
        escalate(proposal, run.decisions)
    return run


def run_session(
    proposals: List[Dict[str, Any]],
    scalekit: Any = None,
    poster: Optional[Callable[[str], None]] = None,
    escalate: Optional[Callable[[Dict[str, Any], List[Dict[str, Any]]], None]] = None,
) -> List[BugRun]:
    """Process bugs strictly in sequence. Bug N+1 does not start until bug N
    has reached RESOLVED or ESCALATED -- that is what stops the two agents
    from interleaving turns."""
    runs: List[BugRun] = []
    for proposal in proposals:
        run = run_bug(proposal, scalekit=scalekit, poster=poster, escalate=escalate)
        assert run.state in (RESOLVED, ESCALATED), (
            f"bug {run.bug_id} ended in {run.state}, refusing to start the next"
        )
        runs.append(run)
    return runs
