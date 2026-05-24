// Tiny SQLite layer. We persist:
//   conversations  - one row per FB customer (PSID)
//   messages       - rolling chat history for each conversation (used as context for Claude)
//   escalations    - links a Telegram message in the company group <-> a FB conversation
//                    so when someone REPLIES to that Telegram message, we know which
//                    customer to forward the human answer to.
import Database from 'better-sqlite3';
import { mkdirSync } from 'node:fs';
import { dirname } from 'node:path';

const DB_PATH = process.env.DB_PATH || './data/bot.db';
mkdirSync(dirname(DB_PATH), { recursive: true });

export const db = new Database(DB_PATH);
db.pragma('journal_mode = WAL');

db.exec(`
CREATE TABLE IF NOT EXISTS conversations (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  fb_psid         TEXT UNIQUE NOT NULL,
  -- 'bot' = bot is responding, 'human' = waiting for / handed off to humans
  mode            TEXT NOT NULL DEFAULT 'bot',
  language        TEXT,
  created_at      INTEGER NOT NULL DEFAULT (strftime('%s','now')),
  last_message_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
);

CREATE TABLE IF NOT EXISTS messages (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  conversation_id INTEGER NOT NULL,
  -- 'user' = customer on FB, 'assistant' = bot, 'human' = answer from team via Telegram
  role            TEXT NOT NULL,
  content         TEXT NOT NULL,
  created_at      INTEGER NOT NULL DEFAULT (strftime('%s','now')),
  FOREIGN KEY (conversation_id) REFERENCES conversations(id)
);

CREATE TABLE IF NOT EXISTS escalations (
  id                   INTEGER PRIMARY KEY AUTOINCREMENT,
  conversation_id      INTEGER NOT NULL,
  telegram_message_id  INTEGER NOT NULL,
  reason               TEXT,
  status               TEXT NOT NULL DEFAULT 'open', -- 'open' | 'answered'
  created_at           INTEGER NOT NULL DEFAULT (strftime('%s','now')),
  FOREIGN KEY (conversation_id) REFERENCES conversations(id)
);

CREATE INDEX IF NOT EXISTS idx_escalations_tg_msg ON escalations(telegram_message_id);
CREATE INDEX IF NOT EXISTS idx_messages_convo ON messages(conversation_id, id);
`);

export function getOrCreateConversation(fbPsid) {
  const existing = db.prepare('SELECT * FROM conversations WHERE fb_psid = ?').get(fbPsid);
  if (existing) return existing;
  const info = db
    .prepare('INSERT INTO conversations (fb_psid) VALUES (?)')
    .run(fbPsid);
  return db.prepare('SELECT * FROM conversations WHERE id = ?').get(info.lastInsertRowid);
}

export function setConversationMode(conversationId, mode) {
  db.prepare('UPDATE conversations SET mode = ? WHERE id = ?').run(mode, conversationId);
}

export function appendMessage(conversationId, role, content) {
  db.prepare(
    'INSERT INTO messages (conversation_id, role, content) VALUES (?, ?, ?)'
  ).run(conversationId, role, content);
  db.prepare('UPDATE conversations SET last_message_at = strftime(\'%s\',\'now\') WHERE id = ?')
    .run(conversationId);
}

// Last N messages for use as Claude conversation context.
export function getRecentMessages(conversationId, limit = 20) {
  return db
    .prepare(
      'SELECT role, content FROM messages WHERE conversation_id = ? ORDER BY id DESC LIMIT ?'
    )
    .all(conversationId, limit)
    .reverse();
}

export function recordEscalation(conversationId, telegramMessageId, reason) {
  db.prepare(
    'INSERT INTO escalations (conversation_id, telegram_message_id, reason) VALUES (?, ?, ?)'
  ).run(conversationId, telegramMessageId, reason || null);
}

export function findEscalationByTelegramMessage(telegramMessageId) {
  return db
    .prepare(
      `SELECT e.*, c.fb_psid AS customer_psid
       FROM escalations e
       JOIN conversations c ON c.id = e.conversation_id
       WHERE e.telegram_message_id = ?`
    )
    .get(telegramMessageId);
}

export function markEscalationAnswered(escalationId) {
  db.prepare("UPDATE escalations SET status = 'answered' WHERE id = ?").run(escalationId);
}
