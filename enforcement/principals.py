"""Real, per-identity GitHub permissions for the dashboard's setup panel.

The audience needs to see, before the first bug runs, that Alice and Bob carry
genuinely different access. So this reads the permissions from GitHub itself,
as each identity, rather than restating a table someone typed.

Method: `GET /repos/{owner}/{repo}` returns a `permissions` object scoped to the
*authenticated* user. Calling it through ScaleKit with each identifier means the
answer comes back from GitHub evaluating that principal's own OAuth token. It is
also read-only, so refreshing it costs nothing and can be re-run in front of a
judge who asks whether the matrix is hardcoded.

(The obvious alternative, `/repos/{owner}/{repo}/collaborators/{user}/permission`,
needs admin rights that neither principal has -- and answering as the repo owner
would defeat the point, since it would no longer be the principal's own view.)

Results are cached to principals.json so the dashboard never blocks on a live
API call mid-demo. Refresh with:  python -m enforcement.principals
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from enforcement.config import (
    ALICE_IDENTIFIER,
    BOB_IDENTIFIER,
    GITHUB_CONNECTION_NAME,
    GITHUB_ORG_OR_OWNER,
    get_scalekit_client,
)

CACHE_PATH = Path(__file__).resolve().parent / "principals.json"

REPOS = ("payments-service", "notifications-service")
AGENTS = {"agent_a": ALICE_IDENTIFIER, "agent_b": BOB_IDENTIFIER}


def _permission_label(permissions: dict) -> str:
    """Collapse GitHub's permission flags into the highest level held."""
    for level in ("admin", "maintain", "push", "triage", "pull"):
        if permissions.get(level):
            return {"push": "write", "pull": "read"}.get(level, level)
    return "none"


def fetch_live() -> dict:
    client = get_scalekit_client()
    principals = {}

    for agent_id, identifier in AGENTS.items():
        entry = {"agent_id": agent_id, "identifier": identifier, "repos": {}}

        try:
            me = client.actions.execute_tool(
                tool_name="github_user_get_authenticated",
                identifier=identifier,
                tool_input={},
            )
            entry["github_login"] = me.data.get("login")
        except Exception as e:
            entry["github_login"] = None
            entry["error"] = f"{type(e).__name__}: {e}"

        for repo in REPOS:
            try:
                resp = client.actions.request(
                    connection_name=GITHUB_CONNECTION_NAME,
                    identifier=identifier,
                    path=f"/repos/{GITHUB_ORG_OR_OWNER}/{repo}",
                    method="GET",
                )
                # actions.request() returns a raw requests.Response, not a
                # ScaleKit result object -- .data is None on it. Parsing this
                # wrongly is quiet rather than loud: a missing `permissions`
                # key collapses to "none", which looks like a real answer and
                # would have put a false matrix on the projector.
                body = resp.json() if hasattr(resp, "json") else resp
                if not isinstance(body, dict) or "permissions" not in body:
                    raise ValueError(
                        f"unexpected response shape, no 'permissions' key "
                        f"(status={getattr(resp, 'status_code', '?')})"
                    )
                entry["repos"][repo] = _permission_label(body["permissions"])
            except Exception as e:
                entry["repos"][repo] = f"error: {type(e).__name__}"

        principals[agent_id] = entry

    return {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "owner": GITHUB_ORG_OR_OWNER,
        "repos": list(REPOS),
        "principals": principals,
    }


def load_cached() -> dict | None:
    if CACHE_PATH.exists():
        try:
            return json.loads(CACHE_PATH.read_text())
        except ValueError:
            return None
    return None


def refresh() -> dict:
    data = fetch_live()
    CACHE_PATH.write_text(json.dumps(data, indent=2))
    return data


if __name__ == "__main__":
    print(json.dumps(refresh(), indent=2))
