import { useEffect, useState } from "react";
import type { Client, Project, Repo } from "../api/client";
import { Note } from "./bits";

/** 空间管理 —— 建空间、绑仓、加成员、签令牌。
 *
 * 之前这些只有 API 没有界面，只能 curl。
 * 「第一个真实用户没有入口」是专家审查点名的缺口之一。 */
export function AdminPanel({ api, projects, onChanged }: {
  api: Client; projects: Project[]; onChanged: () => void;
}) {
  const [slug, setSlug] = useState(projects[0]?.slug ?? "");
  const [repos, setRepos] = useState<Repo[]>([]);
  const [err, setErr] = useState("");
  const [ok, setOk] = useState("");
  const [issued, setIssued] = useState<{ user: string; token: string } | null>(null);
  const proj = projects.find(x => x.slug === slug);

  useEffect(() => {
    if (!slug) return;
    api.repos(slug).then(setRepos).catch(() => setRepos([]));
  }, [api, slug]);

  async function run(what: string, fn: () => Promise<unknown>) {
    setErr(""); setOk("");
    try { await fn(); setOk(`${what}成功`); onChanged(); }
    catch (e) { setErr(`${what}失败：${(e as Error).message}`); }
  }

  return (
    <>
      <div className="row">
        <div>
          <p className="eyebrow">管理</p>
          <h1 className="h1">空间与成员</h1>
          <p className="sub">
            建空间、绑代码仓、加成员、签访问令牌。只有 admin 能改。
          </p>
        </div>
      </div>

      {err && <Note tone="bad">{err}</Note>}
      {ok && <Note tone="ok">{ok}</Note>}

      <div className="sec-h"><h2 className="h2">新建空间</h2>
        <span className="sub" style={{ margin: 0 }}>一个空间 = 一个产品 = 一套仓 + 一条流水线</span>
      </div>
      <NewProject api={api} onDone={() => run("建空间", async () => {})} />

      {projects.length > 0 && (
        <>
          <div className="sec-h">
            <h2 className="h2">管理现有空间</h2>
            <select value={slug} onChange={e => setSlug(e.target.value)}
                    aria-label="选择空间"
                    style={{ padding: "6px 10px", borderRadius: 6,
                             border: "1px solid var(--line-strong)",
                             background: "var(--surface)", color: "var(--ink)" }}>
              {projects.map(p => <option key={p.slug} value={p.slug}>{p.name}</option>)}
            </select>
          </div>

          <Note tone="idle">
            <div>
              <b>一个空间可以绑多个仓。</b>需求会同时落到相关的几个仓上并行开发，
              合并时按仓各自排队。空间有一条<b>集成分支</b>（
              <code className="mono">{proj?.target_branch ?? "vibe/dev"}</code>），
              它在每个仓里是各自的一条分支 —— 仓里还没有的话，
              平台从这个仓自己的主干起步。
            </div>
          </Note>

          <div className="tbl-w" style={{ marginBottom: 14 }}>
            <table>
              <thead><tr><th>已绑代码仓</th><th>地址</th><th>主干分支</th></tr></thead>
              <tbody>
                {repos.length === 0 && (
                  <tr><td colSpan={3} style={{ color: "var(--ink-3)" }}>还没绑仓</td></tr>
                )}
                {repos.map(r => (
                  <tr key={r.id}>
                    <td><b>{r.name}</b></td>
                    <td className="mono" style={{ fontSize: 11.5 }}>{r.url}</td>
                    <td>{r.default_branch}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <AddRepo onAdd={(b) => run("绑仓", () => api.addRepo(slug, b)
            .then(() => api.repos(slug).then(setRepos)))} />
          <AddMember onAdd={(b) => run("加成员", () => api.addMember(slug, b))} />
          <IssueToken onIssue={async (u) => {
            setErr(""); setOk("");
            try {
              const r = await api.issueToken(u);
              setIssued({ user: r.user_id, token: r.token });
            } catch (e) { setErr(`签令牌失败：${(e as Error).message}`); }
          }} />

          {issued && (
            <Note tone="gate">
              <div>
                <b>{issued.user} 的访问令牌（只显示这一次）</b>
                <div className="mono" style={{ marginTop: 6, padding: "8px 10px",
                     background: "var(--surface)", borderRadius: 6, wordBreak: "break-all" }}>
                  {issued.token}
                </div>
                <div style={{ marginTop: 6, fontSize: 12 }}>
                  库里只存哈希，关掉就找不回来了 —— 现在就发给对方。
                </div>
              </div>
            </Note>
          )}
        </>
      )}
    </>
  );
}

function NewProject({ api, onDone }: { api: Client; onDone: () => void }) {
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [orgId, setOrgId] = useState("");
  const [msg, setMsg] = useState("");

  return (
    <div className="card pad">
      <div className="field">
        <label htmlFor="pn">空间名</label>
        <input id="pn" value={name} placeholder="例：商户中台"
               onChange={e => setName(e.target.value)} />
      </div>
      <div className="field">
        <label htmlFor="ps">标识（英文小写，URL 里用）</label>
        <input id="ps" value={slug} placeholder="mc"
               onChange={e => setSlug(e.target.value)} />
      </div>
      <div className="field">
        <label htmlFor="po">组织 ID</label>
        <input id="po" value={orgId} placeholder="从 /admin/bootstrap 拿"
               onChange={e => setOrgId(e.target.value)} />
      </div>
      {msg && <Note tone={msg.startsWith("✓") ? "ok" : "bad"}>{msg}</Note>}
      <button className="btn pri" disabled={!name.trim() || !slug.trim() || !orgId.trim()}
              onClick={async () => {
                try {
                  await api.createProject({ name, slug, org_id: orgId });
                  setMsg(`✓ 已建空间 ${name}`); setName(""); setSlug("");
                  onDone();
                } catch (e) { setMsg(`建空间失败：${(e as Error).message}`); }
              }}>建空间</button>
    </div>
  );
}

function AddRepo({ onAdd }: {
  onAdd: (b: { name: string; url: string; default_branch?: string;
               pat_ref?: string }) => void;
}) {
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [branch, setBranch] = useState("main");
  const [pat, setPat] = useState("");
  return (
    <div className="card pad" style={{ marginBottom: 12 }}>
      <p className="eyebrow">绑一个代码仓</p>
      <div className="row" style={{ gap: 8, marginTop: 10, flexWrap: "wrap" }}>
        <input value={name} placeholder="仓名（如 orders-api）" aria-label="仓名"
               onChange={e => setName(e.target.value)}
               style={{ flex: "0 0 180px", padding: "8px 10px", borderRadius: 6,
                        border: "1px solid var(--line-strong)",
                        background: "var(--surface)", color: "var(--ink)" }} />
        <input value={url} placeholder="https://github.com/org/repo.git" aria-label="仓地址"
               onChange={e => setUrl(e.target.value)}
               style={{ flex: 1, minWidth: 220, padding: "8px 10px", borderRadius: 6,
                        border: "1px solid var(--line-strong)",
                        background: "var(--surface)", color: "var(--ink)" }} />
        <input value={branch} placeholder="主干分支" aria-label="主干分支"
               onChange={e => setBranch(e.target.value)}
               style={{ flex: "0 0 120px", padding: "8px 10px", borderRadius: 6,
                        border: "1px solid var(--line-strong)",
                        background: "var(--surface)", color: "var(--ink)" }} />
        <input value={pat} placeholder="私有仓填 env:变量名" aria-label="凭证引用"
               onChange={e => setPat(e.target.value)}
               style={{ flex: "0 0 190px", padding: "8px 10px", borderRadius: 6,
                        border: "1px solid var(--line-strong)",
                        background: "var(--surface)", color: "var(--ink)" }} />
        <button className="btn" disabled={!name.trim() || !url.trim()}
                onClick={() => onAdd({ name, url, default_branch: branch || "main",
                                       pat_ref: pat || undefined })}>绑定</button>
      </div>
      <p className="sub" style={{ fontSize: 12 }}>
        <b>主干分支按仓填</b>——老仓是 <code className="mono">master</code>、
        新仓是 <code className="mono">main</code> 很常见。这个仓还没有空间集成分支时，
        平台就从它自己的主干起步。填错会在建工位时报「找不到起点分支」。
      </p>
      <p className="sub" style={{ fontSize: 12 }}>
        私有仓必须填凭证引用（如 <code className="mono">env:GH_PAT</code>）——
        <b>库里只存引用不存明文</b>。不填的话 clone 会因为没凭证失败。
      </p>
    </div>
  );
}

function AddMember({ onAdd }: { onAdd: (b: { user_id: string; role: string }) => void }) {
  const [user, setUser] = useState("");
  const [role, setRole] = useState("requester");
  return (
    <div className="card pad" style={{ marginBottom: 12 }}>
      <p className="eyebrow">加成员</p>
      <div className="row" style={{ gap: 8, marginTop: 10 }}>
        <input value={user} placeholder="用户名" aria-label="用户名"
               onChange={e => setUser(e.target.value)}
               style={{ flex: 1, padding: "8px 10px", borderRadius: 6,
                        border: "1px solid var(--line-strong)",
                        background: "var(--surface)", color: "var(--ink)" }} />
        <select value={role} onChange={e => setRole(e.target.value)} aria-label="角色"
                style={{ padding: "8px 10px", borderRadius: 6,
                         border: "1px solid var(--line-strong)",
                         background: "var(--surface)", color: "var(--ink)" }}>
          <option value="requester">requester · 只提需求</option>
          <option value="reviewer">reviewer · 能审核</option>
          <option value="admin">admin · 能改配置</option>
        </select>
        <button className="btn" disabled={!user.trim()}
                onClick={() => onAdd({ user_id: user, role })}>添加</button>
      </div>
    </div>
  );
}

function IssueToken({ onIssue }: { onIssue: (u: string) => void }) {
  const [user, setUser] = useState("");
  return (
    <div className="card pad">
      <p className="eyebrow">签访问令牌</p>
      <div className="row" style={{ gap: 8, marginTop: 10 }}>
        <input value={user} placeholder="给谁签" aria-label="用户名"
               onChange={e => setUser(e.target.value)}
               style={{ flex: 1, padding: "8px 10px", borderRadius: 6,
                        border: "1px solid var(--line-strong)",
                        background: "var(--surface)", color: "var(--ink)" }} />
        <button className="btn" disabled={!user.trim()}
                onClick={() => onIssue(user)}>签发</button>
      </div>
    </div>
  );
}
