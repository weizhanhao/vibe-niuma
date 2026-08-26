import { useEffect, useState } from "react";
import type { Step } from "../api/stream";
import { useStickToBottom } from "../hooks/useStickToBottom";

const ICON: Record<string, string> = {
  tool: "⚙", text: "▸", reasoning: "◇", step: "·", error: "⚠",
};
const KIND_CN: Record<string, string> = {
  tool: "工具", text: "输出", reasoning: "思考", step: "回合", error: "报错",
};

/** agent 的思考过程。
 *
 * **跑的时候默认展开。** 之前默认折叠成一行「正在想… 0 步」——
 * 那跟原来那句「正在看代码…」没有区别，等于白做。
 * 人要看的是它此刻在干什么，不是一个计数器。
 */
export function Thinking({ steps, live }: { steps: Step[]; live?: boolean }) {
  // 跑的时候展开；跑完（历史消息）折叠，免得把结论挤下去
  const [open, setOpen] = useState(Boolean(live));
  const { ref: boxRef, onScroll, pinned, jump } = useStickToBottom<HTMLDivElement>();

  useEffect(() => { if (live) setOpen(true); }, [live]);

  if (steps.length === 0 && !live) return null;
  const tools = steps.filter(s => s.kind === "tool").length;

  return (
    <div className={`think${live ? " live" : ""}`}>
      <button className="think-h" onClick={() => setOpen(v => !v)} aria-expanded={open}>
        <span className="caret" aria-hidden>{open ? "▾" : "▸"}</span>
        {live && <span className="spin" aria-hidden />}
        <span className="think-now">
          {live ? "思考过程（实时）" : "思考过程"}
        </span>
        <span className="think-n">
          {steps.length} 步{tools > 0 && ` · ${tools} 次工具`}
        </span>
      </button>

      {open && (
        <div ref={boxRef} onScroll={onScroll} className="think-list">
          {steps.length === 0 && (
            <div className="ts step"><span className="ic">·</span>
              <div className="tt">正在启动 agent…</div></div>
          )}
          {steps.map((s, i) => (
            <div key={i} className={`ts ${s.kind}`}>
              <span className="ic" aria-hidden>{ICON[s.kind] ?? "·"}</span>
              <div style={{ minWidth: 0, flex: 1 }}>
                <span className="tk">{KIND_CN[s.kind] ?? s.kind}</span>
                <span className="tt">{s.text}</span>
                {s.detail && (
                  <pre className="td">{s.detail.slice(0, 1500)}
                    {s.detail.length > 1500 && "\n…（截断）"}</pre>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
      {open && !pinned && (
        <button className="jump" onClick={jump}>↓ 跳到最新</button>
      )}
    </div>
  );
}
