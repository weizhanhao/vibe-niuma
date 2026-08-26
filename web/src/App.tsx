import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  bucketize, columnsFrom, touchCollisions,
} from "./api/board";
import {
  clearCredential, createClient, loadCredential, saveCredential,
  type Client, type Credential, type EnvRow, type MergeJob, type PipelineDef,
  type Project, type Requirement,
} from "./api/client";
import { Note, Pill, ReqCard } from "./components/bits";
import { RequirementView } from "./components/RequirementView";
import { AdminPanel } from "./components/AdminPanel";
import { Intake } from "./components/Intake";

type View = "board" | "queue" | "envs" | "pipeline" | "admin" | "intake";

export function App({ base = "/api", devUser }: { base?: string; devUser?: string }) {
  // 生产走 bearer token（存 localStorage）；devUser 只在服务端开了
  // VP_DEV_AUTH 时有效，用于本地 demo。
  const [cred, setCred] = useState<Credential>(
    () => (devUser ? { devUser } : loadCredential()));
  const api = useMemo<Client>(() => createClient(base, cred), [base, cred]);
  const [needLogin, setNeedLogin] = useState(false);

  const [projects, setProjects] = useState<Project[]>([]);
  const [slug, setSlug] = useState<string>("");
  const [view, setView] = useState<View>("board");
  const [pipeline, setPipeline] = useState<PipelineDef | null>(null);
  const [reqs, setReqs] = useState<Requirement[]>([]);
  const [openId, setOpenId] = useState<string>("");
  const [queue, setQueue] = useState<MergeJob[]>([]);
  const [envs, setEnvs] = useState<EnvRow[]>([]);
  const [err, setErr] = useState("");
  // 立需求：先谈出一份需求稿，确认了才进流程
  const [draft, setDraft] = useState<Requirement | null>(null);

  useEffect(() => {
    if (!cred.token && !cred.devUser) { setNeedLogin(true); return; }
    api.projects().then(ps => {
      setNeedLogin(false);
      setProjects(ps);
      if (ps.length && !slug) setSlug(ps[0].slug);
    }).catch(e => {
      // 401 是"要登录"，不是"出错了" —— 显示登录页而不是红色横幅
      if ((e as { status?: number }).status === 401) { setNeedLogin(true); return; }
      setErr(e.message);
    });
  }, [api, cred]);  // eslint-disable-line react-hooks/exhaustive-deps

  // 用 ref 记当前想看的空间。切 A→B 时 A 的响应可能后到，
  // 不设防就会把 A 的数据写进 B 的界面：侧栏高亮 B、列表显示 A 的需求，
  // 点进去请求 /projects/B/requirements/{A的id} 拿 404。
  const wantRef = useRef(slug);
  useEffect(() => { wantRef.current = slug; }, [slug]);

  const refresh = useCallback(async () => {
    if (!slug) return;
    const mine = slug;
    setErr("");
    try {
      const [p, rs, q, es] = await Promise.all([
        api.pipeline(mine), api.requirements(mine),
        api.mergeQueue(mine), api.environments(mine),
      ]);
      if (wantRef.current !== mine) return;      // 用户已经切走了，丢弃这批
      setPipeline(p); setReqs(rs); setQueue(q); setEnvs(es);
    } catch (e) {
      if (wantRef.current !== mine) return;
      setErr((e as Error).message);
    }
  }, [api, slug]);

  useEffect(() => { void refresh(); }, [refresh]);

  // **一条需求只开一条 SSE。**
  // 之前这里和 `useAgentStream` 各开一条，同一个 URL 两个长连接：
  // HTTP/1.1 每域名 6 连接上限，两条就吃掉三分之一；而且一个 status
  // 事件会同时触发两边刷新 —— 一次事件打出 5 个 HTTP 请求。
  // 实时刷新统一由对话组件的 onChange 回调驱动（它本来就订阅着）。

  const cols = useMemo(() => pipeline ? columnsFrom(pipeline.stages) : [], [pipeline]);
  const buckets = useMemo(() => bucketize(reqs, cols), [reqs, cols]);
  const collisions = useMemo(() => touchCollisions(reqs), [reqs]);
  const open = reqs.find(r => r.id === openId);
  const project = projects.find(p => p.slug === slug);

  if (needLogin) {
    return <Login onToken={t => { saveCredential(t); setCred({ token: t }); }} />;
  }

  return (
    <div className="app">
      <aside className="rail">
        <div className="row" style={{ padding: "4px 8px 14px", gap: 9 }}>
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" aria-hidden>
            <path d="M3 5h5c4 0 4 7 8 7h5" stroke="var(--accent)" strokeWidth="2" strokeLinecap="round" />
            <path d="M3 12h5" stroke="var(--ink-3)" strokeWidth="2" strokeLinecap="round" />
            <path d="M3 19h5c4 0 4-7 8-7h5" stroke="var(--accent)" strokeWidth="2" strokeLinecap="round" opacity=".55" />
            <circle cx="20" cy="12" r="2.4" fill="var(--accent)" />
          </svg>
          <div className="stack">
            <span style={{ fontFamily: "var(--cond)", fontWeight: 700, fontSize: 15 }}>调度台</span>
            <span className="mono" style={{ fontSize: 10, color: "var(--ink-3)" }}>vibe-niuma v2</span>
          </div>
        </div>

        <div className="nav-lab">空间</div>
        {projects.length === 0 && (
          <div className="sub" style={{ padding: "0 10px", fontSize: 12 }}>
            还没有空间 —— 去「空间管理」建一个。
          </div>
        )}
        {projects.map(p => (
          <button key={p.slug} className="nav-i" aria-current={p.slug === slug ? "page" : undefined}
                  onClick={() => { setSlug(p.slug); setOpenId(""); setView("board"); }}>
            {p.name}
            {p.awaiting_review > 0 && (
              <span className="sp"><Pill tone="gate">{p.awaiting_review}</Pill></span>
            )}
          </button>
        ))}
        <button className="nav-i" aria-current={view === "admin" && !open ? "page" : undefined}
                onClick={() => { setView("admin"); setOpenId(""); }}>
          ＋ 空间管理
        </button>

        <div className="nav-lab">这个空间</div>
        {([["board", "需求池"], ["queue", "合并队列"], ["envs", "环境"],
           ["pipeline", "流水线"]] as [View, string][]).map(([k, label]) => (
          <button key={k} className="nav-i" aria-current={view === k && !open ? "page" : undefined}
                  onClick={() => { setView(k); setOpenId(""); }}>{label}</button>
        ))}

        <div style={{ marginTop: "auto", padding: "12px 10px 4px" }}>
          <button className="btn pri" style={{ width: "100%", justifyContent: "center" }}
                  onClick={() => { setDraft(null); setOpenId(""); setView("intake"); }}>
            ＋ 立需求
          </button>
        </div>
      </aside>

      <div className="main">
        <header className="top">
          <div className="row" style={{ gap: 7, fontSize: 13, color: "var(--ink-3)" }}>
            <span>{project?.name ?? "…"}</span>
            {open && <><span>/</span><b style={{ color: "var(--ink)" }}>{open.ref}</b></>}
          </div>
          <div className="sp row" style={{ gap: 8 }}>
            <span className="tag mono">{project?.target_branch}</span>
            {cred.token && (
              <button className="btn sm" onClick={() => {
                clearCredential(); setCred({}); setNeedLogin(true);
              }}>退出</button>
            )}
          </div>
        </header>

        <main className="body">
          {err && <Note tone="bad">{err}</Note>}

          {view === "intake" && !open ? (
            <Intake api={api} slug={slug} draft={draft} onDraft={setDraft}
                    onCancel={() => { setDraft(null); setView("board"); }}
                    onDone={async r => {
                      setDraft(null); setView("board");
                      await refresh(); setOpenId(r.id);
                    }} />
          ) : view === "admin" && !open ? (
            <AdminPanel api={api} projects={projects} onChanged={() => {
              api.projects().then(setProjects).catch(() => {});
            }} />
          ) : open && pipeline ? (
            <RequirementView api={api} slug={slug} req={open} stages={pipeline.stages}
                             onBack={() => setOpenId("")} onChanged={refresh} />
          ) : view === "board" ? (
            <>
              <div className="row">
                <div>
                  <p className="eyebrow">{project?.name} · 需求池</p>
                  <h1 className="h1">所有人的需求，并行地跑</h1>
                </div>
              </div>
              {reqs.length === 0 && <div className="empty">还没有需求。点左下「提需求」开始。</div>}
              <div className="board">
                {cols.map(c => (
                  <section className="col" key={c.key}>
                    <div className="col-h">
                      <span className="t">{c.label}</span>
                      <span className="sp mono num" style={{ fontSize: 11, color: "var(--ink-3)" }}>
                        {buckets.get(c.key)?.length ?? 0}
                      </span>
                    </div>
                    {(buckets.get(c.key) ?? []).map(r => (
                      <ReqCard key={r.id} r={r} collision={collisions.get(r.id)}
                               onOpen={() => setOpenId(r.id)} />
                    ))}
                  </section>
                ))}
              </div>
            </>
          ) : view === "queue" ? (
            <>
              <p className="eyebrow">{project?.name} · 汇流</p>
              <h1 className="h1">合并队列</h1>
              <p className="sub">
                per-repo <b>串行</b>。并行分支各自验证全过 ≠ 合起来能过 ——
                每次 rebase 后都要重跑验证。
              </p>
              {queue.length === 0 ? <div className="empty">队列是空的。</div> : (
                <div style={{ marginTop: 16 }}>
                  {queue.map(j => (
                    <div className="card pad" key={j.id} style={{ marginBottom: 10 }}>
                      <div className="row" style={{ gap: 10, flexWrap: "wrap" }}>
                        <b className="num">{j.position}</b>
                        <span>{j.requirement_ref}</span>
                        <span className="tag">{j.repo_name}</span>
                        <Pill tone={j.state === "conflict" ? "bad"
                          : j.state === "queued" ? "idle" : "run"}>{j.state}</Pill>
                      </div>
                      {j.conflict_ladder.length > 0 && (
                        <div style={{ marginTop: 10 }}>
                          <p className="eyebrow">冲突处理 · 三档递进</p>
                          {j.conflict_ladder.map((r, i) => (
                            <div key={i} className="row" style={{ gap: 8, padding: "6px 0" }}>
                              <span>{r.ok ? "✓" : "◐"}</span>
                              <b style={{ fontSize: 13 }}>{r.stage}</b>
                              <span className="sub" style={{ margin: 0 }}>{r.detail}</span>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </>
          ) : view === "envs" ? (
            <>
              <p className="eyebrow">{project?.name} · 交付</p>
              <h1 className="h1">三层环境</h1>
              <p className="sub">
                并行分支<b>各自验证全过 ≠ 合起来能过</b>。N 条需求第一次真正「在一起」
                运行就是在测试环境 —— 没有它，集成回归直接发生在生产上。
              </p>
              <div className="stack" style={{ gap: 12, marginTop: 18 }}>
                {envs.map(e => (
                  <div className="card pad" key={e.env}>
                    <div className="row" style={{ gap: 10, flexWrap: "wrap" }}>
                      <span className="h2">{e.env}</span>
                      <Pill tone={e.state === "succeeded" ? "ok" : e.state === "failed" ? "bad"
                        : e.state === "never" ? "idle" : "run"}>{e.state}</Pill>
                      {e.url && <a className="sp mono" href={e.url}>{e.url}</a>}
                    </div>
                  </div>
                ))}
              </div>
            </>
          ) : (
            <>
              <p className="eyebrow">{project?.name} · 流水线</p>
              <h1 className="h1">流程是配出来的，不是写死的</h1>
              <p className="sub">加环节改 YAML，换环节实现换 skill 文件 —— 都不动编排代码。</p>
              <div className="tbl-w" style={{ marginTop: 18 }}>
                <table>
                  <thead><tr><th>环节</th><th>实现</th><th>类型</th><th>环境</th></tr></thead>
                  <tbody>
                    {pipeline?.stages.map(s => (
                      <tr key={s.key}>
                        <td><b>{s.label || s.key}</b>
                          <div className="mono" style={{ fontSize: 11, color: "var(--ink-3)" }}>
                            {s.key}
                          </div></td>
                        <td>{s.skill ? <span className="tag mono">skill · {s.skill}</span>
                          : s.adapter ? <span className="tag mono">adapter · {s.adapter}</span>
                          : <span style={{ color: "var(--ink-3)" }}>内建</span>}</td>
                        <td>{s.human_gate ? <Pill tone="gate">人工闸门</Pill> : "自动"}</td>
                        <td>{s.env ?? ""}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {pipeline && (
                <p className="sub">
                  依赖 {pipeline.required_skills.length} 个 skill：
                  {pipeline.required_skills.join("、")}
                </p>
              )}
            </>
          )}
        </main>
      </div>

    </div>
  );
}

function Login({ onToken }: { onToken: (t: string) => void }) {
  const [token, setToken] = useState("");
  return (
    <div style={{ display: "grid", placeItems: "center", minHeight: "100vh" }}>
      <div className="card pad" style={{ width: "min(420px,92vw)" }}>
        <h1 className="h1">并行开发调度台</h1>
        <p className="sub">
          粘贴你的访问令牌。没有的话找空间管理员用
          <code className="mono"> POST /admin/tokens </code>签一个。
        </p>
        <div className="field" style={{ marginTop: 16 }}>
          <label htmlFor="tok">访问令牌</label>
          <input id="tok" type="password" value={token} autoComplete="off"
                 onChange={e => setToken(e.target.value)} placeholder="vp_…" />
        </div>
        <button className="btn pri" style={{ width: "100%", justifyContent: "center" }}
                disabled={!token.trim()} onClick={() => onToken(token.trim())}>
          进入
        </button>
      </div>
    </div>
  );
}
