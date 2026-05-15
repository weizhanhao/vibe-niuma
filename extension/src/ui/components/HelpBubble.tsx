// Plan 6 · Task 6: 通用「?」帮助气泡。
// 用法：<HelpBubble content={markdownString} ariaLabel="字段名说明" />
//
// 设计要点：
//   - popover 用 ReactDOM portal 挂 document.body，避开父级 overflow:hidden
//   - popover 位置：基于触发器 getBoundingClientRect()，默认在右下方；
//     如果右侧空间不够（<= 320 + 8 padding）则改成左下；底部不够则上翻。
//   - 点击 popover 外部 / 按 Esc / 再点触发器 → 关闭
//   - 外链一律 target="_blank" rel="noopener noreferrer"
import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import ReactMarkdown from 'react-markdown';
import './HelpBubble.css';

interface HelpBubbleProps {
  content: string;
  ariaLabel: string;
}

interface PopoverPos {
  top: number;
  left: number;
}

const POPOVER_MAX_WIDTH = 320;
const POPOVER_GUTTER = 8;

export function HelpBubble({ content, ariaLabel }: HelpBubbleProps) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<PopoverPos>({ top: 0, left: 0 });
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const popoverRef = useRef<HTMLDivElement | null>(null);

  // 计算 popover 位置：触发器右下方，溢出则翻转。
  // 用 useLayoutEffect 避免位置跳变。
  useLayoutEffect(() => {
    if (!open || !triggerRef.current) return;
    const rect = triggerRef.current.getBoundingClientRect();
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    // 优先把左边对齐到触发器左侧 + 一点偏移
    let left = rect.left;
    if (left + POPOVER_MAX_WIDTH + POPOVER_GUTTER > vw) {
      left = Math.max(POPOVER_GUTTER, vw - POPOVER_MAX_WIDTH - POPOVER_GUTTER);
    }
    // 默认在下方；下方不够上翻
    let top = rect.bottom + 6;
    // popover 实际高度此时未渲染，估个保守值；溢出 25% 视口高度就上翻
    if (top + vh * 0.35 > vh) {
      top = Math.max(POPOVER_GUTTER, rect.top - 6 - vh * 0.3);
    }
    // 加 scroll offset（rect 是 viewport，portal 用 document 坐标系）
    setPos({
      top: top + window.scrollY,
      left: left + window.scrollX,
    });
  }, [open]);

  // 点击外部关闭。监听 mousedown，避免吞掉 click 影响 popover 内 a 链跳转。
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      const target = e.target as Node | null;
      if (!target) return;
      if (popoverRef.current?.contains(target)) return;
      if (triggerRef.current?.contains(target)) return;
      setOpen(false);
    };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [open]);

  // Esc 关闭。
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open]);

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        className="help-bubble-trigger"
        aria-label={ariaLabel}
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        ?
      </button>
      {open && createPortal(
        <div
          ref={popoverRef}
          role="tooltip"
          className="help-bubble-popover"
          style={{ top: pos.top, left: pos.left }}
        >
          <ReactMarkdown
            components={{
              // 让所有外链都安全打开；同时给 a 加个 className 给样式表挂钩。
              a: ({ href, children, ...rest }) => (
                <a
                  href={href}
                  target="_blank"
                  rel="noopener noreferrer"
                  {...rest}
                >
                  {children}
                </a>
              ),
            }}
          >
            {content}
          </ReactMarkdown>
        </div>,
        document.body,
      )}
    </>
  );
}
