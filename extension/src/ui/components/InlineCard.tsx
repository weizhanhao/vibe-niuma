// Plan 10 Task 15+ A2: InlineCard —— chat stream 里挂在 user message 下的 CR 状态卡。
//
// 业务员视角：发了「加搜索」→ 输入框下方实时长一张「正在改代码 → 构建预览 → 预览就绪」卡，
// 卡里有「打开预览」按钮，业务员可以一边看 chat 一边看进度。
// A2：跑中的 CR 显示**实时活动流**（最后 6 行 SSE log），有 spinner，
// 业务员能看到 AI 在干活，不会以为程序卡死。
import React, { useEffect, useRef, useState } from 'react';
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

// 跑中状态 —— 这些 state 显示活动流（自动展开 + spinner）；终态收起为可展开的「查看运行日志」。
const RUNNING_STATES: ReadonlySet<ChangeRequestState> = new Set([
  'created', 'clarifying', 'located', 'coding', 'building',
]);

// 业务化日志行：去掉技术噪音前缀，只留业务员看得懂的部分
function humanize(line: string): string {
  return line
    .replace(/^\[[\d\s:.-]+\]\s*/, '')   // 去时间戳
    .trim();
}

// 计时器格式化：< 60s 用 12s；>= 60s 用 m:ss
function formatElapsed(ms: number): string {
  if (ms < 1000) return '';
  const totalSec = Math.floor(ms / 1000);
  if (totalSec < 60) return `${totalSec}s`;
  const m = Math.floor(totalSec / 60);
  const s = totalSec % 60;
  return `${m}:${String(s).padStart(2, '0')}`;
}

// 服务器写 log 用 `datetime.utcnow().isoformat()`，**没 Z 后缀**。
// JS `new Date('...')` 看到没时区的 ISO 字符串当作本地时间，UTC+8 会凭空多 8h。
// 兜底：检测到 naive ISO 时手动补 Z 让 JS 当 UTC 解析。
// （服务端真修 = 改 isoformat 时带时区；这里是客户端最后一道防线。）
function parseTimestamp(ts: string | undefined): number {
  if (!ts) return NaN;
  const hasTz = /Z|[+-]\d{2}:?\d{2}$/.test(ts);
  return new Date(hasTz ? ts : ts + 'Z').getTime();
}

export function InlineCard({ mirror, crMode }: Props): React.ReactElement {
  const stateText = STATE_LABEL[mirror.state] ?? mirror.state;
  const modeBadge = MODE_LABEL[crMode] ?? crMode;
  const isRunning = RUNNING_STATES.has(mirror.state);
  const hasLogs = mirror.logs.length > 0;
  // 跑中默认展开；终态默认收起（业务员想查再点）
  const [expanded, setExpanded] = useState(isRunning);
  const logRef = useRef<HTMLDivElement>(null);

  // 跑中 → 跟随状态自动展开；终态 → 收起一次后业务员可以再点开
  useEffect(() => {
    if (isRunning) setExpanded(true);
  }, [isRunning]);

  // ── 运行时间 ──
  // 起点：首条 log 时间戳（CR 第一次出活动）；没 log 用 lastActivity 兜底
  // 终点：跑中 → 当前时间（每秒 tick）；终态 → 最后一条 log 时间戳
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!isRunning) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [isRunning]);
  const elapsed = (() => {
    // 起点优先用 mirror.startedAt（独立于 logs FIFO trim 永不失真）；
    // 老版本快照可能没这字段，退到 logs[0].ts → lastActivity 三段兜底。
    const startTs = mirror.startedAt ?? mirror.logs[0]?.ts ?? mirror.lastActivity;
    if (!startTs) return 0;
    const start = parseTimestamp(startTs);
    if (Number.isNaN(start)) return 0;
    const end = isRunning
      ? now
      : parseTimestamp(mirror.logs[mirror.logs.length - 1]?.ts ?? startTs);
    if (Number.isNaN(end)) return 0;
    return Math.max(0, end - start);
  })();
  const elapsedLabel = formatElapsed(elapsed);

  // 新 log 来了自动滚到底（cursor-like）
  useEffect(() => {
    if (logRef.current && expanded) {
      logRef.current.scrollTop = logRef.current.scrollHeight;
    }
  }, [mirror.logs.length, expanded]);

  const tailLogs = mirror.logs.slice(-6);

  return (
    <div className={`inline-card inline-card--${mirror.state}${isRunning ? ' is-running' : ''}`}>
      <div className="inline-card__head">
        <span className="inline-card__mode">{modeBadge}</span>
        <span className="inline-card__state">
          {isRunning && <span className="inline-card__spinner" aria-hidden="true" />}
          {stateText}
        </span>
        {elapsedLabel && (
          <span
            className="inline-card__elapsed"
            title={isRunning ? '运行时间（实时）' : '总耗时'}
          >
            {elapsedLabel}
          </span>
        )}
      </div>

      {/* 跑中 + 已展开 + 有 logs → 显示流；跑中无 logs → 准备中占位 */}
      {expanded && hasLogs && (
        <div ref={logRef} className="inline-card__activity" role="log" aria-live="polite">
          {tailLogs.map((entry, i) => (
            <div key={`${entry.ts}-${i}`} className="inline-card__activity-line">
              {humanize(entry.line)}
            </div>
          ))}
        </div>
      )}
      {isRunning && !hasLogs && (
        <div className="inline-card__activity inline-card__activity--placeholder">
          准备中...
        </div>
      )}
      {/* 终态 + 有 logs + 收起 → 显示「查看运行日志 ▾」按钮 */}
      {!isRunning && hasLogs && !expanded && (
        <button
          type="button"
          className="inline-card__activity-toggle"
          onClick={() => setExpanded(true)}
        >
          查看运行日志（{mirror.logs.length} 行）▾
        </button>
      )}
      {/* 终态 + 已展开 → 显示「收起 ▴」 */}
      {!isRunning && hasLogs && expanded && (
        <button
          type="button"
          className="inline-card__activity-toggle"
          onClick={() => setExpanded(false)}
        >
          收起日志 ▴
        </button>
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
