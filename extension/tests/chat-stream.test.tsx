// Plan 10 Task 15: ChatStream —— cursor-like 主体「消息流」。
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { ChatStream } from '../src/ui/components/ChatStream';
import type { ConversationMessage, RequestStateMirror } from '../src/lib/types';

function mkMsg(t: 'user' | 'ai' | 'summary', content: string,
               extras: Partial<ConversationMessage> = {}): ConversationMessage {
  return {
    id: `m-${Math.random().toString(36).slice(2, 8)}`,
    ts: '2026-05-17T10:00:00Z',
    type: t, content, ...extras,
  };
}

function mkMirror(id: string,
                  opts: Partial<RequestStateMirror> = {}): RequestStateMirror {
  return {
    id, state: 'preview-ready', url: 'http://demo',
    requestText: opts.requestText ?? '加搜索',
    branch: 'cr/abc', previewUrl: 'http://localhost:5100',
    failPhase: null, failReason: null,
    pendingQuestion: null, pendingVariants: null,
    logs: [], lastActivity: '2026-05-17T10:30:00Z',
    ...opts,
  };
}

describe('ChatStream', () => {
  it('renders empty state when no messages', () => {
    render(<ChatStream messages={[]} mirrors={{}} />);
    expect(screen.getByText(/还没有对话/)).toBeInTheDocument();
  });

  it('renders user / ai messages with role labels', () => {
    const msgs = [
      mkMsg('user', '加个搜索'),
      mkMsg('ai', '好，正在处理'),
    ];
    render(<ChatStream messages={msgs} mirrors={{}} />);
    expect(screen.getByText('加个搜索')).toBeInTheDocument();
    expect(screen.getByText('好，正在处理')).toBeInTheDocument();
  });

  it('renders summary as a CompactedRangeNotice-like collapsed item', () => {
    render(<ChatStream
      messages={[
        mkMsg('summary', '压缩了 3 条历史', {
          replaces_count: 3, replaces_token_estimate: 1500,
        }),
      ]}
      mirrors={{}}
    />);
    expect(screen.getByText(/3 条/)).toBeInTheDocument();
  });

  it('renders InlineCard for user messages with cr_id pointing to a mirror', () => {
    const userMsg = mkMsg('user', '加搜索', { cr_id: 'cr-1', cr_mode: 'new_cr' });
    const mirror = mkMirror('cr-1');
    render(<ChatStream messages={[userMsg]} mirrors={{ 'cr-1': mirror }} />);
    // 「打开预览」link + 「预览就绪」state 双匹配；任一即可
    expect(screen.getAllByText(/预览/).length).toBeGreaterThan(0);
  });

  it('does not render InlineCard when cr_id missing from mirrors', () => {
    const userMsg = mkMsg('user', '加搜索', { cr_id: 'cr-missing' });
    render(<ChatStream messages={[userMsg]} mirrors={{}} />);
    expect(screen.getByText('加搜索')).toBeInTheDocument();
    expect(screen.queryAllByText(/预览/).length).toBe(0);
  });

  it('renders messages in order top → bottom', () => {
    const msgs = [
      mkMsg('user', 'AAA'),
      mkMsg('ai', 'BBB'),
      mkMsg('user', 'CCC'),
    ];
    render(<ChatStream messages={msgs} mirrors={{}} />);
    const rendered = screen.getAllByText(/^[ABC]{3}$/).map((e) => e.textContent);
    expect(rendered).toEqual(['AAA', 'BBB', 'CCC']);
  });
});
