// 与 Orchestrator REST/SSE 对齐的 TS 类型。改这里也意味着改契约。

export type ChangeRequestState =
  | 'created'
  | 'clarifying'
  | 'located'
  | 'coding'
  | 'building'
  | 'preview-ready'
  | 'merged'
  | 'failed'
  | 'expired'
  | 'discarded';

export const TERMINAL_STATES: ChangeRequestState[] = ['merged', 'failed', 'expired', 'discarded'];

export interface ChangeRequestOut {
  id: string;
  state: ChangeRequestState;
  url: string;
  request_text: string;
  branch: string | null;
  preview_url: string | null;
  fail_phase: string | null;
  fail_reason: string | null;
  retry_of: string | null;
}

export interface BoxCoords {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface Viewport {
  width: number;
  height: number;
}

export interface RawRequestPayload {
  url: string;
  screenshot_b64: string;
  box_coords: BoxCoords;
  viewport: Viewport;
  request_text: string;
}

export interface HtmlMockup {
  id: string;
  title: string;
  html: string;
}

export type SSEEvent =
  | { type: 'status'; data: { state: ChangeRequestState; phase?: string; reason?: string } }
  | { type: 'question'; data: { question_id: string; question: string; options: string[] | null } }
  | { type: 'variants'; data: { question_id: string; variants: HtmlMockup[] } };

export interface RequestStateMirror {
  id: string;
  state: ChangeRequestState;
  url: string;
  branch: string | null;
  previewUrl: string | null;
  failPhase: string | null;
  failReason: string | null;
  pendingQuestion: { questionId: string; question: string; options: string[] | null } | null;
  pendingVariants: { questionId: string; variants: HtmlMockup[] } | null;
}
