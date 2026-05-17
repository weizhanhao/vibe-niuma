// 所有 panel 组件 + ProgressTrail。Plan 4 原计划一文件一 panel，这里合并以加速；
// 各 panel 仍是独立 React 组件、可分别 import，未来拆分容易。
// Plan 6 Task 10：老的 SettingsPanel（只存 baseUrl 到旧 storage key）已删除，
// 新版在 panels/SettingsPanel.tsx；这里只保留 capture/clarify/preview/etc.
import React, { useEffect, useRef, useState } from 'react';
import { MSG, type Message } from '../lib/messages';
import type {
  ChangeRequestState, HtmlMockup, LogEntry, PendingCapture, RequestStateMirror,
} from '../lib/types';

const FSM_ORDER: ChangeRequestState[] = [
  'created', 'clarifying', 'located', 'coding', 'building', 'preview-ready',
];
const STATE_LABELS: Record<ChangeRequestState, string> = {
  'created': '排队中',
  'clarifying': '澄清中',
  'located': '定位完成',
  'coding': 'AI 改代码中',
  'building': '构建预览',
  'preview-ready': '预览就绪',
  'merged': '已合并',
  'failed': '失败',
  'expired': '已过期',
  'discarded': '已丢弃',
};

// Phase F：phase 标签的中文 chip 文案。未知 phase 直接显示原 phase 字符串。
const PHASE_LABEL: Record<string, string> = {
  clarifying: '澄清',
  locating: '定位',
  coding: '编码',
  building: '构建',
  init: '/init',
};

const send = (msg: Message) => chrome.runtime.sendMessage(msg);

// ── ProgressTrail ───────────────────────────────────────────────────
export function ProgressTrail({ state }: { state: ChangeRequestState }) {
  const idx = FSM_ORDER.indexOf(state);
  return (
    <div className="trail" aria-label="进度">
      {FSM_ORDER.map((s, i) => {
        let cls = '';
        if (idx === -1) cls = '';
        else if (i < idx) cls = 'done';
        else if (i === idx) cls = 'active';
        return (
          <div key={s} className={`step ${cls}`}>
            <span className="dot" />
            <span>{STATE_LABELS[s]}</span>
            <span className="meta">{i < idx ? '✓' : i === idx ? '…' : ''}</span>
          </div>
        );
      })}
    </div>
  );
}

// ── CapturePanel ────────────────────────────────────────────────────
// 第 1 步：业务员输需求。两条路：
//   1) 「框选区域」—— 想精确定位某区域时用（触发 overlay）
//   2) 「→ 直接提交」—— 不关心区域时（如「做个新功能」），SW 截当前页面 + 空 box
// 两条都进 ReviewCapturePanel，业务员确认后才 POST。
export function CapturePanel() {
  const [text, setText] = useState('');
  const startFrame = () => send({ type: MSG.UI_START_CAPTURE, requestText: text });
  const submitTextOnly = () => send({ type: MSG.SUBMIT_TEXT_ONLY, requestText: text });
  const disabled = !text.trim();
  return (
    <section>
      <div className="eyebrow">
        <span className="ix">STEP 01</span>
        <span>capture</span>
        <span className="rule" />
      </div>
      <h3 className="title">想改这个页面的哪里？</h3>
      <p className="help">用自己的话写下你想看到的变化。我们不在意「怎么实现」——那是系统的事。</p>
      <label className="field">
        <span className="label">
          <span>业务需求 · INTENT</span>
          <span className="count">{text.length} / 500</span>
        </span>
        <textarea
          aria-label="业务需求"
          rows={4}
          value={text}
          onChange={(e) => setText(e.target.value)}
        />
      </label>
      <div className="btn-row">
        <button
          className="btn btn-secondary"
          onClick={startFrame}
          disabled={disabled}
          title="精确定位：在页面上框出要改的区域"
          aria-label="框选区域"
        ><span className="ico">▢</span> 框选区域</button>
        <button
          className="btn btn-primary"
          onClick={submitTextOnly}
          disabled={disabled}
        >→ 直接提交</button>
      </div>
      <p className="hint">点「框选」会画半透明遮罩，鼠标拖一个框就行；不框就直接提交，AI 按 URL 自己定位。</p>
    </section>
  );
}

