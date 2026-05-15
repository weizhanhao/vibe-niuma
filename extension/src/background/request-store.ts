// 请求状态镜像：纯 reducer + chrome.storage 持久化。
// Phase E：从「单 mirror」升级到「多 mirror map + activeId」。
// 单 mirror 的 reducer（initialState/applyEvent/applySnapshot/clearPending）保留并复用，
// 多 mirror 容器只是把它们组合起来。
import type {
  ChangeRequestOut, ConversationsSnapshot, RequestStateMirror, SSEEvent,
} from '../lib/types';
import { MAX_LOGS_PER_MIRROR, MAX_MIRRORS, TERMINAL_STATES } from '../lib/types';

// Phase E：新的 storage key。旧 key `doskill.requestState` 不读不写——
// MVP 单用户、单台 ECS，丢若干历史可接受；升级语义清晰。
const STORAGE_KEY_V2 = 'doskill_mirrors_v2';
const ACTIVE_KEY_V2 = 'doskill_active_id_v2';

const TERMINAL = new Set<string>(TERMINAL_STATES);

function nowIso(): string {
  return new Date().toISOString();
}

export function isTerminal(m: RequestStateMirror): boolean {
  return TERMINAL.has(m.state);
}

// ── 单 mirror reducer（保留旧契约） ─────────────────────────────────

export function initialState(cr: ChangeRequestOut): RequestStateMirror {
  return {
    id: cr.id,
    state: cr.state,
    url: cr.url,
    requestText: cr.request_text,
    branch: cr.branch,
    previewUrl: cr.preview_url,
    failPhase: cr.fail_phase,
    failReason: cr.fail_reason,
    pendingQuestion: null,
    pendingVariants: null,
    logs: [],
    lastActivity: nowIso(),
  };
}

export function applyEvent(state: RequestStateMirror, evt: SSEEvent): RequestStateMirror {
  switch (evt.type) {
    case 'status': {
      // 注意：preview_url / branch 是后端写 DB 后才有的，第一次 GET 时是 null。
      // 后端发 status 事件时会夹带这些字段；事件里没给的就保留 mirror 现状。
      return {
        ...state,
        state: evt.data.state,
        failPhase: evt.data.phase ?? state.failPhase,
        failReason: evt.data.reason ?? state.failReason,
        previewUrl: evt.data.preview_url ?? state.previewUrl,
        branch: evt.data.branch ?? state.branch,
        lastActivity: nowIso(),
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
        lastActivity: nowIso(),
      };
    }
    case 'variants': {
      return {
        ...state,
        pendingVariants: { questionId: evt.data.question_id, variants: evt.data.variants },
        lastActivity: nowIso(),
      };
    }
    case 'log': {
      // Phase F：append + 截最近 MAX_LOGS_PER_MIRROR 条；不可变更新。
      const nextLogs = [...state.logs, evt.data];
      const trimmed = nextLogs.length > MAX_LOGS_PER_MIRROR
        ? nextLogs.slice(nextLogs.length - MAX_LOGS_PER_MIRROR)
        : nextLogs;
      return { ...state, logs: trimmed, lastActivity: nowIso() };
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
    lastActivity: nowIso(),
  };
}

export function clearPending(state: RequestStateMirror): RequestStateMirror {
  return { ...state, pendingQuestion: null, pendingVariants: null, lastActivity: nowIso() };
}

// ── 多 mirror map 容器 ─────────────────────────────────────────────

export function upsertMirror(
  mirrors: Record<string, RequestStateMirror>,
  m: RequestStateMirror,
): Record<string, RequestStateMirror> {
  return { ...mirrors, [m.id]: m };
}

export function removeMirror(
  mirrors: Record<string, RequestStateMirror>,
  id: string,
): Record<string, RequestStateMirror> {
  if (!(id in mirrors)) return mirrors;
  const out: Record<string, RequestStateMirror> = {};
  for (const [k, v] of Object.entries(mirrors)) if (k !== id) out[k] = v;
  return out;
}

/**
 * 按 lastActivity 降序排（最近的在前）。UI 列表直接用。
 */
export function sortByActivity(mirrors: Record<string, RequestStateMirror>): RequestStateMirror[] {
  return Object.values(mirrors).sort((a, b) => {
    if (a.lastActivity === b.lastActivity) return 0;
    return a.lastActivity < b.lastActivity ? 1 : -1;
  });
}

/**
 * LRU 截断到 MAX_MIRRORS 条。非终态受保护——优先丢终态里最老的。
 * 若全部终态：按 lastActivity 升序丢最老的。
 * 若全是非终态且仍超额：保留——MVP 不会出现这种场景，安全优先。
 */
export function evictWithLRU(
  mirrors: Record<string, RequestStateMirror>,
): Record<string, RequestStateMirror> {
  const all = Object.values(mirrors);
  if (all.length <= MAX_MIRRORS) return mirrors;

  // 按 lastActivity 升序（最老的在前），用于驱逐
  const sortedAsc = [...all].sort((a, b) => {
    if (a.lastActivity === b.lastActivity) return 0;
    return a.lastActivity < b.lastActivity ? -1 : 1;
  });

  const toDrop: Set<string> = new Set();
  const overflow = all.length - MAX_MIRRORS;

  // 第一轮：只动终态
  for (const m of sortedAsc) {
    if (toDrop.size >= overflow) break;
    if (TERMINAL.has(m.state)) toDrop.add(m.id);
  }
  // 第二轮（极端情况）：全是非终态——保留，不丢。

  if (toDrop.size === 0) return mirrors;

  const out: Record<string, RequestStateMirror> = {};
  for (const [k, v] of Object.entries(mirrors)) if (!toDrop.has(k)) out[k] = v;
  return out;
}

// ── 持久化（chrome.storage.local） ─────────────────────────────────

export async function saveMirrors(
  mirrors: Record<string, RequestStateMirror>,
  activeId: string | null,
): Promise<void> {
  await chrome.storage.local.set({
    [STORAGE_KEY_V2]: mirrors,
    [ACTIVE_KEY_V2]: activeId,
  });
}

export async function loadAll(): Promise<ConversationsSnapshot> {
  const got = await chrome.storage.local.get([STORAGE_KEY_V2, ACTIVE_KEY_V2]);
  const mirrors = (got?.[STORAGE_KEY_V2] as Record<string, RequestStateMirror> | undefined) ?? {};
  const activeId = (got?.[ACTIVE_KEY_V2] as string | null | undefined) ?? null;
  return { mirrors, activeId };
}

// ── 兼容旧 API（单 mirror 入口）：service-worker 和测试仍可调用 ───────
// Phase E 不再使用「单 mirror」语义——这两个函数把单条 mirror 当作 map 里的一员存取，
// 用 mirror.id 做 key。loadFromStorage 返回 activeId 指向的那条（或最近活跃的一条）。
// 这样旧测试不需要重写。

export async function saveToStorage(state: RequestStateMirror | null): Promise<void> {
  const { mirrors, activeId } = await loadAll();
  if (!state) {
    if (activeId && activeId in mirrors) {
      const next = removeMirror(mirrors, activeId);
      await saveMirrors(next, null);
    }
    return;
  }
  const next = upsertMirror(mirrors, state);
  await saveMirrors(next, state.id);
}

export async function loadFromStorage(): Promise<RequestStateMirror | null> {
  const { mirrors, activeId } = await loadAll();
  if (activeId && mirrors[activeId]) return mirrors[activeId];
  const sorted = sortByActivity(mirrors);
  return sorted[0] ?? null;
}
