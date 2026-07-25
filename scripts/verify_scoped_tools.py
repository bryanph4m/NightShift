"""
Confirms Alice and Bob are genuinely distinct GitHub principals — the
mandatory sync point before anything downstream (both branches) is real.

list_scoped_tools() alone is NOT a reliable check here: it reflects the
GitHub connector's static tool catalog for a connection, not the
connected user's actual repo permissions, so two genuinely different
GitHub accounts can (and typically do) resolve to an identical tool
*name* list. The only trustworthy signal is executing a tool and reading
back who GitHub says you are — so this script calls
github_user_get_authenticated for each identifier and compares the
returned login.
"""

import os
import sys

from google.protobuf.json_format import MessageToDict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scalekit_client import GITHUB_CONNECTION_NAME, get_client

ALICE_IDENTIFIER = os.environ.get("ALICE_IDENTIFIER", "alice")
BOB_IDENTIFIER = os.environ.get("BOB_IDENTIFIER", "bob")


def print_scoped_tools(scalekit, identifier: str) -> list:
    try:
        # list_scoped_tools returns a (response, call) tuple, not a bare
        # response object — verified against the installed SDK (main's
        # README example omits the second element).
        response, _call = scalekit.tools.list_scoped_tools(
            identifier=identifier,
            filter={"connection_names": [GITHUB_CONNECTION_NAME]},
        )
    except Exception as exc:
        print(f"{identifier}: ERROR — {exc}")
        return []

    # tool.definition is a protobuf Struct — dynamic fields aren't
    # attribute-accessible (tool.definition.name raises AttributeError),
    # so go through MessageToDict.
    names = [
        MessageToDict(t.tool.definition).get("name", t.tool.id)
        for t in response.tools
    ]
    print(f"{identifier}: {len(names)} tools (of {response.total_size} total) — {names}")
    return names


def get_authenticated_login(scalekit, identifier: str) -> str | None:
    try:
        result = scalekit.actions.execute_tool(
            tool_name="github_user_get_authenticated",
            identifier=identifier,
            tool_input={},
        )
    except Exception as exc:
        print(f"{identifier}: ERROR calling github_user_get_authenticated — {exc}")
        return None
    return result.data.get("login")


def main() -> None:
    scalekit = get_client()

    alice_tools = print_scoped_tools(scalekit, ALICE_IDENTIFIER)
    bob_tools = print_scoped_tools(scalekit, BOB_IDENTIFIER)

    if not alice_tools:
        print(
            "\nAlice's scoped tool list is empty. Check: connected account "
            "ACTIVE? SCALEKIT_GITHUB_CONNECTION_NAME matches the dashboard "
            "exactly (case-sensitive)?"
        )
        return

    if not bob_tools:
        print(
            "\nBob has no scoped tools yet (expected until enforcement "
            "sets up Bob's connected account). Re-run once that's done."
        )
        return

    print()
    alice_login = get_authenticated_login(scalekit, ALICE_IDENTIFIER)
    bob_login = get_authenticated_login(scalekit, BOB_IDENTIFIER)
    print(f"alice authenticates as: {alice_login}")
    print(f"bob authenticates as:   {bob_login}")

    if not alice_login or not bob_login:
        print("\nCould not confirm both identities — see errors above.")
    elif alice_login == bob_login:
        print(
            "\nFAIL: Alice and Bob authenticate as the SAME GitHub user "
            f"({alice_login}). Both OAuth flows were completed as the same "
            "account. Redo one of the connected-account flows signed in as "
            "a genuinely separate GitHub account."
        )
    else:
        print(
            f"\nPASS: Alice ({alice_login}) and Bob ({bob_login}) are "
            "genuinely distinct GitHub principals. Sync point passed."
        )


if __name__ == "__main__":
    main()
