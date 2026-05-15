import React, { useState } from 'react';
import { useRequestState } from './hooks/useRequestState';
import {
  CapturePanel, ClarifyPanel, FailedPanel, PreviewPanel,
  SettingsPanel, StatusPanel, VariantsPanel,
} from './panels';

export function App() {
  const state = useRequestState();
  const [showSettings, setShowSettings] = useState(false);

  let body: React.ReactNode;
  if (showSettings) {
    body = <SettingsPanel onClose={() => setShowSettings(false)} />;
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
          onClick={() => setShowSettings((s) => !s)}
        >⚙</button>
      </header>
      <div className="app-body">{body}</div>
    </div>
  );
}
