// Plan 10 Task 11: tabs.ts —— 打开的对话 tab 列表 + active + LRU.
//
// 业务员视角（用户原话）：「每个 tab 是一个会话，+号是加一个新的回话，
// 时钟代表选择历史回话」。tabs 列表 = 顶部 AgentTabBar 显示的「当前打开的」
// 会话；不是历史里所有会话。LRU 防止 tab 数失控。
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  MAX_OPEN_TABS,
  closeTab,
  evictLRU,
  loadTabs,
  openTab,
  saveTabs,
  setActiveTab,
} from '../src/lib/tabs';

beforeEach(async () => {
  await saveTabs({ openTabIds: [], activeTabId: null, lastUsedAt: {} });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('tabs storage CRUD', () => {
  it('loadTabs returns empty default when storage blank', async () => {
    await chrome.storage.local.clear();
    const t = await loadTabs();
    expect(t.openTabIds).toEqual([]);
    expect(t.activeTabId).toBeNull();
    expect(t.lastUsedAt).toEqual({});
  });

  it('openTab adds id and sets it active', async () => {
    const after = await openTab('conv-a');
    expect(after.openTabIds).toContain('conv-a');
    expect(after.activeTabId).toBe('conv-a');
  });

  it('openTab on existing id only switches active, no duplicate', async () => {
    await openTab('conv-a');
    await openTab('conv-b');
    const after = await openTab('conv-a');
    expect(after.openTabIds.filter((i) => i === 'conv-a').length).toBe(1);
    expect(after.activeTabId).toBe('conv-a');
  });

  it('closeTab removes id; if active, advances to neighbor', async () => {
    await openTab('a');
    await openTab('b');
    await openTab('c');
    let after = await closeTab('c');
    expect(after.openTabIds).toEqual(['a', 'b']);
    expect(after.activeTabId).toBe('b');
    after = await closeTab('a');
    expect(after.openTabIds).toEqual(['b']);
    expect(after.activeTabId).toBe('b');
  });

  it('closeTab when no tabs left sets activeTabId null', async () => {
    await openTab('only');
    const after = await closeTab('only');
    expect(after.openTabIds).toEqual([]);
    expect(after.activeTabId).toBeNull();
  });

  it('setActiveTab updates active and lastUsedAt timestamp', async () => {
    await openTab('a');
    await openTab('b');
    const before = (await loadTabs()).lastUsedAt['a'] ?? 0;
    await new Promise((r) => setTimeout(r, 5));
    const after = await setActiveTab('a');
    expect(after.activeTabId).toBe('a');
    expect(after.lastUsedAt['a']).toBeGreaterThan(before);
  });

  it('persistence: saveTabs then loadTabs roundtrips state', async () => {
    await saveTabs({
      openTabIds: ['x', 'y'],
      activeTabId: 'y',
      lastUsedAt: { x: 100, y: 200 },
    });
    const t = await loadTabs();
    expect(t.openTabIds).toEqual(['x', 'y']);
    expect(t.activeTabId).toBe('y');
    expect(t.lastUsedAt['y']).toBe(200);
  });
});

describe('LRU eviction', () => {
  it('MAX_OPEN_TABS is 8', () => {
    expect(MAX_OPEN_TABS).toBe(8);
  });

  it('opening 9th tab evicts the LRU (oldest lastUsedAt)', async () => {
    const ids = Array.from({ length: 8 }, (_, i) => `c${i}`);
    for (const id of ids) {
      await openTab(id);
      await new Promise((r) => setTimeout(r, 2));
    }
    const after = await openTab('c8');
    expect(after.openTabIds.length).toBe(MAX_OPEN_TABS);
    expect(after.openTabIds).not.toContain('c0');
    expect(after.openTabIds).toContain('c8');
  });

  it('evictLRU is no-op when at or below capacity', async () => {
    await openTab('a');
    const before = await loadTabs();
    const after = await evictLRU();
    expect(after.openTabIds).toEqual(before.openTabIds);
  });

  it('evictLRU preserves active tab even if oldest', async () => {
    await saveTabs({
      openTabIds: ['c1', 'c2', 'c3', 'c4', 'c5', 'c6', 'c7', 'c8', 'c9'],
      activeTabId: 'c1',
      lastUsedAt: {
        c1: 1, c2: 2, c3: 3, c4: 4, c5: 5, c6: 6, c7: 7, c8: 8, c9: 9,
      },
    });
    const after = await evictLRU();
    expect(after.openTabIds).toContain('c1');
    expect(after.openTabIds.length).toBe(MAX_OPEN_TABS);
  });
});
