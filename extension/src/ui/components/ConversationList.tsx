// Phase E：侧边栏多对话列表（Cursor 式）。
// - 每行：状态点 + 需求摘要 + 状态 chip + hover「×」删除（带 confirm）
// - 顶部「+ 新对话」按钮：把 active 切到 null（让 App 渲染 CapturePanel）
// - 点非 active 行 → 切换 active
// 列表数据由 useMirrors 提供，按 lastActivity 降序。
import React from 'react';
import { MSG, type Message } from '../../lib/messages';
import type { ChangeRequestState, RequestStateMirror } from '../../lib/types';

const send = (msg: Message) => chrome.runtime.sendMessage(msg);

const STATE_CHIP_LABEL: Record<ChangeRequestState, string> = {
  'created': '排队中',
  'clarifying': '澄清中',
  'located': '定位完成',
  'coding': 'AI 改代码中',
  'building': '构建预览',
  'preview-ready': '预览就绪',
  'merged': '已合并',
  'failed': '失败',
  'expired': '已过期',
  'discarded': '已丢弃',
};

const STATE_TONE: Record<ChangeRequestState, string> = {
  'created': 'tone-muted',
  'clarifying': 'tone-active',
  'located': 'tone-active',
  'coding': 'tone-active',
  'building': 'tone-active',
  'preview-ready': 'tone-accent',
  'merged': 'tone-success',
  'failed': 'tone-danger',
  'expired': 'tone-muted',
  'discarded': 'tone-muted',
};

function summarize(text: string, max = 30): string {
  const trimmed = text.trim();
  if (!trimmed) return '（未填需求）';
  if (trimmed.length <= max) return trimmed;
  return `${trimmed.slice(0, max)}…`;
}

interface ConversationListProps {
  mirrors: RequestStateMirror[]; // 已按 lastActivity 降序
  activeId: string | null;
}

export function ConversationList({ mirrors, activeId }: ConversationListProps) {
  const onNew = () => send({ type: MSG.NEW_CONVERSATION });
  const onSelect = (id: string) => {
    if (id === activeId) return;
    send({ type: MSG.SET_ACTIVE, id });
  };
  const onDelete = (e: React.MouseEvent, id: string) => {
    e.stopPropagation();
    if (confirm('确定删除这条对话？数据将丢失。')) {
      send({ type: MSG.DELETE_CONVERSATION, id });
    }
  };

  return (
    <nav className="conv-list" aria-label="历史对话">
      <div className="conv-list-head">
        <span className="conv-list-title">历史对话</span>
        <button
          className="conv-list-new"
          onClick={onNew}
          aria-label="新对话"
          type="button"
        >+ 新对话</button>
      </div>
      {mirrors.length === 0 ? (
        <div className="conv-list-empty">还没有任何对话。点「+ 新对话」开始。</div>
      ) : (
        <ul className="conv-rows" role="list">
          {mirrors.map((m) => {
            const isActive = m.id === activeId;
            const tone = STATE_TONE[m.state] ?? 'tone-muted';
            return (
              <li
                key={m.id}
                className={`conv-row ${isActive ? 'is-active' : ''}`}
                onClick={() => onSelect(m.id)}
                role="button"
                tabIndex={0}
                aria-current={isActive ? 'true' : undefined}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    onSelect(m.id);
                  }
                }}
              >
                <span className={`conv-dot ${tone}`} aria-hidden="true" />
                <span className="conv-summary">{summarize(m.requestText)}</span>
                <span className={`conv-chip ${tone}`}>{STATE_CHIP_LABEL[m.state]}</span>
                <button
                  type="button"
                  className="conv-del"
                  aria-label="删除对话"
                  onClick={(e) => onDelete(e, m.id)}
                >×</button>
              </li>
            );
          })}
        </ul>
      )}
    </nav>
  );
}
