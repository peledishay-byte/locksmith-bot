// Wrapper around the Anthropic SDK.
//
// Agentic loop: Claude calls tools (lookup car key, send image, escalate),
// gets results back, and decides what to say next.
//
// Returns { actions: [ ... ] } where each action is one of:
//   { kind: 'reply',    text }
//   { kind: 'image',    url, caption? }
//   { kind: 'escalate', reason, summary, customerText? }
//
// The caller (facebook.js) executes these in order.
import Anthropic from '@anthropic-ai/sdk';
import { SYSTEM_PROMPT, ALL_TOOLS } from './knowledge.js';
import { findKeyForVehicle } from './airtable.js';

const client = new Anthropic({ apiKey: process.env.ANTHROPIC_API_KEY });
const MODEL = process.env.ANTHROPIC_MODEL || 'claude-sonnet-4-5';
const MAX_AGENT_ITERATIONS = 5;

export async function decideResponse(history) {
  const messages = history.map((m) => ({
    role: m.role === 'user' ? 'user' : 'assistant',
    content: m.content,
  }));

  const actions = [];
  let pendingEscalation = null;

  for (let i = 0; i < MAX_AGENT_ITERATIONS; i++) {
    const res = await client.messages.create({
      model: MODEL,
      max_tokens: 800,
      system: SYSTEM_PROMPT,
      tools: ALL_TOOLS,
      messages,
    });

    messages.push({ role: 'assistant', content: res.content });

    const textBlocks = res.content
      .filter((b) => b.type === 'text')
      .map((b) => b.text.trim())
      .filter(Boolean);
    const toolUses = res.content.filter((b) => b.type === 'tool_use');

    if (toolUses.length === 0) {
      if (textBlocks.length) actions.push({ kind: 'reply', text: textBlocks.join('\n\n') });
      break;
    }

    if (textBlocks.length) actions.push({ kind: 'reply', text: textBlocks.join('\n\n') });

    const toolResults = [];
    for (const tu of toolUses) {
      const result = await runTool(tu, { pendingEscalationRef: (e) => (pendingEscalation = e), actions });
      toolResults.push({
        type: 'tool_result',
        tool_use_id: tu.id,
        content: typeof result === 'string' ? result : JSON.stringify(result),
      });
    }
    messages.push({ role: 'user', content: toolResults });
  }

  if (pendingEscalation) {
    const lastReply = [...actions].reverse().find((a) => a.kind === 'reply');
    pendingEscalation.customerText = lastReply ? lastReply.text : null;
    if (lastReply) {
      const idx = actions.lastIndexOf(lastReply);
      if (idx >= 0) actions.splice(idx, 1);
    }
    actions.push(pendingEscalation);
  }

  if (actions.length === 0) {
    actions.push({ kind: 'reply', text: "Sorry, could you repeat that?" });
  }
  return { actions };
}

async function runTool(toolUse, ctx) {
  const { name, input } = toolUse;
  try {
    if (name === 'lookup_key_for_vehicle') {
      const matches = await findKeyForVehicle({
        make: input.make,
        model: input.model,
        year: input.year,
      });

      if (matches.length === 0) {
        // Auto-escalate to Telegram immediately — don't wait for Claude to decide.
        console.log(`[claude] NOT_IN_CATALOG: ${input.year} ${input.make} ${input.model}`);
        ctx.pendingEscalationRef({
          kind: 'escalate',
          reason: 'NOT_IN_CATALOG',
          summary: `Customer asked about a key for ${input.year} ${input.make} ${input.model}. Not found in catalog.`,
        });
        return { found: false, matches: [], auto_escalated: true };
      }

      // Include has_image flag so Claude knows whether to call show_key_image.
      const trimmed = matches.slice(0, 3).map((r) => ({
        key_type: r.keyType,
        key_name: r.keyName,
        key_image_url: r.keyImageUrl || '',
        has_image: Boolean(r.keyImageUrl),
        customer_price_usd: r.customerPrice,
      }));
      return { found: true, count: matches.length, matches: trimmed };
    }

    if (name === 'show_key_image') {
      // Guard: never send empty URL to Facebook.
      if (!input.key_image_url || !input.key_image_url.startsWith('http')) {
        console.warn('[claude] show_key_image called with empty URL — skipping');
        return { ok: false, reason: 'empty_url' };
      }
      ctx.actions.push({
        kind: 'image',
        url: input.key_image_url,
        caption: input.caption || null,
      });
      return { ok: true };
    }

    if (name === 'escalate_to_team') {
      ctx.pendingEscalationRef({
        kind: 'escalate',
        reason: input.reason || 'UNCERTAIN',
        summary: input.summary_for_team || '',
      });
      return { ok: true, message: 'Escalation recorded; the team will be paged.' };
    }

    return { error: `Unknown tool: ${name}` };
  } catch (err) {
    console.error(`[claude] tool ${name} failed`, err);
    return { error: String(err.message || err) };
  }
}
