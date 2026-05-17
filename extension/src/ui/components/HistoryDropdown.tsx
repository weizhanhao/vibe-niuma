// Plan 10 Task 14: HistoryDropdown —— 时钟图标弹的历史会话选择面板。
//
// 业务员视角：「时钟代表选择历史回话，点击时钟可以有选择历史会话，
// 用户选择后可以在这个对话中继续」。
import React from 'react';
import type { Conversation } from '../../lib/conversations';

interface Props {
  /** null 表示加载中（loading）；空 array 表示真没有历史。 */
  items: Conversation[] | null;
  onPick: (convId: string) => void;
  onClose: () => void;
}

const UNTITLED = '（未命名）';

function relativeTime(iso: string | null): string {
  if (!iso) return '';
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return '';
  const delta = Date.now() - t;
  const min = Math.floor(delta / 60_000);
  if (min < 1) return '刚刚';
  if (min < 60) return `${min} 分钟前`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr} 小时前`;
  const day = Math.floor(hr / 24);
  if (day < 7) return `${day} 天前`;
  return new Date(t).toLocaleDateString();
}

export function HistoryDropdown({ items, onPick, onClose }: Props): React.ReactElement {
  if (items === null) {
    return (
      <div className="history-dropdown" role="dialog" aria-label="历史对话">
        <div className="history-dropdown__hint">加载中...</div>
      </div>
    );
  }
  if (items.length === 0) {
    return (
      <div className="history-dropdown" role="dialog" aria-label="历史对话">
        <div className="history-dropdown__hint">还没有历史对话</div>
      </div>
    );
  }
  return (
    <div className="history-dropdown" role="dialog" aria-label="历史对话">
      <ul className="history-dropdown__list">
        {items.map((c) => {
          const title = (c.title || '').trim() || UNTITLED;
          return (
            <li key={c.id}>
              <button
                type="button"
                className="history-dropdown__item"
                onClick={() => {
                  onPick(c.id);
                  onClose();
                }}
              >
                <span className="history-dropdown__title">{title}</span>
                <span className="history-dropdown__time">
                  {relativeTime(c.updated_at)}
                </span>
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
