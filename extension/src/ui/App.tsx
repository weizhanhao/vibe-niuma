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
import { SettingsPanel } from './panels/SettingsPanel';
import { SetupWizardPanel } from './panels/SetupWizardPanel';

// loading sentinel：避免在 cfg 还没拉到时短暂渲染 wizard 闪一下。
type ConfigState = 'loading' | Config | null;
const CONFIG_STORAGE_KEY = 'doskill_config_v2';

export function App() {
  // 配置状态：首屏 loading，loadConfig 完成后变 Config | null。
  // chrome.storage.onChanged 监听 doskill_config_v2 → 重新 loadConfig，让 wizard / 主面板切换立即生效。
  const [config, setConfig] = useState<ConfigState>('loading');

  useEffect(() => {
    let mounted = true;
    loadConfig().then((cfg) => {
      if (mounted) setConfig(cfg);
    });

    // chrome.storage.onChanged 在 jsdom 测试里可能不存在；optional chain 自动 noop
    const listener = (
      changes: Record<string, chrome.storage.StorageChange>,
      area: string,
    ) => {
      if (area !== 'local') return;
      if (!(CONFIG_STORAGE_KEY in changes)) return;
      loadConfig().then((cfg) => {
        if (mounted) setConfig(cfg);
      });
    };
    chrome.storage?.onChanged?.addListener?.(listener);
    return () => {
      mounted = false;
      chrome.storage?.onChanged?.removeListener?.(listener);
    };
  }, []);

  if (config === 'loading') {
    return (
      <div className="app">
        <div className="app-body"><p className="help">加载中…</p></div>
      </div>
    );
  }

  // 未配置 → 强制 wizard。即便父组件想 toggle settings 也不允许（设置面板要先有 token 才能拉服务端）。
  if (!isConfigSufficient(config)) {
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

  if (showSettings) {
    return (
      <div className="app">
        <header className="app-head">
          <span className="app-logo">d</span>
          <div className="app-title">doskill <span className="muted">/ 设置</span></div>
          <button
            className="app-gear"
            aria-label="关闭设置"
            onClick={() => setShowSettings(false)}
          >×</button>
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

  return (
    <div className="app">
      <header className="app-head">
        <span className="app-logo">d</span>
        <div className="app-title">doskill <span className="muted">/ change request</span></div>
        <button
          className="app-gear"
          aria-label="设置"
          onClick={() => setShowSettings(true)}
        >⚙</button>
      </header>
      <ConversationList mirrors={mirrors} activeId={activeId} />
      <div className="app-body">{body}</div>
    </div>
  );
}
