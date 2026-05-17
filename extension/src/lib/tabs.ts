// Plan 10 Task 11: cursor-like AgentTabBar 的 tab 存储 + LRU.
//
// 业务员视角（用户原话）：「上面每个 tab 是一个会话，+号是加一个新的回话，
// 时钟代表选择历史回话」。这里管的是「当前打开的 tab」list，不是历史里
// 所有 conversation 列表 —— 那是 HistoryDropdown 拉 GET /conversations。
//
// LRU = 业务员开太多 tab 就把最久不用的 squeezed out（保留 active 不动）。
import type { TabsState } from './types';

/** AgentTabBar 最多同时显示 8 个 tab；多了 LRU evict. */
export const MAX_OPEN_TABS = 8;

const STORAGE_KEY = 'doskill.tabsState.v1';

const EMPTY_STATE: TabsState = {
  openTabIds: [],
  activeTabId: null,
  lastUsedAt: {},
};

export async function loadTabs(): Promise<TabsState> {
  const got = await chrome.storage.local.get(STORAGE_KEY);
  const raw = got?.[STORAGE_KEY] as Partial<TabsState> | undefined;
  if (!raw) return { ...EMPTY_STATE };
  return {
    openTabIds: Array.isArray(raw.openTabIds) ? raw.openTabIds : [],
    activeTabId: typeof raw.activeTabId === 'string' ? raw.activeTabId : null,
    lastUsedAt: raw.lastUsedAt && typeof raw.lastUsedAt === 'object' ? raw.lastUsedAt : {},
  };
}

export async function saveTabs(state: TabsState): Promise<void> {
  await chrome.storage.local.set({ [STORAGE_KEY]: state });
}

/**
 * 打开一个 tab：
 * - 已经在 openTabIds 里 → 只切 active + 更新 lastUsedAt
 * - 不在 → 追加到末尾，set active，触发 LRU evict（保证 ≤ MAX_OPEN_TABS）
 */
export async function openTab(id: string): Promise<TabsState> {
  const cur = await loadTabs();
  const now = Date.now();
  let next: TabsState;
  if (cur.openTabIds.includes(id)) {
    next = {
      openTabIds: cur.openTabIds,
      activeTabId: id,
      lastUsedAt: { ...cur.lastUsedAt, [id]: now },
    };
  } else {
    next = {
      openTabIds: [...cur.openTabIds, id],
      activeTabId: id,
      lastUsedAt: { ...cur.lastUsedAt, [id]: now },
    };
  }
  next = _evictIfNeeded(next);
  await saveTabs(next);
  return next;
}

/**
 * 关闭一个 tab：
 * - 不在列表里 → noop
 * - 在列表里：
 *   - 删除条目
 *   - 如果它是 active，active 滚到右邻居（没有右邻居就左邻居）
 *   - 全空 → active = null
 */
export async function closeTab(id: string): Promise<TabsState> {
  const cur = await loadTabs();
  const idx = cur.openTabIds.indexOf(id);
  if (idx < 0) return cur;
  const remaining = cur.openTabIds.filter((t) => t !== id);
  let newActive = cur.activeTabId;
  if (cur.activeTabId === id) {
    if (remaining.length === 0) {
      newActive = null;
    } else {
      newActive = remaining[idx] ?? remaining[idx - 1] ?? remaining[0];
    }
  }
  const nextLastUsed = { ...cur.lastUsedAt };
  delete nextLastUsed[id];
  const next: TabsState = {
    openTabIds: remaining,
    activeTabId: newActive,
    lastUsedAt: nextLastUsed,
  };
  await saveTabs(next);
  return next;
}

export async function setActiveTab(id: string | null): Promise<TabsState> {
  const cur = await loadTabs();
  const next: TabsState = {
    ...cur,
    activeTabId: id,
    lastUsedAt: id ? { ...cur.lastUsedAt, [id]: Date.now() } : cur.lastUsedAt,
  };
  await saveTabs(next);
  return next;
}

/**
 * LRU evict：把 openTabIds 缩到 MAX_OPEN_TABS 内。永远不 evict activeTabId。
 * 优先丢 lastUsedAt 最小的（最久没用的）。
 */
export async function evictLRU(): Promise<TabsState> {
  const cur = await loadTabs();
  const next = _evictIfNeeded(cur);
  if (next !== cur) await saveTabs(next);
  return next;
}

function _evictIfNeeded(state: TabsState): TabsState {
  if (state.openTabIds.length <= MAX_OPEN_TABS) return state;
  const overflow = state.openTabIds.length - MAX_OPEN_TABS;
  const candidates = state.openTabIds
    .filter((t) => t !== state.activeTabId)
    .map((t) => ({ id: t, ts: state.lastUsedAt[t] ?? 0 }))
    .sort((a, b) => a.ts - b.ts);
  const toEvict = new Set(candidates.slice(0, overflow).map((c) => c.id));
  const remaining = state.openTabIds.filter((t) => !toEvict.has(t));
  const nextLastUsed = { ...state.lastUsedAt };
  for (const id of toEvict) delete nextLastUsed[id];
  return {
    openTabIds: remaining,
    activeTabId: state.activeTabId,
    lastUsedAt: nextLastUsed,
  };
}
