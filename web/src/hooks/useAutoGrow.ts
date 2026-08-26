import { useLayoutEffect } from "react";

/** 让 textarea 随内容长高。
 *
 * CSS 的 `field-sizing: content` 还没全支持，这是兜底。
 * 上限交给 CSS 的 max-height —— 到顶之后 textarea 自己内部滚。
 */
export function useAutoGrow(
  ref: React.RefObject<HTMLTextAreaElement | null>, value: string,
) {
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    // **先归零再读 scrollHeight。** 不归零的话内容删短时高度缩不回去，
    // 输入框会一直保持最高时的样子。
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }, [ref, value]);
}
