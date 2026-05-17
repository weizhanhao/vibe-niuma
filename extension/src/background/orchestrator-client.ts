// Orchestrator 客户端：REST + SSE。
// Plan 6 Task 10：从 module-level singleton 改成 factory。
// 之前 `orchestratorClient` 是顶层导出 const，URL 用 getBaseUrl()/setBaseUrl() 读旧 storage key
// （`doskill.orchestratorBaseUrl`，只存 URL 字符串）。Plan 6 后所有配置走 `lib/config.ts`
// 的 `doskill_config_v2`（含 URL + adminToken + server 字段），service-worker 自己负责
// 从 config 拿 URL 调 createOrchestratorClient。这样 client 不再依赖 chrome.storage。
//
// /change-requests/* 是用户面 API，不要求 admin token；token 参数留着是为以后 SSE 需要
// 鉴权时不破坏 API 形状，目前不附带 header。
import type {
  Attachment, ChangeRequestOut, IntentMode, RawRequestPayload, SSEEvent,
} from '../lib/types';

export class HttpError extends Error {
  constructor(public status: number, public body: string) {
    super(`HTTP ${status}: ${body.slice(0, 200)}`);
  }
}

// Plan 10 Task 13：POST /conversations/{conv_id}/messages 入参/出参契约
export interface PostMessageBody {
  text: string;
  attachments?: Attachment[];
  override_mode?: IntentMode;
}

export interface PostMessageResponse {
  message_id: string;
  mode: IntentMode;
  cr_id?: string | null;
  ai_message_id?: string | null;
  confidence: number;
  is_unsure: boolean;
  reason: string;
}

export interface OrchestratorClient {
  readonly baseUrl: string;
  createChangeRequest(payload: RawRequestPayload): Promise<ChangeRequestOut>;
  getChangeRequest(id: string): Promise<ChangeRequestOut>;
  submitAnswer(id: string, questionId: string, answer: string): Promise<{ status: string }>;
  merge(id: string): Promise<ChangeRequestOut>;
  discard(id: string): Promise<ChangeRequestOut>;
  retry(id: string): Promise<ChangeRequestOut>;
  /**
   * Plan 10 Task 13：统一 message ingress。
   * 后端意图分类后路由到 new_cr / refine_cr / chat_only。
   * - chat_only：response.cr_id 为 null，ai_message_id 是 AI 回复 message id
   * - new_cr / refine_cr：response.cr_id 是新建 CR id，ai_message_id null
   */
  postMessage(convId: string, body: PostMessageBody): Promise<PostMessageResponse>;
  /**
   * 浏览器侧捕到的 React/JS 运行时错误回灌。服务器会触发 self-heal
   * （重跑一次 dev_runner 把错误信息丢给模型修），最多 1 次。
   */
  postRuntimeErrors(
    id: string,
    errors: { message: string; stack?: string; ts: string; pageUrl: string }[],
  ): Promise<{ status: string; will_self_heal: boolean }>;
  subscribeEvents(
    id: string,
    onEvent: (e: SSEEvent) => void,
    onSnapshot?: (cr: ChangeRequestOut) => void,
  ): () => void;
}

/**
 * 工厂：用一份 baseUrl + (可选) token 造一个独立 client。
 * baseUrl 末尾不应带 `/`，由调用方保证（zod schema 已 validate 过 URL 格式）。
 */
export function createOrchestratorClient(baseUrl: string, _token?: string): OrchestratorClient {
  async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const resp = await fetch(`${baseUrl}${path}`, {
      ...init,
      headers: { 'Content-Type': 'application/json', ...(init.headers || {}) },
    });
    const text = await resp.text();
    if (!resp.ok) throw new HttpError(resp.status, text);
    return text ? (JSON.parse(text) as T) : (undefined as unknown as T);
  }

  const client: OrchestratorClient = {
    baseUrl,
    async createChangeRequest(payload) {
      return request<ChangeRequestOut>('/change-requests', {
        method: 'POST', body: JSON.stringify(payload),
      });
    },
    async getChangeRequest(id) {
      return request<ChangeRequestOut>(`/change-requests/${id}`);
    },
    async submitAnswer(id, questionId, answer) {
      return request<{ status: string }>(`/change-requests/${id}/answer`, {
        method: 'POST',
        body: JSON.stringify({ question_id: questionId, answer }),
      });
    },
    async merge(id) {
      return request<ChangeRequestOut>(`/change-requests/${id}/merge`, { method: 'POST' });
    },
    async discard(id) {
      return request<ChangeRequestOut>(`/change-requests/${id}/discard`, { method: 'POST' });
    },
    async retry(id) {
      return request<ChangeRequestOut>(`/change-requests/${id}/retry`, { method: 'POST' });
    },
    async postRuntimeErrors(id, errors) {
      return request<{ status: string; will_self_heal: boolean }>(
        `/change-requests/${id}/runtime-errors`,
        { method: 'POST', body: JSON.stringify({ errors }) },
      );
    },
    async postMessage(convId, body) {
      return request<PostMessageResponse>(`/conversations/${convId}/messages`, {
        method: 'POST',
        body: JSON.stringify(body),
      });
    },

    /**
     * subscribeEvents: 订阅 SSE 事件流；error 时按指数退避重连，重连成功后顺带拉一次 GET。
     * 返回 unsubscribe 函数。
     */
    subscribeEvents(id, onEvent, onSnapshot) {
      let es: EventSource | null = null;
      let backoff = 500;
      let stopped = false;

      const connect = () => {
        const url = `${baseUrl}/change-requests/${id}/events`;
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
        // Plan 10：多题表单（推荐版）—— BrainstormingSkill 用 questions[] 时发这个
        es.addEventListener('form', dispatch('form') as EventListener);
        es.addEventListener('variants', dispatch('variants') as EventListener);
        // Phase F：流式 log 行，phase=clarifying|locating|coding|building|...
        es.addEventListener('log', dispatch('log') as EventListener);
        // Phase C：业务员回答后，后端广播 question-resolved 让前端清掉 pendingQuestion
        es.addEventListener('question-resolved', dispatch('question-resolved') as EventListener);
        es.onerror = () => {
          if (stopped) return;
          es?.close();
          es = null;
          setTimeout(async () => {
            if (stopped) return;
            try {
              const snap = await client.getChangeRequest(id);
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

  return client;
}
