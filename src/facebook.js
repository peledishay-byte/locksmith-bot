// Facebook Messenger integration:
//   - GET  /webhook/facebook   verification handshake (Meta calls this once)
//   - POST /webhook/facebook   incoming messages from customers
//   - sendMessage(psid, text)  outgoing text to customers
//   - sendImage(psid, url)     outgoing image to customers
import crypto from 'node:crypto';
import {
  getOrCreateConversation,
  appendMessage,
  getRecentMessages,
  setConversationMode,
  recordEscalation,
} from './db.js';
import { decideResponse } from './claude.js';
import { sendToCompanyGroup } from './telegram.js';

const GRAPH_API = 'https://graph.facebook.com/v21.0';

// --- Webhook verification (GET) ---------------------------------------------
export function verifyWebhook(req, res) {
  const mode = req.query['hub.mode'];
  const token = req.query['hub.verify_token'];
  const challenge = req.query['hub.challenge'];
  if (mode === 'subscribe' && token === process.env.FB_VERIFY_TOKEN) {
    return res.status(200).send(challenge);
  }
  return res.sendStatus(403);
}

// --- HMAC signature verification (POST) -------------------------------------
function isValidFbSignature(req) {
  const secret = process.env.FB_APP_SECRET;
  if (!secret) return true; // dev-only escape hatch; set FB_APP_SECRET in production
  const header = req.get('x-hub-signature-256');
  if (!header || !header.startsWith('sha256=')) return false;
  const expected = crypto
    .createHmac('sha256', secret)
    .update(req.rawBody || '')
    .digest('hex');
  const provided = header.slice('sha256='.length);
  try {
    return crypto.timingSafeEqual(Buffer.from(expected, 'hex'), Buffer.from(provided, 'hex'));
  } catch {
    return false;
  }
}

// --- Webhook receiver (POST) ------------------------------------------------
export async function handleWebhook(req, res) {
  if (!isValidFbSignature(req)) {
    console.warn('[fb] invalid signature');
    return res.sendStatus(401);
  }
  res.sendStatus(200); // ack immediately

  const body = req.body;
  if (body.object !== 'page') return;

  for (const entry of body.entry || []) {
    for (const event of entry.messaging || []) {
      try {
        await handleMessagingEvent(event);
      } catch (err) {
        console.error('[fb] error handling event', err);
      }
    }
  }
}

async function handleMessagingEvent(event) {
  if (!event.message || event.message.is_echo) return;
  const text = event.message.text;
  if (!text) return;

  const senderPsid = event.sender.id;
  const conversation = getOrCreateConversation(senderPsid);
  appendMessage(conversation.id, 'user', text);

  if (conversation.mode === 'human') {
    console.log(`[fb] convo #${conversation.id} is in human mode; bot staying quiet`);
    return;
  }

  await sendSenderAction(senderPsid, 'typing_on').catch(() => {});

  const history = getRecentMessages(conversation.id, 20);
  const { actions } = await decideResponse(history);

  for (const action of actions) {
    if (action.kind === 'reply') {
      appendMessage(conversation.id, 'assistant', action.text);
      await sendMessage(senderPsid, action.text);
    } else if (action.kind === 'image') {
      appendMessage(conversation.id, 'assistant', `[image: ${action.url}]`);
      if (action.caption) {
        await sendMessage(senderPsid, action.caption);
      }
      await sendImage(senderPsid, action.url);
    } else if (action.kind === 'escalate') {
      await handleEscalation(conversation, action, history);
      // After escalation everything else in `actions` is meaningless because
      // the convo is now in human mode.
      break;
    }
  }
}

async function handleEscalation(conversation, action, history) {
  setConversationMode(conversation.id, 'human');

  const reassurance =
    action.customerText ||
    "One moment — I'm getting a team member to help you with that. They'll reply here shortly.";
  appendMessage(conversation.id, 'assistant', reassurance);
  await sendMessage(conversation.fb_psid, reassurance);

  const transcript = history
    .slice(-6)
    .map((m) => `${m.role === 'user' ? 'Customer' : 'Bot'}: ${m.content}`)
    .join('\n');
  const telegramText =
    `🔔 New escalation (#${conversation.id})\n` +
    `Reason: ${action.reason}\n` +
    `Summary: ${action.summary || '(no summary)'}\n\n` +
    `Recent messages:\n${transcript}\n\n` +
    `↩️ Reply to THIS message in Telegram and your text will be sent to the customer.`;

  const tgMessageId = await sendToCompanyGroup(telegramText);
  if (tgMessageId) {
    recordEscalation(conversation.id, tgMessageId, action.reason);
  } else {
    console.error('[fb] failed to post escalation to Telegram');
  }
}

// --- Outbound API calls -----------------------------------------------------
export async function sendMessage(psid, text) {
  const token = process.env.FB_PAGE_ACCESS_TOKEN;
  const url = `${GRAPH_API}/me/messages?access_token=${encodeURIComponent(token)}`;
  const payload = {
    recipient: { id: psid },
    messaging_type: 'RESPONSE',
    message: { text },
  };
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    console.error('[fb] sendMessage failed', res.status, await res.text());
  }
}

export async function sendImage(psid, imageUrl) {
  const token = process.env.FB_PAGE_ACCESS_TOKEN;
  const url = `${GRAPH_API}/me/messages?access_token=${encodeURIComponent(token)}`;
  const payload = {
    recipient: { id: psid },
    messaging_type: 'RESPONSE',
    message: {
      attachment: {
        type: 'image',
        payload: { url: imageUrl, is_reusable: true },
      },
    },
  };
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    console.error('[fb] sendImage failed', res.status, await res.text());
  }
}

async function sendSenderAction(psid, action) {
  const token = process.env.FB_PAGE_ACCESS_TOKEN;
  const url = `${GRAPH_API}/me/messages?access_token=${encodeURIComponent(token)}`;
  await fetch(url, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ recipient: { id: psid }, sender_action: action }),
  });
}
