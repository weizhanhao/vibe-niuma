// Plan 10 Task 15: InlineCard —— chat stream 里挂在 user message 下的 CR 状态卡。
//
// 业务员视角：发了「加搜索」→ 输入框下方实时长一张「正在改代码 → 构建预览 → 预览就绪」卡，
// 卡里有「打开预览」按钮，业务员可以一边看 chat 一边看进度。
import React from 'react';
import type { ChangeRequestState, IntentMode, RequestStateMirror } from '../../lib/types';

interface Props {
  mirror: RequestStateMirror;
  crMode: IntentMode;
}

const STATE_LABEL: Record<ChangeRequestState, string> = {
  created: '排队中',
  clarifying: '澄清中',
  located: '定位完成',
  coding: 'AI 改代码中',
  building: '构建预览',
  'preview-ready': '预览就绪',
  merged: '已合并',
  failed: '失败',
  expired: '已过期',
  discarded: '已丢弃',
};

const MODE_LABEL: Record<IntentMode, string> = {
  new_cr: '新需求',
  refine_cr: '续改',
  chat_only: '聊天',
};

export function InlineCard({ mirror, crMode }: Props): React.ReactElement {
  const stateText = STATE_LABEL[mirror.state] ?? mirror.state;
  const modeBadge = MODE_LABEL[crMode] ?? crMode;
  return (
    <div className={`inline-card inline-card--${mirror.state}`}>
      <div className="inline-card__head">
        <span className="inline-card__mode">{modeBadge}</span>
        <span className="inline-card__state">{stateText}</span>
      </div>
      {mirror.failPhase && (
        <div className="inline-card__fail">
          失败：{mirror.failPhase} — {mirror.failReason ?? '原因未知'}
        </div>
      )}
      {mirror.previewUrl && (
        <a
          href={mirror.previewUrl}
          target="_blank"
          rel="noreferrer noopener"
          className="inline-card__preview"
        >
          ↗ 打开预览
        </a>
      )}
    </div>
  );
}
