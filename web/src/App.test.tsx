import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";

const CN: Record<string, string> = {
  triage: "分诊", clarify: "澄清", decompose: "拆解", implement: "并行开发",
  verify: "验证", ai_review: "AI 复核", preview: "预览",
  browser_check: "浏览器自检", review: "人工审核",
  merge: "合并", deploy_test: "部署测试环境", integrate: "集成测试", release: "上线",
};
const STAGES = ["triage", "clarify", "decompose", "implement", "verify", "ai_review",
                "preview", "browser_check", "review", "merge", "deploy_test", "integrate",
                "release"]
  .map(key => ({ key, label: CN[key] ?? key, human_gate: key === "review" || key === "release",
                 skill: key === "decompose" ? "to-tickets" : null,
                 adapter: key === "ai_review" ? "ocr" : null, env: null }));

const REQ = {
  id: "r1", ref: "R-1", title: "订单导出支持自定义字段", body: "", requested_by: "chen",
  stage: "review", state: "active", contracts: ["POST /export/orders → {jobId}"],
  sequence_kind: null, awaiting_answer: false, created_at: "",
  tasks: [{ id: "t1", key: "T1", title: "导出弹窗", repos: ["web"], depends_on: [],
            touches: ["src/Export.tsx"], sequence: null, state: "done",
            fail_reason: "", attempts: 1 }],
};

function mockApi(over: Record<string, unknown> = {}) {
  const calls: { url: string; init?: RequestInit }[] = [];
  const routes: Record<string, unknown> = {
    "/projects": [{ id: "p1", name: "商户中台", slug: "mc", target_branch: "vibe/dev",
                    repos: ["web"], quota_parallel_runs: 8,
                    active_requirements: 1, awaiting_review: 1 }],
    "/projects/mc/pipeline": { stages: STAGES, required_skills: ["to-tickets"],
                               target_branch: "vibe/dev" },
    "/projects/mc/requirements": [REQ],
    "/projects/mc/merge-queue": [],
    "/projects/mc/requirements/r1/messages": [],
    "/projects/mc/requirements/r1/activity": [],
    "/projects/mc/requirements/r1/previews": [],
    "/projects/mc/requirements?drafts=true": [],
    "/projects/mc/environments": [
      { env: "preview", state: "never", url: null, finished_at: null },
      { env: "test", state: "succeeded", url: "http://test", finished_at: null },
      { env: "prod", state: "never", url: null, finished_at: null }],
    ...over,
  };
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    const path = url.replace("/api", "").split("?")[0];
    if (init?.method === "POST" && path.endsWith("/review")) {
      return new Response(JSON.stringify({ ok: true }), { status: 201 });
    }
    const body = routes[path] ?? routes[path.replace(/\/[^/]+\/findings$/, "/findings")];
    if (body === undefined) return new Response(JSON.stringify({ detail: "未配置" }), { status: 404 });
    return new Response(JSON.stringify(body), { status: 200 });
  }));
  return calls;
}


/** 假 EventSource。jsdom 没有实现，而「具名事件必须用 addEventListener」
 *  这条正是之前挂 onmessage 收不到任何东西的原因 —— 得测得出来。 */
function fakeEventSource() {
  const instances: {
    url: string; listeners: Map<string, EventListener[]>;
    readyState: number; closed: boolean;
    onerror: ((e: unknown) => void) | null;
  }[] = [];
  class FakeES {
    static readonly CONNECTING = 0;
    static readonly OPEN = 1;
    static readonly CLOSED = 2;
    listeners = new Map<string, EventListener[]>();
    onerror: ((e: unknown) => void) | null = null;
    onopen: ((e: unknown) => void) | null = null;
    onmessage: EventListener | null = null;
    /** **必须真的记录状态。** 之前 close() 是空实现，
     *  于是「onerror 后永久不重连」这个 bug 从原理上就测不出来。 */
    readyState = 0;
    closed = false;
    constructor(public url: string) {
      instances.push(this);
      queueMicrotask(() => { this.readyState = 1; this.onopen?.(new Event("open")); });
    }
    addEventListener(k: string, fn: EventListener) {
      this.listeners.set(k, [...(this.listeners.get(k) ?? []), fn]);
    }
    removeEventListener() {}
    close() { this.readyState = 2; this.closed = true; }
  }
  vi.stubGlobal("EventSource", FakeES as unknown as typeof EventSource);
  return {
    instances,
    /** 服务端明确关闭 —— 浏览器不会自己重连，得我们退避重试 */
    fatal() {
      act(() => {
        for (const i of instances) {
          if (i.closed) continue;
          i.readyState = 2;
          i.onerror?.(new Event("error"));
        }
      });
    },
    emit(kind: string, data: unknown, lastEventId = "0") {
      act(() => {
        for (const i of instances) {
          for (const fn of i.listeners.get(kind) ?? []) {
            fn({ data: JSON.stringify(data), lastEventId } as MessageEvent);
          }
        }
      });
    },
  };
}

