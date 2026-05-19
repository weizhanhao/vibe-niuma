// Plan 7 Task 6: ActionCard —— 把 LLM 输出的结构化 action 渲染成可交互卡。
//
// 6 种 action.type → 6 种渲染：
//   copy_command   button「复制」+ pre 命令；expectsOutput=true 时跟一个 paste-back
//   open_url       button「打开」→ window.open
//   capture_field  挂载即 onCaptureField，banner 显示 field=value（ssh key 自动遮罩）
//   request_output textarea + 「提交」
//   validate       button「验证 {kind}」→ async onValidate；显示 通过/失败
//   transition     挂载即 onTransition，渲染 "→ {to}" hint
//
// 调用方：DeploymentAssistantPanel —— 解析 assistant 消息的 actions 后，按顺序渲染。
import React, { useEffect, useState } from 'react';
import type { AiAction } from '../../ai/actions';

export interface ActionCardProps {
  action: AiAction;
  onCopy?: (command: string) => void | Promise<void>;
  onOpenUrl?: (url: string) => void;
  onCaptureField?: (field: string, value: string) => void | Promise<void>;
  onRequestOutput?: (output: string) => void;
  onValidate?: (kind: 'orchestrator_healthz' | 'admin_config', url: string, token?: string) => Promise<boolean>;
  onTransition?: (to: string) => void;
}

function maskSensitive(field: string, value: string): string {
  if (field !== 'sshPrivateKey' || value.length < 16) return value;
  return `${value.slice(0, 5)}...${value.slice(-5)}`;
}

async function copyToClipboard(text: string): Promise<boolean> {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch { /* fall through */ }
  }
  try {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    // eslint-disable-next-line @typescript-eslint/no-deprecated
    const ok = document.execCommand('copy');
    document.body.removeChild(ta);
    return ok;
  } catch {
    return false;
  }
}

export function ActionCard(props: ActionCardProps) {
  const { action } = props;
  if (action.type === 'copy_command') return <CopyCommandCard {...props} action={action} />;
  if (action.type === 'open_url') {
    return (
      <article className="action-card action-open_url">
        <div className="action-card-label">{action.label}</div>
        <div className="action-card-url">{action.url}</div>
        <button
          className="btn btn-small btn-secondary"
          onClick={() => {
            if (props.onOpenUrl) props.onOpenUrl(action.url);
            else window.open(action.url, '_blank', 'noopener,noreferrer');
          }}
        >打开</button>
      </article>
    );
  }
  if (action.type === 'capture_field') return <CaptureFieldCard {...props} action={action} />;
  if (action.type === 'request_output') return <RequestOutputCard {...props} action={action} />;
  if (action.type === 'validate') return <ValidateCard {...props} action={action} />;
  if (action.type === 'transition') return <TransitionCard {...props} action={action} />;
  return null;
}

function CopyCommandCard({
  action, onCopy,
}: ActionCardProps & { action: Extract<AiAction, { type: 'copy_command' }> }) {
  const [copied, setCopied] = useState(false);
  const [available, setAvailable] = useState(true);

  const handleCopy = async () => {
    let ok = true;
    if (onCopy) await onCopy(action.command);
    else ok = await copyToClipboard(action.command);
    if (ok) {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } else {
      setAvailable(false);
    }
  };

  return (
    <article className="action-card action-copy_command">
      <div className="action-card-label">{action.label}</div>
      <pre className="action-card-pre">{action.command}</pre>
      <div className="action-card-row">
        {available ? (
          <button className="btn btn-small btn-secondary" onClick={() => void handleCopy()}>
            {copied ? '已复制 ✓' : '复制'}
          </button>
        ) : (
          <span className="action-card-error">复制不可用，请手动复制</span>
        )}
      </div>
      {action.expectsOutput && copied && (
        <div className="action-card-hint">
          ↓ 把命令输出粘到下面聊天框发我
        </div>
      )}
    </article>
  );
}

function CaptureFieldCard({
  action, onCaptureField,
}: ActionCardProps & { action: Extract<AiAction, { type: 'capture_field' }> }) {
  useEffect(() => {
    void onCaptureField?.(action.field, action.value);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const displayed = maskSensitive(action.field, action.value);
  return (
    <article className="action-card action-capture_field">
      <div className="action-card-capture-banner">
        <span className="k">{action.field}</span>
        <span className="eq"> = </span>
        <span className="v">{displayed}</span>
        <span className="ok">✓ 已记录</span>
      </div>
    </article>
  );
}

function RequestOutputCard({
  action,
}: ActionCardProps & { action: Extract<AiAction, { type: 'request_output' }> }) {
  return (
    <article className="action-card action-request_output">
      <div className="action-card-hint">
        ↓ {action.placeholder || '把内容粘到下面聊天框发我'}
      </div>
    </article>
  );
}

type ValidateState = 'idle' | 'loading' | 'pass' | 'fail';
function ValidateCard({
  action, onValidate,
}: ActionCardProps & { action: Extract<AiAction, { type: 'validate' }> }) {
  const [state, setState] = useState<ValidateState>('idle');

  const run = async () => {
    setState('loading');
    try {
      const ok = onValidate ? await onValidate(action.kind, action.url, action.token) : false;
      setState(ok ? 'pass' : 'fail');
    } catch {
      setState('fail');
    }
  };

  return (
    <article className="action-card action-validate">
      <div className="action-card-label">验证 {action.kind}</div>
      <div className="action-card-row">
        {state === 'idle' && (
          <button className="btn btn-small btn-secondary" onClick={() => void run()}>验证</button>
        )}
        {state === 'loading' && <span className="action-card-spinner">检查中…</span>}
        {state === 'pass' && <span className="action-card-pass">✓ 通过</span>}
        {state === 'fail' && (
          <>
            <span className="action-card-fail">✗ 失败</span>
            <button className="btn btn-small btn-ghost" onClick={() => void run()}>重试</button>
          </>
        )}
      </div>
    </article>
  );
}

function TransitionCard({
  action, onTransition,
}: ActionCardProps & { action: Extract<AiAction, { type: 'transition' }> }) {
  useEffect(() => {
    onTransition?.(action.to);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return <div className="action-card-transition-hint">→ {action.to}</div>;
}
