import React, { useState } from 'react';
import { ConversationList } from './components/ConversationList';
import { useMirrors } from './hooks/useMirrors';
import { usePendingCapture } from './hooks/usePendingCapture';
import { useRequestState } from './hooks/useRequestState';
import {
  CapturePanel, ClarifyPanel, FailedPanel, PreviewPanel, ReviewCapturePanel,
  SettingsPanel, StatusPanel, VariantsPanel,
} from './panels';

export function App() {
  // Phase E：state 仍跟当前 active 一一对应；额外 useMirrors 取列表。
  const state = useRequestState();
  const { mirrors, activeId } = useMirrors();
  // Phase G：业务员框完区域后 SW 暂存的 pending review 数据。非空时优先弹 review。
  const pendingCapture = usePendingCapture();
  const [showSettings, setShowSettings] = useState(false);

  let body: React.ReactNode;
  if (showSettings) {
    body = <SettingsPanel onClose={() => setShowSettings(false)} />;
  } else if (pendingCapture) {
    // Phase G：优先级最高（仅次于 settings）——业务员刚框完，必须先确认。
    body = <ReviewCapturePanel pendingCapture={pendingCapture} />;
  } else if (!state) {
    // active=null → 新对话起点
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
          onClick={() => setShowSettings((s) => !s)}
        >⚙</button>
      </header>
      {!showSettings && <ConversationList mirrors={mirrors} activeId={activeId} />}
      <div className="app-body">{body}</div>
    </div>
  );
}