// ── ReviewCapturePanel ──────────────────────────────────────────────
// Phase G：业务员框选后先弹这个 review 页。
// 截图原图 + 蓝框可视化叠加 + 业务需求可编辑 textarea +「重新框选 / 确认提交」两按钮。
// 蓝框定位：img 实际渲染宽度 / viewport.width 作为缩放因子；box 全部坐标乘缩放因子。
// 业务员看到自己框对了再点确认；点重新框则丢弃 pendingCapture 回 CapturePanel。
export function ReviewCapturePanel({ pendingCapture }: { pendingCapture: PendingCapture }) {
  const [text, setText] = useState<string>(pendingCapture.requestText);
  const imgRef = useRef<HTMLImageElement | null>(null);
  // 已渲染 img 的 px 宽度——拿来按 viewport 比例缩放蓝框。img onLoad 后才有效。
  const [renderedWidth, setRenderedWidth] = useState<number>(0);

  useEffect(() => {
    // 切换 pendingCapture（重新框选后又来一次）时复位输入框默认值。
    setText(pendingCapture.requestText);
  }, [pendingCapture]);

  const onImgLoad = () => {
    if (imgRef.current) {
      setRenderedWidth(imgRef.current.getBoundingClientRect().width);
    }
  };

  const scale = renderedWidth > 0 && pendingCapture.viewport.width > 0
    ? renderedWidth / pendingCapture.viewport.width
    : 0;

  const box = pendingCapture.boxCoords;
  const boxStyle: React.CSSProperties = scale > 0 ? {
    position: 'absolute',
    left: box.x * scale,
    top: box.y * scale,
    width: box.width * scale,
    height: box.height * scale,
    border: '2px solid var(--accent)',
    boxShadow: '0 0 0 9999px oklch(20% 0.02 250 / 0.18)',
    borderRadius: 2,
    pointerEvents: 'none',
    boxSizing: 'border-box',
  } : { display: 'none' };

  const retake = () => send({ type: MSG.RETAKE_CAPTURE });
  const confirm = () => send({ type: MSG.CONFIRM_CAPTURE, requestText: text });

  return (
    <section>
      <div className="eyebrow">
        <span className="ix">STEP 02</span>
        <span>review · confirm region</span>
        <span className="rule" />
      </div>
      <h3 className="title">框对了吗？</h3>
      <p className="help">看一眼框选的位置；不对就重新框，对了就提交给 AI。</p>

      <section className="cap">
        <div
          className="review-shot"
          aria-label="框选区域预览"
        >
          <img
            ref={imgRef}
            onLoad={onImgLoad}
            alt="页面截图"
            src={`data:image/png;base64,${pendingCapture.screenshotB64}`}
            style={{ display: 'block', width: '100%', height: 'auto' }}
          />
          <div data-testid="review-box" style={boxStyle} aria-hidden="true" />
        </div>
        <div className="cap-meta">
          <div className="r"><span className="k">URL</span><span className="v path">{pendingCapture.url}</span></div>
          {pendingCapture.viewport.width > 0 && (
            <div className="r"><span className="k">VIEWPORT</span><span className="v">{pendingCapture.viewport.width}×{pendingCapture.viewport.height}</span></div>
          )}
          <div className="r">
            <span className="k">REGION</span>
            {box.width > 0 && box.height > 0 ? (
              <span className="v">{Math.round(box.width)}×{Math.round(box.height)} px</span>
            ) : (
              <span className="v" style={{ color: 'var(--accent)' }}>无框选 · AI 按 URL 定位</span>
            )}
          </div>
        </div>
        <div className="cap-foot">
          <b>URL</b> 定位仓库里的源文件；<b>截图</b> 给 AI 看你框的区域。两者一并发给 orchestrator。
        </div>
      </section>

      <label className="field">
        <span className="label">
          <span>业务需求 · 可编辑</span>
          <span className="count">{text.length} / 500</span>
        </span>
        <textarea
          aria-label="业务需求"
          rows={3}
          value={text}
          onChange={(e) => setText(e.target.value)}
        />
      </label>

      <div className="btn-row">
        <button className="btn btn-secondary" onClick={retake}>← 重新框选</button>
        <button className="btn btn-primary" onClick={confirm} disabled={!text.trim()}>
          确认提交 →
        </button>
      </div>
    </section>
  );
}

