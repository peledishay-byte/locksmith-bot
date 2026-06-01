"""Shared configuration. Reads from environment variables (or .env if present)."""
import os
from dotenv import load_dotenv

load_dotenv()

# Telegram
TELEGRAM_API_ID = int(os.environ.get("TELEGRAM_API_ID", "0"))
TELEGRAM_API_HASH = os.environ.get("TELEGRAM_API_HASH", "")
TELEGRAM_PHONE = os.environ.get("TELEGRAM_PHONE", "")
TELEGRAM_SESSION_STRING = os.environ.get("TELEGRAM_SESSION_STRING", "")
TELEGRAM_GROUP_NAME = os.environ.get("TELEGRAM_GROUP_NAME", "IP LEADS")

# Pinecone
PINECONE_API_KEY = os.environ.get("PINECONE_API_KEY", "")
PINECONE_INDEX_NAME = os.environ.get("PINECONE_INDEX_NAME", "ip-leads")
PINECONE_NAMESPACE = os.environ.get("PINECONE_NAMESPACE", "telegram")

# Anthropic
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")

# Messenger / Meta
MESSENGER_PAGE_ACCESS_TOKEN = os.environ.get("MESSENGER_PAGE_ACCESS_TOKEN", "")
MESSENGER_APP_SECRET = os.environ.get("MESSENGER_APP_SECRET", "")
MESSENGER_VERIFY_TOKEN = os.environ.get("MESSENGER_VERIFY_TOKEN", "")
MESSENGER_FALLBACK_TEXT = os.environ.get(
    "MESSENGER_FALLBACK_TEXT",
    "Thanks for reaching out! I'll check with the team and get back to you within an hour.",
)

# Airtable (legacy - left in place for fallback, no longer the primary source)
AIRTABLE_TOKEN = os.environ.get("AIRTABLE_TOKEN", "")
AIRTABLE_BASE_ID = os.environ.get("AIRTABLE_BASE_ID", "appaX6NcK4uZjrcDY")
AIRTABLE_KEYS_TABLE = os.environ.get("AIRTABLE_KEYS_TABLE", "tblfSNUeAEHtuNCD0")
AIRTABLE_PRICE_RANGE_PCT = int(os.environ.get("AIRTABLE_PRICE_RANGE_PCT", "15"))

# Google Sheets (Lead Qualifier - car key catalog, primary source)
# The sheet must be shared "Anyone with the link - Viewer" so the gviz
# CSV export endpoint can read it without credentials.
SHEETS_SPREADSHEET_ID = os.environ.get(
    "SHEETS_SPREADSHEET_ID", "1tYbL7XRuGuHhgyXiEd2JWp8Q5sSpIAOa6EEA3cgPClo"
)
SHEETS_TAB_NAME = os.environ.get("SHEETS_TAB_NAME", "Locksmith_Keys_Catalog")
SHEETS_PRICE_RANGE_PCT = int(os.environ.get("SHEETS_PRICE_RANGE_PCT", "15"))

# Web server
PORT = int(os.environ.get("PORT", "8000"))


def require(name, value):
    if not value:
        raise SystemExit(f"Missing required env var: {name}")


def require_telegram_creds():
    require("TELEGRAM_API_ID", TELEGRAM_API_ID)
    require("TELEGRAM_API_HASH", TELEGRAM_API_HASH)
    require("TELEGRAM_PHONE", TELEGRAM_PHONE)


def require_pinecone():
    require("PINECONE_API_KEY", PINECONE_API_KEY)


def messenger_enabled():
    return bool(
        MESSENGER_PAGE_ACCESS_TOKEN
        and MESSENGER_APP_SECRET
        and MESSENGER_VERIFY_TOKEN
        and ANTHROPIC_API_KEY
    )
