// Plan 10 Task 12: 客户端 intent classifier mirror.
//
// 业务员视角：用户输入框 ENTER 时 SW 立刻给个 UX hint「我准备走 X 模式」，
// 让业务员看到再发；server 收到 POST /messages 还会再判一次最终决策。
// 客户端不需要 100% 跟 server 一致，但常见 case 要稳定一致。
//
// 镜像后端 test_intent_classifier.py 的 6 个 case，确保两边决策同步。
import { describe, expect, it } from 'vitest';
import { classifyIntent, isUnsure } from '../src/lib/intent';
import type { ConversationMessage } from '../src/lib/types';

function msgs(...pairs: Array<['user' | 'ai', string]>): ConversationMessage[] {
  return pairs.map(([type, content], i) => ({
    id: `m${i}`, ts: 't', type, content,
  }));
}

describe('intent classifier mirror', () => {
  it('continuation phrase ("字号大一点") after preview-ready → refine_cr', () => {
    const d = classifyIntent({
      messageText: '字号大一点',
      conversationMessages: msgs(['user', '改红'], ['ai', '改完了']),
      lastCrState: 'preview-ready',
    });
    expect(d.mode).toBe('refine_cr');
  });

  it('question phrase ("怎么样") → chat_only', () => {
    const d = classifyIntent({
      messageText: '你觉得这次改得怎么样？',
      conversationMessages: msgs(['user', '改红']),
      lastCrState: 'merged',
    });
    expect(d.mode).toBe('chat_only');
  });

  it('new intent verb ("加搜索") → new_cr regardless of history', () => {
    const d = classifyIntent({
      messageText: '加个搜索',
      conversationMessages: msgs(['user', '改红'], ['ai', '改完']),
      lastCrState: 'preview-ready',
    });
    expect(d.mode).toBe('new_cr');
  });

  it('no history + verb → new_cr', () => {
    const d = classifyIntent({
      messageText: '加搜索',
      conversationMessages: [],
      lastCrState: null,
    });
    expect(d.mode).toBe('new_cr');
  });

  it('override forces mode and short-circuits heuristic', () => {
    const d = classifyIntent({
      messageText: '字号大一点',
      conversationMessages: msgs(['user', '改红']),
      lastCrState: 'preview-ready',
      override: 'new_cr',
    });
    expect(d.mode).toBe('new_cr');
    expect(d.confidence).toBe(1);
    expect(isUnsure(d)).toBe(false);
  });

  it('ambiguous one-word message → low confidence (is_unsure)', () => {
    const d = classifyIntent({
      messageText: '再来',
      conversationMessages: msgs(['user', '改红']),
      lastCrState: 'preview-ready',
    });
    expect(d.confidence).toBeLessThan(0.6);
    expect(isUnsure(d)).toBe(true);
  });

  it('isUnsure: high confidence → false', () => {
    expect(isUnsure({ mode: 'new_cr', confidence: 0.95, reason: '' })).toBe(false);
  });

  it('isUnsure: low confidence → true', () => {
    expect(isUnsure({ mode: 'refine_cr', confidence: 0.4, reason: '' })).toBe(true);
  });
});