// ── ClarifyPanel ────────────────────────────────────────────────────
// 业务员点「我自己描述」不能立即 submit —— 服务器收到字面字符串就废了一轮。
// 点击后就地展开 textarea，输入后真值提交，继续 brainstorm 下一轮。
// 这些字串匹配「自定义答」选项；server 端 BrainstormingSkill prompt 约定
// 最后一个 option 固定为「我自己描述」。
const _FREE_OPTION_PAT = /^(我自己描述|自己描述|自定义|其他|手动输入|让我说)$/;

export function ClarifyPanel({ state }: { state: RequestStateMirror }) {
  const q = state.pendingQuestion;
  const [freeMode, setFreeMode] = useState(false);
  const [freeText, setFreeText] = useState('');
  // 题目换了（questionId 变）就重置内联编辑状态
  useEffect(() => {
    setFreeMode(false);
    setFreeText('');
  }, [q?.questionId]);

  if (!q) return (
    <section>
      <p className="help">等待澄清问题…</p>
      <LogFeed logs={state.logs} compact maxRows={20} />
    </section>
  );
  const reply = (answer: string) => send({
    type: MSG.SUBMIT_ANSWER, requestId: state.id, questionId: q.questionId, answer,
  });
  const submitFree = () => {
    const t = freeText.trim();
    if (!t) return;
    reply(t);
    setFreeMode(false);
    setFreeText('');
  };
  const totalOptions = q.options?.length ?? 0;
  return (
    <section>
      <div className="eyebrow">
        <span className="ix">STEP 03</span>
        <span>clarify</span>
        <span className="rule" />
        {totalOptions > 0 && <span className="right">{totalOptions} OPTIONS</span>}
      </div>
      <h3 className="title">{q.question}</h3>
      <p className="help">这是业务问题，不涉及技术细节。</p>
      {q.options && q.options.length > 0 && (
        <div className="option-row">
          {q.options.map((opt) => {
            const isFree = _FREE_OPTION_PAT.test(opt);
            const selected = freeMode && isFree;
            return (
              <button
                key={opt}
                className={`option${selected ? ' option--selected' : ''}`}
                onClick={() => (isFree ? setFreeMode(true) : reply(opt))}
              >
                {opt}
              </button>
            );
          })}
        </div>
      )}
      {freeMode && (
        <div className="clarify-freeform">
          <textarea
            autoFocus
            rows={3}
            maxLength={500}
            value={freeText}
            onChange={(e) => setFreeText(e.target.value)}
            placeholder="具体说说你想要的样子……⌘↵ 提交"
            onKeyDown={(e) => {
              if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
                e.preventDefault();
                submitFree();
              }
            }}
          />
          <div className="clarify-freeform__actions">
            <button
              type="button"
              className="btn btn-ghost btn-small"
              onClick={() => { setFreeMode(false); setFreeText(''); }}
            >
              取消
            </button>
            <button
              type="button"
              className="btn btn-primary btn-small"
              disabled={!freeText.trim()}
              onClick={submitFree}
            >
              提交补充 →
            </button>
          </div>
        </div>
      )}
      <div className="btn-row">
        <button
          type="button"
          className="btn btn-ghost btn-small"
          onClick={() => reply('__STOP_CLARIFY__')}
          title="不再问，按现在的理解直接开干"
        >
          ✓ 够了，直接干
        </button>
      </div>
      <LogFeed logs={state.logs} compact maxRows={20} />
    </section>
  );
}

