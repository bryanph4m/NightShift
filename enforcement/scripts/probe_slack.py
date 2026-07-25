"""Probe the ScaleKit environment for a Slack connection and Slack tools.

Phase 2.4 needs a Slack connector with a completed OAuth flow. That setup is a
dashboard action, not an SDK one -- this script only reports what actually
exists so the setup step is verified rather than assumed.

Note: the SDK's list_* methods return a (response, metadata) tuple, not a bare
response -- see verify_identities.py.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from google.protobuf.json_format import MessageToDict

from enforcement.config import get_scalekit_client


def main() -> None:
    client = get_scalekit_client()

    print("=== all connected accounts (unfiltered) ===")
    try:
        resp, _ = client.connected_accounts.list_connected_accounts()
        accounts = [MessageToDict(a) for a in resp.connected_accounts]
        for a in accounts:
            print(json.dumps({
                k: a.get(k) for k in
                ("identifier", "connector", "provider", "status", "connectionId", "id")
            }, indent=2))
        if not accounts:
            print("(none)")
    except Exception as e:
        print("ERR:", type(e).__name__, e)

    print("\n=== slack-named tools in the catalog ===")
    try:
        found = set()
        page_token = None
        while True:
            kwargs = {"page_size": 100}
            if page_token:
                kwargs["page_token"] = page_token
            resp, _ = client.tools.list_tools(**kwargs)
            page = MessageToDict(resp)
            for t in page.get("tools", []):
                name = t.get("name") or t.get("id") or ""
                provider = str(t.get("provider", ""))
                if "slack" in str(name).lower() or "slack" in provider.lower():
                    found.add(f"{name}  (provider={provider})")
            page_token = page.get("nextPageToken")
            if not page_token:
                break
        print(f"count: {len(found)}")
        for name in sorted(found):
            print("   ", name)
    except Exception as e:
        print("ERR:", type(e).__name__, e)


if __name__ == "__main__":
    main()
