"""
Creates (or fetches) Alice's ScaleKit connected account for the GitHub
connection and prints an authorization link if it isn't ACTIVE yet.

Run this, then open the printed link in a browser signed in as Alice's
real GitHub account. Re-run afterward to confirm status is ACTIVE.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scalekit_client import GITHUB_CONNECTION_NAME, get_client

ALICE_IDENTIFIER = os.environ.get("ALICE_IDENTIFIER", "alice")


def main() -> None:
    scalekit = get_client()

    response = scalekit.actions.get_or_create_connected_account(
        connection_name=GITHUB_CONNECTION_NAME,
        identifier=ALICE_IDENTIFIER,
    )

    status = response.connected_account.status
    print(f"connected_account for '{ALICE_IDENTIFIER}': status={status}")

    if status != "ACTIVE":
        auth = scalekit.actions.get_authorization_link(
            connection_name=GITHUB_CONNECTION_NAME,
            identifier=ALICE_IDENTIFIER,
        )
        print(f"Authorize as Alice: {auth.link}")
        print(
            "Open this in a browser signed in as Alice's real GitHub account, "
            "then re-run this script to confirm status is ACTIVE."
        )
    else:
        print("Alice's connected account is already ACTIVE. Nothing to do.")


if __name__ == "__main__":
    main()
