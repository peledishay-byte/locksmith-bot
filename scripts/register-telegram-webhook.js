// One-shot script to register the Telegram webhook URL with Telegram.
// Run AFTER you've deployed and have a public URL:
//   PUBLIC_BASE_URL=https://your-app.up.railway.app \
//   TELEGRAM_BOT_TOKEN=... \
//   TELEGRAM_WEBHOOK_SECRET=... \
//   npm run register-telegram-webhook
import 'dotenv/config';

const token = process.env.TELEGRAM_BOT_TOKEN;
const baseUrl = process.env.PUBLIC_BASE_URL;
const secret = process.env.TELEGRAM_WEBHOOK_SECRET;

if (!token || !baseUrl || !secret) {
  console.error('Missing TELEGRAM_BOT_TOKEN, PUBLIC_BASE_URL, or TELEGRAM_WEBHOOK_SECRET in .env');
  process.exit(1);
}

const url = `${baseUrl.replace(/\/$/, '')}/webhook/telegram`;

const res = await fetch(`https://api.telegram.org/bot${token}/setWebhook`, {
  method: 'POST',
  headers: { 'content-type': 'application/json' },
  body: JSON.stringify({
    url,
    secret_token: secret,
    allowed_updates: ['message', 'edited_message'],
  }),
});
const data = await res.json();
console.log(JSON.stringify(data, null, 2));
