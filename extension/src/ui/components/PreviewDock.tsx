// Plan 9 Task 9: 预览浮卡 —— 底部 sticky dock。
//
// 触发：active CR 的 previewUrl 存在（preview-ready / merged / discarded）。
// 展示：cr/分支 chip + URL + [↗ 预览] + [丢弃] + [合并 →]。
// 业务员在输入框继续聊不阻塞浮卡，浮卡始终显示「最新一条带 preview 的 CR」。
//
// merged/discarded 状态加 × 关闭按钮：业务员看完决定收起来，
// onClose 是 caller 控制（local state 或持久化都行）。
import React from 'react';
import { MSG, type Message } from '../../lib/messages';
import type { RequestStateMirror } from '../../lib/types';

const send = (msg: Message) => chrome.runtime.sendMessage(msg);

interface Props {
  state: RequestStateMirror | null;
  onClose?: () => void;
}

export function PreviewDock({ state, onClose }: Props) {
  if (!state || !state.previewUrl) return null;
  if (state.state !== 'preview-ready' && state.state !== 'merged' && state.state !== 'discarded') {
    return null;
  }

  const isMerged = state.state === 'merged';
  const isDiscarded = state.state === 'discarded';
  const showClose = (isMerged || isDiscarded) && !!onClose;
  const merge = () => send({ type: MSG.MERGE, requestId: state.id });
  const discard = () => {
    if (confirm('确定丢弃？分支会保留方便事后查看。')) {
      send({ type: MSG.DISCARD, requestId: state.id });
    }
  };
  const open = () => chrome.tabs.create({ url: state.previewUrl! });

  // branch 完整 SHA（cr/3713c774e3c14a0d92cbedd2e2c4ad6b）在窄侧栏会撑爆，
  // 显示短哈希 cr/3713c774…，完整 SHA 放 title 鼠标 hover 看。
  const branchShort = state.branch
    ? (state.branch.length > 14 ? `${state.branch.slice(0, 11)}…` : state.branch)
    : null;

  return (
    <div className={`preview-dock ${isMerged ? 'is-merged' : ''} ${isDiscarded ? 'is-discarded' : ''}`}>
      <div className="preview-dock-top">
        {branchShort && (
          <span className="preview-dock-branch" title={state.branch ?? undefined}>
            {branchShort}
          </span>
        )}
        <a
          className="preview-dock-url"
          href={state.previewUrl}
          onClick={(e) => { e.preventDefault(); open(); }}
          title={state.previewUrl}
        >
          {state.previewUrl}
        </a>
        {showClose && (
          <button
            type="button"
            className="preview-dock-close"
            onClick={onClose}
            title="关闭浮卡"
            aria-label="关闭浮卡"
          >
            ×
          </button>
        )}
      </div>
      {!isMerged && !isDiscarded && (
        <div className="preview-dock-actions">
          <button
            type="button"
            className="preview-dock-btn preview-dock-btn--ghost"
            onClick={open}
            title="新标签打开预览"
          >
            ↗ 预览
          </button>
          <span className="preview-dock-actions__spacer" />
          <button
            type="button"
            className="preview-dock-btn preview-dock-btn--danger"
            onClick={discard}
          >
            丢弃
          </button>
          <button
            type="button"
            className="preview-dock-btn preview-dock-btn--primary"
            onClick={merge}
          >
            合并 →
          </button>
        </div>
      )}
      {isMerged && (
        <div className="preview-dock-state preview-dock-state-merged">✓ 已合并到 main</div>
      )}
      {isDiscarded && (
        <div className="preview-dock-state preview-dock-state-discarded">已丢弃（分支保留）</div>
      )}
    </div>
  );
}
