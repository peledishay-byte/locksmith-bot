// Telegram integration:
//   - sendToCompanyGroup(text)            -- post a new escalation message
//   - handleWebhook(req, res)             -- receive replies from team members
//   - The contract with team members: REPLY to the bot's escalation message
//     (using Telegram's built-in reply feature). Whatever you type goes to the
//     customer on Facebook. Don't include internal info — the customer will see it.
import {
  findEscalationByTelegramMessage,
  appendMessage,
  setConversationMode,
  markEscalationAnswered,
} from './db.js';
import { sendMessage as sendFbMessage } from './facebook.js';

const TG_API = (token) => `https://api.telegram.org/bot${token}`;

export async function sendToCompanyGroup(text) {
  const token = process.env.TELEGRAM_BOT_TOKEN;
  const chatId = process.env.TELEGRAM_GROUP_CHAT_ID;
  const res = await fetch(`${TG_API(token)}/sendMessage`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ chat_id: chatId, text }),
  });
  const data = await res.json();
  if (!data.ok) {
    console.error('[tg] sendMessage failed', data);
    return null;
  }
  return data.result.message_id;
}

// Telegram webhook receiver. Expects Telegram to be configured with a
// secret_token whose value matches process.env.TELEGRAM_WEBHOOK_SECRET.
export async function handleWebhook(req, res) {
  const secretHeader = req.get('x-telegram-bot-api-secret-token');
  if (secretHeader !== process.env.TELEGRAM_WEBHOOK_SECRET) {
    return res.sendStatus(401);
  }
  res.sendStatus(200); // ack immediately

  const update = req.body;
  const message = update.message || update.edited_message;
  if (!message) return;

  // Only act on messages that REPLY to one of our escalation messages.
  const repliedTo = message.reply_to_message;
  if (!repliedTo) return;

  const escalation = findEscalationByTelegramMessage(repliedTo.message_id);
  if (!escalation) {
    console.log('[tg] reply did not match any escalation; ignoring');
    return;
  }

  const text = message.text || message.caption;
  if (!text) return;

  // Forward the team's text to the customer on Facebook.
  try {
    await sendFbMessage(escalation.customer_psid, text);
  } catch (err) {
    console.error('[tg] failed to forward to customer', err);
    return;
  }

  appendMessage(escalation.conversation_id, 'human', text);
  markEscalationAnswered(escalation.id);

  // Hand control back to the bot for any further customer messages. (Tweak
  // this if you'd rather the conversation stay in human mode permanently.)
  setConversationMode(escalation.conversation_id, 'bot');

  console.log(
    `[tg] forwarded team reply to FB customer (convo #${escalation.conversation_id})`
  );
}
