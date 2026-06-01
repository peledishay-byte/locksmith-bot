"""Lead-qualifier Messenger webhook.

The bot is a front-line lead qualifier (NOT a cashier):
- greets the lead
- checks whether we offer the service they need
- for car keys, looks up our Google Sheets catalog for a price range
- collects phone number + best time to call
- forwards a structured lead to the technician Telegram group
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
import conversation_state
import lead_forwarder
import sheets_client
import telegram_helpers

log = logging.getLogger(__name__)

GRAPH_API_URL = "https://graph.facebook.com/v22.0/me/messages"

TOOLS = [
    {
        "name": "lookup_car_key",
        "description": (
            "Look up our catalog for a car key by make / model / year. "
            "Use this when the lead has given you the car manufacturer AND model. "
            "Year is optional but helpful. Returns matching SKUs with a price range."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "manufacturer": {
                    "type": "string",
                    "description": "Car manufacturer, e.g. Honda, Toyota, Chevrolet",
                },
                "model": {
                    "type": "string",
                    "description": "Car model, e.g. Civic, Camry, Malibu",
                },
                "year": {
                    "type": "integer",
                    "description": "Optional: year of the vehicle",
                },
            },
            "required": ["manufacturer", "model"],
        },
    },
    {
        "name": "forward_lead",
        "description": (
            "Forward this qualified lead to a technician on our team. "
            "ONLY call this when you have BOTH: a phone number AND a clear "
            "best_time_to_call from the lead. After this tool returns, send "
            "the lead ONE short confirmation message saying a technician will "
            "call at the time they specified."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "service": {
                    "type": "string",
                    "description": "Service category, e.g. 'Car key replacement', 'Lock change', 'Lockout'",
                },
                "phone": {
                    "type": "string",
                    "description": "The lead's phone number, as they gave it.",
                },
                "best_time_to_call": {
                    "type": "string",
                    "description": "When the lead said to call, e.g. 'today after 5pm'",
                },
                "summary": {
                    "type": "string",
                    "description": "1-3 sentence summary of the lead's situation for the technician.",
                },
                "city": {
                    "type": "string",
                    "description": "Optional: city or area",
                },
                "vehicle": {
                    "type": "string",
                    "description": "Optional: e.g. 'Honda Civic 2015' for car-key leads",
                },
                "price_range_low": {
                    "type": "integer",
                    "description": "Optional: low end of the price range you quoted the lead.",
                },
                "price_range_high": {
                    "type": "integer",
                    "description": "Optional: high end of the price range you quoted the lead.",
                },
            },
            "required": ["service", "phone", "best_time_to_call", "summary"],
        },
    },
]

SYSTEM_PROMPT = """\
You are the front-line lead qualifier for a locksmith business that handles:
- Car key replacement and duplication (mechanical, transponder, remote, flip, smart/prox)
- Lock change and installation (residential / commercial)
- Lockouts (car / home / business)

Your job is NOT to give exact quotes or to schedule jobs yourself. Your job
is to greet the lead, find out what they need, and -- IF it's something we
do -- collect their phone number and best time to call, then hand them off
to a technician via forward_lead.

Style:
- Friendly, brief, conversational. 1-2 short sentences per message.
- Reply in the same language the lead writes in (English by default; Hebrew
  if they wrote in Hebrew).
- Ask ONE thing at a time. Don't dump a form.
- Speak in the business's own voice. Never reveal you are an AI or talk about
  tools / internal systems.

Flow:
1. Greet warmly and ask how you can help.
2. Figure out the service category. If it is something we do NOT offer
   (e.g. safe cracking, garage doors, alarms, IT/networking) - politely
   say so in one line and end the conversation. Do NOT collect their info.
