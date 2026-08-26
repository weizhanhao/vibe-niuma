import { useCallback, useEffect, useRef, useState } from "react";

/** 流式内容的「吸底」滚动。
 *
 * 两个坑必须一起躲开：
 *
 * **① 不能用数组长度当触发信号。** opencode 逐 token 推，同一个 part 的
 * 增量是**原地合并**进最后一条的 —— 数组长度纹丝不动。之前依赖
 * `[steps.length]` 的 effect 在整段流式输出期间**一次都不触发**：
 * 文字不断往下长出可视区，滚动条却不动。用户看到的正是
 * 「上半截停在那，下面还在长，但我看不见」——字面意义上的只能看到一半。
 * 所以用 ResizeObserver 盯**内容真实高度**。
 *
 * **② 用户往回翻的时候不能硬拽。** 无条件滚到底的话，人正在看三分钟前
 * 读了哪个文件，一条新增量到达就把他拽回底部，几秒一次持续十分钟。
 * 所以一旦向上滚离底部就解除吸底，滚回底部附近再自动恢复。
 */
export function useStickToBottom<T extends HTMLElement>() {
  const ref = useRef<T | null>(null);
  const stuck = useRef(true);
  const [pinned, setPinned] = useState(true);

  const toBottom = useCallback(() => {
    const el = ref.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, []);

  const jump = useCallback(() => {
    stuck.current = true; setPinned(true); toBottom();
  }, [toBottom]);

  const onScroll = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    const gap = el.scrollHeight - el.scrollTop - el.clientHeight;
    const near = gap < 32;
    if (near !== stuck.current) { stuck.current = near; setPinned(near); }
  }, []);

  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    // 观察内容高度变化 —— 这才是流式输出真正的信号
    const ro = new ResizeObserver(() => { if (stuck.current) toBottom(); });
    ro.observe(el);
    for (const child of Array.from(el.children)) ro.observe(child);
    return () => ro.disconnect();
  }, [toBottom]);

  return { ref, onScroll, pinned, jump };
}
