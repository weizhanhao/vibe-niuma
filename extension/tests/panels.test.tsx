import { fireEvent, render, screen } from '@testing-library/react';
import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import {
  CapturePanel, ClarifyPanel, FailedPanel, LogFeed, PreviewPanel, ProgressTrail,
  SettingsPanel, StatusPanel, VariantsPanel,
} from '../src/ui/panels';
import type { LogEntry, RequestStateMirror } from '../src/lib/types';

// Phase E：新增 requestText / lastActivity 字段（test fixture 跟着 types 升级，仅类型补齐，无测试逻辑改动）
const base: RequestStateMirror = {
  id: 'r1', state: 'clarifying', url: 'http://x', requestText: 't', branch: null, previewUrl: null,
  failPhase: null, failReason: null, pendingQuestion: null, pendingVariants: null,
  logs: [], lastActivity: '2026-05-15T17:00:00.000Z',
};

describe('CapturePanel', () => {
  it('buttons disabled until text entered', () => {
    render(<CapturePanel />);
    const frameBtn = screen.getByRole('button', { name: /框选/ });
    const submitBtn = screen.getByRole('button', { name: /直接提交/ });
    expect(frameBtn).toBeDisabled();
    expect(submitBtn).toBeDisabled();
    fireEvent.change(screen.getByLabelText('业务需求'), { target: { value: '改个颜色' } });
    expect(frameBtn).not.toBeDisabled();
    expect(submitBtn).not.toBeDisabled();
  });

  it('framing button sends UI_START_CAPTURE', () => {
    const sent: unknown[] = [];
    vi.mocked(chrome.runtime.sendMessage).mockImplementation((m) => {
      sent.push(m);
      return Promise.resolve();
    });
    render(<CapturePanel />);
    fireEvent.change(screen.getByLabelText('业务需求'), { target: { value: '改个颜色' } });
    fireEvent.click(screen.getByRole('button', { name: /框选/ }));
    expect(sent[0]).toEqual({ type: 'UI_START_CAPTURE', requestText: '改个颜色' });
  });

  it('submit button sends SUBMIT_TEXT_ONLY without framing', () => {
    const sent: unknown[] = [];
    vi.mocked(chrome.runtime.sendMessage).mockImplementation((m) => {
      sent.push(m);
      return Promise.resolve();
    });
    render(<CapturePanel />);
    fireEvent.change(screen.getByLabelText('业务需求'), { target: { value: '加个新功能' } });
    fireEvent.click(screen.getByRole('button', { name: /直接提交/ }));
    expect(sent[0]).toEqual({ type: 'SUBMIT_TEXT_ONLY', requestText: '加个新功能' });
  });
});

describe('ClarifyPanel', () => {
  it('renders question and option buttons', () => {
    const state = { ...base, pendingQuestion: { questionId: 'q1', question: '想要哪种？', options: ['A', 'B'] } };
    render(<ClarifyPanel state={state} />);
    expect(screen.getByText('想要哪种？')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'A' })).toBeInTheDocument();
  });

  it('skip sends empty answer', () => {
    const sent: unknown[] = [];
    vi.mocked(chrome.runtime.sendMessage).mockImplementation((m) => {
      sent.push(m);
      return Promise.resolve();
    });
    const state = { ...base, pendingQuestion: { questionId: 'q1', question: '?', options: null } };
    render(<ClarifyPanel state={state} />);
    fireEvent.click(screen.getByRole('button', { name: '跳过' }));
    expect(sent[0]).toMatchObject({ type: 'SUBMIT_ANSWER', questionId: 'q1', answer: '' });
  });
});

describe('VariantsPanel', () => {
  it('disabled until a card is picked, then submits selected id', () => {
    const sent: unknown[] = [];
    vi.mocked(chrome.runtime.sendMessage).mockImplementation((m) => {
      sent.push(m);
      return Promise.resolve();
    });
    const state = {
      ...base,
      pendingVariants: { questionId: 'q1', variants: [
        { id: 'a', title: 'A', html: '<a/>' }, { id: 'b', title: 'B', html: '<b/>' },
      ] },
    };
    render(<VariantsPanel state={state} />);
    const go = screen.getByRole('button', { name: /用选中的方向/ });
    expect(go).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'A' }));
    expect(go).not.toBeDisabled();
    fireEvent.click(go);
    expect(sent[0]).toMatchObject({ answer: 'a' });
  });
});