beforeEach(() => vi.unstubAllGlobals());

describe("调度台", () => {
  it("开发模式下每个请求都带 X-User", async () => {
    const calls = mockApi({ "/projects/mc/requirements/r1/findings": [] });
    render(<App devUser="chen" />);
    // 「商户中台」在侧栏和顶栏都出现 —— 用 findAllByText 避免歧义
    await screen.findAllByText("商户中台");
    await waitFor(() => expect(calls.length).toBeGreaterThan(3));
    for (const c of calls) {
      expect((c.init?.headers as Record<string, string>)["X-User"]).toBe("chen");
    }
  });

  it("看板按流水线环节分列，需求落到对应列", async () => {
    mockApi({ "/projects/mc/requirements/r1/findings": [] });
    render(<App devUser="chen" />);
    const col = await screen.findByText("待审核");
    const section = col.closest("section")!;
    expect(within(section).getByText(REQ.title)).toBeInTheDocument();
  });

  it("打开需求能看到任务、契约和闸门轨", async () => {
    mockApi({ "/projects/mc/requirements/r1/findings": [] });
    render(<App devUser="chen" />);
    await userEvent.click(await screen.findByText(REQ.title));
    expect(await screen.findByText(/R-1 · chen 提出/)).toBeInTheDocument();
    expect(screen.getByText("POST /export/orders → {jobId}")).toBeInTheDocument();
    expect(screen.getByText("src/Export.tsx")).toBeInTheDocument();
    expect(screen.getByLabelText("流水线进度")).toBeInTheDocument();
  });

  it("零发现时不能说「没有问题」", async () => {
    // §9.11 实测：同一 diff 三次跑出 2/0/0，召回不稳定
    mockApi({ "/projects/mc/requirements/r1/findings": [] });
    render(<App devUser="chen" />);
    await userEvent.click(await screen.findByText(REQ.title));
    expect(await screen.findByText(/不等于没有问题/)).toBeInTheDocument();
    expect(screen.getByText(/不要据此跳过人工审核/)).toBeInTheDocument();
  });

  it("复核发现按轴分组展示", async () => {
    mockApi({
      "/projects/mc/requirements/r1/findings": [
        { id: "f1", axis: "defect", severity: "high", category: "correctness",
          path: "app/export.py", start_line: 38, claim: "状态停在 running",
          failure_scenario: "第 3 个分片抛异常 → 前端永远转圈",
          kept: true, confidence: "high", verdict_reason: "破坏错误契约" },
        { id: "f2", axis: "spec", severity: "high", category: "contract",
          path: "src/api.ts", start_line: 27, claim: "没实现契约里的轮询",
          failure_scenario: "", kept: true, confidence: "high", verdict_reason: "" },
      ],
    });
    render(<App devUser="chen" />);
    await userEvent.click(await screen.findByText(REQ.title));
    expect(await screen.findByText("状态停在 running")).toBeInTheDocument();
    expect(screen.getByText("没实现契约里的轮询")).toBeInTheDocument();
    expect(screen.getByText(/第 3 个分片抛异常/)).toBeInTheDocument();
    expect(screen.getByText("缺陷轴")).toBeInTheDocument();
    expect(screen.getByText("规格轴")).toBeInTheDocument();
  });

  it("审核按钮只在 review 环节出现", async () => {
    mockApi({
      "/projects/mc/requirements": [{ ...REQ, stage: "implement" }],
      "/projects/mc/requirements/r1/findings": [],
    });
    render(<App devUser="chen" />);
    await userEvent.click(await screen.findByText(REQ.title));
    await screen.findByText(/R-1 · chen 提出/);
    expect(screen.queryByText(/通过，进合并队列/)).not.toBeInTheDocument();
  });

  it("点通过会发审核请求", async () => {
    const calls = mockApi({ "/projects/mc/requirements/r1/findings": [] });
    render(<App devUser="chen" />);
    await userEvent.click(await screen.findByText(REQ.title));
    await userEvent.click(await screen.findByText(/通过，进合并队列/));
    await waitFor(() => {
      const post = calls.find(c => c.init?.method === "POST" && c.url.includes("/review"));
      expect(post).toBeTruthy();
      expect(JSON.parse(post!.init!.body as string).decision).toBe("approve");
    });
  });

  it("服务端错误要显示出来，不能吞掉", async () => {
    vi.stubGlobal("fetch", vi.fn(async () =>
      new Response(JSON.stringify({ detail: "chen 不是空间 mc 的成员" }), { status: 403 })));
    render(<App devUser="chen" />);
    expect(await screen.findByText(/不是空间 mc 的成员/)).toBeInTheDocument();
  });

  it("流水线页显示每个环节由什么实现", async () => {
    mockApi({ "/projects/mc/requirements/r1/findings": [] });
    render(<App devUser="chen" />);
    await userEvent.click(await screen.findByText("流水线"));
    expect(await screen.findByText("skill · to-tickets")).toBeInTheDocument();
    expect(screen.getByText("adapter · ocr")).toBeInTheDocument();
    expect(screen.getAllByText("人工闸门")).toHaveLength(2);
  });

  it("环境页列出三层", async () => {
    mockApi({ "/projects/mc/requirements/r1/findings": [] });
    render(<App devUser="chen" />);
    await userEvent.click(await screen.findByText("环境"));
    for (const e of ["preview", "test", "prod"]) {
      expect(await screen.findByText(e)).toBeInTheDocument();
    }
  });
});

