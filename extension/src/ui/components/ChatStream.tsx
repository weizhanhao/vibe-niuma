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
  const containerRef = useRef<HTMLDivElement>(null);

  // 新消息进来自动滚到底；只有当用户已经在底部附近时才滚（不打断主动滚阅读历史）
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    if (nearBottom) {
      el.scrollTop = el.scrollHeight;
    }
  }, [messages.length, messages[messages.length - 1]?.content]);

  // 也跟 mirror.logs 数量变化滚（流式日志增长时跟）
  const totalLogs = Object.values(mirrors).reduce((s, m) => s + m.logs.length, 0);
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    if (nearBottom) el.scrollTop = el.scrollHeight;
  }, [totalLogs]);

  if (messages.length === 0) {
    return (
      <div ref={containerRef} className="chat-stream chat-stream--empty">
        <p>还没有对话。在下方输入框开聊。</p>
      </div>
    );
  }
  return (
    <div ref={containerRef} className="chat-stream" role="log" aria-live="polite">
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
        return (
          <div key={m.id} className={`chat-stream__msg chat-stream__msg--${m.type}`}>
            <div className="chat-stream__bubble">
              {m.content}
            </div>
            {mirror && (
              <InlineCard mirror={mirror} crMode={m.cr_mode ?? 'new_cr'} />
            )}
          </div>
        );
      })}
    </div>
  );
}
