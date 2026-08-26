import { describe, expect, it } from "vitest";
import {
  bucketize, columnsFrom, groupByAxis, reviewSummary, touchCollisions, trackStates,
} from "./board";
import type { Finding, Requirement, StageDef } from "./client";

const STAGES: StageDef[] = [
  "triage", "clarify", "decompose", "implement", "verify", "ai_review",
  "preview", "review", "merge", "deploy_test", "integrate", "release",
].map(key => ({ key, label: key, human_gate: key === "review" || key === "release",
                skill: null, adapter: null, env: null }));

function req(p: Partial<Requirement> = {}): Requirement {
  return {
    id: "r1", ref: "R-1", title: "t", body: "", requested_by: "chen",
    stage: "implement", state: "active", contracts: [], sequence_kind: null,
    tasks: [], awaiting_answer: false, created_at: "", ...p,
  };
}
function task(p: Partial<Requirement["tasks"][0]> = {}) {
  return { id: "t", key: "T1", title: "x", repos: [], depends_on: [],
           touches: [], sequence: null, state: "pending",
           fail_reason: "", attempts: 1, ...p };
}

describe("看板列由流水线配置推导", () => {
  it("按语义归组", () => {
    const cols = columnsFrom(STAGES);
    expect(cols.map(c => c.key)).toEqual(
      ["intake", "plan", "build", "review", "merge", "deliver"]);
    expect(cols.find(c => c.key === "build")!.stages).toContain("ai_review");
  });

  it("新增环节不会让需求从看板消失", () => {
    // D8 的验收：加环节只改 YAML。没被分组认领的必须自成一列，不能静默丢掉
    const withNew: StageDef[] = [...STAGES, { key: "security_scan", label: "安全扫描",
                                  human_gate: false, skill: null, adapter: null, env: null }];
    const cols = columnsFrom(withNew);
    expect(cols.some(c => c.stages.includes("security_scan"))).toBe(true);

    const buckets = bucketize([req({ stage: "security_scan" })], cols);
    expect([...buckets.values()].flat()).toHaveLength(1);
  });

  it("丢弃的需求不上看板", () => {
    const cols = columnsFrom(STAGES);
    const b = bucketize([req({ state: "discarded" })], cols);
    expect([...b.values()].flat()).toHaveLength(0);
  });
});

describe("轨道状态", () => {
  it("映射任务状态", () => {
    const r = req({ tasks: [task({ state: "done" }), task({ state: "running" }),
                            task({ state: "failed" }), task({ state: "pending" })] });
    expect(trackStates(r)).toEqual(["done", "running", "failed", "idle"]);
  });
});

describe("touches 冲突预警", () => {
  it("两条需求触达同一文件时双向标记", () => {
    const a = req({ id: "a", tasks: [task({ touches: ["app/export.py"] })] });
    const b = req({ id: "b", tasks: [task({ touches: ["app/export.py", "x.py"] })] });
    const hits = touchCollisions([a, b]);
    expect(hits.get("a")).toEqual(["app/export.py"]);
    expect(hits.get("b")).toEqual(["app/export.py"]);
  });

  it("不相交则无预警", () => {
    const a = req({ id: "a", tasks: [task({ touches: ["a.py"] })] });
    const b = req({ id: "b", tasks: [task({ touches: ["b.py"] })] });
    expect(touchCollisions([a, b]).size).toBe(0);
  });

  it("wide refactor 豁免 —— 大面积相交是预期的", () => {
    // §8.4：正确处理不是卡住，是识别成 expand→migrate→contract 序列
    const wide = req({ id: "w", sequence_kind: "expand",
                       tasks: [task({ touches: ["a.py", "b.py"], sequence: "migrate" })] });
    const normal = req({ id: "n", tasks: [task({ touches: ["a.py"] })] });
    expect(touchCollisions([wide, normal]).size).toBe(0);
  });

  it("已完成的需求不参与预警", () => {
    const done = req({ id: "d", state: "done", tasks: [task({ touches: ["a.py"] })] });
    const active = req({ id: "a", tasks: [task({ touches: ["a.py"] })] });
    expect(touchCollisions([done, active]).size).toBe(0);
  });
});

describe("复核结论呈现", () => {
  const f = (p: Partial<Finding> = {}): Finding => ({
    id: "f", axis: "defect", severity: "medium", category: "bug", path: "a.py",
    start_line: 1, claim: "c", failure_scenario: "", kept: true,
    confidence: "high", verdict_reason: "", ...p });

  it("按 severity 排序并分轴", () => {
    const g = groupByAxis([f({ severity: "low" }), f({ severity: "critical" }),
                           f({ axis: "spec", severity: "high" })]);
    expect(g.defect.map(x => x.severity)).toEqual(["critical", "low"]);
    expect(g.spec).toHaveLength(1);
  });

  it("零发现的文案不能说「没有问题」", () => {
    // §9.11 实测：同一 diff 三次跑出 2/0/0，召回不稳定
    const s = reviewSummary([]);
    expect(s.label).toContain("未发现");
    expect(s.label).toContain("不等于没有问题");
  });

  it("被过滤掉的发现不影响结论", () => {
    expect(reviewSummary([f({ severity: "critical", kept: false })]).label)
      .toContain("未发现");
  });

  it("有 critical 时标红", () => {
    expect(reviewSummary([f({ severity: "critical" })]).tone).toBe("bad");
  });
});

describe("回归：专家审查发现的问题", () => {
  it("单条需求的多个 task 触达同一文件时不算冲突", () => {
    // 之前 owners 用数组，同一条需求的 id 被塞两次 → length>=2 → 误报"和自己冲突"
    const r = req({ id: "a", tasks: [
      task({ key: "T1", touches: ["app/x.py"] }),
      task({ key: "T2", touches: ["app/x.py"] }),
    ]});
    expect(touchCollisions([r]).size).toBe(0);
  });

  it("两条需求触达同一文件仍然报冲突", () => {
    const a = req({ id: "a", tasks: [task({ touches: ["app/x.py"] })] });
    const b = req({ id: "b", tasks: [task({ touches: ["app/x.py"] })] });
    expect(touchCollisions([a, b]).size).toBe(2);
  });
});

// ── 逐 token 增量要合并 ─────────────────────────────────────────
describe("agent 思考流", () => {
  it("同一个 part 的增量拼成一段，不是一行一个字", async () => {
    const { append } = await import("./stream");
    let steps: import("./stream").Step[] = [];
    for (const t of ["我", "先看", "一下", "导出"]) {
      steps = append(steps, { kind: "reasoning", text: t, part_id: "p1", delta: true });
    }
    expect(steps).toHaveLength(1);
    expect(steps[0].text).toBe("我先看一下导出");
  });

  it("换了 part 就另起一段", async () => {
    const { append } = await import("./stream");
    let steps: import("./stream").Step[] = [];
    steps = append(steps, { kind: "reasoning", text: "想", part_id: "p1", delta: true });
    steps = append(steps, { kind: "text", text: "答", part_id: "p2", delta: true });
    expect(steps.map(s => s.kind)).toEqual(["reasoning", "text"]);
  });

  it("工具调用不参与合并", async () => {
    const { append } = await import("./stream");
    let steps: import("./stream").Step[] = [];
    steps = append(steps, { kind: "reasoning", text: "想", part_id: "p1", delta: true });
    steps = append(steps, { kind: "tool", text: "读文件：a.py", tool: "read" });
    steps = append(steps, { kind: "reasoning", text: "再想", part_id: "p1", delta: true });
    // 工具插在中间 → 后面的思考另起一段，不该跳回去接前面那段
    expect(steps).toHaveLength(3);
  });
});
