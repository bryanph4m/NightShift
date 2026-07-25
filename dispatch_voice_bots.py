"""Dispatch both agents into the meeting with the voice control channel enabled.

socket_connection_url can only be set at bot-creation time, so bots already in
the call cannot be upgraded to speak — these are new ones.
"""

import json
import os
import sys

import httpx
from dotenv import load_dotenv

load_dotenv(override=True)

WS_URL = os.environ.get("VOICE_WS_URL")
if not WS_URL:
    sys.exit("VOICE_WS_URL is not set (the wss:// tunnel onto voice_server.py)")


def dispatch(bot_name: str) -> str | None:
    body = {
        "meeting_link": os.environ["MEET_LINK"],
        "bot_name": bot_name,
        "video_required": False,
        "socket_connection_url": {"websocket_url": WS_URL},
    }
    resp = httpx.post(
        "https://api.meetstream.ai/api/v1/bots/create_bot",
        headers={"Authorization": f"Token {os.environ['MEETSTREAM_API_KEY']}"},
        json=body,
        timeout=60,
    )
    print(f"{bot_name}: {resp.status_code} {resp.text[:200]}")
    return resp.json().get("bot_id") if resp.status_code == 201 else None


if __name__ == "__main__":
    ids = {"agent_a": dispatch("Agent Alice"), "agent_b": dispatch("Agent Bob")}
    with open("voice_bots.json", "w") as fh:
        json.dump(ids, fh)
    print(json.dumps(ids))