// ── VariantsPanel ───────────────────────────────────────────────────
export function VariantsPanel({ state }: { state: RequestStateMirror }) {
  const v = state.pendingVariants;
  const [picked, setPicked] = useState<string | null>(null);
  if (!v) return null;
  const submit = (id: string | null) => send({
    type: MSG.SUBMIT_ANSWER, requestId: state.id, questionId: v.questionId, answer: id ?? '',
  });
  return (
    <section>
      <div className="eyebrow">
        <span className="ix">STEP 03</span>
        <span>pick a direction</span>
        <span className="rule" />
        <span className="right">{v.variants.length} OPTIONS</span>
      </div>
      <h3 className="title">你想要哪种感觉？</h3>
      <p className="help">这几套不是最终成品，只是「方向锚点」——挑一个，AI 会按这种感觉在真实代码里实现。</p>
      <div className="variants">
        {v.variants.map((m: HtmlMockup) => {
          const selected = picked === m.id;
          return (
            <article
              key={m.id}
              className={`variant ${selected ? 'is-selected' : ''}`}
              onClick={() => setPicked(m.id)}
              role="button"
              aria-pressed={selected}
              aria-label={m.title}
            >
              <div className="variant-preview">
                <iframe
                  title={m.title}
                  srcDoc={m.html}
                  sandbox=""
                />
              </div>
              <div className="variant-meta" aria-hidden="true">
                <span className="t">{m.title}</span>
                <span className="tag">{selected ? 'SELECTED' : 'OPTION'}</span>
              </div>
            </article>
          );
        })}
      </div>
      <div className="btn-row">
        <button className="btn btn-ghost" onClick={() => submit(null)}>都不像</button>
        <button className="btn btn-primary" disabled={!picked} onClick={() => picked && submit(picked)}>
          用选中的方向继续 →
        </button>
      </div>
    </section>
  );
}

// ── LogFeed ─────────────────────────────────────────────────────────
// Phase F：流式 log 显示器。
// - 折叠态：最新 1 行（带 phase chip）
// - 展开态：最近 maxRows 条滚动列表，自动滚到底
// - 空 logs：什么都不渲染（避免占地方）
interface LogFeedProps {
  logs: LogEntry[];
  defaultExpanded?: boolean;
  maxRows?: number;
  compact?: boolean;
}

