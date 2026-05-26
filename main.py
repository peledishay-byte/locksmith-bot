"""Long-running service.

Runs three things concurrently in one asyncio loop:
  1. Telegram listener - captures every new IP LEADS message into Pinecone.
  2. One-time history scan on first boot (if Pinecone namespace is empty).
  3. FastAPI webhook server for Facebook Messenger (always on, even if
     Messenger isn't configured yet - it just returns a healthcheck and
     refuses /webhook hits until env vars are set).
"""
import asyncio
import logging

import uvicorn
from telethon import events

import config
import messenger
import pinecone_store
from telegram_helpers import build_client, resolve_group, message_to_record

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger(__name__)


async def scan_history_if_empty(client, group, chat_name):
    try:
        stats = pinecone_store.stats()
        ns_stats = (stats.get("namespaces") or {}).get(config.PINECONE_NAMESPACE) or {}
        vector_count = ns_stats.get("vector_count", 0)
    except Exception:
        log.exception("Could not read Pinecone stats; skipping history scan to be safe.")
        return

    if vector_count > 0:
        log.info("Skipping history scan - Pinecone already has %d records in namespace %r.",
                 vector_count, config.PINECONE_NAMESPACE)
        return

    log.info("Pinecone namespace %r is empty - running one-time history scan...",
             config.PINECONE_NAMESPACE)
    batch = []
    total = 0
    skipped = 0
    async for message in client.iter_messages(group, limit=None, reverse=True):
        record = await message_to_record(message, chat_name=chat_name)
        if record is None:
            skipped += 1
            continue
        batch.append(record)
        if len(batch) >= 90:
            pinecone_store.upsert_messages(batch)
            total += len(batch)
            batch.clear()
    if batch:
        pinecone_store.upsert_messages(batch)
        total += len(batch)
    log.info("History scan complete. Stored %d messages (skipped %d empty/media-only).",
             total, skipped)


async def run_telegram_listener():
    config.require_telegram_creds()
    config.require_pinecone()
    pinecone_store.ensure_index()

    client = build_client()
    await client.start()
    me = await client.get_me()
    log.info("Connected as %s (id=%s)", me.first_name, me.id)

    # Expose the client to other modules for real-time admin notifications
    # (e.g. messenger.py pings the user's Saved Messages on each event).
    import telegram_helpers as _th
    _th.set_active_client(client)
    try:
        await _th.notify_admin(f"[bot] Online as {me.first_name} (id={me.id})")
    except Exception:
        pass

    group = await resolve_group(client, config.TELEGRAM_GROUP_NAME)
    chat_name = getattr(group, "title", None) or config.TELEGRAM_GROUP_NAME
    log.info("Listening to group: %s (id=%s)", chat_name, group.id)

    await scan_history_if_empty(client, group, chat_name)

    @client.on(events.NewMessage(chats=group))
    async def on_new_message(event):
        try:
            record = await message_to_record(event.message, chat_name=chat_name)
            if record is None:
                return
            pinecone_store.upsert_messages([record])
            preview = record["text"][:80].replace("\n", " ")
            log.info("[telegram] %s: %s", record["sender_name"], preview)
        except Exception:
            log.exception("Failed to process new message")

    @client.on(events.MessageEdited(chats=group))
    async def on_message_edited(event):
        try:
            record = await message_to_record(event.message, chat_name=chat_name)
            if record is None:
                return
            pinecone_store.upsert_messages([record])
            log.info("[telegram] Updated (edit): %s", record["id"])
        except Exception:
            log.exception("Failed to process edit")

    log.info("Telegram listener is live. Waiting for messages...")
    await client.run_until_disconnected()


async def run_webhook_server():
    app = messenger.build_app()
    server_config = uvicorn.Config(
        app,
        host="0.0.0.0",
        port=config.PORT,
        log_level="info",
        loop="asyncio",
    )
    server = uvicorn.Server(server_config)
    log.info("HTTP webhook server starting on port %d (messenger_enabled=%s).",
             config.PORT, config.messenger_enabled())
    await server.serve()


async def main():
    await asyncio.gather(
        run_telegram_listener(),
        run_webhook_server(),
    )


if __name__ == "__main__":
    asyncio.run(main())
