// Plan 10 Task 15: ChatStream —— cursor-like 主体「消息流」。
//
// 业务员视角：所有 message（user / ai / summary）按时间倒序铺在主体；
// user message 若关联了 CR（user 发完 send → 后端起 pipeline → cr_id 写回），
// 在该 user message 下面挂一张 InlineCard 显示该 CR 的实时状态 + preview link。
import React, { useEffect, useRef, useState } from 'react';
import type { Attachment, ConversationMessage, RequestStateMirror } from '../../lib/types';
import { InlineCard } from './InlineCard';

interface Props {
  messages: ConversationMessage[];
  mirrors: Record<string, RequestStateMirror>;
}

export function ChatStream({ messages, mirrors }: Props): React.ReactElement {
  // 同界面 lightbox —— Chrome 扩展不让 data: URL 开新标签，所以做 in-panel modal
  // （和 ChatInputBar 的 lightbox 同款，UX 一致）
  const [lightboxAtt, setLightboxAtt] = useState<Attachment | null>(null);
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
        // 只 user message 挂 InlineCard —— 后端在 preview-ready 时给同一个 CR
        // 同时 append 一条带 cr_id 的 AI message「改完了 → 预览就绪 ...」。
        // 那条 AI 已经被降级成 footnote（见下 isCrSuccessFootnote），它不该再
        // mount 第二张 InlineCard 跟 user message 的卡片重复。
        const mirror = (m.type === 'user' && m.cr_id) ? mirrors[m.cr_id] : undefined;
        const atts = m.attachments ?? [];
        // 后端 pipeline 在 preview-ready 时往 conversation append 一条形如
        //   「改完了 → 预览就绪 http://... \n 分支 cr/... commit abcdef」
        // 的 ai 消息。它和上面的 InlineCard 信息完全重复 —— 视觉上降级成 inline 脚注，
        // 不再当独立 bubble 漂在 InlineCard 下面（用户反馈：「布局有些违和」）。
        const isCrSuccessFootnote = m.type === 'ai'
          && typeof m.content === 'string'
          && m.content.startsWith('改完了 → 预览就绪');
        return (
          <div key={m.id} className={`chat-stream__msg chat-stream__msg--${m.type}`}>
            <div
              className="chat-stream__bubble"
              {...(isCrSuccessFootnote ? { 'data-footer-style': 'success' } : {})}
            >
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
                        onClick={() => setLightboxAtt(a)}
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
            {/* 乐观 user message 发出后到拿到 cr_id（intent_classifier 1-3s）
                之间不能让业务员面对空白；App.tsx 给这条 message 打 meta.pending，
                ChatStream 渲染一个「思考中...」占位，spinner + 一行字。
                onSubmitted 后 App 清掉 meta.pending → InlineCard 接管。 */}
            {(m.meta as { pending?: boolean } | undefined)?.pending && (
              <div className="chat-stream__thinking" role="status" aria-live="polite">
                <span className="chat-stream__thinking-spinner" aria-hidden="true" />
                <span>思考中...</span>
              </div>
            )}
          </div>
        );
      })}
      <div ref={bottomRef} />

      {lightboxAtt && (
        <div
          className="attachment-lightbox"
          role="dialog"
          aria-label="附件原图"
          onClick={() => setLightboxAtt(null)}
          onKeyDown={(e) => { if (e.key === 'Escape') setLightboxAtt(null); }}
          tabIndex={-1}
        >
          <button
            type="button"
            className="attachment-lightbox__close"
            aria-label="关闭"
            onClick={(e) => { e.stopPropagation(); setLightboxAtt(null); }}
          >×</button>
          <img
            src={`data:${lightboxAtt.mime};base64,${lightboxAtt.b64}`}
            alt={lightboxAtt.name ?? '附件原图'}
            onClick={(e) => e.stopPropagation()}
          />
        </div>
      )}
    </div>
  );
}
