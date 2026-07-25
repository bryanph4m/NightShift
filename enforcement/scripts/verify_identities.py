"""Phase 2.1 identity-verification utility.

Prints Bob's and Alice's real ScaleKit-scoped GitHub tools side by side and
diffs them, so a human can read the result instead of trusting a summary.
Run this again during rehearsal, not just once now.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from google.protobuf.json_format import MessageToDict

from enforcement.config import (
    ALICE_IDENTIFIER,
    BOB_IDENTIFIER,
    GITHUB_CONNECTION_NAME,
    get_scalekit_client,
)

OUTPUT_DIR = Path(__file__).resolve().parent / "verify_output"


def fetch_connected_account(client, identifier):
    resp, _ = client.connected_accounts.list_connected_accounts(
        connection_names=[GITHUB_CONNECTION_NAME], identifier=identifier
    )
    accounts = [MessageToDict(a) for a in resp.connected_accounts]
    return accounts[0] if accounts else None


def fetch_all_scoped_tools(client, identifier):
    tools = []
    page_token = None
    while True:
        kwargs = {
            "identifier": identifier,
            "filter": {"connection_names": [GITHUB_CONNECTION_NAME]},
            "page_size": 100,
        }
        if page_token:
            kwargs["page_token"] = page_token
        resp, _ = client.tools.list_scoped_tools(**kwargs)
        page = MessageToDict(resp)
        tools.extend(page.get("tools", []))
        page_token = page.get("nextPageToken")
        if not page_token:
            break
    return tools


def tool_key(entry):
    tool = entry.get("tool", {})
    return tool.get("id"), tool.get("provider")


def main():
    OUTPUT_DIR.mkdir(exist_ok=True)
    client = get_scalekit_client()

    identities = {"bob": BOB_IDENTIFIER, "alice": ALICE_IDENTIFIER}
    accounts = {}
    tool_sets = {}

    for label, identifier in identities.items():
        account = fetch_connected_account(client, identifier)
        accounts[label] = account
        tools = fetch_all_scoped_tools(client, identifier)
        tool_sets[label] = tools
        (OUTPUT_DIR / f"{label}_connected_account.json").write_text(
            json.dumps(account, indent=2)
        )
        (OUTPUT_DIR / f"{label}_scoped_tools.json").write_text(
            json.dumps(tools, indent=2)
        )

    print("=== Connected account identity ===")
    for label in identities:
        a = accounts[label]
        if a is None:
            print(f"{label}: NO CONNECTED ACCOUNT FOUND")
            continue
        print(
            f"{label}: identifier={a.get('identifier')!r} "
            f"id={a.get('id')} status={a.get('status')} "
            f"provider={a.get('provider')} connector={a.get('connector')} "
            f"connectionId={a.get('connectionId')}"
        )

    same_connected_account = (
        accounts["bob"] is not None
        and accounts["alice"] is not None
        and accounts["bob"].get("id") == accounts["alice"].get("id")
    )
    print()
    print(
        "Bob and Alice resolve to the SAME connected account id -- BROKEN"
        if same_connected_account
        else "Bob and Alice resolve to DIFFERENT connected account ids -- good"
    )

    print()
    print("=== Scoped tool set comparison ===")
    bob_keys = {tool_key(t) for t in tool_sets["bob"]}
    alice_keys = {tool_key(t) for t in tool_sets["alice"]}
    print(f"bob: {len(tool_sets['bob'])} tools, {len(bob_keys)} unique ids")
    print(f"alice: {len(tool_sets['alice'])} tools, {len(alice_keys)} unique ids")
    only_bob = bob_keys - alice_keys
    only_alice = alice_keys - bob_keys
    print(f"tools only in bob's scoped set: {len(only_bob)}")
    print(f"tools only in alice's scoped set: {len(only_alice)}")
    if only_bob:
        print("  bob-only sample:", list(only_bob)[:5])
    if only_alice:
        print("  alice-only sample:", list(only_alice)[:5])

    if bob_keys == alice_keys:
        print()
        print(
            "NOTE: the scoped GitHub *tool schemas* are identical for bob and "
            "alice. This is expected for OAuth-based connections -- the tool "
            "interface (which REST calls exist) is generic. The real "
            "per-identity enforcement happens at execute_tool time, when "
            "each identity's own GitHub OAuth token is checked against the "
            "target repo by GitHub itself, not here. Do not treat identical "
            "tool lists as a broken identity separation -- verify separation "
            "instead via the connected-account ids above (different) and, "
            "conclusively, via a real execute_tool call against a repo only "
            "one of them can write to."
        )

    print()
    print(f"Full JSON written to {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
