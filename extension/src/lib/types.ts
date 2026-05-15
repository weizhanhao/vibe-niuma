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

// Phase F：log 事件 —— 子进程每行进度文字。phase 限于 created/clarifying/locating/coding/building；
// 容错地用 string 而非 union，后端如果增加新 phase 也不破前端 schema。
export interface LogEntry {
  phase: string;
  line: string;
  ts: string;
}

// Phase F：每个 mirror 最多保留这么多条 log；溢出 FIFO 丢老的。
export const MAX_LOGS_PER_MIRROR = 200;

export type SSEEvent =
  | { type: 'status'; data: { state: ChangeRequestState; phase?: string; reason?: string } }
  | { type: 'question'; data: { question_id: string; question: string; options: string[] | null } }
  | { type: 'variants'; data: { question_id: string; variants: HtmlMockup[] } }
  | { type: 'log'; data: LogEntry };

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
  logs: LogEntry[];
}
