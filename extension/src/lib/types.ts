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

// Phase E：侧边栏对话列表最多保留这么多条；溢出按 LRU 丢「终态」最老的。
// 非终态受保护，不会被驱逐（除非全是非终态——MVP 不会发生）。
export const MAX_MIRRORS = 50;

export type SSEEvent =
  | { type: 'status'; data: { state: ChangeRequestState; phase?: string; reason?: string; preview_url?: string; branch?: string } }
  | { type: 'question'; data: { question_id: string; question: string; options: string[] | null } }
  | { type: 'variants'; data: { question_id: string; variants: HtmlMockup[] } }
  | { type: 'log'; data: LogEntry }
  // Phase C：业务员回答后后端广播此事件，订阅者据此清掉同 question_id 的 pendingQuestion/Variants。
  // 重连历史回放时也会出现，所以兜底「已答问题闪现」问题。
  | { type: 'question-resolved'; data: { question_id: string } };

export interface RequestStateMirror {
  id: string;
  state: ChangeRequestState;
  url: string;
  // Phase E：列表里要给业务员看的需求摘要文本。在 initialState 时从 cr.request_text 灌进来。
  requestText: string;
  branch: string | null;
  previewUrl: string | null;
  failPhase: string | null;
  failReason: string | null;
  pendingQuestion: { questionId: string; question: string; options: string[] | null } | null;
  pendingVariants: { questionId: string; variants: HtmlMockup[] } | null;
  logs: LogEntry[];
  // Phase E：最后一次活动时间戳（ISO）。任何 apply* / clearPending 都会刷新。
  // 排序 + LRU 驱逐都用它。
  lastActivity: string;
}

// Phase E：service-worker 持久化的整张多对话快照。
export interface ConversationsSnapshot {
  mirrors: Record<string, RequestStateMirror>;
  activeId: string | null;
}

// Phase G：业务员框选完，先暂存 review 用的数据；点「确认提交」才 POST 给 orchestrator。
// 截图被 SW 压缩成 JPEG（小～300 KB）；mime 跟着传给后端识别。
export interface PendingCapture {
  screenshotB64: string;
  screenshotMime: string;  // "image/jpeg" 或 "image/png"
  url: string;
  boxCoords: BoxCoords;
  viewport: Viewport;
  requestText: string;
}

// ── Plan 10 Task 11+：多附件 + cursor-like 对话 tab + 意图模式 ──────

/**
 * 业务员一次输入携带的附件（≤3 张）。kind 对齐后端 pydantic AttachmentKind：
 * - framed_region：业务员在 demo 页面拖框出的精确区域（带 box + url）
 * - screenshot_active_tab：SW 截当前 tab，全屏，box 留空
 * - pasted_image：剪贴板 / 文件选择器贴的图，box / url 一般留空
 * - attached_file：PDF 等非图文件
 */
export type AttachmentKind =
  | 'framed_region'
  | 'screenshot_active_tab'
  | 'pasted_image'
  | 'attached_file';

export interface Attachment {
  kind: AttachmentKind;
  mime: string;
  b64: string;
  url?: string;
  box?: BoxCoords;
  viewport?: Viewport;
  name?: string;
}

/**
 * 意图模式 —— 客户端 mirror 后端 IntentMode。
 * - new_cr：新需求 / 新动作动词 → 跑完整 pipeline
 * - refine_cr：调整上一次刚做完的修改（追加修饰词）→ 复用 base branch
 * - chat_only：业务员在讨论 / 提问 / 评价 → LLM 文字回复，不进 pipeline
 */
export type IntentMode = 'new_cr' | 'refine_cr' | 'chat_only';

/**
 * conversation.messages JSON 里每条 message 的 shape。
 * 与后端 schemas.MessageOut 对齐。id 由 server 写入（user msg 在 POST /messages
 * 时分配；ai/summary 由 chat_responder / compaction 分配）。
 */
export type MessageType = 'user' | 'ai' | 'summary' | 'system';

export interface ConversationMessage {
  id: string;
  ts: string;
  type: MessageType;
  content: string;
  attachments?: Attachment[];
  cr_id?: string;
  cr_mode?: IntentMode;
  // summary 类型时由 compaction 写入
  replaces_count?: number;
  replaces_token_estimate?: number;
  meta?: Record<string, unknown>;
}

/**
 * AgentTabBar 顶部「当前打开的」对话 tab。不是历史里所有会话 ——
 * 历史走 HistoryDropdown 拉 GET /conversations。这里是 LRU 缓存。
 */
export interface TabsState {
  /** AgentTabBar 渲染顺序（左 → 右） */
  openTabIds: string[];
  /** 当前选中的 tab，null = 没有 open 的 tab（业务员新开会话或全关了） */
  activeTabId: string | null;
  /** LRU 用：tabId → 最后被 setActiveTab 的时间戳（epoch ms） */
  lastUsedAt: Record<string, number>;
}
