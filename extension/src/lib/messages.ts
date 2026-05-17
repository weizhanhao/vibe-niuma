// content ↔ background ↔ ui 的消息协议。所有消息都带 type 字段做 narrow。
import type { BoxCoords, PendingCapture, RequestStateMirror, Viewport } from './types';

// content → background
export interface CaptureResultMsg {
  type: 'CAPTURE_RESULT';
  url: string;
  boxCoords: BoxCoords;
  viewport: Viewport;
}

export interface CaptureCancelMsg {
  type: 'CAPTURE_CANCEL';
}

// background → content
export interface StartCaptureMsg {
  type: 'START_CAPTURE';
}

// ui → background
export interface UiStartCaptureMsg {
  type: 'UI_START_CAPTURE';
  requestText: string;
}

export interface SubmitAnswerMsg {
  type: 'SUBMIT_ANSWER';
  requestId: string;
  questionId: string;
  answer: string;
}

export interface MergeMsg { type: 'MERGE'; requestId: string; }
export interface DiscardMsg { type: 'DISCARD'; requestId: string; }
export interface RetryMsg { type: 'RETRY'; requestId: string; }

// Phase E：多对话切换 / 删除 / 新建
export interface SetActiveMsg { type: 'SET_ACTIVE'; id: string | null; }
export interface DeleteConversationMsg { type: 'DELETE_CONVERSATION'; id: string; }
export interface NewConversationMsg { type: 'NEW_CONVERSATION'; }
export interface GetMirrorsMsg { type: 'GET_MIRRORS'; }

// background → ui
// Phase E：broadcast 单条 mirror 变更，附 activeId 让 UI 自己判断是否当前。
export interface RequestStateChangedMsg {
  type: 'REQUEST_STATE_CHANGED';
  state: RequestStateMirror | null;
  activeId: string | null;
}

// Phase E：broadcast 整张快照（列表层级变化时用，如新建 / 删除 / 切换 active）。
export interface MirrorsChangedMsg {
  type: 'MIRRORS_CHANGED';
  mirrors: Record<string, RequestStateMirror>;
  activeId: string | null;
}

export interface RequestStateQueryMsg { type: 'GET_REQUEST_STATE'; }

// Phase G：框选完 review 流。
// 业务员在 demo 页面框完一个区域后，扩展先把截图 + box 暂存为 pendingCapture，
// 弹 ReviewCapturePanel 让业务员看截图 + 蓝框确认；点「确认提交」才 POST 给 orchestrator。
// 避免业务员框错了直接误提交。
export interface ConfirmCaptureMsg {
  type: 'CONFIRM_CAPTURE';
  // 业务员在 review 阶段可能改了需求文本，所以一并回传，service-worker 用它而不是 pendingRequestText。
  requestText: string;
}
export interface RetakeCaptureMsg { type: 'RETAKE_CAPTURE'; }
export interface GetPendingCaptureMsg { type: 'GET_PENDING_CAPTURE'; }
export interface PendingCaptureChangedMsg {
  type: 'PENDING_CAPTURE_CHANGED';
  pending: PendingCapture | null;
}
// 直接提交：不框选，SW 截当前页 + 空 box，进 Review。
export interface SubmitTextOnlyMsg {
  type: 'SUBMIT_TEXT_ONLY';
  requestText: string;
}

// Plan 10 Task 13：cursor-like 多轮 message ingress。SW 拿当前 active conversation_id
// 调 POST /messages，后端意图分类后路由到 new_cr / refine_cr / chat_only。
// 与老的 SUBMIT_TEXT_ONLY / CONFIRM_CAPTURE 并存（v0.5 兼容）；MainShell（Task 17）
// 改造完后会切到 SUBMIT_MESSAGE。
import type { Attachment, IntentMode } from './types';

export interface SetConversationMsg {
  type: 'SET_CONVERSATION';
  id: string | null;
}

export interface SubmitMessageMsg {
  type: 'SUBMIT_MESSAGE';
  text: string;
  attachments?: Attachment[];
  override_mode?: IntentMode;
  /** Plan 10 fix：UI 直接带 convId 不依赖 SW session（避免 SW 死了 / SET_CONVERSATION 还没到的竞态）。
   *  SW 优先用这个；老 client 不带 → fallback 到 session.activeConversationId。 */
  conversation_id?: string;
}

/** SW → UI：框选完截屏 + 压缩好，作为 attachment 推给 UI 加到输入栏 chip。
 *  替代老的 PENDING_CAPTURE_CHANGED → ReviewCapturePanel 流程。 */
export interface CaptureAttachedMsg {
  type: 'CAPTURE_ATTACHED';
  attachment: Attachment;
}

/** UI → SW：业务员点「📷 截图标注」按钮。SW 先 captureVisibleTab 拿 PNG，
 *  再把 dataURL 发给 content script 弹标注 overlay。 */
