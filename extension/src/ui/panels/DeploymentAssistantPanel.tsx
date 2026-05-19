// Plan 7 Task 7: DeploymentAssistantPanel —— 把 ChatPanel + ActionCard + 状态机串起来。
//
// 入口：用户首次装好扩展、还没填 orchestratorUrl / adminToken 时（isConfigSufficient===false）
// App.tsx 路由到这里。完成后 chrome.storage.local 落 vibe_niuma_deployment_completed_at，
// App 自动退场到 MainShell。
//
// 持久化：
//   - chrome.storage.local: vibe_niuma_deployment_state（FSM）、vibe_niuma_deployment_history
//     （ChatMessage[]，cap 16 条）、ai_deepseek_key、collected_<field>
//   - chrome.storage.session: vibe_niuma_ssh_key（关浏览器即丢）
//
// 流程：
//   gathering_deepseek_key → 填 DeepSeek key（纯表单，不走 LLM）
//   choosing_path → collecting_info → executing → verifying → done
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { DeepSeekClient, type ChatMessage } from '../../ai/DeepSeekClient';
import { parseActionsFromAssistant } from '../../ai/actions';
import {
  initialState, transition, type DeploymentEvent, type DeploymentState, type CollectedInfo,
} from '../../ai/DeploymentState';
import { buildSystemPrompt } from '../../ai/systemPrompt';
import { saveConfig } from '../../lib/config';
import { ActionCard } from '../components/ActionCard';
import { ChatPanel } from './ChatPanel';

const STATE_KEY = 'vibe_niuma_deployment_state';
const HISTORY_KEY = 'vibe_niuma_deployment_history';
const KEY_KEY = 'ai_deepseek_key';
const COMPLETED_KEY = 'vibe_niuma_deployment_completed_at';
const SSH_SESSION_KEY = 'vibe_niuma_ssh_key';

interface PersistedShape {
  state: DeploymentState;
  history: ChatMessage[];
}

async function loadPersisted(): Promise<PersistedShape> {
  if (!chrome?.storage?.local?.get) return { state: initialState(), history: [] };
  const out = (await chrome.storage.local.get([STATE_KEY, HISTORY_KEY])) as Record<string, unknown>;
  const state = (out[STATE_KEY] as DeploymentState | undefined) ?? initialState();
  const history = Array.isArray(out[HISTORY_KEY]) ? (out[HISTORY_KEY] as ChatMessage[]) : [];
  return { state, history: history.slice(Math.max(0, history.length - 16)) };
}

async function persist(state: DeploymentState, history: ChatMessage[]) {
  if (!chrome?.storage?.local?.set) return;
  await chrome.storage.local.set({
    [STATE_KEY]: state,
    [HISTORY_KEY]: history.slice(Math.max(0, history.length - 16)),
  });
}

async function loadDeepSeekKey(): Promise<string> {
  if (!chrome?.storage?.local?.get) return '';
  const out = (await chrome.storage.local.get([KEY_KEY])) as Record<string, unknown>;
  return typeof out[KEY_KEY] === 'string' ? (out[KEY_KEY] as string) : '';
}

async function saveDeepSeekKey(k: string) {
  if (!chrome?.storage?.local?.set) return;
  await chrome.storage.local.set({ [KEY_KEY]: k });
}

async function captureFieldToStorage(field: string, value: string): Promise<void> {
  if (field === 'sshPrivateKey') {
    if (chrome?.storage?.session?.set) {
      await chrome.storage.session.set({ [SSH_SESSION_KEY]: value });
    }
    return;
  }
  if (!chrome?.storage?.local?.set) return;
  await chrome.storage.local.set({ [`collected_${field}`]: value });
}

