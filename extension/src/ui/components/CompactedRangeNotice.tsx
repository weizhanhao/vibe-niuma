// Plan 9 Task 11: CompactedRangeNotice —— chat 里折叠条「已折叠 N 条历史 ▾」
//
// 用法（ChatPanel 渲染 messages 时，遇 type=summary 用这条折叠条替代展开消息）：
//   <CompactedRangeNotice replacesCount={47} replacesTokenEstimate={12345}>
//     {olderMessages.map(m => <ChatBubble key={m.ts} msg={m} />)}
//   </CompactedRangeNotice>
//
// children 是被折叠的老消息原文 —— 平时不渲染，业务员主动点开才显示。
// 这样 React tree 平时轻、需要回看历史时即点即开。
import { useState, type ReactNode } from 'react';

interface Props {
  replacesCount: number;
  replacesTokenEstimate: number;
  initiallyOpen?: boolean;
  children?: ReactNode;
}

function formatTokens(n: number): string {
  if (n >= 1000) {
    const k = (n / 1000).toFixed(1).replace(/\.0$/, '');
    return `~${k}k tokens`;
  }
  return `~${n} tokens`;
}

export function CompactedRangeNotice({
  replacesCount,
  replacesTokenEstimate,
  initiallyOpen = false,
  children,
}: Props) {
  const [open, setOpen] = useState<boolean>(initiallyOpen);
  const label = `已折叠 ${replacesCount} 条历史${
    replacesTokenEstimate > 0 ? `（${formatTokens(replacesTokenEstimate)}）` : ''
  }`;
  return (
    <div className="compacted-range">
      <button
        type="button"
        className="compacted-range-toggle"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <span className="compacted-range-arrow" aria-hidden="true">{open ? '▴' : '▾'}</span>
        <span className="compacted-range-label">{label}</span>
      </button>
      {open && children ? (
        <div className="compacted-range-drawer">{children}</div>
      ) : null}
    </div>
  );
}
