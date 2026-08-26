import { useEffect, useState } from "react";
import type { Client, Finding, Preview, Requirement, StageDef } from "../api/client";
import { groupByAxis, reviewSummary } from "../api/board";
import { guideFor, WHO_LABEL } from "../api/stages";
import { ActivityLog } from "./Activity";
import { Conversation } from "./Conversation";
import { GateRail, Note, Pill } from "./bits";

const AXES: Record<string, { name: string; by: string; desc: string }> = {
  defect: { name: "缺陷轴", by: "ocr", desc: "代码本身有没有 bug —— NPE / 线程安全 / 注入等" },
  spec:   { name: "规格轴", by: "skill code-review", desc: "有没有做到需求和契约说的事" },
  norm:   { name: "规范轴", by: "skill code-review", desc: "符不符合本仓已记录的编码规范" },
};
const SEV_TONE: Record<string, "bad" | "gate" | "idle"> = {
  critical: "bad", high: "bad", medium: "gate", low: "idle",
};

export function RequirementView({ api, slug, req, stages, onBack, onChanged }: {
  api: Client; slug: string; req: Requirement; stages: StageDef[];
  onBack: () => void; onChanged: () => void;
}) {
  const [findings, setFindings] = useState<Finding[]>([]);
  // 点闸门轨上任意一格，看那一环做什么；默认看当前环节
  const [picked, setPicked] = useState(req.stage);
  // 需求一往前走就重取流程记录。
  // **不能用字符串长度当版本号** —— `verify`(6)→`review`(6)、
  // `implement`(9)→`ai_review`(9) 长度相同，而这恰恰是最重要的两次状态
  // 迁移：用户最想看「验证为什么挂了」的时候，流程记录是旧的。
  const tick = `${req.stage}:${req.tasks.filter(t => t.state === "done").length}`;
  const [showDropped, setShowDropped] = useState(false);
  const [previews, setPreviews] = useState<Preview[]>([]);
  const [busy, setBusy] = useState(false);
  const stuck = req.state === "failed" || req.state === "blocked";
  const [err, setErr] = useState("");

  useEffect(() => {
    // 过期响应防护：切需求时上一条的复核发现不能渲染到新需求上 ——
    // 「复核发现张冠李戴」会直接误导审核决定
    let alive = true;
    api.findings(slug, req.id, showDropped)
      .then(f => { if (alive) setFindings(f); })
      .catch(e => { if (alive) setErr(String(e.message)); });
    return () => { alive = false; };
  }, [api, slug, req.id, showDropped]);
  // 需求往前走了就跟着看新环节，别停在旧的那一格上
  useEffect(() => { setPicked(req.stage); }, [req.stage]);
  useEffect(() => {
    let alive = true;
    api.previews(slug, req.id)
      .then(p => { if (alive) setPreviews(p); })
      .catch(() => { if (alive) setPreviews([]); });
    return () => { alive = false; };
  }, [api, slug, req.id, req.stage]);

  const here = stages.find(x => x.key === req.stage);
  const shown = stages.find(x => x.key === picked) ?? here;
  const guide = guideFor(shown?.key ?? req.stage);
  const atPicked = picked === req.stage;
  const kept = findings.filter(f => f.kept);
  const summary = reviewSummary(findings);
  const byAxis = groupByAxis(findings);

  async function decide(decision: string) {
    setBusy(true); setErr("");
    try { await api.review(slug, req.id, decision); onChanged(); }
    catch (e) { setErr((e as Error).message); }
    finally { setBusy(false); }
  }

  return (
    <>
      <div className="row">
        <div style={{ minWidth: 0 }}>
          <p className="eyebrow">{req.ref} · {req.requested_by} 提出</p>
          <h1 className="h1">{req.title}</h1>
        </div>
        <button className="btn sp" onClick={onBack}>← 回需求池</button>
      </div>

      <GateRail stages={stages} current={req.stage}
                picked={picked} onPick={setPicked} />

      {/* 「现在到底在干嘛、我该干嘛」—— 之前界面只有一条轨，
          用户看到环节名并不知道要不要动手。 */}
      {shown && (
        <div className="card pad stagecard">
          <div className="row" style={{ gap: 9, flexWrap: "wrap" }}>
            <h2 className="h2" style={{ margin: 0 }}>{shown.label || shown.key}</h2>
            <Pill tone={guide.who === "you" ? "gate" : atPicked ? "run" : "idle"}>
              {atPicked ? WHO_LABEL[guide.who] : "尚未走到"}
            </Pill>
            {!atPicked && (
              <button className="btn sm sp" onClick={() => setPicked(req.stage)}>
                回到当前环节
              </button>
            )}
          </div>
          <p className="sub" style={{ margin: "8px 0 0" }}
             dangerouslySetInnerHTML={{ __html: guide.doing }} />
          {guide.can && (
            <p className="sub" style={{ margin: "6px 0 0" }}>
              <b style={{ color: "var(--ink-2)" }}>你能做的：</b>{guide.can}
            </p>
          )}
        </div>
      )}

      {req.body.trim() && (
        <div className="card pad" style={{ marginTop: 12 }}>
          <p className="eyebrow">需求原文</p>
          <div style={{ whiteSpace: "pre-wrap", marginTop: 7, fontSize: 13.5,
                        lineHeight: 1.6 }}>{req.body}</div>
        </div>
      )}

      {/* 对话放在最前面 —— 澄清环节 AI 就在这里等回话 */}
      <Conversation api={api} slug={slug} reqId={req.id} stage={req.stage}
                    onSent={onChanged} />

      {stuck && (
        <Note tone="bad">
          <div style={{ flex: 1 }}>
            <b>这条需求停在「{here?.label || req.stage}」了。</b>
            {req.state === "blocked"
              ? "平台缺了这个环节需要的能力（agent / 工位 / 复核器），"
                + "不是代码问题 —— 先把能力补上再重试。"
              : "下面「流程记录」里有失败原因。补充说明后它会带着新信息重跑；"
                + "如果只是外部抖动，直接重试。"}
          </div>
          <button className="btn sp" disabled={busy} onClick={async () => {
            setBusy(true); setErr("");
            try { await api.retry(slug, req.id); onChanged(); }
            catch (e) { setErr((e as Error).message); }
            finally { setBusy(false); }
          }}>↻ 从这一环重试</button>
        </Note>
      )}

      {req.state === "discarded" && (
        <Note tone="idle">这条需求已被关闭，不再推进。</Note>
      )}

      {req.sequence_kind && (
        <Note tone="idle">
          <b>wide refactor</b> —— 这条需求走 expand → migrate → contract 序列。
          它的 touches 大面积相交是<b>预期的</b>，不按普通冲突规则卡住。
        </Note>
      )}

      {req.tasks.length > 0 && (
        <>
          <div className="sec-h"><h2 className="h2">并行任务</h2>
            <span className="tag mono">skill · to-tickets</span>
            <span className="sub" style={{ margin: 0 }}>
              垂直切片 + 声明 blocking edges 与 touches
            </span>
          </div>
          <div className="tbl-w">
            <table>
              <thead><tr><th>任务</th><th>仓</th><th>预计触达</th><th>依赖</th><th>状态</th></tr></thead>
              <tbody>
                {req.tasks.map(t => (
                  <tr key={t.id}>
                    <td><b>{t.key}</b> · {t.title}
                      {t.sequence && <span className="tag" style={{ marginLeft: 6 }}>{t.sequence}</span>}
                    </td>
                    <td>{t.repos.map(r => <span key={r} className="tag">{r}</span>)}</td>
                    <td>{t.touches.map(p => (
                      <div key={p} className="mono" style={{ color: "var(--ink-2)" }}>{p}</div>
                    ))}</td>
                    <td>{t.depends_on.length ? t.depends_on.join(", ")
                      : <span style={{ color: "var(--ink-3)" }}>无 —— 可并行</span>}</td>
                    <td>
                      {t.state}
                      {t.attempts > 1 && (
                        <span className="tag" style={{ marginLeft: 5 }}>
                          第 {t.attempts} 次
                        </span>
                      )}
                      {t.fail_reason && (
                        <div className="sub" style={{ margin: "4px 0 0", fontSize: 12,
                                                      color: "var(--bad)" }}>
                          {t.fail_reason}
                        </div>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {previews.length > 0 && (
        <>
          <div className="sec-h"><h2 className="h2">预览</h2>
            <span className="sub" style={{ margin: 0 }}>
              每个并行分支一个独立环境，点开就能看效果
            </span>
          </div>
          <div className="card pad stack" style={{ gap: 8 }}>
            {previews.map(pv => (
              <div key={pv.branch} className="row" style={{ gap: 9 }}>
                <span className="tag">{pv.task_key || pv.branch}</span>
                <a className="mono sp" href={pv.url} target="_blank" rel="noreferrer"
                   style={{ color: "var(--accent)", fontSize: 12.5 }}>{pv.url} ↗</a>
              </div>
            ))}
          </div>
          <p className="sub" style={{ fontSize: 12 }}>
            工位回收后链接就没了 —— 这里不显示就是已经收掉了，不是坏了。
          </p>
        </>
      )}

      <ActivityLog api={api} slug={slug} reqId={req.id} tick={tick}
                   labelOf={k => stages.find(x => x.key === k)?.label || k} />

      {req.contracts.length > 0 && (
        <>
          <div className="sec-h"><h2 className="h2">接口契约</h2>
            <span className="sub" style={{ margin: 0 }}>跨仓任务先把契约定死，两边才能真并行</span>
          </div>
          <div className="card pad stack" style={{ gap: 7 }}>
            {req.contracts.map(c => (
              <code key={c} className="mono" style={{ padding: "8px 10px",
                background: "var(--surface-3)", borderRadius: 6 }}>{c}</code>
            ))}
          </div>
        </>
      )}

      {/* 加载错误在任何环节都要看得见 —— 之前只在 review 态渲染，
          其它环节出错时页面只是空着，用户不知道发生了什么 */}
      {err && req.stage !== "review" && <Note tone="bad">{err}</Note>}

      <div className="sec-h">
        <h2 className="h2">AI 复核</h2>
        <Pill tone={summary.tone as never}>{summary.label}</Pill>
        <button className="btn sm sp" onClick={() => setShowDropped(v => !v)}>
          {showDropped ? "只看保留的" : "含被过滤的"}
        </button>
      </div>

      <Note tone="run">
        两轴复核跑在<b>独立于写代码那个 session</b> 的进程里 —— 写的人不觉得自己写错了。
        <br />（相反：合并阶段的解冲突 agent <b>必须</b>带原会话，因为它需要知道意图。）
      </Note>

      {Object.entries(AXES).map(([k, ax]) => (
        <div key={k} style={{ marginTop: 16 }}>
          <div className="sec-h" style={{ marginTop: 0 }}>
            <h2 className="h2">{ax.name}</h2>
            <span className="tag mono">{ax.by}</span>
            <span className="sp">
              {byAxis[k]?.length
                ? <Pill tone="gate">{byAxis[k].length} 条</Pill>
                : <Pill tone="idle">本次未发现</Pill>}
            </span>
          </div>
          <p className="sub" style={{ margin: "0 0 10px" }}>{ax.desc}</p>
          {(byAxis[k] ?? []).map(f => (
            <div key={f.id} className="card pad"
                 style={{ marginBottom: 9, opacity: f.kept ? 1 : 0.55 }}>
              <div className="row" style={{ gap: 8, flexWrap: "wrap", marginBottom: 7 }}>
                <Pill tone={SEV_TONE[f.severity] ?? "idle"}>{f.severity}</Pill>
                <span className="tag">{f.category}</span>
                <span className="mono" style={{ fontSize: 11.5, color: "var(--ink-2)" }}>
                  {f.path}:{f.start_line}
                </span>
                {!f.kept && <span className="tag">已被过滤</span>}
              </div>
              <div style={{ fontWeight: 600, fontSize: 13.5,
                            textDecoration: f.kept ? "none" : "line-through" }}>{f.claim}</div>
              {f.failure_scenario && (
                <div className="sub" style={{ margin: 0 }}>
                  <b style={{ color: "var(--ink-2)" }}>怎么挂：</b>{f.failure_scenario}
                </div>
              )}
              {f.verdict_reason && (
                <div className="sub" style={{ margin: "6px 0 0", fontSize: 12 }}>
                  裁决（{f.confidence}）：{f.verdict_reason}
                </div>
              )}
            </div>
          ))}
        </div>
      ))}

      {kept.length === 0 && (
        <Note tone="gate">
          <b>「未发现」不等于「没有问题」。</b>实测同一份 diff 三次跑出 2 / 0 / 0 ——
          复核的召回不稳定，是它用召回换精确的设计取舍。
          <b>不要据此跳过人工审核。</b>
        </Note>
      )}

      {here?.human_gate && (
        <div className="card pad" style={{ marginTop: 18 }}>
          <p className="eyebrow">你的决定 · {here.label || here.key}</p>
          {err && <Note tone="bad">{err}</Note>}
          <div className="row" style={{ gap: 8, marginTop: 10 }}>
            <button className="btn ok" disabled={busy} onClick={() => decide("approve")}>
              {req.stage === "release" ? "✓ 放行上生产" : "✓ 通过，进合并队列"}
            </button>
            <button className="btn" disabled={busy} onClick={() => decide("changes")}>
              ✎ 打回改
            </button>
            <button className="btn" disabled={busy} onClick={() => decide("reject")}>
              ✕ 拒绝
            </button>
          </div>
          <p className="sub" style={{ fontSize: 12 }}>
            流程停在这里期间<b>不占并行工位</b> —— 工位已回收，其他需求能用。
          </p>
        </div>
      )}
    </>
  );
}