export function DeploymentAssistantPanel({ onComplete }: { onComplete: () => void }) {
  const [bootLoading, setBootLoading] = useState(true);
  const [state, setState] = useState<DeploymentState>(() => initialState());
  const [history, setHistory] = useState<ChatMessage[]>([]);
  const [deepseekKey, setDeepseekKey] = useState('');
  const [keyError, setKeyError] = useState<string | null>(null);
  const [collected, setCollected] = useState<Partial<CollectedInfo>>({});

  useEffect(() => {
    let mounted = true;
    void (async () => {
      const [persisted, k] = await Promise.all([loadPersisted(), loadDeepSeekKey()]);
      if (!mounted) return;
      setState(persisted.state);
      setHistory(persisted.history);
      setDeepseekKey(k);
      if (persisted.state.phase === 'collecting_info' || persisted.state.phase === 'executing') {
        setCollected(persisted.state.collected as Partial<CollectedInfo>);
      }
      setBootLoading(false);
    })();
    return () => { mounted = false; };
  }, []);

  useEffect(() => {
    if (!bootLoading) void persist(state, history);
  }, [state, history, bootLoading]);

  const doneFired = useRef(false);
  useEffect(() => {
    if (state.phase === 'done' && !doneFired.current) {
      doneFired.current = true;
      if (chrome?.storage?.local?.set) {
        void chrome.storage.local.set({ [COMPLETED_KEY]: state.completedAt });
      }
      onComplete();
    }
  }, [state, onComplete]);

  const client = useMemo(() => {
    if (!deepseekKey) return null;
    return new DeepSeekClient({ apiKey: deepseekKey });
  }, [deepseekKey]);

  const systemPrompt = useMemo(() => {
    const path = state.phase === 'collecting_info' || state.phase === 'executing' || state.phase === 'verifying'
      ? state.path
      : null;
    return buildSystemPrompt(path);
  }, [state]);

  const dispatch = (ev: DeploymentEvent) => {
    setState((prev) => {
      const next = transition(prev, ev);
      return 'error' in next ? prev : next;
    });
  };

  // ChatPanel 是业务员唯一的输入入口（Plan 7 UX 收敛：不再渲染 ActionCard 内 textarea）。
  // 业务员粘 `sk-xxx` 时自动当 DeepSeek API Key 处理：存 storage + 推进 wizard
  // 状态；其它内容当普通 user 消息追加进 history 让 AI 接管。
  const onAppend = (m: ChatMessage) => {
    if (m.role === 'user') {
      const trimmed = m.content.trim();
      if (trimmed.startsWith('sk-') && trimmed.length >= 16) {
        setDeepseekKey(trimmed);
        void saveDeepSeekKey(trimmed);
        if (state.phase === 'gathering_deepseek_key') {
          dispatch({ type: 'deepseek_key_set' });
        }
        setHistory((h) => [...h, { role: 'user', content: `[已提交 DeepSeek API Key，前缀 ${trimmed.slice(0, 8)}…]` }]);
        return;
      }
    }
    setHistory((h) => [...h, m]);
  };

  const onValidate = async (
    kind: 'orchestrator_healthz' | 'admin_config',
    url: string,
    token?: string,
  ): Promise<boolean> => {
    try {
      if (kind === 'orchestrator_healthz') {
        // 端点实际是 /health（无 z）。kind 字段沿用历史名 orchestrator_healthz，
        // 只是个 enum 标签 —— 改名要同步 schema/prompts/tests，不值当。
        const r = await fetch(`${url.replace(/\/$/, '')}/health`);
        return r.ok;
      }
      const r = await fetch(`${url.replace(/\/$/, '')}/admin/config`, {
        headers: token ? { 'X-Admin-Token': token } : {},
      });
      return r.ok;
    } catch {
      return false;
    }
  };

  const onCapture = (field: string, value: string) => {
    // 兜底：AI 在非 gathering_deepseek_key phase 也可能引导业务员填 deepseek key
    // （比如业务员之前清掉过 key，或 AI 想 re-validate）。无论当前 phase，认 deepseek
    // key 字段就走 saveDeepSeekKey + dispatch deepseek_key_set 路径，让按钮真有反应。
    const looksLikeDeepseekKey =
      /deepseek/i.test(field) || field === 'apiKey' || field === 'api_key';
    if (looksLikeDeepseekKey && value.startsWith('sk-')) {
      setDeepseekKey(value);
      void saveDeepSeekKey(value);
      if (state.phase === 'gathering_deepseek_key') {
        dispatch({ type: 'deepseek_key_set' });
      }
      return;
    }
    void captureFieldToStorage(field, value);
    setCollected((c) => ({ ...c, [field]: value }));
    if (state.phase === 'collecting_info') {
      dispatch({ type: 'collect', patch: { [field]: value } as Partial<CollectedInfo> });
    }
    if (field === 'orchestratorUrl' || field === 'adminToken') {
      void saveConfig({ [field]: value });
    }
  };

  const onTransition = (to: string) => {
    if (state.phase === 'gathering_deepseek_key' && to === 'choosing_path') {
      dispatch({ type: 'deepseek_key_set' });
      return;
    }
    if (state.phase === 'choosing_path' && (to === 'local' || to === 'ecs')) {
      dispatch({ type: 'path_chosen', path: to });
      return;
    }
    if (state.phase === 'collecting_info' && to === 'executing') {
      dispatch({
        type: 'collection_complete',
        collected: state.collected as CollectedInfo,
        firstStep: '准备',
      });
      return;
    }
    if (state.phase === 'executing' && to === 'verifying') {
      dispatch({
        type: 'execution_done',
        orchestratorUrl: (collected as { orchestratorUrl?: string }).orchestratorUrl ?? '',
        adminToken: (collected as { adminToken?: string }).adminToken ?? '',
      });
      return;
    }
    if (state.phase === 'verifying' && to === 'done') {
      dispatch({ type: 'verification_passed', completedAt: Date.now() });
    }
  };

  const reset = () => {
    if (!confirm('重新开始会丢失对话历史，DeepSeek key 保留。确定？')) return;
    setState(initialState());
    setHistory([]);
    setCollected({});
    if (chrome?.storage?.local?.remove) {
      void chrome.storage.local.remove([STATE_KEY, HISTORY_KEY]);
    }
  };

  const handleKeySubmit = async () => {
    if (!deepseekKey.startsWith('sk-')) {
      setKeyError('DeepSeek key 一般以 sk- 开头');
      return;
    }
    await saveDeepSeekKey(deepseekKey);
    dispatch({ type: 'deepseek_key_set' });
  };

  if (bootLoading) {
    return <div className="app-body"><p className="help">加载中…</p></div>;
  }

  if (state.phase === 'gathering_deepseek_key') {
    return (
      <div className="app-body">
        <section>
          <h3 className="title">填一个 DeepSeek API Key</h3>
          <p className="help">剩下的步骤会由 AI 助手对话引导你完成。</p>
          <label className="field">
            <span className="label"><span>DeepSeek API Key</span></span>
            <input
              type="password"
              aria-label="DeepSeek API Key"
              value={deepseekKey}
              onChange={(e) => { setDeepseekKey(e.target.value); setKeyError(null); }}
              placeholder="sk-..."
            />
          </label>
          {keyError && <p className="wizard-error">{keyError}</p>}
          <div className="btn-row">
            <button
              className="btn btn-primary"
              onClick={() => void handleKeySubmit()}
              disabled={!deepseekKey}
            >继续 →</button>
          </div>
          <p className="hint">没有？去 <a href="https://platform.deepseek.com" target="_blank" rel="noopener noreferrer" style={{ color: 'var(--accent)' }}>platform.deepseek.com</a> 注册 + 充值 ≥ ¥10。</p>
        </section>
      </div>
    );
  }

  if (!client) {
    return (
      <div className="app-body">
        <p className="help">DeepSeek client 未初始化（请回上一步）</p>
        <div className="btn-row">
          <button className="btn btn-ghost" onClick={reset}>重新开始</button>
        </div>
      </div>
    );
  }

  const lastAssistant = [...history].reverse().find((m) => m.role === 'assistant');
  const actions = lastAssistant ? parseActionsFromAssistant(lastAssistant.content).actions : [];

  // 把 phase code 翻成业务员看得懂的中文（不显示英文 enum 名）
  const phaseLabel: Record<string, string> = {
    choosing_path: '选择部署方式',
    collecting_info: '收集部署信息',
    executing: '执行部署',
    verifying: '验证连接',
    done: '完成',
  };
  const phaseText = phaseLabel[state.phase] ?? state.phase;

  return (
    <div className="app-body">
      <div className="wizard-phase-bar">
        <span className="wizard-phase-bar__label">{phaseText}</span>
        <button className="btn btn-small btn-ghost" onClick={reset}>重置</button>
      </div>
      <ChatPanel
        client={client}
        systemPrompt={systemPrompt}
        history={history}
        onAppend={onAppend}
        autoSendOnEmpty="我已经填好 DeepSeek API Key，告诉我接下来该做什么。"
      />
      {actions.length > 0 && (
        <div className="actions-list">
          {actions.map((a, i) => (
            <ActionCard
              key={i}
              action={a}
              onCaptureField={onCapture}
              onValidate={onValidate}
              onTransition={onTransition}
            />
          ))}
        </div>
      )}
    </div>
  );
}
