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

  return (
    <div className={`preview-dock ${isMerged ? 'is-merged' : ''} ${isDiscarded ? 'is-discarded' : ''}`}>
      <div className="preview-dock-top">
        {state.branch && <span className="branch-chip">{state.branch}</span>}
        <span className="preview-dock-url" title={state.previewUrl}>
          {state.previewUrl}
        </span>
        <button
          className="btn btn-small btn-secondary"
          onClick={open}
          title="新标签打开预览"
          aria-label="新标签打开预览"
        >
          ↗ 预览
        </button>
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
          <button className="btn btn-small btn-danger" onClick={discard}>丢弃</button>
          <button className="btn btn-small btn-primary" onClick={merge}>合并 →</button>
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
