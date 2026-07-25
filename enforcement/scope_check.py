"""Phase 2.3 scope-check service.

Receives a proposal object, attempts the real commit as the *responding*
agent's own ScaleKit identity, and returns a decision object. The call is
never pre-checked -- it succeeds or fails on the real GitHub OAuth scope,
and the failure (if any) is classified and returned, never swallowed.

Contract note -- target_branch (optional, not in main's original contract):
Branch protection on `main` in both demo repos blocks *any* identity from
committing there directly (verified empirically, see HANDOFF.md) -- that's
what makes bug 3's "both denied" outcome real. But that also means bug 1
and bug 2's fixes can't land on `main` directly either; they land on a
fresh feature branch created from `main`'s current HEAD, which only
requires ordinary repo write access, unaffected by main's protection.
`target_branch` lets a proposal force a direct attempt against a specific
existing branch (e.g. "main", for bug 3) instead of creating a new one.
Omit it (the bug 1 / bug 2 case) and the service creates `fix/{bug_id}`
from HEAD and commits there. This is an additive, optional field -- flagged
to Person 1 in HANDOFF.md, not assumed silently.
"""

import base64
import json
import sys

from enforcement.config import (
    ALICE_IDENTIFIER,
    BOB_IDENTIFIER,
    GITHUB_ORG_OR_OWNER,
    get_scalekit_client,
)
from enforcement.db import get_connection, write_audit_log
from enforcement.errors import classify_error

REQUIRED_FIELDS = [
    "bug_id",
    "session_id",
    "description",
    "target_repo",
    "target_path",
    "proposed_change",
    "proposing_agent",
]

AGENT_IDENTIFIERS = {"agent_a": ALICE_IDENTIFIER, "agent_b": BOB_IDENTIFIER}
OTHER_AGENT = {"agent_a": "agent_b", "agent_b": "agent_a"}


def _decision(bug_id, decision, identity_used, reason, commit_url, error):
    return {
        "bug_id": bug_id,
        "decision": decision,
        "identity_used": identity_used,
        "reason": reason,
        "commit_url": commit_url,
        "error": error,
    }


def _commit_message(proposal: dict) -> str:
    # No AI attribution -- this commit is the demo's primary evidence and
    # must read as ordinary work by the engineer whose identity ran it.
    return f"Fix: {proposal['description']}"


def _log_and_return(decision: dict, *, session_id, proposing_agent, responding_agent) -> dict:
    # Every check gets a row -- allow, deny, or a malformed/pre-flight
    # rejection that never reached ScaleKit. identity_used is whatever the
    # decision actually carries (None if we never resolved one), not
    # backfilled from context.
    conn = get_connection()
    write_audit_log(
        conn,
        session_id=session_id,
        bug_id=decision["bug_id"],
        proposing_agent=proposing_agent,
        responding_agent=responding_agent,
        identity_used=decision["identity_used"],
        decision=decision["decision"],
        reason=decision["reason"],
        commit_url=decision["commit_url"],
    )
    conn.close()
    return decision


def process_proposal(proposal: dict) -> dict:
    bug_id = proposal.get("bug_id")
    session_id = proposal.get("session_id")
    proposing_agent = proposal.get("proposing_agent")
    responding_agent = OTHER_AGENT.get(proposing_agent)

    missing = [f for f in REQUIRED_FIELDS if f not in proposal]
    if missing:
        decision = _decision(
            bug_id, "deny", None,
            f"malformed proposal, missing required field(s): {missing}",
            None, "malformed_input",
        )
        return _log_and_return(
            decision, session_id=session_id,
            proposing_agent=proposing_agent, responding_agent=responding_agent,
        )

    identifier = AGENT_IDENTIFIERS.get(responding_agent)
    if identifier is None:
        decision = _decision(
            bug_id, "deny", None,
            f"unrecognized proposing_agent {proposing_agent!r}",
            None, "malformed_input",
        )
        return _log_and_return(
            decision, session_id=session_id,
            proposing_agent=proposing_agent, responding_agent=responding_agent,
        )

    target_repo = proposal["target_repo"]
    target_path = proposal["target_path"]
    target_branch = proposal.get("target_branch")  # optional -- see module docstring

    client = get_scalekit_client()

    try:
        if target_branch:
            branch_to_commit = target_branch
        else:
            head = client.actions.execute_tool(
                tool_name="github_branch_get",
                identifier=identifier,
                tool_input={"owner": GITHUB_ORG_OR_OWNER, "repo": target_repo, "branch": "main"},
            )
            head_sha = head.data["commit"]["sha"]
            branch_to_commit = f"fix/{bug_id}"
            try:
                client.actions.execute_tool(
                    tool_name="github_branch_create",
                    identifier=identifier,
                    tool_input={
                        "owner": GITHUB_ORG_OR_OWNER,
                        "repo": target_repo,
                        "branch_name": branch_to_commit,
                        "sha": head_sha,
                    },
                )
            except Exception as e:
                # "Reference already exists" (422) means a prior run already
                # created this branch -- not a permission problem, and this
                # service needs to survive being rehearsed more than once.
                # Any other failure here (a real permission denial creating
                # the branch) must still propagate to the outer handler.
                _, reason = classify_error(e)
                if "already exists" not in reason.lower():
                    raise

        file_sha = None
        try:
            existing = client.actions.execute_tool(
                tool_name="github_file_contents_get",
                identifier=identifier,
                tool_input={
                    "owner": GITHUB_ORG_OR_OWNER,
                    "repo": target_repo,
                    "path": target_path,
                    "ref": branch_to_commit,
                },
            )
            file_sha = existing.data.get("sha")
        except Exception:
            file_sha = None  # new file -- omit sha; a real read denial will resurface on the write attempt

        commit_input = {
            "owner": GITHUB_ORG_OR_OWNER,
            "repo": target_repo,
            "path": target_path,
            "message": _commit_message(proposal),
            "content": base64.b64encode(proposal["proposed_change"].encode()).decode(),
            "branch": branch_to_commit,
        }
        if file_sha:
            commit_input["sha"] = file_sha

        result = client.actions.execute_tool(
            tool_name="github_file_create_update",
            identifier=identifier,
            tool_input=commit_input,
        )
        commit_url = result.data.get("commit", {}).get("html_url")
        decision = _decision(
            bug_id, "allow", identifier,
            f"write access to {target_repo} verified", commit_url, None,
        )
    except Exception as e:
        error_type, reason = classify_error(e)
        decision = _decision(bug_id, "deny", identifier, reason, None, error_type)

    return _log_and_return(
        decision, session_id=session_id,
        proposing_agent=proposing_agent, responding_agent=responding_agent,
    )


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: python -m enforcement.scope_check <fixture.json>", file=sys.stderr)
        sys.exit(1)

    with open(sys.argv[1]) as f:
        proposal = json.load(f)

    result = process_proposal(proposal)
    print(json.dumps(result, indent=2))
