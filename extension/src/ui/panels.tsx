// 所有 panel 组件 + ProgressTrail。Plan 4 原计划一文件一 panel，这里合并以加速；
// 各 panel 仍是独立 React 组件、可分别 import，未来拆分容易。
import React, { useEffect, useRef, useState } from 'react';
import { MSG, type Message } from '../lib/messages';
import { getBaseUrl, setBaseUrl } from '../background/orchestrator-client';
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
export function CapturePanel() {
  const [text, setText] = useState('');
  const start = () => send({ type: MSG.UI_START_CAPTURE, requestText: text });
  return (
    <section>
      <div className="eyebrow">第 1 步</div>
      <h3 className="title">想改这个页面的哪里？</h3>
      <p className="help">用自己的话写下你想看到的变化。我们不在意「怎么实现」。</p>
      <label className="field">
        <span className="field-label">业务需求</span>
        <textarea
          aria-label="业务需求"
          rows={4}
          value={text}
          onChange={(e) => setText(e.target.value)}
        />
      </label>
      <div className="btn-row">
        <button className="btn btn-primary" onClick={start} disabled={!text.trim()}>
          开始框选区域
        </button>
      </div>
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
      <div className="eyebrow">第 2 步 · 确认要改的区域</div>
      <h3 className="title">框对了吗？</h3>
      <p className="help">看一眼框选的位置；不对就重新框，对了就提交给 AI。</p>

      <div
        className="review-shot"
        style={{ position: 'relative', display: 'block', width: '100%', borderRadius: 'var(--r-md)', overflow: 'hidden', border: '1px solid var(--line)' }}
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

      <div className="card" style={{ display: 'grid', gap: '0.25rem', fontSize: '0.78rem', color: 'var(--ink-soft)' }}>
        <div style={{ wordBreak: 'break-all' }}>URL: <code>{pendingCapture.url}</code></div>
        <div>视口: {pendingCapture.viewport.width}×{pendingCapture.viewport.height}</div>
        <div>框选: {Math.round(box.width)}×{Math.round(box.height)} px</div>
      </div>

      <label className="field">
        <span className="field-label">业务需求（可编辑）</span>
        <textarea
          aria-label="业务需求"
          rows={3}
          value={text}
          onChange={(e) => setText(e.target.value)}
        />
      </label>

      <div className="btn-row">
        <button className="btn btn-secondary" onClick={retake}>← 重新框选</button>
        <button className="btn btn-accent" onClick={confirm} disabled={!text.trim()}>
          ✓ 确认提交 →
        </button>
      </div>
    </section>
  );
}

