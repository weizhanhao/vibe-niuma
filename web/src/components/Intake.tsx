import { useEffect, useRef, useState } from "react";
import type { Client, Message, Requirement } from "../api/client";
import { useAgentStream } from "../api/stream";
import { useAutoGrow } from "../hooks/useAutoGrow";
import { Note, Pill } from "./bits";
import { Thinking } from "./Thinking";

/** 立需求 —— **先谈，谈成型再进流程**。
 *
 * 之前这里是个表单：填完标题正文就直接进 triage 往下跑。可是业务员
 * 坐下来时脑子里往往只有一句「导出太难用了」——表单逼他一次写清楚，
 * 写不清楚就带着含糊往下走，后面拆解、实现全按错的理解做完，
 * 到人工审核才发现方向不对。 */
export function Intake({ api, slug, draft, onDraft, onDone, onCancel }: {
  api: Client; slug: string; draft: Requirement | null;
  onDraft: (r: Requirement) => void; onDone: (r: Requirement) => void;
  onCancel: () => void;
}) {
  const [msgs, setMsgs] = useState<Message[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const boxRef = useRef<HTMLDivElement>(null);
  const boxInput = useRef<HTMLTextAreaElement>(null);
  useAutoGrow(boxInput, text);

  const load = async () => {
    if (!draft) return;
    const [m, r] = await Promise.all([
      api.messages(slug, draft.id), api.requirement(slug, draft.id)]);
    setMsgs(m); onDraft(r);
  };

  // agent 边跑边推的思考。环节走完时回调，取回落到消息上的那份
  const { steps, live } = useAgentStream(api, slug, draft?.id ?? "",
                                         () => { void load().catch(() => {}); });

  useEffect(() => { void load().catch(e => setErr((e as Error).message)); },
    [api, slug, draft?.id]); // eslint-disable-line

  // 草稿还在谈的时候后台在跑，隔几秒取一次
  useEffect(() => {
    if (!draft) return;
    const t = setInterval(() => { void load().catch(() => {}); }, 3000);
    return () => clearInterval(t);
  }, [api, slug, draft?.id]); // eslint-disable-line

  useEffect(() => {
    const b = boxRef.current;
    if (b && typeof b.scrollTo === "function") b.scrollTo({ top: b.scrollHeight });
  }, [msgs.length]);

  useEffect(() => {
    if (draft && !editing) { setTitle(draft.title); setBody(draft.body); }
  }, [draft?.title, draft?.body]); // eslint-disable-line

  const awaiting = msgs.some(m => m.role === "agent" && m.awaiting_answer);
  const thinking = Boolean(draft) && !awaiting
    && msgs.length > 0 && msgs[msgs.length - 1].role === "user";
  // 有需求稿才能确认 —— AI 还在提问的时候不该出现「进流程」
  const hasDraft = Boolean(draft && draft.body.includes("验收标准"));

  async function say() {
    const t = text.trim();
    if (!t) return;
    setBusy(true); setErr("");
    try {
      if (!draft) onDraft(await api.intake(slug, t));
      else { await api.say(slug, draft.id, t); await load(); }
      setText("");
      // **发送后把焦点还回去。** disabled 会让焦点掉到 body，
      // 用户每发一句就要重新点一次输入框。
      boxInput.current?.focus();
    } catch (e) { setErr((e as Error).message); }
    finally { setBusy(false); }
  }

  /** **中文输入法必须防误发。**
   *
   * 用拼音打字时，敲空格/回车上屏候选词也会触发 keydown —— 没有组合态
   * 判断的话，一句话打到一半就被发出去了。对一个全中文用户的产品，
   * 这是最高频最恼人的单点 bug。
   * `keyCode === 229` 是给不支持 isComposing 的老 WebView 兜底。
   */
  function onKey(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.nativeEvent.isComposing || e.keyCode === 229) return;
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void say(); }
  }

  const WHO: Record<string, string> = { user: "你", agent: "AI", system: "平台" };

  return (
    <>
      <div className="row">
        <div>
          <p className="eyebrow">立需求</p>
          <h1 className="h1">先说说你想要什么</h1>
          <p className="sub">
            不用一次写清楚。说一句大白话就行，AI 会看代码、问你几个问题，
            再写成一份需求稿。<b>你确认之后它才进流程。</b>
          </p>
        </div>
        <button className="btn sp" onClick={onCancel}>← 回需求池</button>
      </div>

      {err && <Note tone="bad">{err}</Note>}

      <div className="card pad">
        <div ref={boxRef} className="chat" style={{ maxHeight: 300, overflow: "auto" }}>
          {msgs.length === 0 && (
            <div className="msg ai">
              <span className="who">AI</span>
              你想解决什么问题？比如「订单导出太难用了，每次都要手动删列」。
            </div>
          )}
          {msgs.map(m => (
            <div key={m.id} className={`msg ${m.role === "user" ? "me" : "ai"}`}>
              <span className="who">{WHO[m.role] ?? m.role}</span>
              {m.trace?.length > 0 && <Thinking steps={m.trace} />}
              <div style={{ whiteSpace: "pre-wrap" }}>{m.body}</div>
            </div>
          ))}
          {(live || thinking) && (
            <div className="msg ai" style={{ maxWidth: "92%" }}>
              <span className="who">AI</span>
              <Thinking steps={steps} live />
            </div>
          )}
        </div>

        <div className="compose">
          <textarea ref={boxInput} value={text} rows={1}
                    aria-label="说说你想要什么"
                    placeholder={draft ? "接着说…（⇧Enter 换行）" : "比如：订单导出太难用了"}
                    onChange={e => setText(e.target.value)}
                    onKeyDown={onKey} />
          <button className="btn pri" disabled={busy || !text.trim()}
                  onClick={() => void say()}>发送</button>
        </div>
      </div>

      {draft && (
        <>
          <div className="sec-h">
            <h2 className="h2">需求稿</h2>
            {hasDraft ? <Pill tone="ok">已成型</Pill> : <Pill tone="idle">还在谈</Pill>}
            <button className="btn sm sp" onClick={() => setEditing(v => !v)}>
              {editing ? "取消编辑" : "✎ 直接改"}
            </button>
          </div>

          {editing ? (
            <div className="card pad">
              <div className="field">
                <label htmlFor="dt">标题</label>
                <input id="dt" value={title} onChange={e => setTitle(e.target.value)} />
              </div>
              <div className="field">
                <label htmlFor="db">正文</label>
                <textarea id="db" rows={10} value={body}
                          onChange={e => setBody(e.target.value)} />
              </div>
              <button className="btn" disabled={busy} onClick={async () => {
                setBusy(true); setErr("");
                try {
                  onDraft(await api.editDraft(slug, draft.id, { title, body }));
                  setEditing(false);
                } catch (e) { setErr((e as Error).message); }
                finally { setBusy(false); }
              }}>保存</button>
            </div>
          ) : (
            <div className="card pad">
              <div style={{ fontWeight: 650, fontSize: 15 }}>{draft.title}</div>
              <div style={{ whiteSpace: "pre-wrap", marginTop: 8, fontSize: 13.5,
                            lineHeight: 1.65, color: "var(--ink-2)" }}>
                {draft.body}
              </div>
            </div>
          )}

          <div className="row" style={{ gap: 9, marginTop: 14 }}>
            <button className="btn pri" disabled={busy || !draft.title.trim()}
                    onClick={async () => {
                      setBusy(true); setErr("");
                      try { onDone(await api.submitDraft(slug, draft.id)); }
                      catch (e) { setErr((e as Error).message); }
                      finally { setBusy(false); }
                    }}>
              ✓ 确认，进流程
            </button>
            <span className="sub" style={{ margin: 0, fontSize: 12 }}>
              {hasDraft
                ? "进流程后就开始并行拆解开发了。还想改就先在上面接着说。"
                : "还没谈出需求稿。你也可以现在就进流程 —— 后面的澄清环节还会再问一轮。"}
            </span>
          </div>
        </>
      )}
    </>
  );
}
