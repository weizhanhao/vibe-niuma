// Plan 10 Task 14: cursor-like 顶部对话 tab 栏。
//
// 业务员视角：「上面每个 tab 是一个会话，+ 号是加一个新的回话，时钟代表
// 选择历史回话」。点 tab 切换；× 关闭单个 tab；+ 新建空对话；时钟 → 弹历史下拉。
import React from 'react';

export interface AgentTab {
  id: string;
  title: string;
}

interface Props {
  tabs: AgentTab[];
  activeTabId: string | null;
  onActivate: (id: string) => void;
  onClose: (id: string) => void;
  onNew: () => void;
  onShowHistory: () => void;
}

const UNTITLED = '（未命名）';

export function AgentTabBar({
  tabs, activeTabId, onActivate, onClose, onNew, onShowHistory,
}: Props): React.ReactElement {
  return (
    <div className="agent-tab-bar" role="tablist" aria-label="对话 tab">
      <div className="agent-tab-bar__tabs">
        {tabs.length === 0 && (
          <div className="agent-tab-bar__empty">还没打开任何会话</div>
        )}
        {tabs.map((t) => {
          const title = t.title.trim() || UNTITLED;
          const isActive = t.id === activeTabId;
          return (
            <div
              key={t.id}
              role="tab"
              aria-current={isActive ? 'page' : undefined}
              aria-label={title}
              className={`agent-tab${isActive ? ' is-active' : ''}`}
              onClick={() => { if (!isActive) onActivate(t.id); }}
              tabIndex={0}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault();
                  onActivate(t.id);
                }
              }}
            >
              <span className="agent-tab__title">{title}</span>
              <button
                type="button"
                aria-label={`关闭 ${title}`}
                className="agent-tab__close"
                onClick={(e) => {
                  e.stopPropagation();
                  onClose(t.id);
                }}
              >
                ×
              </button>
            </div>
          );
        })}
      </div>
      <div className="agent-tab-bar__actions">
        <button
          type="button"
          aria-label="新建对话"
          className="agent-tab-bar__icon-btn"
          onClick={onNew}
          title="新建对话"
        >
          +
        </button>
        <button
          type="button"
          aria-label="历史对话"
          className="agent-tab-bar__icon-btn"
          onClick={onShowHistory}
          title="历史对话"
        >
          🕐
        </button>
      </div>
    </div>
  );
}
