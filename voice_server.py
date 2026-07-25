"""
Control-channel WebSocket server that gives the two bots a voice.

MeetStream connects OUT to this server (the bot is the WS client, we are the
server) when a bot is created with socket_connection_url. Once the bot sends its
"ready" handshake we can push it a `sendaudio` command, which plays PCM16 audio
through the bot's virtual microphone so every participant hears it.

Audio path: OpenAI TTS -> raw PCM16 LE @ 24kHz -> resampled to 48kHz -> base64.
MeetStream wants raw samples: no WAV header, no MP3 container, mono only.

Run:  python voice_server.py          (expects an ngrok tunnel onto PORT)
"""

import asyncio
import base64
import json
import os

import numpy as np
import websockets
from dotenv import load_dotenv

load_dotenv()

PORT = int(os.environ.get("VOICE_WS_PORT", "8787"))
TTS_MODEL = os.environ.get("OPENAI_TTS_MODEL", "tts-1")

# Distinct voices so an audience can tell the two agents apart by ear alone.
VOICE_FOR_AGENT = {"agent_a": "nova", "agent_b": "onyx"}

# bot_id -> live websocket, populated as each bot completes its handshake.
CONNECTED: dict[str, websockets.WebSocketServerProtocol] = {}
# bot_id -> "agent_a" | "agent_b", registered by the caller before dispatch.
BOT_ROLE: dict[str, str] = {}


def synthesize_pcm16_48k(text: str, voice: str) -> bytes:
    """TTS to raw PCM16 LE at 48kHz mono, which is what sendaudio expects."""
    from openai import OpenAI

    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    # response_format="pcm" returns headerless signed 16-bit LE mono @ 24kHz.
    resp = client.audio.speech.create(
        model=TTS_MODEL, voice=voice, input=text, response_format="pcm"
    )
    pcm_24k = resp.content
    return _resample_to_48k(pcm_24k, 24000)


def _resample_to_48k(pcm_bytes: bytes, source_rate: int) -> bytes:
    if source_rate == 48000:
        return pcm_bytes
    samples = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32)
    num_out = int(len(samples) * 48000 / source_rate)
    t_in = np.linspace(0, 1, len(samples), endpoint=False)
    t_out = np.linspace(0, 1, num_out, endpoint=False)
    resampled = np.interp(t_out, t_in, samples)
    return np.clip(resampled, -32768, 32767).astype(np.int16).tobytes()


def build_sendaudio(bot_id: str, pcm_bytes: bytes) -> dict:
    return {
        "command": "sendaudio",
        "bot_id": bot_id,
        "audiochunk": base64.b64encode(pcm_bytes).decode("utf-8"),
        "sample_rate": 48000,
        "encoding": "pcm16",
        "channels": 1,
        "endianness": "little",
    }


async def speak(bot_id: str, text: str) -> bool:
    """Say `text` out loud as the given bot. Returns False if it is not connected."""
    ws = CONNECTED.get(bot_id)
    if ws is None:
        print(f"[voice] bot {bot_id[:8]} is not connected yet")
        return False

    role = BOT_ROLE.get(bot_id, "agent_a")
    voice = VOICE_FOR_AGENT.get(role, "nova")
    pcm = await asyncio.to_thread(synthesize_pcm16_48k, text, voice)
    await ws.send(json.dumps(build_sendaudio(bot_id, pcm)))
    print(f"[voice] {role} spoke {len(pcm)} bytes: {text[:60]}")
    return True


# The handoff, spoken. Kept here rather than in a separate process because the
# live websockets only exist inside this one — a second process importing this
# module would see an empty CONNECTED map and never speak.
SCRIPT = [
    ("agent_a", "C I is red on notifications service. Diagnosing now."),
    ("agent_a", "The dispatch loop appends the subject instead of the recipient. "
                "I have a fix, but I don't have write access to that repo. "
                "Bob, can you take it?"),
    ("agent_b", "Taking it. Committing as Bob now."),
    ("agent_b", "Confirmed. The commit landed under my account."),
    ("agent_b", "Now payments service is red. The payer balance is credited "
                "instead of debited. I can't write there. Alice, over to you."),
    ("agent_a", "Got it. Committed as Alice."),
    ("agent_a", "The last fix has to land on protected main. Neither of us can "
                "merge. Paging the on call engineer."),
]

_script_started = False


async def play_script() -> None:
    """Speak the handoff once, strictly one turn at a time."""
    role_to_bot = {role: bid for bid, role in BOT_ROLE.items()}
    for role, line in SCRIPT:
        bot_id = role_to_bot.get(role)
        if not bot_id:
            continue
        await speak(bot_id, line)
        # Hold the floor until the utterance finishes, so turns never overlap.
        await asyncio.sleep(max(3.0, len(line) / 13.0))
    print("[voice] script complete")


async def _maybe_start_script() -> None:
    global _script_started
    expected = len(BOT_ROLE) or 2
    if _script_started or len(CONNECTED) < expected:
        return
    _script_started = True
    print(f"[voice] both bots connected — speaking in 2s")
    await asyncio.sleep(2)
    await play_script()


async def handler(ws) -> None:
    """MeetStream's bot connects here and announces itself, then waits."""
    bot_id = None
    try:
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue

            if msg.get("type") == "ready":
                bot_id = msg.get("bot_id")
                CONNECTED[bot_id] = ws
                print(f"[voice] READY  bot={bot_id}  role={BOT_ROLE.get(bot_id, '?')}")
                asyncio.create_task(_maybe_start_script())
            else:
                print(f"[voice] msg from {str(bot_id)[:8]}: {str(msg)[:160]}")
    except websockets.ConnectionClosed:
        pass
    finally:
        if bot_id:
            CONNECTED.pop(bot_id, None)
            print(f"[voice] closed bot={bot_id[:8]}")


def load_roles() -> None:
    """Map bot_id -> agent role from the file dispatch_voice_bots.py writes."""
    try:
        with open("voice_bots.json") as fh:
            for role, bot_id in json.load(fh).items():
                if bot_id:
                    BOT_ROLE[bot_id] = role
        print(f"[voice] roles loaded: {BOT_ROLE}")
    except FileNotFoundError:
        print("[voice] no voice_bots.json yet — run dispatch_voice_bots.py")


async def main() -> None:
    load_roles()
    async with websockets.serve(handler, "0.0.0.0", PORT, ping_interval=20):
        print(f"[voice] control server listening on :{PORT}")
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
