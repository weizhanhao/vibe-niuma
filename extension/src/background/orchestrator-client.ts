// Orchestrator 客户端：REST + SSE。base URL 从 chrome.storage 读，缺省 localhost:9000。
import type { ChangeRequestOut, RawRequestPayload, SSEEvent } from '../lib/types';

const STORAGE_KEY = 'doskill.orchestratorBaseUrl';
const DEFAULT_BASE = 'http://localhost:9000';

export class HttpError extends Error {
  constructor(public status: number, public body: string) {
    super(`HTTP ${status}: ${body.slice(0, 200)}`);
  }
}

export async function getBaseUrl(): Promise<string> {
  try {
    const got = await chrome.storage.local.get(STORAGE_KEY);
    return (got?.[STORAGE_KEY] as string) || DEFAULT_BASE;
  } catch {
    return DEFAULT_BASE;
  }
}

export async function setBaseUrl(url: string): Promise<void> {
  await chrome.storage.local.set({ [STORAGE_KEY]: url });
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const base = await getBaseUrl();
  const resp = await fetch(`${base}${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init.headers || {}) },
  });
  const text = await resp.text();
  if (!resp.ok) throw new HttpError(resp.status, text);
  return text ? (JSON.parse(text) as T) : (undefined as unknown as T);
}

export const orchestratorClient = {
  async createChangeRequest(payload: RawRequestPayload) {
    return request<ChangeRequestOut>('/change-requests', {
      method: 'POST', body: JSON.stringify(payload),
    });
  },
  async getChangeRequest(id: string) {
    return request<ChangeRequestOut>(`/change-requests/${id}`);
  },
  async submitAnswer(id: string, questionId: string, answer: string) {
    return request<{ status: string }>(`/change-requests/${id}/answer`, {
      method: 'POST',
      body: JSON.stringify({ question_id: questionId, answer }),
    });
  },
  async merge(id: string) {
    return request<ChangeRequestOut>(`/change-requests/${id}/merge`, { method: 'POST' });
  },
  async discard(id: string) {
    return request<ChangeRequestOut>(`/change-requests/${id}/discard`, { method: 'POST' });
  },
  async retry(id: string) {
    return request<ChangeRequestOut>(`/change-requests/${id}/retry`, { method: 'POST' });
  },

  /**
   * subscribeEvents: 订阅 SSE 事件流；error 时按指数退避重连，重连成功后顺带拉一次 GET。
   * 返回 unsubscribe 函数。
   */
  subscribeEvents(
    id: string,
    onEvent: (e: SSEEvent) => void,
    onSnapshot?: (cr: ChangeRequestOut) => void,
  ): () => void {
    let es: EventSource | null = null;
    let backoff = 500;
    let stopped = false;

    const connect = async () => {
      const base = await getBaseUrl();
      const url = `${base}/change-requests/${id}/events`;
      es = new EventSource(url);
      const dispatch = (type: SSEEvent['type']) => (ev: MessageEvent) => {
        try {
          const data = JSON.parse(ev.data);
          onEvent({ type, data } as SSEEvent);
        } catch {
          /* ignore */
        }
      };
      es.addEventListener('status', dispatch('status') as EventListener);
      es.addEventListener('question', dispatch('question') as EventListener);
      es.addEventListener('variants', dispatch('variants') as EventListener);
      es.onerror = () => {
        if (stopped) return;
        es?.close();
        es = null;
        setTimeout(async () => {
          if (stopped) return;
          try {
            const snap = await orchestratorClient.getChangeRequest(id);
            onSnapshot?.(snap);
          } catch {
            /* ignore */
          }
          backoff = Math.min(backoff * 2, 10_000);
          connect();
        }, backoff);
      };
      es.onopen = () => { backoff = 500; };
    };
    connect();
    return () => { stopped = true; es?.close(); es = null; };
  },
};
