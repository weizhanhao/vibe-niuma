/** 看板列 ↔ 流水线环节的映射，以及卡片衍生状态。
 *
 * 流水线是配置（后端 /pipeline 返回），看板列是它的**视图**。
 * 环节增删只改 YAML，这里按语义归组，不硬编码环节全集。
 */
import type { Finding, Requirement, StageDef } from "./client";

export interface Column { key: string; label: string; stages: string[] }

const GROUPS: { key: string; label: string; match: (s: StageDef) => boolean }[] = [
  { key: "intake",  label: "分诊 / 澄清", match: s => ["triage", "clarify"].includes(s.key) },
  { key: "plan",    label: "拆解",        match: s => s.key === "decompose" },
  { key: "build",   label: "并行开发",    match: s => ["implement", "verify", "ai_review", "preview", "browser_check"].includes(s.key) },
  { key: "review",  label: "待审核",      match: s => s.key === "review" },
  { key: "merge",   label: "合并队列",    match: s => s.key === "merge" },
  { key: "deliver", label: "交付",        match: s => ["deploy_test", "integrate", "release"].includes(s.key) },
];

export function columnsFrom(stages: StageDef[]): Column[] {
  const cols = GROUPS.map(g => ({
    key: g.key, label: g.label, stages: stages.filter(g.match).map(s => s.key),
  })).filter(c => c.stages.length > 0);

  // 兜底：没被任何分组认领的环节自成一列，否则它的需求会从看板上凭空消失
  const claimed = new Set(cols.flatMap(c => c.stages));
  const orphans = stages.filter(s => !claimed.has(s.key));
  for (const s of orphans) {
    // 自定义环节自成一列，用它的显示名
    cols.push({ key: s.key, label: s.label || s.key, stages: [s.key] });
  }
  return cols;
}

export function bucketize(reqs: Requirement[], cols: Column[]): Map<string, Requirement[]> {
  const out = new Map<string, Requirement[]>(cols.map(c => [c.key, []]));
  for (const r of reqs) {
    if (r.state === "discarded") continue;
    const col = cols.find(c => c.stages.includes(r.stage));
    if (col) out.get(col.key)!.push(r);
  }
  return out;
}

export type TrackState = "done" | "running" | "failed" | "idle";

export function trackStates(r: Requirement): TrackState[] {
  return r.tasks.map(t =>
    t.state === "done" ? "done"
      : t.state === "running" ? "running"
      : t.state === "failed" ? "failed" : "idle");
}

/** 跨需求 touches 相交 —— 冲突预警前置到调度期（§8.3 保险 ①）。
 *
 * wide refactor 的 touches 大面积相交是**预期的**，不算冲突（§8.4）。 */
export function touchCollisions(reqs: Requirement[]): Map<string, string[]> {
  // 用 Set 而不是数组：同一条需求的多个 task 触达同一文件时，
  // 数组会把它自己的 id 塞两次 → length >= 2 → 误报"和自己冲突"。
  const owners = new Map<string, Set<string>>();
  for (const r of reqs) {
    if (r.state !== "active" || r.sequence_kind) continue;
    for (const t of r.tasks) {
      if (t.sequence) continue;          // wide refactor 序列豁免
      for (const p of t.touches) {
        (owners.get(p) ?? owners.set(p, new Set()).get(p)!).add(r.id);
      }
    }
  }
  const hits = new Map<string, string[]>();
  for (const [path, ids] of owners) {
    if (ids.size < 2) continue;          // 真的是两条不同需求才算冲突
    for (const id of ids) {
      hits.set(id, [...new Set([...(hits.get(id) ?? []), path])]);
    }
  }
  return hits;
}

export const SEVERITY_ORDER = ["critical", "high", "medium", "low"] as const;

export function groupByAxis(fs: Finding[]): Record<string, Finding[]> {
  const out: Record<string, Finding[]> = { defect: [], spec: [], norm: [] };
  for (const f of fs) (out[f.axis] ??= []).push(f);
  for (const k of Object.keys(out)) {
    out[k].sort((a, b) =>
      SEVERITY_ORDER.indexOf(a.severity as never) - SEVERITY_ORDER.indexOf(b.severity as never));
  }
  return out;
}

/** 复核结论的呈现口径。
 *
 * **0 条发现 ≠ 代码干净**（§9.11 实测：同一 diff 三次跑出 2/0/0）。
 * 所以文案只能说「未发现」，且不得据此跳过人工审核。 */
export function reviewSummary(fs: Finding[]): { label: string; tone: string } {
  const kept = fs.filter(f => f.kept);
  if (kept.length === 0) return { label: "本次未发现问题（不等于没有问题）", tone: "idle" };
  const worst = SEVERITY_ORDER.find(s => kept.some(f => f.severity === s))!;
  return {
    label: `${kept.length} 条 · 最高 ${worst}`,
    tone: worst === "critical" ? "bad" : worst === "high" ? "bad" : "gate",
  };
}
