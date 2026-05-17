// Plan 10 Task 15+ A2: InlineCard —— chat stream 里挂在 user message 下的 CR 状态卡。
//
// 业务员视角：发了「加搜索」→ 输入框下方实时长一张「正在改代码 → 构建预览 → 预览就绪」卡，
// 卡里有「打开预览」按钮，业务员可以一边看 chat 一边看进度。
// A2：跑中的 CR 显示**实时活动流**（最后 6 行 SSE log），有 spinner，
// 业务员能看到 AI 在干活，不会以为程序卡死。
import React, { useEffect, useRef } from 'react';
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

// 跑中状态 —— 这些 state 显示活动流；终态收起。
const RUNNING_STATES: ReadonlySet<ChangeRequestState> = new Set([
  'created', 'clarifying', 'located', 'coding', 'building',
]);

// 业务化日志行：去掉技术噪音前缀，只留业务员看得懂的部分
function humanize(line: string): string {
  return line
    .replace(/^\[[\d\s:.-]+\]\s*/, '')   // 去时间戳
    .replace(/^\s*npm\s.*$/, '$&')        // 保留 npm 行原样
    .trim();
}

export function InlineCard({ mirror, crMode }: Props): React.ReactElement {
  const stateText = STATE_LABEL[mirror.state] ?? mirror.state;
  const modeBadge = MODE_LABEL[crMode] ?? crMode;
  const isRunning = RUNNING_STATES.has(mirror.state);
  const tailLogs = isRunning ? mirror.logs.slice(-6) : [];
  const logRef = useRef<HTMLDivElement>(null);

  // 新 log 来了自动滚到底（cursor-like）
  useEffect(() => {
    if (logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight;
    }
  }, [tailLogs.length]);

  return (
    <div className={`inline-card inline-card--${mirror.state}${isRunning ? ' is-running' : ''}`}>
      <div className="inline-card__head">
        <span className="inline-card__mode">{modeBadge}</span>
        <span className="inline-card__state">
          {isRunning && <span className="inline-card__spinner" aria-hidden="true" />}
          {stateText}
        </span>
      </div>

      {isRunning && tailLogs.length > 0 && (
        <div ref={logRef} className="inline-card__activity" role="log" aria-live="polite">
          {tailLogs.map((entry, i) => (
            <div key={`${entry.ts}-${i}`} className="inline-card__activity-line">
              {humanize(entry.line)}
            </div>
          ))}
        </div>
      )}

      {isRunning && tailLogs.length === 0 && (
        <div className="inline-card__activity inline-card__activity--placeholder">
          准备中...
        </div>
      )}

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
