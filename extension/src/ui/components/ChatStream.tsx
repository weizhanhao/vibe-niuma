// Plan 10 Task 15: ChatStream —— cursor-like 主体「消息流」。
//
// 业务员视角：所有 message（user / ai / summary）按时间倒序铺在主体；
// user message 若关联了 CR（user 发完 send → 后端起 pipeline → cr_id 写回），
// 在该 user message 下面挂一张 InlineCard 显示该 CR 的实时状态 + preview link。
import React, { useEffect, useRef } from 'react';
import type { ConversationMessage, RequestStateMirror } from '../../lib/types';
import { InlineCard } from './InlineCard';

interface Props {
  messages: ConversationMessage[];
  mirrors: Record<string, RequestStateMirror>;
}

export function ChatStream({ messages, mirrors }: Props): React.ReactElement {
  // 实际 scroll 容器是 .app-body（父级），不是 ChatStream 自己。用 bottom
  // sentinel + scrollIntoView 不依赖具体哪一层滚动，永远把哨兵推到可视区。
  const bottomRef = useRef<HTMLDivElement>(null);

  const scrollToBottomIfNearBottom = () => {
    const sentinel = bottomRef.current;
    if (!sentinel) return;
    // 找最近的可滚动祖先；如果它已经接近底部才滚（不打断业务员翻阅历史）
    let scroller: HTMLElement | null = sentinel.parentElement;
    while (scroller) {
      const overflowY = getComputedStyle(scroller).overflowY;
      if (overflowY === 'auto' || overflowY === 'scroll') break;
      scroller = scroller.parentElement;
    }
    if (!scroller) return;
    const nearBottom =
      scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 120;
    if (nearBottom) {
      sentinel.scrollIntoView({ behavior: 'smooth', block: 'end' });
    }
  };

  // 新消息进来 / 最后一条内容变 / 流式日志总数变 → 跟着滚
  const lastContent = messages[messages.length - 1]?.content ?? '';
  const totalLogs = Object.values(mirrors).reduce((s, m) => s + m.logs.length, 0);
  useEffect(() => {
    scrollToBottomIfNearBottom();
  }, [messages.length, lastContent, totalLogs]);

  if (messages.length === 0) {
    return (
      <div className="chat-stream chat-stream--empty">
        <p>还没有对话。在下方输入框开聊。</p>
        <div ref={bottomRef} />
      </div>
    );
  }
  return (
    <div className="chat-stream" role="log" aria-live="polite">
      {messages.map((m) => {
        if (m.type === 'summary') {
          return (
            <div key={m.id} className="chat-stream__summary">
              <span className="chat-stream__summary-icon" aria-hidden="true">↑</span>
              <span>
                合并了 {m.replaces_count ?? '?'} 条历史
                {m.replaces_token_estimate
                  ? `（约 ${m.replaces_token_estimate} tokens）`
                  : ''}
              </span>
            </div>
          );
        }
        const mirror = m.cr_id ? mirrors[m.cr_id] : undefined;
        const atts = m.attachments ?? [];
        return (
          <div key={m.id} className={`chat-stream__msg chat-stream__msg--${m.type}`}>
            <div className="chat-stream__bubble">
              {atts.length > 0 && (
                <div className="chat-stream__attachments">
                  {atts.map((a, i) => {
                    const isImage = a.mime?.startsWith('image/');
                    if (!isImage) {
                      return (
                        <span key={i} className="chat-stream__att chat-stream__att--file">
                          📎 {a.name ?? a.kind}
                        </span>
                      );
                    }
                    return (
                      <img
                        key={i}
                        className="chat-stream__att-img"
                        src={`data:${a.mime};base64,${a.b64}`}
                        alt={a.name ?? '截图'}
                        loading="lazy"
                      />
                    );
                  })}
                </div>
              )}
              {m.content}
            </div>
            {mirror && (
              <InlineCard mirror={mirror} crMode={m.cr_mode ?? 'new_cr'} />
            )}
          </div>
        );
      })}
      <div ref={bottomRef} />
    </div>
  );
}
