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

export type Message =
  | CaptureResultMsg | CaptureCancelMsg | StartCaptureMsg
  | UiStartCaptureMsg | SubmitAnswerMsg | MergeMsg | DiscardMsg | RetryMsg
  | SetActiveMsg | DeleteConversationMsg | NewConversationMsg | GetMirrorsMsg
  | RequestStateChangedMsg | MirrorsChangedMsg | RequestStateQueryMsg
  | ConfirmCaptureMsg | RetakeCaptureMsg | GetPendingCaptureMsg
  | PendingCaptureChangedMsg;

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
};