export function LogFeed({ logs, defaultExpanded = false, maxRows = 50, compact = false }: LogFeedProps) {
  const [expanded, setExpanded] = useState(defaultExpanded);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  // 展开态：每次 logs 变化自动滚到底。
  useEffect(() => {
    if (expanded && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [logs, expanded]);

  if (!logs || logs.length === 0) return null;
  const latest = logs[logs.length - 1];
  const visible = logs.slice(Math.max(0, logs.length - maxRows));

  return (
    <div className={`log-feed${compact ? ' log-feed-compact' : ''}`} aria-label="阶段日志">
      <button
        type="button"
        className="log-feed-head"
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
      >
        <PhaseChip phase={latest.phase} />
        <span className="log-feed-latest">{latest.line}</span>
        <span className="log-feed-toggle" aria-hidden="true">{expanded ? '收起' : '展开'}</span>
      </button>
      {expanded && (
        <div className="log-feed-body" ref={scrollRef} role="log">
          {visible.map((entry, i) => (
            <div key={`${entry.ts}-${i}`} className="log-row">
              <PhaseChip phase={entry.phase} />
              <span className="log-line">{entry.line}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function PhaseChip({ phase }: { phase: string }) {
  const label = PHASE_LABEL[phase] ?? phase;
  return <span className={`phase-chip phase-${phase}`}>{label}</span>;
}

// ── StatusPanel ─────────────────────────────────────────────────────
export function StatusPanel({ state }: { state: RequestStateMirror }) {
  return (
    <section>
      <div className="eyebrow">
        <span className="ix">STEP 04</span>
        <span>AI is working</span>
        <span className="rule" />
      </div>
      <h3 className="title">{STATE_LABELS[state.state]}</h3>
      <p className="help">你不用守在这儿——好了会通知你。下面是实时进度。</p>
      <ProgressTrail state={state.state} />
      {state.branch && (
        <p className="hint">
          分支 <span className="branch-chip">{state.branch}</span>
        </p>
      )}
      <LogFeed logs={state.logs} />
    </section>
  );
}

// ── PreviewPanel ────────────────────────────────────────────────────
export function PreviewPanel({ state }: { state: RequestStateMirror }) {
  const merged = state.state === 'merged';
  const discarded = state.state === 'discarded';
  if (merged) {
    return (
      <section>
        <div className="eyebrow">
          <span className="ix">STEP 06</span>
          <span>complete</span>
          <span className="rule" />
        </div>
        <div className="success-card">
          <span className="tick">✓</span>
          <div>
            <div className="t">合并成功<span aria-hidden="true"> · MERGED → main</span></div>
            <div className="d">刷新原页面就能看到效果了。</div>
          </div>
        </div>
      </section>
    );
  }
  if (discarded) {
    return (
      <section>
        <div className="eyebrow">
          <span className="ix">STATE</span>
          <span>discarded</span>
          <span className="rule" />
        </div>
        <div className="card" style={{ padding: '14px' }}>
          <div style={{ fontFamily: 'var(--mono)', fontSize: '11px', color: 'var(--ink-mute)', letterSpacing: '0.1em', textTransform: 'uppercase' }}>已丢弃</div>
          <div style={{ marginTop: 6, color: 'var(--ink-soft)' }}>分支已保留，方便事后查看。</div>
        </div>
      </section>
    );
  }
  const open = () => state.previewUrl && chrome.tabs.create({ url: state.previewUrl });
  const merge = () => send({ type: MSG.MERGE, requestId: state.id });
  const discard = () => {
    if (confirm('确定丢弃？分支会保留方便事后查看。')) {
      send({ type: MSG.DISCARD, requestId: state.id });
    }
  };
  return (
    <section>
      <div className="eyebrow">
        <span className="ix">STEP 05</span>
        <span>preview ready</span>
        <span className="rule" />
      </div>
      <h3 className="title">预览就绪了——你看下满不满意</h3>
      <section className="preview">
        <div className="preview-strip">change request</div>
        <div className="preview-meta">
          {state.branch && <div className="branch-chip">{state.branch}</div>}
          <div className="preview-url">
            <span className="k">PREVIEW</span><br />
            <span className="v">{state.previewUrl}</span>
          </div>
        </div>
      </section>
      <div className="btn-row">
        <button className="btn btn-secondary" onClick={open}>
          <span className="ico">↗</span> 新标签页打开
        </button>
      </div>
      <p className="help mute" style={{ fontSize: '11.5px' }}>看完觉得 OK 就点「确认合并」，变更进入主分支、上线给所有人；觉得不行就丢弃，分支会留着方便事后翻看。</p>
      <div className="btn-row">
        <button className="btn btn-danger" onClick={discard}>丢弃</button>
        <button className="btn btn-primary" onClick={merge}>确认合并 →</button>
      </div>
    </section>
  );
}

// ── FailedPanel ─────────────────────────────────────────────────────
export function FailedPanel({ state }: { state: RequestStateMirror }) {
  return (
    <section>
      <div className="eyebrow">
        <span className="ix">FAIL</span>
        <span>halted</span>
        <span className="rule" />
      </div>
      <div className="fail-card">
        <div className="phase">PHASE · {state.failPhase ?? '?'}</div>
        <div style={{ color: 'var(--ink-soft)', fontSize: 12 }}>原因：{state.failReason ?? '?'}</div>
      </div>
      <div className="btn-row">
        <button className="btn btn-primary" onClick={() => send({ type: MSG.RETRY, requestId: state.id })}>
          重试
        </button>
      </div>
    </section>
  );
}

// Plan 6 Task 10：老 SettingsPanel 已迁移到 panels/SettingsPanel.tsx，不再在此导出。
