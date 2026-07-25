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
# 1.0 is OpenAI's default; 0.25-4.0 is the accepted range.
TTS_SPEED = float(os.environ.get("TTS_SPEED", "0.82"))
# "auto" prefers OpenAI and falls back to the local Windows voice on any
# failure; "openai" or "sapi" force one backend (useful for rehearsing offline).
TTS_BACKEND = os.environ.get("TTS_BACKEND", "auto").lower()

# Turn pacing. CHARS_PER_SECOND must track TTS_SPEED — it is how long we assume
# an utterance takes, and underestimating makes the agents talk over each other.
CHARS_PER_SECOND = float(os.environ.get("TTS_CHARS_PER_SEC", "10.9"))
MIN_TURN_S = 3.0
INTER_TURN_PAUSE_S = float(os.environ.get("TTS_TURN_PAUSE", "0.9"))
HANDOFF_PAUSE_S = float(os.environ.get("TTS_HANDOFF_PAUSE", "1.6"))

# Distinct voices so an audience can tell the two agents apart by ear alone.
# Alice reads female, Bob male, matching how they are named on screen.
VOICE_FOR_AGENT = {"agent_a": "nova", "agent_b": "onyx"}

# Local Windows equivalents, matched by name. Zira is female, David is male.
SAPI_VOICE_FOR = {"nova": "Zira", "onyx": "David"}

# bot_id -> live websocket, populated as each bot completes its handshake.
CONNECTED: dict[str, websockets.WebSocketServerProtocol] = {}
# bot_id -> "agent_a" | "agent_b", registered by the caller before dispatch.
BOT_ROLE: dict[str, str] = {}


def synthesize_pcm16_48k(text: str, voice: str) -> bytes:
    """TTS to raw PCM16 LE at 48kHz mono, which is what sendaudio expects.

    Tries OpenAI first for voice quality, then falls back to the Windows speech
    engine. The fallback exists because a demo that dies on an exhausted API
    quota is worse than one that sounds a little synthetic — and TTS quota is
    exactly the thing that runs out mid-event.
    """
    if os.environ.get("OPENAI_API_KEY") and TTS_BACKEND in ("auto", "openai"):
        try:
            return _synthesize_openai(text, voice)
        except Exception as exc:
            if TTS_BACKEND == "openai":
                raise
            print(f"[voice] OpenAI TTS unavailable ({type(exc).__name__}), using local voice")
    return _synthesize_sapi(text, voice)


def _synthesize_openai(text: str, voice: str) -> bytes:
    from openai import OpenAI

    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    # response_format="pcm" returns headerless signed 16-bit LE mono @ 24kHz.
    # Default pace is brisk for a conference call where the listener is also
    # reading a terminal, so it is slowed a little.
    resp = client.audio.speech.create(
        model=TTS_MODEL,
        voice=voice,
        input=text,
        response_format="pcm",
        speed=TTS_SPEED,
    )
    return _resample_to_48k(resp.content, 24000)


# SAPI rate is -10..10 where 0 is normal; roughly maps our 0.82 speed to -2.
_SAPI_RATE = int(round((TTS_SPEED - 1.0) * 10))


def _synthesize_sapi(text: str, voice: str) -> bytes:
    """Windows SAPI to 48kHz PCM16 mono, via a temp WAV we strip the header from."""
    import tempfile
    import wave

    import win32com.client

    engine = win32com.client.Dispatch("SAPI.SpVoice")

    # Pick a different installed voice per agent so the two are distinguishable
    # by ear. Matched by name rather than list index: index order is not stable
    # across machines, and getting it backwards makes Alice sound like Bob —
    # which reads as "one agent talking to itself" rather than a handoff.
    try:
        voices = engine.GetVoices()
        available = [voices.Item(i) for i in range(voices.Count)]
        wanted = SAPI_VOICE_FOR.get(voice, "")
        chosen = next(
            (v for v in available if wanted.lower() in v.GetDescription().lower()),
            None,
        )
        if chosen is None and available:
            # Fall back to opposite ends of the list so the two still differ.
            chosen = available[0] if voice == "nova" else available[-1]
        if chosen is not None:
            engine.Voice = chosen
    except Exception:
        pass

    engine.Rate = _SAPI_RATE

    path = tempfile.mktemp(suffix=".wav")
    stream = win32com.client.Dispatch("SAPI.SpFileStream")
    stream.Open(path, 3)  # SSFMCreateForWrite
    engine.AudioOutputStream = stream
    engine.Speak(text)
    stream.Close()

    with wave.open(path, "rb") as wf:
        rate = wf.getframerate()
        frames = wf.readframes(wf.getnframes())
        if wf.getnchannels() == 2:  # downmix to mono
            samples = np.frombuffer(frames, dtype=np.int16).reshape(-1, 2)
            frames = samples.mean(axis=1).astype(np.int16).tobytes()

    try:
        os.remove(path)
    except OSError:
        pass

    return _resample_to_48k(frames, rate)


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
# Roughly three minutes of spoken dialogue, paced for an audience that is also
# watching a terminal. Every claim here is one the live run actually produces —
# the repos, the defects, the refusal reasons and the escalation trigger all
# match what demo.py prints, so nothing said aloud overstates the system.
SCRIPT = [
    ("agent_a", "Bob, we have a problem. C I just went red on notifications "
                "service. Diagnosing now."),
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
    prev_role = None
    for idx, (role, line) in enumerate(SCRIPT):
        bot_id = role_to_bot.get(role)
        if not bot_id:
            continue

        # A beat before the other agent answers reads as thinking rather than
        # lag, and gives an audience time to look from the terminal to the call.
        if prev_role is not None and role != prev_role:
            await asyncio.sleep(HANDOFF_PAUSE_S)

        await speak(bot_id, line)

        # Hold the floor for the length of the utterance so turns never overlap.
        # CHARS_PER_SECOND is tuned to TTS_SPEED; too low and they talk over
        # each other, which is the one failure an audience notices instantly.
        spoken = len(line) / CHARS_PER_SECOND
        await asyncio.sleep(max(MIN_TURN_S, spoken) + INTER_TURN_PAUSE_S)
        prev_role = role
        print(f"[voice] turn {idx + 1}/{len(SCRIPT)} done")
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
