"""MeetStream bot lifecycle and the shared post_to_meeting posting layer.

Shared contract (canonical copy on main): post_to_meeting(agent_id, text)
is the single path both agents use to post into the meeting. Bot A's id is
owned by perception; Bot B's id is owned here.
"""

import os

import requests

from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://api.meetstream.ai/api/v1"


def _headers():
    return {
        "Authorization": f"Token {os.environ['MEETSTREAM_API_KEY']}",
        "Content-Type": "application/json",
    }


def create_bot(meeting_link: str, bot_name: str, video_required: bool = False, **kwargs) -> dict:
    """Create a bot and have it join the given meeting link.

    Returns the response body: {bot_id, transcript_id, meeting_url, status}.
    """
    body = {
        "meeting_link": meeting_link,
        "bot_name": bot_name,
        "video_required": video_required,
        **kwargs,
    }
    resp = requests.post(f"{BASE_URL}/bots/create_bot", json=body, headers=_headers())
    resp.raise_for_status()
    return resp.json()


def remove_bot(bot_id: str) -> dict:
    resp = requests.get(f"{BASE_URL}/bots/{bot_id}/remove_bot", headers=_headers())
    resp.raise_for_status()
    return resp.json()


def get_bot_status(bot_id: str) -> dict:
    resp = requests.get(f"{BASE_URL}/bots/{bot_id}/status", headers=_headers())
    resp.raise_for_status()
    return resp.json()


def send_message(bot_id: str, message: str) -> dict:
    resp = requests.post(
        f"{BASE_URL}/bots/{bot_id}/send_message",
        json={"message": message},
        headers=_headers(),
    )
    resp.raise_for_status()
    return resp.json()


def get_chats(bot_id: str) -> dict:
    resp = requests.get(f"{BASE_URL}/bots/{bot_id}/get_chats", headers=_headers())
    resp.raise_for_status()
    return resp.json()


# agent_id -> bot_id. Bot A's id comes from perception once it dispatches;
# Bot B's id comes from this branch. Populated via env for now -- becomes a
# shared-store lookup (the `sessions` row) at integration.
_AGENT_BOT_IDS = {
    "agent_a": os.environ.get("AGENT_A_BOT_ID"),
    "agent_b": os.environ.get("AGENT_B_BOT_ID"),
}


def post_to_meeting(agent_id: str, text: str) -> None:
    """Route a message to the correct bot and post it to the meeting chat.

    agent_id: "agent_a" | "agent_b"
    """
    bot_id = _AGENT_BOT_IDS.get(agent_id)
    if not bot_id:
        raise ValueError(
            f"no bot_id configured for {agent_id!r} -- set AGENT_A_BOT_ID / "
            f"AGENT_B_BOT_ID in .env"
        )
    send_message(bot_id, text)
