import { z } from 'zod';

// ---------------------------------------------------------------------------
// AiAction — discriminated union (one variant per action type)
// ---------------------------------------------------------------------------

export type AiAction =
  | { type: 'copy_command'; label: string; command: string; expectsOutput: boolean }
  | { type: 'open_url'; label: string; url: string }
  | { type: 'capture_field'; field: string; value: string }
  | { type: 'request_output'; placeholder: string }
  | { type: 'validate'; kind: 'orchestrator_healthz' | 'admin_config'; url: string; token?: string }
  | { type: 'transition'; to: string };

export const AiActionSchema: z.ZodSchema<AiAction> = z.discriminatedUnion('type', [
  z.object({
    type: z.literal('copy_command'),
    label: z.string(),
    command: z.string(),
    expectsOutput: z.boolean(),
  }),
  z.object({
    type: z.literal('open_url'),
    label: z.string(),
    url: z.string(),
  }),
  z.object({
    type: z.literal('capture_field'),
    field: z.string(),
    value: z.string(),
  }),
  z.object({
    type: z.literal('request_output'),
    placeholder: z.string(),
  }),
  z.object({
    type: z.literal('validate'),
    kind: z.enum(['orchestrator_healthz', 'admin_config']),
    url: z.string(),
    token: z.string().optional(),
  }),
  z.object({
    type: z.literal('transition'),
    to: z.string(),
  }),
]);

// ---------------------------------------------------------------------------
// ParseResult
// ---------------------------------------------------------------------------

export interface ParseResult {
  prose: string;
  actions: AiAction[];
  parseError?: string;
}

// ---------------------------------------------------------------------------
// parseActionsFromAssistant
// ---------------------------------------------------------------------------

// Matches the LAST <actions>...</actions> occurrence (s flag for newlines).
// The prefix is greedy so it skips over any earlier <actions> blocks.
const ACTIONS_RE = /^([\s\S]*)<actions>([\s\S]+?)<\/actions>([\s\S]*)$/;
// Strip any remaining <actions>...</actions> remnants from prose.
const ALL_ACTIONS_RE = /<actions>[\s\S]*?<\/actions>/g;

export function parseActionsFromAssistant(text: string): ParseResult {
  const match = ACTIONS_RE.exec(text);

  if (!match) {
    return { prose: text, actions: [] };
  }

  const [, before, raw, after] = match;
  // Remove any earlier <actions> blocks left in `before`, then trim.
  const prose = (before.replace(ALL_ACTIONS_RE, '') + after).trimEnd();

  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return { prose, actions: [], parseError: `JSON parse error: ${msg}` };
  }

  const result = z.array(AiActionSchema).safeParse(parsed);
  if (!result.success) {
    return {
      prose,
      actions: [],
      parseError: `Action schema error: ${result.error.issues.map((i) => i.message).join('; ')}`,
    };
  }

  return { prose, actions: result.data };
}
