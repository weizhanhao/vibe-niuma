// 请求状态镜像：纯 reducer + chrome.storage 持久化。
import type { ChangeRequestOut, RequestStateMirror, SSEEvent } from '../lib/types';
import { MAX_LOGS_PER_MIRROR } from '../lib/types';

const STORAGE_KEY = 'doskill.requestState';

export function initialState(cr: ChangeRequestOut): RequestStateMirror {
  return {
    id: cr.id,
    state: cr.state,
    url: cr.url,
    branch: cr.branch,
    previewUrl: cr.preview_url,
    failPhase: cr.fail_phase,
    failReason: cr.fail_reason,
    pendingQuestion: null,
    pendingVariants: null,
    logs: [],
  };
}

export function applyEvent(state: RequestStateMirror, evt: SSEEvent): RequestStateMirror {
  switch (evt.type) {
    case 'status': {
      return {
        ...state,
        state: evt.data.state,
        failPhase: evt.data.phase ?? state.failPhase,
        failReason: evt.data.reason ?? state.failReason,
      };
    }
    case 'question': {
      return {
        ...state,
        pendingQuestion: {
          questionId: evt.data.question_id,
          question: evt.data.question,
          options: evt.data.options ?? null,
        },
      };
    }
    case 'variants': {
      return {
        ...state,
        pendingVariants: { questionId: evt.data.question_id, variants: evt.data.variants },
      };
    }
    case 'log': {
      // Phase F：append + 截最近 MAX_LOGS_PER_MIRROR 条；不可变更新。
      const nextLogs = [...state.logs, evt.data];
      const trimmed = nextLogs.length > MAX_LOGS_PER_MIRROR
        ? nextLogs.slice(nextLogs.length - MAX_LOGS_PER_MIRROR)
        : nextLogs;
      return { ...state, logs: trimmed };
    }
  }
}

export function applySnapshot(state: RequestStateMirror, cr: ChangeRequestOut): RequestStateMirror {
  return {
    ...state,
    state: cr.state,
    branch: cr.branch,
    previewUrl: cr.preview_url,
    failPhase: cr.fail_phase,
    failReason: cr.fail_reason,
  };
}

export function clearPending(state: RequestStateMirror): RequestStateMirror {
  return { ...state, pendingQuestion: null, pendingVariants: null };
}

export async function saveToStorage(state: RequestStateMirror | null): Promise<void> {
  await chrome.storage.local.set({ [STORAGE_KEY]: state });
}

export async function loadFromStorage(): Promise<RequestStateMirror | null> {
  const got = await chrome.storage.local.get(STORAGE_KEY);
  return (got?.[STORAGE_KEY] as RequestStateMirror | null) ?? null;
}
