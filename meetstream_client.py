import os

import httpx

MEETSTREAM_BASE_URL = "https://api.meetstream.ai/api/v1"

# Posting to the meeting (send_message) is owned by the enforcement branch's
# post_to_meeting() — not duplicated here. This module only covers what
# perception needs: dispatching/removing Bot A and reading bot state.


def _client() -> httpx.Client:
    api_key = os.environ["MEETSTREAM_API_KEY"]
    return httpx.Client(
        base_url=MEETSTREAM_BASE_URL,
        headers={"Authorization": f"Token {api_key}"},
        timeout=30,
    )


def create_bot(bot_name: str, meeting_link: str, public_webhook_base_url: str) -> dict:
    """Dispatches a MeetStream bot into the meeting. Returns bot_id, transcript_id,
    meeting_url, status. video_required is explicitly False — it defaults to True."""
    body = {
        "meeting_link": meeting_link,
        "bot_name": bot_name,
        "video_required": False,
        "callback_url": f"{public_webhook_base_url}/webhook/meetstream/lifecycle",
        "live_transcription_required": {
            "webhook_url": f"{public_webhook_base_url}/webhook/meetstream/transcript"
        },
    }
    with _client() as client:
        resp = client.post("/bots/create_bot", json=body)
        resp.raise_for_status()
        return resp.json()


def remove_bot(bot_id: str) -> None:
    with _client() as client:
        resp = client.get(f"/bots/{bot_id}/remove_bot")
        resp.raise_for_status()


def get_bot_status(bot_id: str) -> dict:
    with _client() as client:
        resp = client.get(f"/bots/{bot_id}/status")
        resp.raise_for_status()
        return resp.json()


def get_chats(bot_id: str) -> list:
    """Polls posted meeting-chat messages (agent-to-agent coordination goes
    through chat, not the live transcript webhook — see main's README,
    'chat and transcript are different channels'). Each entry carries
    speakerName / speakerDisplayName for attribution."""
    with _client() as client:
        resp = client.get(f"/bots/{bot_id}/get_chats")
        resp.raise_for_status()
        return resp.json()