describe('ProgressTrail', () => {
  it('marks current and previous phases', () => {
    const { container } = render(<ProgressTrail state="coding" />);
    const dones = container.querySelectorAll('.step.done');
    const actives = container.querySelectorAll('.step.active');
    expect(dones.length).toBe(3);
    expect(actives.length).toBe(1);
  });
});

describe('StatusPanel', () => {
  it('shows phase label as heading and branch', () => {
    render(<StatusPanel state={{ ...base, state: 'building', branch: 'cr/abc' }} />);
    expect(screen.getByRole('heading', { name: '构建预览' })).toBeInTheDocument();
    expect(screen.getByText('cr/abc')).toBeInTheDocument();
  });
});

describe('PreviewPanel', () => {
  it('shows merge + discard buttons in preview-ready state', () => {
    const state = { ...base, state: 'preview-ready' as const, previewUrl: 'http://x:5101' };
    render(<PreviewPanel state={state} />);
    expect(screen.getByRole('button', { name: /确认合并/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /丢弃/ })).toBeInTheDocument();
  });

  it('shows success card when merged', () => {
    render(<PreviewPanel state={{ ...base, state: 'merged' }} />);
    expect(screen.getByText('合并成功')).toBeInTheDocument();
  });
});

describe('FailedPanel', () => {
  it('shows phase + reason and retry button', () => {
    const sent: unknown[] = [];
    vi.mocked(chrome.runtime.sendMessage).mockImplementation((m) => {
      sent.push(m);
      return Promise.resolve();
    });
    const state = { ...base, state: 'failed' as const, failPhase: 'coding', failReason: 'no-changes' };
    render(<FailedPanel state={state} />);
    expect(screen.getByText(/coding/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '重试' }));
    expect(sent[0]).toMatchObject({ type: 'RETRY' });
  });
});

describe('SettingsPanel', () => {
  it('saves base URL to storage', async () => {
    const onClose = vi.fn();
    render(<SettingsPanel onClose={onClose} />);
    const input = await screen.findByLabelText('Orchestrator Base URL');
    fireEvent.change(input, { target: { value: 'http://ecs.example:9000' } });
    fireEvent.click(screen.getByRole('button', { name: '保存' }));
    await new Promise((r) => setTimeout(r, 0));
    expect(chrome.storage.local.set).toHaveBeenCalled();
    expect(onClose).toHaveBeenCalled();
  });
});

// ── Phase F：LogFeed ────────────────────────────────────────────────
describe('LogFeed', () => {
  const mkLog = (phase: string, line: string, i = 0): LogEntry => ({
    phase, line, ts: `2026-05-15T17:00:0${i}`,
  });

  it('renders nothing when logs are empty', () => {
    const { container } = render(<LogFeed logs={[]} />);
    expect(container.querySelector('.log-feed')).toBeNull();
  });

  it('shows latest line in collapsed state', () => {
    render(<LogFeed logs={[mkLog('coding', '正在分析 OrderList.tsx', 0), mkLog('coding', '改完 1 个文件', 1)]} />);
    expect(screen.getByText('改完 1 个文件')).toBeInTheDocument();
    // 折叠态：仅 head 可见，body 不渲染
    expect(document.querySelector('.log-feed-body')).toBeNull();
  });

  it('expanding reveals scrollable list of recent rows', () => {
    render(
      <LogFeed
        logs={[mkLog('clarifying', 'a', 0), mkLog('coding', 'b', 1), mkLog('building', 'c', 2)]}
      />,
    );
    fireEvent.click(screen.getByRole('button'));
    const body = document.querySelector('.log-feed-body');
    expect(body).not.toBeNull();
    expect(body?.textContent).toContain('a');
    expect(body?.textContent).toContain('b');
    expect(body?.textContent).toContain('c');
  });

  it('phase chip uses phase-specific class for color', () => {
    render(<LogFeed logs={[mkLog('coding', 'x')]} />);
    expect(document.querySelector('.phase-chip.phase-coding')).not.toBeNull();
  });
});

describe('StatusPanel + LogFeed wiring', () => {
  it('renders log feed when logs are present', () => {
    const state = {
      ...base,
      state: 'coding' as const,
      logs: [{ phase: 'coding', line: '▸ npm install...', ts: '2026-05-15T17:00:00' }],
    };
    render(<StatusPanel state={state} />);
    expect(screen.getByText('▸ npm install...')).toBeInTheDocument();
  });
});