// ── ClarifyPanel ────────────────────────────────────────────────────
export function ClarifyPanel({ state }: { state: RequestStateMirror }) {
  const q = state.pendingQuestion;
  const [free, setFree] = useState('');
  if (!q) return (
    <section>
      <p className="help">等待澄清问题…</p>
      <LogFeed logs={state.logs} compact maxRows={20} />
    </section>
  );
  const reply = (answer: string) => send({
    type: MSG.SUBMIT_ANSWER, requestId: state.id, questionId: q.questionId, answer,
  });
  return (
    <section>
      <div className="eyebrow">第 3 步 · 澄清</div>
      <h3 className="title">{q.question}</h3>
      <p className="help">这是业务问题，不涉及技术细节。</p>
      {q.options && q.options.length > 0 && (
        <div className="option-row">
          {q.options.map((opt) => (
            <button key={opt} className="option" onClick={() => reply(opt)}>{opt}</button>
          ))}
        </div>
      )}
      <label className="field">
        <span className="field-label">或直接回答</span>
        <input value={free} onChange={(e) => setFree(e.target.value)} />
      </label>
      <div className="btn-row">
        <button className="btn btn-secondary" onClick={() => reply('')}>跳过</button>
        <button className="btn btn-primary" onClick={() => reply(free)} disabled={!free.trim()}>
          回答
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
      <div className="eyebrow">第 3 步 · 选方向</div>
      <h3 className="title">你想要哪种感觉？</h3>
      <p className="help">挑一个意图锚点，AI 会按这种感觉在真实代码里实现。</p>
      <div style={{ display: 'grid', gap: '0.6rem' }}>
        {v.variants.map((m: HtmlMockup) => (
          <article
            key={m.id}
            className="card"
            style={{
              cursor: 'pointer',
              borderColor: picked === m.id ? 'var(--accent)' : undefined,
              boxShadow: picked === m.id ? '0 0 0 3px var(--accent-soft)' : undefined,
            }}
            onClick={() => setPicked(m.id)}
            role="button"
            aria-pressed={picked === m.id}
          >
            <div style={{ fontWeight: 500, marginBottom: '0.4rem' }}>{m.title}</div>
            <iframe
              title={m.title}
              srcDoc={m.html}
              sandbox=""
              style={{ width: '100%', height: '120px', border: '1px solid var(--line-soft)', borderRadius: 6 }}
            />
          </article>
        ))}
      </div>
      <div className="btn-row">
        <button className="btn btn-secondary" onClick={() => submit(null)}>都不像</button>
        <button className="btn btn-primary" disabled={!picked} onClick={() => picked && submit(picked)}>
          用选中的方向继续
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
      <div className="eyebrow">进行中</div>
      <h3 className="title">{STATE_LABELS[state.state]}</h3>
      <ProgressTrail state={state.state} />
      {state.branch && (
        <p className="help">分支 <code>{state.branch}</code></p>
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
        <div className="success-card">
          <span className="tick">✓</span>
          <div>
            <strong style={{ display: 'block' }}>合并成功</strong>
            刷新原页面就能看到效果了。
          </div>
        </div>
      </section>
    );
  }
  if (discarded) {
    return (
      <section>
        <div className="card"><strong>已丢弃</strong></div>
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
      <div className="eyebrow">第 5 步 · 看效果</div>
      <h3 className="title">预览就绪</h3>
      <div className="card">
        <div style={{ fontSize: '0.78rem', color: 'var(--ink-mute)' }}>预览地址</div>
        <div style={{ wordBreak: 'break-all' }}><code>{state.previewUrl}</code></div>
      </div>
      <div className="btn-row">
        <button className="btn btn-secondary" onClick={open}>新标签打开</button>
      </div>
      <div className="btn-row">
        <button className="btn btn-danger" onClick={discard}>丢弃</button>
        <button className="btn btn-accent" onClick={merge}>确认合并 →</button>
      </div>
    </section>
  );
}

// ── FailedPanel ─────────────────────────────────────────────────────
export function FailedPanel({ state }: { state: RequestStateMirror }) {
  return (
    <section>
      <div className="eyebrow">失败</div>
      <div className="fail-card">
        <div className="phase">阶段：{state.failPhase ?? '?'} · 原因：{state.failReason ?? '?'}</div>
      </div>
      <div className="btn-row">
        <button className="btn btn-primary" onClick={() => send({ type: MSG.RETRY, requestId: state.id })}>
          重试
        </button>
      </div>
    </section>
  );
}

// ── SettingsPanel ───────────────────────────────────────────────────
export function SettingsPanel({ onClose }: { onClose: () => void }) {
  const [url, setUrl] = useState('');
  useEffect(() => { getBaseUrl().then(setUrl); }, []);
  const save = async () => {
    await setBaseUrl(url.trim() || 'http://localhost:9000');
    onClose();
  };
  return (
    <section>
      <div className="eyebrow">设置</div>
      <h3 className="title">Orchestrator 地址</h3>
      <p className="help">填 ECS 地址或本地 <code>http://localhost:9000</code>。</p>
      <label className="field">
        <span className="field-label">Base URL</span>
        <input
          aria-label="Orchestrator Base URL"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
        />
      </label>
      <div className="btn-row">
        <button className="btn btn-secondary" onClick={onClose}>取消</button>
        <button className="btn btn-primary" onClick={save}>保存</button>
      </div>
    </section>
  );
}
