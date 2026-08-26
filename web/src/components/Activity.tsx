import { useEffect, useState } from "react";
import type { Activity as Row, Client } from "../api/client";
import { Pill, type Tone } from "./bits";

const TONE: Record<string, Tone> = {
  done: "ok", running: "run", failed: "bad", blocked: "bad",
  awaiting_human: "gate", awaiting_user: "gate", decided: "ok",
};
const STATE_CN: Record<string, string> = {
  running: "进行中", done: "完成", failed: "失败", blocked: "卡住",
  awaiting_human: "等人审核", awaiting_user: "等你回答", decided: "已决定",
};

/** 这条需求身上发生过什么。
 *
 * 之前只有实时 SSE：中途打开页面的人、以及需求挂了之后回来看的人，
 * 界面上一片空白 —— 不知道跑到哪一步、也不知道为什么停了。 */
export function ActivityLog({ api, slug, reqId, tick, labelOf }: {
  api: Client; slug: string; reqId: string; tick: string;
  labelOf: (key: string) => string;
}) {
  const [rows, setRows] = useState<Row[]>([]);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    // **必须防过期响应。** 快速切换需求时，上一条的响应可能后到，
    // 把 A 的流程记录渲染在 B 的页面上 —— 比看板串数据严重得多。
    let alive = true;
    api.activity(slug, reqId)
      .then(r => { if (alive) setRows(r); })
      .catch(() => { if (alive) setRows([]); });
    return () => { alive = false; };
  }, [api, slug, reqId, tick]);

  if (rows.length === 0) return null;
  const shown = open ? rows : rows.slice(-6);
  const bad = rows.filter(r => r.state === "failed" || r.state === "blocked");

  return (
    <>
      <div className="sec-h">
        <h2 className="h2">流程记录</h2>
        {bad.length > 0 && <Pill tone="bad">{bad.length} 次未通过</Pill>}
        {rows.length > shown.length && (
          <button className="btn sm sp" onClick={() => setOpen(true)}>
            展开全部 {rows.length} 条
          </button>
        )}
        {open && rows.length > 6 && (
          <button className="btn sm sp" onClick={() => setOpen(false)}>收起</button>
        )}
      </div>
      <div className="tbl-w">
        <table>
          <thead><tr><th>时间</th><th>环节</th><th>结果</th><th>说明</th></tr></thead>
          <tbody>
            {shown.map(r => (
              <tr key={r.id}>
                <td className="mono" style={{ fontSize: 11.5, whiteSpace: "nowrap" }}>
                  {r.created_at ? new Date(r.created_at).toLocaleString("zh-CN",
                    { month: "2-digit", day: "2-digit",
                      hour: "2-digit", minute: "2-digit" }) : "—"}
                </td>
                <td>{labelOf(r.stage)}</td>
                <td><Pill tone={TONE[r.state] ?? "idle"}>
                  {STATE_CN[r.state] ?? r.state ?? r.kind}
                </Pill></td>
                <td style={{ color: "var(--ink-2)", fontSize: 12.5 }}>
                  {r.detail || <span style={{ color: "var(--ink-3)" }}>—</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
