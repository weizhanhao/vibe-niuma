import { useEffect, useRef, useState } from "react";
import type { Client, Message } from "../api/client";
import { useAgentStream } from "../api/stream";
import { useAutoGrow } from "../hooks/useAutoGrow";
import { Note } from "./bits";
import { Thinking } from "./Thinking";

/** 需求上的对话 —— 澄清问答、续改反馈。
 *
 * 之前完全没有这层：用户提完需求就只能干等，一句话都插不进去。
 * 而「真多轮澄清」是这套东西的核心 —— 业务员表达不清楚，
 * 后面拆解和实现全是白干。 */
export function Conversation({ api, slug, reqId, stage, onSent }: {
  api: Client; slug: string; reqId: string; stage: string; onSent: () => void;
}) {
  const [msgs, setMsgs] = useState<Message[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const boxRef = useRef<HTMLDivElement>(null);
  const boxInput = useRef<HTMLTextAreaElement>(null);
  useAutoGrow(boxInput, draft);

  const load = () => api.messages(slug, reqId).then(setMsgs)
    .catch(e => setErr((e as Error).message));

  // agent 边跑边推的思考。环节走完时回调，取回落到消息上的那份
  const { steps, live } = useAgentStream(api, slug, reqId, () => { void load(); });

  useEffect(() => { void load(); }, [api, slug, reqId]); // eslint-disable-line
  useEffect(() => {
    const box = boxRef.current;
    // jsdom 没有 scrollTo；滚动只是体验，不该让整个页面炸掉
    if (box && typeof box.scrollTo === "function") {
      box.scrollTo({ top: box.scrollHeight });
    }
  }, [msgs.length]);

  const awaiting = msgs.some(m => m.role === "agent" && m.awaiting_answer);

  async function send(proceed = false) {
    const body = draft.trim() || (proceed ? "按现有信息开工" : "");
    if (!body) return;
    setBusy(true); setErr("");
    try {
      await api.say(slug, reqId, body, proceed);
      setDraft("");
      boxInput.current?.focus();      // 别让焦点掉到 body
      await load();
      onSent();
    } catch (e) { setErr((e as Error).message); }
    finally { setBusy(false); }
  }

  /** **中文输入法必须防误发。**
   *
   * 用拼音打字时，敲空格/回车上屏候选词也会触发 keydown —— 没有组合态
   * 判断的话，一句话打到一半就被发出去了。全中文用户的产品，这是最高频的坑。
   * `keyCode === 229` 给不支持 isComposing 的老 WebView 兜底。
   */
  function onKey(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.nativeEvent.isComposing || e.keyCode === 229) return;
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void send(); }
  }

  const WHO: Record<string, string> = { user: "你", agent: "AI", system: "平台" };

  return (
    <div className="card pad" style={{ marginTop: 16 }}>
      <div className="sec-h" style={{ marginTop: 0 }}>
        <h2 className="h2">对话</h2>
        {awaiting && <span className="pill gate"><i className="dot" />等你回答</span>}
        <span className="sub sp" style={{ margin: 0 }}>
          任何阶段都能追加反馈，AI 会接着改
        </span>
      </div>

      {err && <Note tone="bad">{err}</Note>}

      <div ref={boxRef} className="chat" style={{ maxHeight: 320, overflow: "auto" }}>
        {msgs.length === 0 && (
          <div className="sub" style={{ margin: 0 }}>
            还没有对话。AI 觉得需求不清楚时会在这里提问；
            你也可以主动补充说明。
          </div>
        )}
        {msgs.map(m => (
          <div key={m.id} className={`msg ${m.role === "user" ? "me" : "ai"}`}>
            <span className="who">
              {WHO[m.role] ?? m.role}
              {m.stage && ` · ${m.stage}`}
            </span>
            {m.trace?.length > 0 && <Thinking steps={m.trace} />}
            <div style={{ whiteSpace: "pre-wrap" }}>{m.body}</div>
          </div>
        ))}
        {live && (
          <div className="msg ai" style={{ maxWidth: "92%" }}>
            <span className="who">AI</span>
            <Thinking steps={steps} live />
          </div>
        )}
      </div>

      <div className="compose">
        <textarea
          ref={boxInput}
          value={draft}
          rows={1}
          placeholder={awaiting ? "回答 AI 的问题…（⇧Enter 换行）"
                                : "补充说明，或提出修改意见…"}
          onChange={e => setDraft(e.target.value)}
          onKeyDown={onKey}
          aria-label="发消息"
        />
        <button className="btn pri" disabled={busy || !draft.trim()}
                onClick={() => void send()}>发送</button>
      </div>
      {awaiting && (
        <div className="quick">
          <button className="btn sm ok" disabled={busy} onClick={() => void send(true)}>
            ✓ 够了直接干
          </button>
          <span className="sub" style={{ margin: 0, fontSize: 12 }}>
            不再等 AI 追问，按现有信息开工
          </span>
        </div>
      )}
    </div>
  );
}