describe("回归：切换空间的响应竞态", () => {
  it("切走之后旧空间的响应不能覆盖新空间的数据", async () => {
    // 之前没有 stale 检查：切 A→B 时 A 的响应后到，会把 A 的需求写进 B 的界面，
    // 侧栏高亮 B、列表显示 A 的数据，点进去拿 404
    const slow = new Map<string, number>();
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      const path = url.replace("/api", "").split("?")[0];
      if (path === "/projects") {
        return new Response(JSON.stringify([
          { id: "p1", name: "商户中台", slug: "mc", target_branch: "vibe/dev",
            repos: [], quota_parallel_runs: 8, active_requirements: 0, awaiting_review: 0 },
          { id: "p2", name: "仓配作业", slug: "wms", target_branch: "vibe/dev",
            repos: [], quota_parallel_runs: 8, active_requirements: 0, awaiting_review: 0 },
        ]), { status: 200 });
      }
      // mc 的请求故意变慢
      if (path.startsWith("/projects/mc")) {
        slow.set(path, (slow.get(path) ?? 0) + 1);
        await new Promise(r => setTimeout(r, 60));
      }
      if (path.endsWith("/pipeline")) {
        return new Response(JSON.stringify({ stages: STAGES, required_skills: [],
                                             target_branch: "vibe/dev" }), { status: 200 });
      }
      if (path.endsWith("/requirements")) {
        const which = path.includes("/mc/") ? "MC 的需求" : "WMS 的需求";
        return new Response(JSON.stringify([{ ...REQ, id: which, title: which }]),
                            { status: 200 });
      }
      return new Response(JSON.stringify([]), { status: 200 });
    }));

    render(<App devUser="chen" />);
    await screen.findByText("仓配作业");
    await userEvent.click(screen.getByText("仓配作业"));

    // 等 mc 的慢响应也回来
    await new Promise(r => setTimeout(r, 150));
    expect(screen.queryByText("MC 的需求")).not.toBeInTheDocument();
    expect(await screen.findByText("WMS 的需求")).toBeInTheDocument();
  });
});


