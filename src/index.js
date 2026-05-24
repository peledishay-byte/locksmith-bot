import 'dotenv/config';
import express from 'express';
import { verifyWebhook, handleWebhook as fbWebhook } from './facebook.js';
import { handleWebhook as tgWebhook } from './telegram.js';

const app = express();

// Capture the raw body for FB so we can verify its HMAC signature.
app.use(
  '/webhook/facebook',
  express.json({
    verify: (req, _res, buf) => {
      req.rawBody = buf.toString('utf8');
    },
  })
);
app.use('/webhook/telegram', express.json());
app.use(express.json());

app.get('/', (_req, res) => res.json({ ok: true, name: 'locksmith-bot' }));

app.get('/webhook/facebook', verifyWebhook);
app.post('/webhook/facebook', fbWebhook);
app.post('/webhook/telegram', tgWebhook);

const port = Number(process.env.PORT || 3000);
app.listen(port, () => console.log(`[server] listening on :${port}`));
