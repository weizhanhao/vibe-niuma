import { describe, expect, it } from 'vitest';
import {
  applyEvent, applySnapshot, clearPending, initialState,
  loadFromStorage, saveToStorage,
} from '../src/background/request-store';
import type { ChangeRequestOut, RequestStateMirror, SSEEvent } from '../src/lib/types';

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
});
