// Phase E：多 mirror map 容器 + LRU + 持久化契约。
import { describe, expect, it } from 'vitest';
import {
  evictWithLRU, initialState, isTerminal, loadAll, removeMirror, saveMirrors,
  sortByActivity, upsertMirror,
} from '../src/background/request-store';
import type { ChangeRequestOut, RequestStateMirror } from '../src/lib/types';
import { MAX_MIRRORS } from '../src/lib/types';

function mkCr(id: string, overrides: Partial<ChangeRequestOut> = {}): ChangeRequestOut {
  return {
    id, state: 'created', url: 'http://x/orders', request_text: `t-${id}`,
    branch: null, preview_url: null, fail_phase: null, fail_reason: null, retry_of: null,
    ...overrides,
  };
}

function mkMirror(id: string, opts: Partial<RequestStateMirror> = {}): RequestStateMirror {
  return { ...initialState(mkCr(id)), ...opts };
}

describe('request-store multi-mirror container', () => {
  it('initialState carries requestText + lastActivity', () => {
    const m = initialState(mkCr('r1'));
    expect(m.requestText).toBe('t-r1');
    expect(typeof m.lastActivity).toBe('string');
    expect(m.lastActivity.length).toBeGreaterThan(0);
  });

  it('upsertMirror returns a new object (immutable)', () => {
    const map: Record<string, RequestStateMirror> = {};
    const next = upsertMirror(map, mkMirror('r1'));
    expect(map).toEqual({});
    expect(next.r1.id).toBe('r1');
  });

  it('removeMirror drops the entry', () => {
    const map = upsertMirror({}, mkMirror('r1'));
    const next = removeMirror(map, 'r1');
    expect(next.r1).toBeUndefined();
  });

  it('removeMirror noop for missing id', () => {
    const map = upsertMirror({}, mkMirror('r1'));
    expect(removeMirror(map, 'r2')).toBe(map);
  });

  it('sortByActivity orders newest first', () => {
    const map: Record<string, RequestStateMirror> = {
      r1: mkMirror('r1', { lastActivity: '2026-05-15T10:00:00.000Z' }),
      r2: mkMirror('r2', { lastActivity: '2026-05-15T12:00:00.000Z' }),
      r3: mkMirror('r3', { lastActivity: '2026-05-15T11:00:00.000Z' }),
    };
    const sorted = sortByActivity(map);
    expect(sorted.map((m) => m.id)).toEqual(['r2', 'r3', 'r1']);
  });

  it('isTerminal recognizes terminal states', () => {
    expect(isTerminal(mkMirror('a', { state: 'merged' }))).toBe(true);
    expect(isTerminal(mkMirror('b', { state: 'failed' }))).toBe(true);
    expect(isTerminal(mkMirror('c', { state: 'expired' }))).toBe(true);
    expect(isTerminal(mkMirror('d', { state: 'discarded' }))).toBe(true);
    expect(isTerminal(mkMirror('e', { state: 'coding' }))).toBe(false);
    expect(isTerminal(mkMirror('f', { state: 'preview-ready' }))).toBe(false);
  });

  describe('evictWithLRU', () => {
    it('noop when under cap', () => {
      const map: Record<string, RequestStateMirror> = {};
      for (let i = 0; i < 5; i++) {
        map[`r${i}`] = mkMirror(`r${i}`, { lastActivity: `2026-05-15T10:00:0${i}.000Z` });
      }
      expect(evictWithLRU(map)).toBe(map);
    });

    it('drops oldest terminal mirrors when over cap', () => {
      const map: Record<string, RequestStateMirror> = {};
      // 51 条；全部终态（merged），lastActivity 递增。应丢掉最老的 1 条（r0）。
      for (let i = 0; i < MAX_MIRRORS + 1; i++) {
        const ts = `2026-05-15T10:${String(i).padStart(2, '0')}:00.000Z`;
        map[`r${i}`] = mkMirror(`r${i}`, { state: 'merged', lastActivity: ts });
      }
      const next = evictWithLRU(map);
      expect(Object.keys(next).length).toBe(MAX_MIRRORS);
      expect(next.r0).toBeUndefined();
      expect(next[`r${MAX_MIRRORS}`]).toBeDefined();
    });

    it('protects in-flight mirrors—skips them even if oldest', () => {
      const map: Record<string, RequestStateMirror> = {};
      // r0 是最老的非终态；r1..r50 全部终态。应丢 r1（最老的终态），保留 r0。
      map.r0 = mkMirror('r0', { state: 'coding', lastActivity: '2026-05-15T09:00:00.000Z' });
      for (let i = 1; i <= MAX_MIRRORS; i++) {
        const ts = `2026-05-15T10:${String(i).padStart(2, '0')}:00.000Z`;
        map[`r${i}`] = mkMirror(`r${i}`, { state: 'merged', lastActivity: ts });
      }
      const next = evictWithLRU(map);
      expect(Object.keys(next).length).toBe(MAX_MIRRORS);
      expect(next.r0).toBeDefined();
      expect(next.r1).toBeUndefined();
    });

    it('falls back to keeping all when only in-flight exceed cap', () => {
      const map: Record<string, RequestStateMirror> = {};
      for (let i = 0; i <= MAX_MIRRORS; i++) {
        const ts = `2026-05-15T10:${String(i).padStart(2, '0')}:00.000Z`;
        map[`r${i}`] = mkMirror(`r${i}`, { state: 'coding', lastActivity: ts });
      }
      const next = evictWithLRU(map);
      // 无终态可丢，保留全部；MVP 不会出现这种场景，但实现不该崩。
      expect(Object.keys(next).length).toBe(MAX_MIRRORS + 1);
    });
  });

  describe('saveMirrors / loadAll', () => {
    it('round-trips mirrors + activeId via chrome.storage', async () => {
      const map: Record<string, RequestStateMirror> = {
        r1: mkMirror('r1'),
        r2: mkMirror('r2'),
      };
      await saveMirrors(map, 'r1');
      const got = await loadAll();
      expect(got.activeId).toBe('r1');
      expect(Object.keys(got.mirrors).sort()).toEqual(['r1', 'r2']);
    });

    it('loadAll returns empty defaults when storage empty', async () => {
      await chrome.storage.local.clear();
      const got = await loadAll();
      expect(got.mirrors).toEqual({});
      expect(got.activeId).toBeNull();
    });

    it('uses v2 storage key (not the legacy single-mirror key)', async () => {
      await chrome.storage.local.clear();
      await saveMirrors({ r1: mkMirror('r1') }, 'r1');
      const raw = await chrome.storage.local.get(null);
      expect(raw.vibe_niuma_mirrors_v2).toBeDefined();
      expect(raw.vibe_niuma_active_id_v2).toBe('r1');
      // 旧 key 不应再被写
      expect(raw['vibe-niuma.requestState']).toBeUndefined();
    });
  });
});
