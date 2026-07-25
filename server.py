"""Night Shift webhook server.

One public surface, three routes:
  POST /webhook/github                  GitHub Actions workflow_run events
  POST /webhook/meetstream/lifecycle    bot joined / in-meeting / left / failed
  POST /webhook/meetstream/transcript   live transcript segments

Every environment variable is read lazily *inside* a handler so the module
imports cleanly with no API keys present (which is also what makes it
unit-testable).
"""

import logging
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, Request, Response

try:
    from dotenv import load_dotenv

    # Same pattern as scalekit_client / diagnosis / extraction. Without this,
    # `uvicorn server:app` sees NONE of .env -- MEET_LINK and MEETSTREAM_API_KEY
    # would be empty and Bot A would never be dispatched. override=False so a
    # real environment variable (and pytest's monkeypatch) always wins.
    load_dotenv(override=False)
except Exception:  # pragma: no cover - dotenv is in requirements.txt
    pass

import db
import github_events
import meetstream_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("nightshift.server")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init_db()
    log.info("night shift server up; schema ready")
    yield


app = FastAPI(title="Night Shift", lifespan=lifespan)

# Module state. Bot A's id is stashed here at dispatch time so it can be
# removed cleanly at the end of the session. Bot B belongs to the enforcement
# branch, which polls the sessions table -- this server never touches it.
STATE: dict = {
    "bot_a_id": None,
    "session_id": None,
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.get("/state")
def state() -> dict:
    """Bot A's id and the open session id, over HTTP.

    STATE is per-process, so `import server; server.STATE` only works for code
    running *inside* this uvicorn worker. Anything in another process (the
    enforcement poller, a get_chats reader, teardown) must read it from here.
    """
    return dict(STATE)


# --------------------------------------------------------------------------
# GitHub
# --------------------------------------------------------------------------


@app.post("/webhook/github")
async def github_webhook(request: Request):
    # The signature is HMAC over the RAW bytes -- re-serialising a parsed dict
    # would change whitespace and break the digest.
    raw = await request.body()
    secret = os.environ.get("GITHUB_WEBHOOK_SECRET", "")
    if not secret:
        # An empty secret does not disable verification -- it just makes every
        # real GitHub delivery 401 for a reason that is invisible in the logs.
        # Say so out loud instead.
        log.error(
            "GITHUB_WEBHOOK_SECRET is not set; every GitHub delivery will be "
            "rejected as an invalid signature. Set it in .env AND in the repo "
            "webhook settings."
        )

    try:
        github_events.verify_signature(
            raw, request.headers.get("X-Hub-Signature-256"), secret
        )
    except github_events.InvalidSignature as exc:
        log.warning("rejected github webhook: %s", exc)
        return Response(status_code=401, content="invalid signature")

    try:
        payload = await request.json()
    except Exception:
        return Response(status_code=400, content="malformed json")

    if not github_events.is_failed_workflow_run(payload):
        # Green builds, in-progress runs and cancellations never open a session.
        return {"status": "ignored"}

    info = github_events.extract_failure_info(payload)
    log.info(
        "CI failure: %s/%s run %s (%s)",
        info["owner"], info["repo"], info["run_id"], info.get("workflow_name"),
    )

    try:
        logs = github_events.fetch_job_logs(info["owner"], info["repo"], info["run_id"])
    except Exception as exc:
        # A missing GITHUB_API_TOKEN must not sink the session -- diagnosis can
        # still proceed from the failure metadata alone.
        log.error("could not fetch job logs: %s", exc)
        logs = ""

    session_id = str(uuid.uuid4())
    meet_link = os.environ.get("MEET_LINK", "")
    triggered_by = f"{info['owner']}/{info['repo']}#{info['run_id']}"

    db.insert_session(
        session_id=session_id,
        meet_link=meet_link,
        triggered_by=triggered_by,
        started_at=_now(),
    )
    STATE["session_id"] = session_id
    log.info("session %s opened (%s), %d bytes of logs", session_id, triggered_by, len(logs))

    bot_id = None
    base_url = os.environ.get("PUBLIC_WEBHOOK_BASE_URL", "")
    if not meet_link or not base_url:
        log.error(
            "MEET_LINK=%r PUBLIC_WEBHOOK_BASE_URL=%r -- one is empty, so Bot A "
            "will either fail to join or join with unreachable callbacks.",
            meet_link, base_url,
        )
    try:
        bot = meetstream_client.create_bot(
            "Agent Alice",
            meet_link,
            base_url,
        )
        bot_id = bot.get("bot_id")
        STATE["bot_a_id"] = bot_id
        log.info("dispatched Bot A (Agent Alice) bot_id=%s", bot_id)
    except Exception as exc:
        log.error("failed to dispatch Bot A: %s", exc)

    # Bot B is enforcement's job -- they poll the sessions table. The row above
    # is the entire handoff; nothing else happens here for Bot B.
    return {
        "status": "session_opened",
        "session_id": session_id,
        "bot_a_id": bot_id,
        "failure": info,
    }


# --------------------------------------------------------------------------
# MeetStream lifecycle
# --------------------------------------------------------------------------

_LIFECYCLE_OK = {
    "bot.joining": "Bot A is joining the meeting",
    "bot.in_waiting_room": "Bot A is in the waiting room -- someone must admit it",
    "bot.inmeeting": "Bot A is in the meeting",
    "bot.stopped": "Bot A has left the meeting",
}

_LIFECYCLE_FAIL = {"bot.denied", "bot.notallowed", "bot.kicked", "bot.failed"}


@app.post("/webhook/meetstream/lifecycle")
async def meetstream_lifecycle(request: Request) -> dict:
    try:
        payload = await request.json()
    except Exception:
        payload = {}

    event = payload.get("bot_event", "")
    bot_id = payload.get("bot_id")
    message = payload.get("message", "")

    if event in _LIFECYCLE_FAIL:
        # Loud: if the bot never gets into the room there is no demo, and the
        # only symptom otherwise is silence.
        log.error(
            "BOT LIFECYCLE FAILURE %s bot_id=%s status=%s message=%r",
            event, bot_id, payload.get("bot_status"), message,
        )
    elif event in _LIFECYCLE_OK:
        log.info("%s (bot_id=%s) %s", _LIFECYCLE_OK[event], bot_id, message)
    else:
        log.info("unhandled bot_event %r bot_id=%s %s", event, bot_id, message)

    if event == "bot.inmeeting" and bot_id:
        STATE["bot_a_id"] = bot_id

    return {"status": "ok"}


# --------------------------------------------------------------------------
# MeetStream transcript
# --------------------------------------------------------------------------


@app.post("/webhook/meetstream/transcript")
async def meetstream_transcript(request: Request) -> dict:
    try:
        payload = await request.json()
    except Exception:
        payload = {}

    # Transcript events arrive incrementally and interim results CHANGE as the
    # recogniser revises them. Committing every event would fill
    # transcript_segments with partial duplicates of the same utterance, so a
    # row is written only when the turn is closed and the text is final.
    if not payload.get("end_of_turn"):
        return {"status": "interim"}

    text = payload.get("transcript") or payload.get("new_text") or ""
    if not text.strip():
        return {"status": "empty"}

    speaker = payload.get("speakerName") or "unknown"
    session_id = STATE.get("session_id") or payload.get("session_id") or ""

    db.insert_transcript_segment(
        session_id=session_id,
        speaker_label=speaker,
        text=text,
        timestamp=payload.get("timestamp") or _now(),
    )
    log.info("transcript [%s] %s", speaker, text)
    return {"status": "stored"}
