// content ↔ background ↔ ui 的消息协议。所有消息都带 type 字段做 narrow。
import type { BoxCoords, RequestStateMirror, Viewport } from './types';

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

// background → ui
export interface RequestStateChangedMsg {
  type: 'REQUEST_STATE_CHANGED';
  state: RequestStateMirror | null;
}

export interface RequestStateQueryMsg { type: 'GET_REQUEST_STATE'; }

export type Message =
  | CaptureResultMsg | CaptureCancelMsg | StartCaptureMsg
  | UiStartCaptureMsg | SubmitAnswerMsg | MergeMsg | DiscardMsg | RetryMsg
  | RequestStateChangedMsg | RequestStateQueryMsg;

export const MSG = {
  START_CAPTURE: 'START_CAPTURE' as const,
  CAPTURE_RESULT: 'CAPTURE_RESULT' as const,
  CAPTURE_CANCEL: 'CAPTURE_CANCEL' as const,
  UI_START_CAPTURE: 'UI_START_CAPTURE' as const,
  SUBMIT_ANSWER: 'SUBMIT_ANSWER' as const,
  MERGE: 'MERGE' as const,
  DISCARD: 'DISCARD' as const,
  RETRY: 'RETRY' as const,
  REQUEST_STATE_CHANGED: 'REQUEST_STATE_CHANGED' as const,
  GET_REQUEST_STATE: 'GET_REQUEST_STATE' as const,
};
