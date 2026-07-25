"""
Prints each principal's real scoped GitHub tools side by side.

Phase 1.1 (this branch) only needs Alice's list to be non-empty. The
"Alice and Bob resolve to genuinely different results" check is the
mandatory sync point with Person 2 once BOB_IDENTIFIER also has an
ACTIVE connected account on the enforcement side.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scalekit_client import GITHUB_CONNECTION_NAME, get_client

ALICE_IDENTIFIER = os.environ.get("ALICE_IDENTIFIER", "alice")
BOB_IDENTIFIER = os.environ.get("BOB_IDENTIFIER", "bob")


def print_scoped_tools(scalekit, identifier: str) -> list:
    try:
        tools = scalekit.tools.list_scoped_tools(
            identifier=identifier,
            filter={"connection_names": [GITHUB_CONNECTION_NAME]},
        )
    except Exception as exc:
        print(f"{identifier}: ERROR — {exc}")
        return []

    names = [t.tool.definition.name for t in tools.tools]
    print(f"{identifier}: {names}")
    return names


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

    if bob_tools:
        if alice_tools == bob_tools:
            print(
                "\nWARNING: Alice and Bob resolve to IDENTICAL scoped tools. "
                "This means both OAuth flows were completed as the same "
                "GitHub user. Redo one of the connected-account flows with "
                "a separate browser profile before proceeding."
            )
        else:
            print("\nAlice and Bob resolve to different scoped tools. Sync point passed.")
    else:
        print(
            "\nBob has no scoped tools yet (expected until enforcement "
            "sets up Bob's connected account). Re-run once that's done."
        )


if __name__ == "__main__":
    main()
