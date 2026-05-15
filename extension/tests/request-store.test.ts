import { describe, expect, it } from 'vitest';
import {
  applyEvent, applySnapshot, clearPending, initialState,
  loadFromStorage, saveToStorage,
} from '../src/background/request-store';
import type { ChangeRequestOut, RequestStateMirror, SSEEvent } from '../src/lib/types';
import { MAX_LOGS_PER_MIRROR } from '../src/lib/types';

const baseCr: ChangeRequestOut = {
  id: 'r1', state: 'created', url: 'http://x/orders', request_text: 't',
  branch: null, preview_url: null, fail_phase: null, fail_reason: null, retry_of: null,
};

const baseMirror: RequestStateMirror = initialState(baseCr);

describe('request-store reducer', () => {
  it('status event advances state', () => {
    const next = applyEvent(baseMirror, { type: 'status', data: { state: 'clarifying' } });
    expect(next.state).toBe('clarifying');
  });

  it('question event sets pendingQuestion', () => {
    const evt: SSEEvent = { type: 'question', data: { question_id: 'q1', question: '?', options: null } };
    const next = applyEvent(baseMirror, evt);
    expect(next.pendingQuestion?.questionId).toBe('q1');
  });

  it('variants event sets pendingVariants', () => {
    const evt: SSEEvent = {
      type: 'variants',
      data: { question_id: 'q1', variants: [{ id: 'v1', title: 't', html: '<a/>' }] },
    };
    const next = applyEvent(baseMirror, evt);
    expect(next.pendingVariants?.variants[0].id).toBe('v1');
  });

  it('clearPending removes question + variants', () => {
    const withQ = applyEvent(baseMirror, { type: 'question', data: { question_id: 'q', question: '?', options: null } });
    const cleared = clearPending(withQ);
    expect(cleared.pendingQuestion).toBeNull();
    expect(cleared.pendingVariants).toBeNull();
  });

  it('snapshot updates state + branch + previewUrl', () => {
    const next = applySnapshot(baseMirror, {
      ...baseCr, state: 'preview-ready', branch: 'cr/x', preview_url: 'http://x:5101',
    });
    expect(next.state).toBe('preview-ready');
    expect(next.branch).toBe('cr/x');
    expect(next.previewUrl).toBe('http://x:5101');
  });

  it('persists + reloads from chrome.storage', async () => {
    await saveToStorage(baseMirror);
    const loaded = await loadFromStorage();
    expect(loaded?.id).toBe('r1');
  });

  it('loadFromStorage returns null when missing', async () => {
    await chrome.storage.local.clear();
    expect(await loadFromStorage()).toBeNull();
  });

  // ── Phase F：log 事件折叠到 mirror.logs ────────────────────────────
  it('initialState seeds an empty logs array', () => {
    expect(initialState(baseCr).logs).toEqual([]);
  });

  it('log event appends to mirror.logs', () => {
    const evt: SSEEvent = {
      type: 'log',
      data: { phase: 'coding', line: '正在改 OrderList.tsx', ts: '2026-05-15T17:00:00' },
    };
    const next = applyEvent(baseMirror, evt);
    expect(next.logs).toHaveLength(1);
    expect(next.logs[0]).toMatchObject({ phase: 'coding', line: '正在改 OrderList.tsx' });
  });

  it('caps logs at MAX_LOGS_PER_MIRROR (FIFO drop)', () => {
    // 灌 250 条（MAX 是 200），验证保留最近 200 条
    let state = baseMirror;
    const overflow = 50;
    for (let i = 0; i < MAX_LOGS_PER_MIRROR + overflow; i++) {
      state = applyEvent(state, {
        type: 'log',
        data: { phase: 'coding', line: `line-${i}`, ts: String(i) },
      });
    }
    expect(state.logs).toHaveLength(MAX_LOGS_PER_MIRROR);
    // 头条应是 line-50，尾条应是 line-249
    expect(state.logs[0].line).toBe(`line-${overflow}`);
    expect(state.logs[state.logs.length - 1].line).toBe(`line-${MAX_LOGS_PER_MIRROR + overflow - 1}`);
  });
});
