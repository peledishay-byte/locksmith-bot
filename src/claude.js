// Wrapper around the Anthropic SDK.
//
// We run a small agentic loop: Claude can call tools (lookup a car key,
// send an image, escalate), get the result back, and decide what to say next.
//
// Returns { actions: [ ... ] } where each action is one of:
//   { kind: 'reply',    text }
//   { kind: 'image',    url, caption? }
//   { kind: 'escalate', reason, summary, customerText? }
//
// The caller (facebook.js) executes these actions in order on the FB side.
import Anthropic from '@anthropic-ai/sdk';
import { SYSTEM_PROMPT, ALL_TOOLS } from './knowledge.js';
import { findKeyForVehicle } from './airtable.js';

const client = new Anthropic({ apiKey: process.env.ANTHROPIC_API_KEY });
const MODEL = process.env.ANTHROPIC_MODEL || 'claude-sonnet-4-5';
const MAX_AGENT_ITERATIONS = 5;

export async function decideResponse(history) {
  // Map our DB roles to Claude roles.
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

    // Push the full assistant turn back into the conversation so Claude's
    // own tool_use blocks are preserved when we append tool_results.
    messages.push({ role: 'assistant', content: res.content });

    const textBlocks = res.content
      .filter((b) => b.type === 'text')
      .map((b) => b.text.trim())
      .filter(Boolean);
    const toolUses = res.content.filter((b) => b.type === 'tool_use');

    // If there are no tool calls, we're done — emit any text Claude produced.
    if (toolUses.length === 0) {
      if (textBlocks.length) actions.push({ kind: 'reply', text: textBlocks.join('\n\n') });
      break;
    }

    // Emit any text the assistant included BEFORE the tool calls (e.g., a
    // reassuring "one sec, checking on that" message). This is what the
    // customer sees before the tool runs.
    if (textBlocks.length) actions.push({ kind: 'reply', text: textBlocks.join('\n\n') });

    // Run each tool call and gather tool_result blocks.
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
    // Loop again — Claude now sees the tool results and can produce its final text.
  }

  // If Claude requested escalation, attach any preceding reply text as the
  // customer-facing follow-up so facebook.js can send it before the handoff.
  if (pendingEscalation) {
    // The last reply (if any) is the customer-facing reassurance.
    const lastReply = [...actions].reverse().find((a) => a.kind === 'reply');
    pendingEscalation.customerText = lastReply ? lastReply.text : null;
    // Remove the reply action — facebook.js will send it as part of the
    // escalation flow so we don't double-send.
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
        return { found: false, matches: [] };
      }
      // Return a compact, model-friendly summary. Cap at 3 to avoid blowing up tokens.
      const trimmed = matches.slice(0, 3).map((r) => ({
        key_type: r.keyType,
        key_name: r.keyName,
        key_image_url: r.keyImageUrl,
        customer_price_usd: r.customerPrice,
      }));
      return { found: true, count: matches.length, matches: trimmed };
    }

    if (name === 'show_key_image') {
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