export interface UiStartAnnotateMsg {
  type: 'UI_START_ANNOTATE';
}

/** SW → content：把刚截的 PNG dataURL 喂给 content script 启 overlay。 */
export interface StartAnnotateMsg {
  type: 'START_ANNOTATE';
  screenshotDataUrl: string;
}

/** content → SW：业务员标注完点「完成」。pngB64 是烘焙后的 PNG base64
 *  （不含 data:image/png;base64, 前缀）。 */
export interface AnnotateResultMsg {
  type: 'ANNOTATE_RESULT';
  pngB64: string;
}

/** content → SW：业务员按 Esc 或点「取消」。SW 不广播 attachment。 */
export interface AnnotateCancelMsg {
  type: 'ANNOTATE_CANCEL';
}

/** content → SW：preview 页运行时 JS 出错（window.error / unhandledrejection）。
 *  SW 查 previewUrlToCrId map → 转发 orchestrator 触发 self-heal。 */
export interface RuntimeErrorReportMsg {
  type: 'RUNTIME_ERROR_REPORT';
  pageUrl: string;       // 出错所在页面 URL（http://x:51xx/...）
  message: string;
  stack?: string;
  ts: string;            // ISO UTC（含 Z）
}

export type Message =
  | CaptureResultMsg | CaptureCancelMsg | StartCaptureMsg
  | UiStartCaptureMsg | SubmitAnswerMsg | MergeMsg | DiscardMsg | RetryMsg
  | SetActiveMsg | DeleteConversationMsg | NewConversationMsg | GetMirrorsMsg
  | RequestStateChangedMsg | MirrorsChangedMsg | RequestStateQueryMsg
  | ConfirmCaptureMsg | RetakeCaptureMsg | GetPendingCaptureMsg
  | PendingCaptureChangedMsg | SubmitTextOnlyMsg
  | SetConversationMsg | SubmitMessageMsg | CaptureAttachedMsg
  | UiStartAnnotateMsg | StartAnnotateMsg | AnnotateResultMsg | AnnotateCancelMsg
  | RuntimeErrorReportMsg;

export const MSG = {
  START_CAPTURE: 'START_CAPTURE' as const,
  CAPTURE_RESULT: 'CAPTURE_RESULT' as const,
  CAPTURE_CANCEL: 'CAPTURE_CANCEL' as const,
  UI_START_CAPTURE: 'UI_START_CAPTURE' as const,
  SUBMIT_ANSWER: 'SUBMIT_ANSWER' as const,
  MERGE: 'MERGE' as const,
  DISCARD: 'DISCARD' as const,
  RETRY: 'RETRY' as const,
  SET_ACTIVE: 'SET_ACTIVE' as const,
  DELETE_CONVERSATION: 'DELETE_CONVERSATION' as const,
  NEW_CONVERSATION: 'NEW_CONVERSATION' as const,
  GET_MIRRORS: 'GET_MIRRORS' as const,
  REQUEST_STATE_CHANGED: 'REQUEST_STATE_CHANGED' as const,
  MIRRORS_CHANGED: 'MIRRORS_CHANGED' as const,
  GET_REQUEST_STATE: 'GET_REQUEST_STATE' as const,
  // Phase G
  CONFIRM_CAPTURE: 'CONFIRM_CAPTURE' as const,
  RETAKE_CAPTURE: 'RETAKE_CAPTURE' as const,
  GET_PENDING_CAPTURE: 'GET_PENDING_CAPTURE' as const,
  PENDING_CAPTURE_CHANGED: 'PENDING_CAPTURE_CHANGED' as const,
  // Phase G+：直接提交（不框选）；SW 截当前页 + 空 box，走 Review。
  SUBMIT_TEXT_ONLY: 'SUBMIT_TEXT_ONLY' as const,
  // Plan 10 Task 13：多轮 message ingress 走 POST /messages
  SET_CONVERSATION: 'SET_CONVERSATION' as const,
  SUBMIT_MESSAGE: 'SUBMIT_MESSAGE' as const,
  // 框选完直接推 Attachment 给 UI（替代 ReviewCapturePanel 接管 body）
  CAPTURE_ATTACHED: 'CAPTURE_ATTACHED' as const,
  // 截图 + 标注流（替代框选的演进版）
  UI_START_ANNOTATE: 'UI_START_ANNOTATE' as const,
  START_ANNOTATE: 'START_ANNOTATE' as const,
  ANNOTATE_RESULT: 'ANNOTATE_RESULT' as const,
  ANNOTATE_CANCEL: 'ANNOTATE_CANCEL' as const,
  // 浏览器侧运行时错误上报 → SW → orchestrator self-heal
  RUNTIME_ERROR_REPORT: 'RUNTIME_ERROR_REPORT' as const,
};