3. If it is a car key: ask make, model, then year (one at a time if
   missing). Also find out (gently, when natural) whether they still have
   an original/working key - this changes the price ("spare" vs "full
   replacement"). When you have at least make + model, call
   lookup_car_key.

   The tool returns EVERY key type we stock for that exact vehicle and
   year. The list is authoritative - it is our actual catalog. Use it
   carefully.

   CRITICAL RULE: When you reply to the lead AFTER a lookup, your reply
   MUST explicitly name the key type(s) from the results. Never reply
   "I don't have that in our catalog" without first saying what we DO
   have for that car/year. The lead cannot help you if they don't know
   what we stock.

   - If the lead asks about a key type that is NOT in the results, do
     NOT just say "we don't have it". Tell them what we DO have for
     their year, by key type and SKU, and suggest checking if maybe they
     meant a different year. Concrete example:
       Lead: "Can you do a smart key for my 2015 Buick Enclave?"
       Tool returns: B111 Transponder Key for 2007-2017.
       GOOD reply: "For a 2015 Enclave we stock a transponder key (B111),
       not a smart/proximity key — those started on the Enclave in 2018.
       If your original was a smart key, can you double-check the year?"
       BAD reply: "I don't have that exact spec in our catalog."
       BAD reply: "Yes we handle smart keys, our tech will quote."
   - If results include a price: quote the RANGE matching their
     situation (spare if they have a working key, replacement otherwise).
     Never quote a single exact number. E.g. "For a spare on your 2015
     Enclave's transponder key, the key itself runs $low-$high;
     programming and labor are extra and the tech will confirm on site."
   - If results have NO price (catalog row empty): tell them what we
     stock by key type and SKU, then say a tech will confirm pricing.
     E.g. "We do stock the transponder key (B111) for your 2015 Enclave -
     let me have a tech check current pricing and call you back with a
     firm quote."
   - If no catalog match at all: "Let me have a tech look at the exact
     spec and call you back with a quote."
4. For lock changes / lockouts / other supported services: get a one-line
   description of what they need.
5. ALWAYS collect: phone number AND best time to call. Don't skip these.
6. Once you have service + phone + best_time_to_call, call forward_lead with
   ALL the fields you know (city, vehicle, summary, price range if quoted).
   Then send ONE confirmation reply: "Got it -- a technician will call you
   at <time>. Anything else for now?"

Rules:
- NEVER quote single exact prices for car keys -- always a range.
- NEVER promise an arrival time / appointment slot yourself.
- NEVER ask for the lead's payment info.
- If the lead seems angry / urgent / locked out RIGHT NOW: still collect phone
  and call forward_lead immediately with summary='URGENT lockout' so the tech
  knows.
"""

_anthropic_client: Anthropic | None = None
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


async def _execute_tool(tool_name: str, tool_input: dict, psid: str) -> str:
    """Run a tool call and return a short text result for Claude."""
    if tool_name == "lookup_car_key":
        mfg = tool_input.get("manufacturer", "")
        model = tool_input.get("model")
        year = tool_input.get("year")
        try:
            matches = await sheets_client.lookup_car_key(mfg, model, year)
        except Exception as e:
            log.exception("lookup_car_key failed")
            _log_event("tool_call", psid=psid, tool="lookup_car_key",
                       input=tool_input, error=str(e))
            return f"Lookup failed: {e}. Tell the lead you'll have a tech check pricing."
        _log_event("tool_call", psid=psid, tool="lookup_car_key",
                   input=tool_input,
                   n_matches=len(matches),
                   matches_summary=[
                       f"{m.get('manufacturer')} {m.get('model')} {m.get('year_raw')} "
                       f"[{m.get('key_type')}] {m.get('key_name')}"
                       for m in matches[:5]
                   ])
        if not matches:
            return (
                f"No catalog match for '{mfg} {model} {year or ''}'. "
                "Tell the lead: 'I don't have that exact model in front of me - "
                "let me have a tech check pricing and call you back.' Then collect "
                "phone + best time and forward the lead."
            )
        out = []
        for m in matches[:5]:
            line = f"- {m.get('manufacturer')} {m.get('model')} {m.get('year_raw') or ''}".strip()
            if m.get("key_type"):
                line += f" [{m['key_type']}]"
            if m.get("key_name"):
                line += f" ({m['key_name']})"
            replace_pr = sheets_client.price_range(m.get("price_usd"))
            spare_pr = sheets_client.price_range(m.get("price_spare_usd"))
            price_parts = []
            if replace_pr:
                price_parts.append("replacement (no original) $%d-$%d" % replace_pr)
            if spare_pr:
                price_parts.append("spare (has original) $%d-$%d" % spare_pr)
            if price_parts:
                line += ": " + " | ".join(price_parts) + " (programming/labor extra)"
            else:
                line += ": price not set in catalog (have tech follow up)"
            out.append(line)
        year_part = f" {year}" if year else ""
        out.append(
            f"^^ This is EVERY key type we stock for {mfg} {model}{year_part}, "
            "from our actual catalog. The list is authoritative. If the lead "
            "asks about a key type not listed above, do NOT generically agree "
            "we have it - tell them what we DO have for their year, and "
            "suggest double-checking the year if they mention a different "
            "type. Always name the key type (transponder / smart / flip) so "
            "the lead can confirm. Quote prices as a range, matching whether "
            "they still have a working key (spare) or lost the only one "
            "(replacement). Never quote a single exact number."
        )
        return "\n".join(out)

    if tool_name == "forward_lead":
        ok = await lead_forwarder.forward_lead(
            service=tool_input["service"],
            phone=tool_input["phone"],
            best_time_to_call=tool_input["best_time_to_call"],
            summary=tool_input["summary"],
            city=tool_input.get("city"),
            vehicle=tool_input.get("vehicle"),
            psid=psid,
            price_range_quoted=(
                (tool_input["price_range_low"], tool_input["price_range_high"])
                if tool_input.get("price_range_low") is not None
                and tool_input.get("price_range_high") is not None
                else None
            ),
        )
        if ok:
            await telegram_helpers.notify_admin(
                "[Lead forwarded] " + tool_input["service"]
                + " | " + tool_input["phone"]
                + " | call at: " + tool_input["best_time_to_call"]
            )
            _log_event("lead_forwarded", psid=psid, service=tool_input["service"],
                       phone=tool_input["phone"])
            return ("Lead forwarded successfully. Now send the lead a short "
                    "confirmation message and reset the conversation.")
        return "Forwarding failed - apologize and ask the lead to try again."

    return f"Unknown tool: {tool_name}"


async def generate_reply(psid: str, lead_message: str):
    """Run the lead-qualifier loop. Returns (reply_text, n_tool_calls)."""
    conversation_state.add_user(psid, lead_message)

    n_tool_calls = 0
    final_text = None
    for _ in range(6):  # safety bound on tool-use rounds
        history = conversation_state.get_history(psid)
        try:
            response = _anthropic().messages.create(
                model=config.ANTHROPIC_MODEL,
                max_tokens=600,
                system=SYSTEM_PROMPT,
                tools=TOOLS,
                messages=history,
            )
        except Exception as e:
            log.exception("Claude call failed")
            return (config.MESSENGER_FALLBACK_TEXT, n_tool_calls)

        conversation_state.add_assistant(psid, response.content)

        tool_uses = [b for b in response.content if getattr(b, "type", "") == "tool_use"]
        text_blocks = [b for b in response.content if getattr(b, "type", "") == "text"]

        if tool_uses:
            for tu in tool_uses:
                result = await _execute_tool(tu.name, tu.input, psid)
                conversation_state.add_tool_result(psid, tu.id, result)
                n_tool_calls += 1
            # Loop again to get Claude's final text after tool results
            continue

        final_text = "".join(b.text for b in text_blocks).strip()
        break

    if not final_text:
        final_text = config.MESSENGER_FALLBACK_TEXT
    return (final_text, n_tool_calls)


async def send_messenger_reply(psid: str, text: str) -> int:
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
    return r.status_code


async def handle_messaging_event(event: dict[str, Any]) -> None:
    sender = event.get("sender", {}).get("id")
    message = event.get("message") or {}
    if message.get("is_echo"):
        return
    if not sender:
        return
    text = (message.get("text") or "").strip()
    if not text:
        _log_event("incoming_non_text", sender=sender)
        return

    log.info("Lead %s: %s", sender, text[:120])
    _log_event("incoming", sender=sender, text=text)
    await telegram_helpers.notify_admin(f"[MSG IN] {sender}:\n{text[:600]}")

    t0 = time.time()
    try:
        reply, n_tools = await generate_reply(sender, text)
    except Exception:
        log.exception("generate_reply crashed")
        reply, n_tools = config.MESSENGER_FALLBACK_TEXT, 0
    duration_ms = int((time.time() - t0) * 1000)

    status = await send_messenger_reply(sender, reply)
    _log_event("reply", sender=sender, tool_calls=n_tools,
               duration_ms=duration_ms, reply=reply, send_status=status)
    await telegram_helpers.notify_admin(
        f"[MSG OUT] {n_tools} tool calls | {duration_ms}ms\n{reply[:600]}"
    )


def build_app() -> FastAPI:
    app = FastAPI(title="ip-leads-bot lead-qualifier")

    @app.get("/")
    async def healthcheck():
        return {
            "service": "ip-leads-bot",
            "version": "lead-qualifier-v1",
            "messenger_enabled": config.messenger_enabled(),
            "events_in_buffer": len(_event_log),
            "open_conversations": conversation_state.n_conversations(),
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
        _log_event("webhook_verify_failed")
        raise HTTPException(status_code=403, detail="Verification failed")

    @app.post("/webhook")
    async def receive(request: Request):
        body = await request.body()
        signature = request.headers.get("x-hub-signature-256", "")
        if not _verify_signature(config.MESSENGER_APP_SECRET, body, signature):
            log.warning("Rejected webhook with bad signature")
            _log_event("bad_signature")
            raise HTTPException(status_code=403, detail="Bad signature")
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="Bad JSON")

        entry_count = sum(len(e.get("messaging", []) or []) for e in payload.get("entry", []))
        _log_event("webhook_post", entries=len(payload.get("entry", [])), events=entry_count)

        for entry in payload.get("entry", []):
            for ev in entry.get("messaging", []):
                try:
                    await handle_messaging_event(ev)
                except Exception:
                    log.exception("Failed to handle event")
                    _log_event("event_error", sender=ev.get("sender", {}).get("id"))
        return {"status": "ok"}

    return app
