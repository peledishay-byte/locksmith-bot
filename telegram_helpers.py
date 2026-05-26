"""Shared Telegram + message-to-record helpers."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.custom.message import Message
from telethon.tl.types import Channel, Chat, User

import config

log = logging.getLogger(__name__)


# A reference to the live Telegram client, set by main.py once connected.
# Other modules (e.g. messenger.py) can call notify_admin() to push real-time
# status messages to the user's Saved Messages in Telegram.
_active_client: TelegramClient | None = None


def set_active_client(client: TelegramClient) -> None:
    global _active_client
    _active_client = client


async def notify_admin(text: str) -> None:
    """Send a status line to the user's Saved Messages (their own Telegram chat).
    No-op if the client is not yet connected. Trimmed to 3500 chars."""
    if _active_client is None:
        return
    try:
        await _active_client.send_message("me", text[:3500])
    except Exception:
        log.exception("Failed to send admin notification")


def build_client() -> TelegramClient:
    """Build a TelegramClient from the saved session string."""
    if not config.TELEGRAM_SESSION_STRING:
        raise SystemExit(
            "TELEGRAM_SESSION_STRING is empty. Run auth_step1.py + auth_step2.py first."
        )
    return TelegramClient(
        StringSession(config.TELEGRAM_SESSION_STRING),
        config.TELEGRAM_API_ID,
        config.TELEGRAM_API_HASH,
    )


async def resolve_group(client: TelegramClient, name_or_id: str):
    """Find a dialog whose title matches name_or_id (or whose id matches if numeric)."""
    target = name_or_id.strip()
    # numeric?
    numeric: Optional[int] = None
    try:
        numeric = int(target)
    except ValueError:
        pass

    async for dialog in client.iter_dialogs():
        if numeric is not None and dialog.id == numeric:
            return dialog.entity
        if dialog.name and dialog.name.strip().lower() == target.lower():
            return dialog.entity
    raise SystemExit(f"Could not find a group named '{name_or_id}'. "
                     f"Make sure the account is a member of it.")


def _sender_name(sender) -> str:
    if sender is None:
        return "Unknown"
    if isinstance(sender, User):
        parts = [sender.first_name or "", sender.last_name or ""]
        name = " ".join(p for p in parts if p).strip()
        if not name:
            name = sender.username or f"user_{sender.id}"
        return name
    if isinstance(sender, (Channel, Chat)):
        return sender.title or f"chat_{sender.id}"
    return str(getattr(sender, "id", "unknown"))


async def message_to_record(message: Message, chat_name: str) -> dict | None:
    """Convert a Telethon Message into a Pinecone record. Returns None for empty messages."""
    text = (message.message or "").strip()
    if not text:
        # skip pure-media messages with no caption (for now)
        return None

    sender = None
    try:
        sender = await message.get_sender()
    except Exception:
        pass

    sender_id = getattr(sender, "id", None) or getattr(message, "sender_id", None) or 0
    sender_name = _sender_name(sender)

    chat_id = getattr(message, "chat_id", 0) or 0

    dt: datetime = message.date or datetime.now(timezone.utc)
    # ensure aware UTC
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    reply_to_id = getattr(message.reply_to, "reply_to_msg_id", None) if message.reply_to else None

    return {
        "id": f"{chat_id}_{message.id}",
        "text": text,
        "chat_id": str(chat_id),
        "chat_name": chat_name,
        "message_id": message.id,
        "sender_id": str(sender_id),
        "sender_name": sender_name,
        "timestamp": int(dt.timestamp()),
        "date_iso": dt.isoformat(),
        "reply_to": reply_to_id or 0,
    }