describe("认证", () => {
  beforeEach(() => { try { localStorage.clear(); } catch { /* ignore */ } });

  it("没有凭证时显示登录页，不是红色错误横幅", async () => {
    mockApi();
    render(<App />);          // 不传 devUser，localStorage 也是空的
    expect(await screen.findByLabelText("访问令牌")).toBeInTheDocument();
    expect(screen.queryByText(/失败|错误/)).not.toBeInTheDocument();
  });

  it("输入 token 后带 Authorization 头请求", async () => {
    const calls = mockApi({ "/projects/mc/requirements/r1/findings": [] });
    render(<App />);
    await userEvent.type(await screen.findByLabelText("访问令牌"), "vp_secret");
    await userEvent.click(screen.getByText("进入"));
    await screen.findAllByText("商户中台");
    const authed = calls.filter(c =>
      (c.init?.headers as Record<string, string>)?.Authorization === "Bearer vp_secret");
    expect(authed.length).toBeGreaterThan(0);
  });

  it("服务端 401 时回到登录页，不显示错误横幅", async () => {
    vi.stubGlobal("fetch", vi.fn(async () =>
      new Response(JSON.stringify({ detail: "需要 Authorization" }), { status: 401 })));
    render(<App devUser="chen" />);
    expect(await screen.findByLabelText("访问令牌")).toBeInTheDocument();
  });

  it("token 存进 localStorage 以便刷新后免登", async () => {
    mockApi({ "/projects/mc/requirements/r1/findings": [] });
    render(<App />);
    await userEvent.type(await screen.findByLabelText("访问令牌"), "vp_keepme");
    await userEvent.click(screen.getByText("进入"));
    await screen.findAllByText("商户中台");
    expect(localStorage.getItem("vp.token")).toBe("vp_keepme");
  });

  // ── 需求对话：澄清问答 / 续改 ────────────────────────────────
  it("澄清环节能看到 AI 的问题并回答", async () => {
    const calls = mockApi({
      "/projects/mc/requirements": [{ ...REQ, stage: "clarify", awaiting_answer: true }],
      "/projects/mc/requirements/r1/findings": [],
      "/projects/mc/requirements/r1/messages": [
        { id: "m1", role: "agent", author: "ai", stage: "clarify",
          body: "1. 自定义字段是每人一套还是全公司一套？", awaiting_answer: true,
          created_at: "" },
      ],
    });
    render(<App devUser="chen" />);
    await userEvent.click(await screen.findByText("订单导出支持自定义字段"));
    await screen.findByText(/自定义字段是每人一套/);

    const box = screen.getByLabelText("发消息");
    await userEvent.type(box, "每人一套");
    await userEvent.click(screen.getByRole("button", { name: "发送" }));

    const post = calls.find(c => c.init?.method === "POST"
                              && c.url.includes("/messages"));
    expect(post).toBeTruthy();
    expect(JSON.parse(String(post!.init!.body))).toEqual(
      { body: "每人一套", proceed: false });
  });

  it("「够了直接干」跳过追问", async () => {
    const calls = mockApi({
      "/projects/mc/requirements": [{ ...REQ, stage: "clarify", awaiting_answer: true }],
      "/projects/mc/requirements/r1/findings": [],
      "/projects/mc/requirements/r1/messages": [
        { id: "m1", role: "agent", author: "ai", stage: "clarify", body: "还有个问题…",
          awaiting_answer: true, created_at: "" },
      ],
    });
    render(<App devUser="chen" />);
    await userEvent.click(await screen.findByText("订单导出支持自定义字段"));
    await userEvent.click(await screen.findByRole("button", { name: /够了直接干/ }));
    const post = calls.find(c => c.init?.method === "POST" && c.url.includes("/messages"));
    expect(JSON.parse(String(post!.init!.body)).proceed).toBe(true);
  });

  it("没在等回答时不出现「够了直接干」", async () => {
    mockApi({ "/projects/mc/requirements/r1/findings": [] });
    render(<App devUser="chen" />);
    await userEvent.click(await screen.findByText("订单导出支持自定义字段"));
    await screen.findByText("对话");
    expect(screen.queryByRole("button", { name: /够了直接干/ })).toBeNull();
  });

  it("在等回答的需求在看板上有标记", async () => {
    mockApi({
      "/projects/mc/requirements": [{ ...REQ, stage: "clarify", awaiting_answer: true }],
      "/projects/mc/requirements/r1/findings": [],
    });
    render(<App devUser="chen" />);
    expect(await screen.findByText("等你回答")).toBeTruthy();
  });

  // ── 环节说明 ────────────────────────────────────────────────
  it("闸门轨每一格可点开，说明这一环做什么", async () => {
    mockApi({ "/projects/mc/requirements/r1/findings": [] });
    render(<App devUser="chen" />);
    await userEvent.click(await screen.findByText("订单导出支持自定义字段"));
    // 默认停在当前环节
    await screen.findByText(/流程停在这里等你拍板/);
    // 点别的环节看它做什么，不影响需求本身
    await userEvent.click(screen.getByTitle(/^合并/));
    expect(screen.getByText(/冲突三级处理/)).toBeTruthy();
    expect(screen.getByText("尚未走到")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "回到当前环节" }));
    expect(screen.getByText(/流程停在这里等你拍板/)).toBeTruthy();
  });

  // ── 人工闸门不能只认 review ──────────────────────────────────
  it("release 也是人工闸门，要能在界面上放行", async () => {
    const calls = mockApi({
      "/projects/mc/requirements": [{ ...REQ, stage: "release" }],
      "/projects/mc/requirements/r1/findings": [],
    });
    render(<App devUser="chen" />);
    await userEvent.click(await screen.findByText("订单导出支持自定义字段"));
    await userEvent.click(await screen.findByRole("button", { name: /放行上生产/ }));
    const post = calls.find(c => c.init?.method === "POST" && c.url.endsWith("/review"));
    expect(JSON.parse(String(post!.init!.body)).decision).toBe("approve");
  });

  // ── 空间管理 ────────────────────────────────────────────────
  it("能新建空间", async () => {
    const calls = mockApi({ "/projects/mc/requirements/r1/findings": [],
                            "/admin/projects/mc/repos": [] });
    render(<App devUser="chen" />);
    await userEvent.click(await screen.findByRole("button", { name: /空间管理/ }));
    await userEvent.type(screen.getByLabelText("空间名"), "结算中心");
    await userEvent.type(screen.getByLabelText(/标识/), "settle");
    await userEvent.type(screen.getByLabelText("组织 ID"), "o1");
    await userEvent.click(screen.getByRole("button", { name: "建空间" }));
    const post = calls.find(c => c.init?.method === "POST"
                              && c.url.endsWith("/admin/projects"));
    expect(JSON.parse(String(post!.init!.body))).toMatchObject(
      { name: "结算中心", slug: "settle", org_id: "o1" });
  });

  it("签出的令牌只显示一次并说清楚", async () => {
    mockApi({ "/projects/mc/requirements/r1/findings": [],
              "/admin/projects/mc/repos": [],
              "/admin/tokens": { token: "vp_abc", user_id: "li" } });
    render(<App devUser="chen" />);
    await userEvent.click(await screen.findByRole("button", { name: /空间管理/ }));
    await userEvent.type(screen.getAllByLabelText("用户名")[1], "li");
    await userEvent.click(screen.getByRole("button", { name: "签发" }));
    expect(await screen.findByText("vp_abc")).toBeTruthy();
    expect(screen.getByText(/关掉就找不回来/)).toBeTruthy();
  });

  // ── 需求出问题时，界面上要看得见 ───────────────────────────
  it("失败的环节和原因在流程记录里能看到", async () => {
    mockApi({
      "/projects/mc/requirements/r1/findings": [],
      "/projects/mc/requirements/r1/activity": [
        { id: 1, kind: "status", stage: "implement", state: "done",
          detail: "", created_at: "2026-08-25T10:00:00Z" },
        { id: 2, kind: "status", stage: "verify", state: "failed",
          detail: "3 个用例挂了：test_export_fields", created_at: "2026-08-25T10:04:00Z" },
      ],
    });
    render(<App devUser="chen" />);
    await userEvent.click(await screen.findByText("订单导出支持自定义字段"));
    await screen.findByText("流程记录");
    expect(screen.getByText(/3 个用例挂了/)).toBeTruthy();
    expect(screen.getByText("1 次未通过")).toBeTruthy();
  });

  it("任务失败原因贴在任务行上", async () => {
    mockApi({
      "/projects/mc/requirements": [{ ...REQ, tasks: [
        { ...REQ.tasks[0], state: "failed", attempts: 2,
          fail_reason: "找不到 orders 表的迁移" }] }],
      "/projects/mc/requirements/r1/findings": [],
    });
    render(<App devUser="chen" />);
    await userEvent.click(await screen.findByText("订单导出支持自定义字段"));
    expect(await screen.findByText("找不到 orders 表的迁移")).toBeTruthy();
    expect(screen.getByText("第 2 次")).toBeTruthy();
  });

  it("需求原文要显示出来", async () => {
    mockApi({
      "/projects/mc/requirements": [{ ...REQ, body: "希望能选导出哪些列" }],
      "/projects/mc/requirements/r1/findings": [],
    });
    render(<App devUser="chen" />);
    await userEvent.click(await screen.findByText("订单导出支持自定义字段"));
    expect(await screen.findByText("希望能选导出哪些列")).toBeTruthy();
  });

  // ── 卡住的需求 ──────────────────────────────────────────────
  it("卡住的需求在看板上有标记", async () => {
    mockApi({
      "/projects/mc/requirements": [{ ...REQ, stage: "verify", state: "failed" }],
      "/projects/mc/requirements/r1/findings": [],
    });
    render(<App devUser="chen" />);
    expect(await screen.findByText("卡住了")).toBeTruthy();
  });

  it("卡住的需求能从当前环节重试", async () => {
    const calls = mockApi({
      "/projects/mc/requirements": [{ ...REQ, stage: "verify", state: "failed" }],
      "/projects/mc/requirements/r1/findings": [],
      "/projects/mc/requirements/r1/retry": { ok: true, stage: "verify", attempt: 1 },
    });
    render(<App devUser="chen" />);
    await userEvent.click(await screen.findByText("订单导出支持自定义字段"));
    expect(screen.getByText(/停在「验证」了/)).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: /从这一环重试/ }));
    expect(calls.some(c => c.init?.method === "POST"
                        && c.url.endsWith("/retry"))).toBe(true);
  });

  it("缺能力和跑挂了要分开说", async () => {
    mockApi({
      "/projects/mc/requirements": [{ ...REQ, stage: "implement", state: "blocked" }],
      "/projects/mc/requirements/r1/findings": [],
    });
    render(<App devUser="chen" />);
    await userEvent.click(await screen.findByText("订单导出支持自定义字段"));
    expect(await screen.findByText(/平台缺了这个环节需要的能力/)).toBeTruthy();
  });

  it("正常需求不显示重试", async () => {
    mockApi({ "/projects/mc/requirements/r1/findings": [] });
    render(<App devUser="chen" />);
    await userEvent.click(await screen.findByText("订单导出支持自定义字段"));
    await screen.findByText("对话");
    expect(screen.queryByRole("button", { name: /重试/ })).toBeNull();
  });

  it("预览地址能点开", async () => {
    mockApi({
      "/projects/mc/requirements": [{ ...REQ, stage: "preview" }],
      "/projects/mc/requirements/r1/findings": [],
      "/projects/mc/requirements/r1/previews": [
        { branch: "cr/1-t1", task_key: "T1", url: "http://127.0.0.1:5101" }],
    });
    render(<App devUser="chen" />);
    await userEvent.click(await screen.findByText("订单导出支持自定义字段"));
    const a = await screen.findByRole("link", { name: /5101/ });
    expect(a.getAttribute("href")).toBe("http://127.0.0.1:5101");
  });

  it("没有预览时不显示空的预览区", async () => {
    mockApi({ "/projects/mc/requirements/r1/findings": [] });
    render(<App devUser="chen" />);
    await userEvent.click(await screen.findByText("订单导出支持自定义字段"));
    await screen.findByText("对话");
    // 「预览」在闸门轨上也有，这里查的是那个小标题
    expect(screen.queryByRole("heading", { name: "预览" })).toBeNull();
  });

  // ── 立需求：先谈，谈成型再进流程 ───────────────────────────
  it("提需求变成对话，不是表单", async () => {
    mockApi({ "/projects/mc/requirements/r1/findings": [] });
    render(<App devUser="chen" />);
    await userEvent.click(await screen.findByRole("button", { name: /立需求/ }));
    expect(screen.getByText("先说说你想要什么")).toBeTruthy();
    // 表单那两个框没了
    expect(screen.queryByLabelText("标题")).toBeNull();
  });

  it("第一句话开一个草稿，不直接进流程", async () => {
    const calls = mockApi({
      "/projects/mc/requirements/r1/findings": [],
      "/projects/mc/intake": { ...REQ, id: "d1", stage: "intake", state: "draft",
                               title: "导出太难用了", body: "导出太难用了", tasks: [] },
      "/projects/mc/requirements/d1": { ...REQ, id: "d1", stage: "intake",
                                        state: "draft", title: "导出太难用了",
                                        body: "导出太难用了", tasks: [] },
      "/projects/mc/requirements/d1/messages": [],
    });
    render(<App devUser="chen" />);
    await userEvent.click(await screen.findByRole("button", { name: /立需求/ }));
    await userEvent.type(screen.getByLabelText("说说你想要什么"), "导出太难用了");
    await userEvent.click(screen.getByRole("button", { name: "发送" }));

    const post = calls.find(c => c.init?.method === "POST" && c.url.endsWith("/intake"));
    expect(JSON.parse(String(post!.init!.body))).toEqual({ opening: "导出太难用了" });
    // 没有任何东西直接进流程
    expect(calls.some(c => c.init?.method === "POST"
                        && c.url.endsWith("/requirements"))).toBe(false);
  });

  it("谈成型后能确认进流程", async () => {
    const draft = { ...REQ, id: "d1", stage: "intake", state: "draft",
                    title: "订单导出支持自定义列",
                    body: "标题: 订单导出支持自定义列\n验收标准:\n- [ ] 只导所选列",
                    tasks: [] };
    const calls = mockApi({
      "/projects/mc/requirements/r1/findings": [],
      "/projects/mc/intake": draft,
      "/projects/mc/requirements/d1": draft,
      "/projects/mc/requirements/d1/messages": [],
      "/projects/mc/requirements/d1/submit": { ...draft, state: "active",
                                               stage: "triage" },
    });
    render(<App devUser="chen" />);
    await userEvent.click(await screen.findByRole("button", { name: /立需求/ }));
    await userEvent.type(screen.getByLabelText("说说你想要什么"), "导出");
    await userEvent.click(screen.getByRole("button", { name: "发送" }));

    expect(await screen.findByText("已成型")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: /确认，进流程/ }));
    expect(calls.some(c => c.init?.method === "POST"
                        && c.url.endsWith("/submit"))).toBe(true);
  });

  it("还没谈出需求稿时不说「已成型」", async () => {
    const draft = { ...REQ, id: "d1", stage: "intake", state: "draft",
                    title: "导出", body: "导出", tasks: [] };
    mockApi({
      "/projects/mc/requirements/r1/findings": [],
      "/projects/mc/intake": draft,
      "/projects/mc/requirements/d1": draft,
      "/projects/mc/requirements/d1/messages": [
        { id: "m1", role: "agent", author: "ai", stage: "intake",
          body: "是每人一套还是全公司一套？", awaiting_answer: true, created_at: "" }],
    });
    render(<App devUser="chen" />);
    await userEvent.click(await screen.findByRole("button", { name: /立需求/ }));
    await userEvent.type(screen.getByLabelText("说说你想要什么"), "导出");
    await userEvent.click(screen.getByRole("button", { name: "发送" }));
    expect(await screen.findByText("还在谈")).toBeTruthy();
    expect(screen.getByText(/每人一套还是全公司一套/)).toBeTruthy();
  });

  it("需求稿能直接改", async () => {
    const draft = { ...REQ, id: "d1", stage: "intake", state: "draft",
                    title: "导出", body: "验收标准: 无", tasks: [] };
    const calls = mockApi({
      "/projects/mc/requirements/r1/findings": [],
      "/projects/mc/intake": draft,
      "/projects/mc/requirements/d1": draft,
      "/projects/mc/requirements/d1/messages": [],
    });
    render(<App devUser="chen" />);
    await userEvent.click(await screen.findByRole("button", { name: /立需求/ }));
    await userEvent.type(screen.getByLabelText("说说你想要什么"), "导出");
    await userEvent.click(screen.getByRole("button", { name: "发送" }));
    await userEvent.click(await screen.findByRole("button", { name: /直接改/ }));
    await userEvent.type(screen.getByLabelText("标题"), "补充");
    await userEvent.click(screen.getByRole("button", { name: "保存" }));
    expect(calls.some(c => c.init?.method === "PATCH")).toBe(true);
  });

  // ── 一个空间多个仓 ──────────────────────────────────────────
  it("绑仓时能按仓填主干分支", async () => {
    const calls = mockApi({ "/projects/mc/requirements/r1/findings": [],
                            "/admin/projects/mc/repos": [] });
    render(<App devUser="chen" />);
    await userEvent.click(await screen.findByRole("button", { name: /空间管理/ }));
    await userEvent.type(screen.getByLabelText("仓名"), "legacy-web");
    await userEvent.type(screen.getByLabelText("仓地址"), "https://x/legacy.git");
    const b = screen.getByLabelText("主干分支");
    await userEvent.clear(b);
    await userEvent.type(b, "master");
    await userEvent.click(screen.getByRole("button", { name: "绑定" }));
    const post = calls.find(c => c.init?.method === "POST" && c.url.endsWith("/repos"));
    expect(JSON.parse(String(post!.init!.body))).toMatchObject(
      { name: "legacy-web", default_branch: "master" });
  });

  it("空间与仓的关系要说清楚", async () => {
    mockApi({ "/projects/mc/requirements/r1/findings": [],
              "/admin/projects/mc/repos": [] });
    render(<App devUser="chen" />);
    await userEvent.click(await screen.findByRole("button", { name: /空间管理/ }));
    expect(await screen.findByText(/一个空间可以绑多个仓/)).toBeTruthy();
  });

  // ── agent 的思考过程 ────────────────────────────────────────
  it("展示的是真的思考过程，不是「正在看代码…」", async () => {
    const draft = { ...REQ, id: "d1", stage: "intake", state: "draft",
                    title: "导出", body: "导出", tasks: [] };
    mockApi({
      "/projects/mc/requirements/r1/findings": [],
      "/projects/mc/intake": draft,
      "/projects/mc/requirements/d1": draft,
      "/projects/mc/requirements/d1/messages": [],
    });
    const es = fakeEventSource();
    render(<App devUser="chen" />);
    await userEvent.click(await screen.findByRole("button", { name: /立需求/ }));
    await userEvent.type(screen.getByLabelText("说说你想要什么"), "导出");
    await userEvent.click(screen.getByRole("button", { name: "发送" }));

    await waitFor(() => expect(es.instances.length).toBeGreaterThan(0));
    es.emit("agent_step", { kind: "tool", text: "读文件：exporter.py",
                            tool: "read", detail: "def export():" });
    es.emit("agent_step", { kind: "text", text: "看明白了" });

    // **跑的时候默认展开**，逐条显示 —— 不再是一个「0 步」计数器
    expect(await screen.findByText("看明白了")).toBeTruthy();
    expect(screen.getByText("读文件：exporter.py")).toBeTruthy();
    expect(screen.getByText(/2 步/)).toBeTruthy();
    expect(screen.queryByText("正在看代码...")).toBeNull();
  });

  it("思考过程可以展开看细节", async () => {
    const draft = { ...REQ, id: "d1", stage: "intake", state: "draft",
                    title: "导出", body: "导出", tasks: [] };
    mockApi({
      "/projects/mc/requirements/r1/findings": [],
      "/projects/mc/intake": draft,
      "/projects/mc/requirements/d1": draft,
      "/projects/mc/requirements/d1/messages": [],
    });
    const es = fakeEventSource();
    render(<App devUser="chen" />);
    await userEvent.click(await screen.findByRole("button", { name: /立需求/ }));
    await userEvent.type(screen.getByLabelText("说说你想要什么"), "导出");
    await userEvent.click(screen.getByRole("button", { name: "发送" }));
    await waitFor(() => expect(es.instances.length).toBeGreaterThan(0));
    es.emit("agent_step", { kind: "tool", text: "执行命令：ls",
                            tool: "bash", detail: "exporter.py\nmodels.py" });

    // 实时态默认展开，工具输出直接可见
    expect(await screen.findByText(/models\.py/)).toBeTruthy();
    // 折起来就看不到了
    await userEvent.click(screen.getByRole("button", { name: /思考过程/ }));
    expect(screen.queryByText(/models\.py/)).toBeNull();
  });

  it("刷新页面后历史消息的思考仍然能展开", async () => {
    mockApi({
      "/projects/mc/requirements": [{ ...REQ, stage: "clarify" }],
      "/projects/mc/requirements/r1/findings": [],
      "/projects/mc/requirements/r1/messages": [
        { id: "m1", role: "agent", author: "ai", stage: "clarify",
          body: "是每人一套吗？", awaiting_answer: true, created_at: "",
          trace: [{ kind: "tool", text: "读文件：exporter.py", tool: "read",
                    detail: "def export():" }] },
      ],
    });
    render(<App devUser="chen" />);
    await userEvent.click(await screen.findByText("订单导出支持自定义字段"));
    // 历史消息默认折叠（免得把结论挤下去），点开能看
    await userEvent.click(await screen.findByRole("button", { name: /思考过程/ }));
    expect(screen.getByText(/def export/)).toBeTruthy();
  });

  it("具名事件必须收得到（onmessage 收不到具名事件）", async () => {
    mockApi({ "/projects/mc/requirements/r1/findings": [] });
    const es = fakeEventSource();
    render(<App devUser="chen" />);
    await userEvent.click(await screen.findByText("订单导出支持自定义字段"));
    await waitFor(() => expect(es.instances.length).toBeGreaterThan(0));
    // 服务端发的是 `event: status`，只挂 onmessage 的话一条都收不到
    expect(es.instances.some(i => i.listeners.has("status"))).toBe(true);
    expect(es.instances.some(i => i.listeners.has("agent_step"))).toBe(true);
  });

  // ── 止血：三位专家共同点出的致命 bug ──────────────────────
  it("SSE 断了要重连，不能永久死掉", async () => {
    mockApi({ "/projects/mc/requirements/r1/findings": [] });
    const es = fakeEventSource();
    render(<App devUser="chen" />);
    await userEvent.click(await screen.findByText("订单导出支持自定义字段"));
    await waitFor(() => expect(es.instances.length).toBeGreaterThan(0));

    // 服务端明确关闭（CLOSED）→ 必须退避重连，不能 close() 了事。
    // 之前 `es.onerror = () => es.close()` 把 readyState 永久置成 CLOSED，
    // 一轮跑 15 分钟，中间抖一下流就死了，UI 上还转着圈。
    const before = es.instances.length;
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      es.fatal();
      // 指数退避：第一次约 0.5~1s。推 40 秒足够覆盖前几次重试
      await act(async () => { await vi.advanceTimersByTimeAsync(40_000); });
      expect(es.instances.length).toBeGreaterThan(before);
    } finally {
      vi.useRealTimers();
    }
  });

  it("切换需求时 lastEventId 归零，历史不会被跳过", async () => {
    mockApi({
      "/projects/mc/requirements": [REQ, { ...REQ, id: "r2", ref: "R-2",
                                           title: "第二条需求" }],
      "/projects/mc/requirements/r1/findings": [],
      "/projects/mc/requirements/r2/findings": [],
      "/projects/mc/requirements/r2": { ...REQ, id: "r2", ref: "R-2" },
      "/projects/mc/requirements/r2/messages": [],
      "/projects/mc/requirements/r2/activity": [],
      "/projects/mc/requirements/r2/previews": [],
    });
    const es = fakeEventSource();
    render(<App devUser="chen" />);
    await userEvent.click(await screen.findByText("订单导出支持自定义字段"));
    await waitFor(() => expect(es.instances.length).toBeGreaterThan(0));
    es.emit("agent_step", { kind: "text", text: "x" }, "8500");

    await userEvent.click(screen.getByRole("button", { name: "← 回需求池" }));
    await userEvent.click(await screen.findByText("第二条需求"));
    await waitFor(() => {
      const last = es.instances[es.instances.length - 1];
      // 后端是 `Event.id > last_event_id` 且 id 是项目级全局自增 ——
      // 带着 8500 去订阅新需求，它那些 id 更小的历史全被跳过，页面空白
      expect(new URL(last.url, "http://x").searchParams.get("lastEventId"))
        .toBe("0");
    });
  });

  it("一条需求只开一条 SSE", async () => {
    mockApi({ "/projects/mc/requirements/r1/findings": [] });
    const es = fakeEventSource();
    render(<App devUser="chen" />);
    await userEvent.click(await screen.findByText("订单导出支持自定义字段"));
    await waitFor(() => expect(es.instances.length).toBeGreaterThan(0));
    // 之前 App 和 useAgentStream 各开一条：同一个 URL 两个长连接，
    // 一个事件打出 5 个 HTTP 请求
    const alive = es.instances.filter(i => !i.closed);
    expect(alive.length).toBe(1);
  });

  it("上一轮的思考不会顶着「实时」显示给下一轮", async () => {
    mockApi({ "/projects/mc/requirements/r1/findings": [] });
    const es = fakeEventSource();
    render(<App devUser="chen" />);
    await userEvent.click(await screen.findByText("订单导出支持自定义字段"));
    await waitFor(() => expect(es.instances.length).toBeGreaterThan(0));
    es.emit("agent_step", { kind: "tool", text: "上一轮读的文件" });
    expect(await screen.findByText("上一轮读的文件")).toBeTruthy();
    es.emit("status", { stage: "clarify", state: "done" });   // 一轮结束
    await waitFor(() => expect(screen.queryByText("上一轮读的文件")).toBeNull());
  });
});
