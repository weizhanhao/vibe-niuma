// 主路由：
//   - 加载中 → 占位
//   - 未配置（chrome.storage 空 / config 不足）→ 强制 SetupWizardPanel（无法跳过到主面板）
//   - 已配置 + 齿轮按钮 toggle → SettingsPanel
//   - 已配置 + 默认 → 现有主流程（pendingCapture / state 路由）
// Plan 6 Task 10：从静态布局升级为「按 config 是否就绪」分两枝路由。
import React, { useEffect, useState } from 'react';
import { isConfigSufficient, loadConfig, type Config } from '../lib/config';
import { ConversationList } from './components/ConversationList';
import { useMirrors } from './hooks/useMirrors';
import { usePendingCapture } from './hooks/usePendingCapture';
import { useRequestState } from './hooks/useRequestState';
import {
  CapturePanel, ClarifyPanel, FailedPanel, PreviewPanel, ReviewCapturePanel,
  StatusPanel, VariantsPanel,
} from './panels';
import { DeploymentAssistantPanel } from './panels/DeploymentAssistantPanel';
import { SettingsPanel } from './panels/SettingsPanel';
import { SetupWizardPanel } from './panels/SetupWizardPanel';
import type { ChangeRequestState } from '../lib/types';
import { loadTheme, readThemeSync, saveTheme, type Theme } from '../lib/theme';

const ASSISTANT_COMPLETED_KEY = 'doskill_deployment_completed_at';

// loading sentinel：避免在 cfg 还没拉到时短暂渲染 wizard 闪一下。
type ConfigState = 'loading' | Config | null;
const CONFIG_STORAGE_KEY = 'doskill_config_v2';

// FSM → head 状态徽章（READY/RUNNING/SELECTING/MERGED/FAIL）。design 里 head 右侧 pill。
function statusPillFromState(state: ChangeRequestState | null, pendingCapture: boolean): { label: string; tone: string } {
  if (pendingCapture) return { label: 'SELECTING', tone: 'tone-warn' };
  if (!state) return { label: 'READY', tone: '' };
  switch (state) {
    case 'created':
    case 'clarifying':
    case 'located':
    case 'coding':
    case 'building':
      return { label: 'RUNNING', tone: 'tone-running' };
    case 'preview-ready':
      return { label: 'READY', tone: '' };
    case 'merged':
      return { label: 'MERGED', tone: 'tone-merged' };
    case 'failed':
    case 'expired':
      return { label: 'FAIL', tone: 'tone-fail' };
    case 'discarded':
      return { label: 'READY', tone: '' };
  }
}

function GearIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.2" aria-hidden="true">
      <circle cx="7" cy="7" r="1.6" />
      <path d="M7 1v1.6M7 11.4V13M1 7h1.6M11.4 7H13M2.76 2.76l1.13 1.13M10.11 10.11l1.13 1.13M2.76 11.24l1.13-1.13M10.11 3.89l1.13-1.13" />
    </svg>
  );
}

function SunIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.2" aria-hidden="true">
      <circle cx="7" cy="7" r="2.4" />
      <path d="M7 0.6V2M7 12V13.4M0.6 7H2M12 7H13.4M2.46 2.46l1l1M10.54 10.54l1 1M2.46 11.54l1-1M10.54 3.46l1-1" />
    </svg>
  );
}
function MoonIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.2" aria-hidden="true">
      <path d="M11.5 8.4A4.6 4.6 0 0 1 5.6 2.5a.5.5 0 0 0-.66-.6 5.6 5.6 0 1 0 7.16 7.16.5.5 0 0 0-.6-.66z" />
    </svg>
  );
}

