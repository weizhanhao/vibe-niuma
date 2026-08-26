import type { ReactNode } from "react";
import type { Requirement, StageDef } from "../api/client";
import { trackStates } from "../api/board";

export type Tone = "run" | "gate" | "bad" | "ok" | "idle";

export function Pill({ tone, children }: { tone: Tone; children: ReactNode }) {
  return <span className={`pill ${tone}`}><i className="dot" />{children}</span>;
}

export function Note({ tone, children }: { tone: Tone; children: ReactNode }) {
  const glyph = { bad: "⚠", gate: "✋", ok: "✓", run: "◆", idle: "ℹ" }[tone];
  return <div className={`note ${tone}`}><span>{glyph}</span><div>{children}</div></div>;
}

/** 闸门轨 —— 流水线各环节。人工闸门单独标 ✋，因为它会停下来等人。
 *
 * 每一格可点：点开看这一环到底在做什么、你能做什么。光看
 * 「clarify」三个字没人知道该干嘛。 */
export function GateRail({ stages, current, picked, onPick }: {
  stages: StageDef[]; current: string;
  picked?: string; onPick?: (key: string) => void;
}) {
  const at = stages.findIndex(s => s.key === current);
  return (
    <div className="gates" aria-label="流水线进度">
      {stages.map((s, i) => {
        const cls = i < at ? "done" : i > at ? "idle" : s.human_gate ? "wait" : "now";
        const label = i < at ? "已完成" : i > at ? "待进行" : s.human_gate ? "等你处理" : "进行中";
        return (
          <button key={s.key} type="button"
                  className={`gate-s ${cls}${picked === s.key ? " picked" : ""}`}
                  aria-current={i === at ? "step" : undefined}
                  aria-pressed={picked === s.key}
                  title={`${s.label || s.key} —— 点开看这一环做什么`}
                  onClick={() => onPick?.(s.key)}>
            <div className="st">{label}</div>
            <div className="nm">{s.label || s.key}</div>
            {s.human_gate && <span className="human" title="人工闸门">✋</span>}
          </button>
        );
      })}
    </div>
  );
}

/** 需求卡。横条 = 拆出的并行任务，一眼看出跑到哪了。 */
export function ReqCard({ r, collision, onOpen }:
  { r: Requirement; collision?: string[]; onOpen: () => void }) {
  const tracks = trackStates(r);
  return (
    <button className="rq" onClick={onOpen}>
      <div className="ti">{r.title}</div>
      {tracks.length > 0 && (
        <div className="trackmini" aria-label={`${tracks.length} 个并行任务`}>
          {tracks.map((t, i) => <i key={i} className={t} />)}
        </div>
      )}
      <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
        <span className="tag">{r.ref}</span>
        {r.awaiting_answer && <Pill tone="gate">等你回答</Pill>}
        {/* 挂掉的需求之前在看板上跟正常的长得一模一样，
            看着像在跑，实际早就停了 */}
        {r.state === "failed" && <Pill tone="bad">卡住了</Pill>}
        {r.state === "blocked" && <Pill tone="bad">缺能力</Pill>}
        {r.sequence_kind && <Pill tone="idle">wide refactor</Pill>}
        {collision?.length ? <Pill tone="gate">冲突预警 {collision.length}</Pill> : null}
        {tracks.length > 0 && <span className="tag">{tracks.length} 任务</span>}
        <span className="sp" style={{ fontSize: 11.5, color: "var(--ink-3)" }}>
          {r.requested_by}
        </span>
      </div>
    </button>
  );
}
