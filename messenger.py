"""Facebook Messenger webhook + Claude answer engine.

Exposes a FastAPI app with three endpoints:
  GET  /         -> healthcheck
  GET  /webhook  -> verification handshake with Meta
  POST /webhook  -> incoming messages from leads
  GET  /recent   -> last 50 events (real-time monitoring)

For every event we also push a one-line status to the user's Saved Messages
in Telegram, so monitoring works from your phone without opening Railway.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any

import httpx
from anthropic import Anthropic
from fastapi import FastAPI, HTTPException, Request, Response

import config
import pinecone_store
import telegram_helpers

log = logging.getLogger(__name__)

GRAPH_API_URL = "https://graph.facebook.com/v22.0/me/messages"

# Magic token Claude uses to signal "I don't have enough info - escalate".
ESCALATE_TOKEN = "[ESCALATE]"

SYSTEM_PROMPT = f"""\
You are the front-line assistant for an IP / networking business, replying to
leads on Facebook Messenger. You speak in the business's own voice (never
mention that you are an AI or that the knowledge comes from internal chat).

You will receive:
  1. The lead's latest message.
  2. Top-matching excerpts from the team's own past Telegram conversations.
     These are your ONLY source of truth - never invent facts not in there.

Rules:
- Reply in the same language the lead wrote in (English by default).
- Be brief: 1-3 sentences, friendly, conversational.
- Quote concrete numbers / model names / prices verbatim when they appear in
  context.
- If the context clearly answers the lead's question, give the answer directly.
- If the context does NOT clearly answer the question, OR if the question is
  technical and you are not confident, reply with exactly the literal string
  "{ESCALATE_TOKEN}" and nothing else. Do not apologize, do not explain -
  the system will route the question to a technician on your behalf.
"""

_anthropic_client: Anthropic | None = None

# In-memory ring buffer of the last 50 events. Each entry is a dict.
_event_log: deque = deque(maxlen=50)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log_event(event_type: str, **fields: Any) -> dict:
    entry = {"ts": _now_iso(), "type": event_type, **fields}
    _event_log.append(entry)
    return entry


def _anthropic() -> Anthropic:
    global _anthropic_client
    if _anthropic_client is None:
        _anthropic_client = Anthropic(api_key=config.ANTHROPIC_API_KEY)
    return _anthropic_client


def _verify_signature(app_secret: str, body: bytes, signature_header: str) -> bool:
    if not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest("sha256=" + expected, signature_header)


def _format_context(hits: list[dict]) -> str:
    if not hits:
        return "(no relevant past conversation found)"
    parts = []
    for i, hit in enumerate(hits, 1):
        ts = hit.get("date_iso", "")
        who = hit.get("sender_name", "?")
        text = hit.get("text", "")
        parts.append(f"[{i}] ({ts}) {who}: {text}")
    return "\n".join(parts)


async def generate_reply(lead_message: str):
    """Run Pinecone retrieval + Claude.
    Returns (reply_text, was_escalated, hits_found)."""
    hits = []
    try:
        hits = pinecone_store.search(lead_message, top_k=8)
    except Exception:
        log.exception("Pinecone search failed; escalating.")
        return (config.MESSENGER_FALLBACK_TEXT, True, 0)

    context = _format_context(hits)
    user_message = (
        f"Lead's message: {lead_message}\n\n"
        f"Relevant past chat excerpts (top-k by similarity):\n{context}"
    )

    try:
        response = _anthropic().messages.create(
            model=config.ANTHROPIC_MODEL,
            max_tokens=400,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )
        text_parts = [b.text for b in response.content if getattr(b, "type", "") == "text"]
        reply = "".join(text_parts).strip()
    except Exception:
        log.exception("Claude call failed; escalating.")
        return (config.MESSENGER_FALLBACK_TEXT, True, len(hits))

    if not reply or ESCALATE_TOKEN in reply:
        log.info("Claude chose to escalate. Falling back.")
        return (config.MESSENGER_FALLBACK_TEXT, True, len(hits))
    return (reply, False, len(hits))


async def send_messenger_reply(psid, text):
    payload = {
        "recipient": {"id": psid},
        "message": {"text": text[:1900]},
        "messaging_type": "RESPONSE",
    }
    params = {"access_token": config.MESSENGER_PAGE_ACCESS_TOKEN}
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(GRAPH_API_URL, params=params, json=payload)
    if r.status_code >= 400:
        log.error("Messenger send failed: %d %s", r.status_code, r.text)
    else:
        log.info("Sent reply to %s (%d chars)", psid, len(text))
    return r.status_code


async def handle_messaging_event(event):
    sender = event.get("sender", {}).get("id")
    message = event.get("message") or {}

    if message.get("is_echo"):
        return
    if not sender:
        return

    text = (message.get("text") or "").strip()
    if not text:
        log.info("Skipping non-text message from %s", sender)
        _log_event("incoming_non_text", sender=sender)
        return

    log.info("Lead %s says: %s", sender, text[:120])
    _log_event("incoming", sender=sender, text=text)
    await telegram_helpers.notify_admin(
        f"[Messenger IN] {sender}:\n{text[:1000]}"
    )

    t0 = time.time()
    reply, escalated, hits = await generate_reply(text)
    duration_ms = int((time.time() - t0) * 1000)

    status = await send_messenger_reply(sender, reply)
    _log_event(
        "reply",
        sender=sender,
        escalated=escalated,
        hits=hits,
        duration_ms=duration_ms,
        reply=reply,
        send_status=status,
    )
    tag = "ESCALATED" if escalated else "ANSWERED"
    await telegram_helpers.notify_admin(
        f"[Messenger OUT] {tag} ({hits} hits, {duration_ms}ms)\n{reply[:1000]}"
    )


def build_app():
    app = FastAPI(title="ip-leads-bot webhook")

    @app.get("/")
    async def healthcheck():
        return {
            "service": "ip-leads-bot",
            "messenger_enabled": config.messenger_enabled(),
            "events_in_buffer": len(_event_log),
        }

    @app.get("/recent")
    async def recent():
        return {"events": list(reversed(_event_log))}

    @app.get("/webhook")
    async def verify(request: Request):
        params = request.query_params
        if (
            params.get("hub.mode") == "subscribe"
            and params.get("hub.verify_token") == config.MESSENGER_VERIFY_TOKEN
        ):
            return Response(content=params.get("hub.challenge", ""), media_type="text/plain")
        _log_event("webhook_verify_failed", params=dict(params))
        raise HTTPException(status_code=403, detail="Verification failed")

    @app.post("/webhook")
    async def receive(request: Request):
        body = await request.body()

        signature = request.headers.get("x-hub-signature-256", "")
        if not _verify_signature(config.MESSENGER_APP_SECRET, body, signature):
            log.warning("Rejected webhook with bad signature")
            _log_event("bad_signature", signature=signature[:20])
            raise HTTPException(status_code=403, detail="Bad signature")

        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="Bad JSON")

        entry_count = sum(len(e.get("messaging", []) or []) for e in payload.get("entry", []))
        _log_event("webhook_post", entries=len(payload.get("entry", [])), events=entry_count)

        for entry in payload.get("entry", []):
            for event in entry.get("messaging", []):
                try:
                    await handle_messaging_event(event)
                except Exception:
                    log.exception("Failed to handle messaging event")
                    _log_event("event_error", sender=event.get("sender", {}).get("id"))

        return {"status": "ok"}

    return app