// 小型主题切换钩子 —— App 启动校正 + 提供 toggle。
function useThemeToggle(): [Theme, () => void] {
  // 初始值用 sync read，避免首帧错主题
  const [theme, setTheme] = useState<Theme>(() => readThemeSync());
  useEffect(() => {
    // mount 后从 chrome.storage 校正一次（别处可能改过）
    let mounted = true;
    void loadTheme().then((t) => {
      if (mounted && t !== theme) setTheme(t);
    });
    return () => { mounted = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const toggle = () => {
    const next: Theme = theme === 'light' ? 'dark' : 'light';
    setTheme(next);
    void saveTheme(next);
  };
  return [theme, toggle];
}

export function App() {
  const [config, setConfig] = useState<ConfigState>('loading');
  const [assistantCompleted, setAssistantCompleted] = useState<number | null | 'loading'>('loading');

  useEffect(() => {
    let mounted = true;
    loadConfig().then((cfg) => { if (mounted) setConfig(cfg); });

    // 拉一次 deployment_completed_at —— Plan 7 退场判据
    if (chrome?.storage?.local?.get) {
      void (async () => {
        const out = (await chrome.storage.local.get([ASSISTANT_COMPLETED_KEY])) as Record<string, unknown>;
        if (!mounted) return;
        const v = out[ASSISTANT_COMPLETED_KEY];
        setAssistantCompleted(typeof v === 'number' ? v : null);
      })();
    } else {
      setAssistantCompleted(null);
    }

    const listener = (
      changes: Record<string, chrome.storage.StorageChange>,
      area: string,
    ) => {
      if (area !== 'local') return;
      if (CONFIG_STORAGE_KEY in changes) {
        loadConfig().then((cfg) => { if (mounted) setConfig(cfg); });
      }
      if (ASSISTANT_COMPLETED_KEY in changes) {
        const v = changes[ASSISTANT_COMPLETED_KEY].newValue;
        if (mounted) setAssistantCompleted(typeof v === 'number' ? v : null);
      }
    };
    chrome.storage?.onChanged?.addListener?.(listener);
    return () => {
      mounted = false;
      chrome.storage?.onChanged?.removeListener?.(listener);
    };
  }, []);

  if (config === 'loading' || assistantCompleted === 'loading') {
    return (
      <div className="app">
        <div className="app-body"><p className="help">加载中…</p></div>
      </div>
    );
  }

  // 未配置 + 助手未跑完 → 走 Plan 7 部署助手；
  // 未配置 + 助手已跑完（异常状态） → 兜底走 Plan 6 的 SetupWizardPanel
  if (!isConfigSufficient(config)) {
    if (assistantCompleted === null) {
      return (
        <div className="app">
          <DeploymentAssistantPanel onComplete={() => { void loadConfig().then(setConfig); }} />
        </div>
      );
    }
    return (
      <div className="app">
        <SetupWizardPanel onComplete={() => { void loadConfig().then(setConfig); }} />
      </div>
    );
  }

  return <MainShell />;
}

// ── 主壳：拆出去是为了让 wizard 那条 return 干净，避免 hooks 顺序在 wizard / 主面板之间漂移 ──
function MainShell() {
  const state = useRequestState();
  const { mirrors, activeId } = useMirrors();
  const pendingCapture = usePendingCapture();
  const [showSettings, setShowSettings] = useState(false);
  const [theme, toggleTheme] = useThemeToggle();

  if (showSettings) {
    return (
      <div className="app">
        <header className="app-head">
          <span className="app-logo">d</span>
          <div className="app-title">
            <div>DO<em>SKILL</em></div>
            <span className="muted">settings</span>
          </div>
          <div className="app-status"><span className="pulse" />SETTINGS</div>
          <div className="app-head-actions">
            <button
              className="app-gear"
              aria-label={theme === 'light' ? '切换深色' : '切换浅色'}
              title={theme === 'light' ? '切换深色' : '切换浅色'}
              onClick={toggleTheme}
            >{theme === 'light' ? <MoonIcon /> : <SunIcon />}</button>
            <button
              className="app-gear"
              aria-label="关闭设置"
              onClick={() => setShowSettings(false)}
            >×</button>
          </div>
        </header>
        <div className="app-body">
          <SettingsPanel onClose={() => setShowSettings(false)} />
        </div>
      </div>
    );
  }

  let body: React.ReactNode;
  if (pendingCapture) {
    // Phase G：优先级最高——业务员刚框完，必须先确认。
    body = <ReviewCapturePanel pendingCapture={pendingCapture} />;
  } else if (!state) {
    body = <CapturePanel />;
  } else if (state.pendingVariants) {
    body = <VariantsPanel state={state} />;
  } else if (state.pendingQuestion) {
    body = <ClarifyPanel state={state} />;
  } else if (state.state === 'failed' || state.state === 'expired') {
    body = <FailedPanel state={state} />;
  } else if (
    state.state === 'preview-ready' ||
    state.state === 'merged' ||
    state.state === 'discarded'
  ) {
    body = <PreviewPanel state={state} />;
  } else {
    body = <StatusPanel state={state} />;
  }

  const pill = statusPillFromState(state ? state.state : null, !!pendingCapture);

  return (
    <div className="app">
      <header className="app-head">
        <span className="app-logo">d</span>
        <div className="app-title">
          <div>DO<em>SKILL</em></div>
          <span className="muted">change request</span>
        </div>
        <div className={`app-status ${pill.tone}`}><span className="pulse" />{pill.label}</div>
        <div className="app-head-actions">
          <button
            className="app-gear"
            aria-label={theme === 'light' ? '切换深色' : '切换浅色'}
            title={theme === 'light' ? '切换深色' : '切换浅色'}
            onClick={toggleTheme}
          >{theme === 'light' ? <MoonIcon /> : <SunIcon />}</button>
          <button
            className="app-gear"
            aria-label="设置"
            onClick={() => setShowSettings(true)}
          ><GearIcon /></button>
        </div>
      </header>
      <ConversationList mirrors={mirrors} activeId={activeId} />
      <div className="app-body">{body}</div>
    </div>
  );
}
