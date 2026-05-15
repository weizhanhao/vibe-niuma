// Phase E：ConversationList 组件契约。
// 验证渲染、切换、删除（含 confirm）、新对话四类交互。
import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ConversationList } from '../src/ui/components/ConversationList';
import type { RequestStateMirror } from '../src/lib/types';

function mk(id: string, opts: Partial<RequestStateMirror> = {}): RequestStateMirror {
  return {
    id,
    state: 'clarifying',
    url: 'http://demo/orders',
    requestText: `需求 ${id}`,
    branch: null,
    previewUrl: null,
    failPhase: null,
    failReason: null,
    pendingQuestion: null,
    pendingVariants: null,
    logs: [],
    lastActivity: '2026-05-15T17:00:00.000Z',
    ...opts,
  };
}

let sent: unknown[];
beforeEach(() => {
  sent = [];
  vi.mocked(chrome.runtime.sendMessage).mockImplementation((m) => {
    sent.push(m);
    return Promise.resolve();
  });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('ConversationList', () => {
  it('renders empty hint when no mirrors', () => {
    render(<ConversationList mirrors={[]} activeId={null} />);
    expect(screen.getByText(/还没有任何对话/)).toBeInTheDocument();
  });

  it('renders one row per mirror with summary + state chip', () => {
    const mirrors = [
      mk('r1', { requestText: '把保存改成立即保存', state: 'preview-ready' }),
      mk('r2', { requestText: '在订单列表加搜索', state: 'coding' }),
    ];
    render(<ConversationList mirrors={mirrors} activeId="r2" />);
    expect(screen.getByText('把保存改成立即保存')).toBeInTheDocument();
    expect(screen.getByText('在订单列表加搜索')).toBeInTheDocument();
    expect(screen.getByText('预览就绪')).toBeInTheDocument();
    expect(screen.getByText('AI 改代码中')).toBeInTheDocument();
  });

  it('truncates long requestText to ~30 chars with ellipsis', () => {
    const long = '一二三四五六七八九十'.repeat(5); // 50 字
    render(<ConversationList mirrors={[mk('r1', { requestText: long })]} activeId={null} />);
    const summary = screen.getByText((_t, el) => !!el?.classList.contains('conv-summary'));
    expect(summary.textContent?.endsWith('…')).toBe(true);
    expect(summary.textContent?.length).toBeLessThanOrEqual(31); // 30 + …
  });

  it('shows placeholder when requestText is empty', () => {
    render(<ConversationList mirrors={[mk('r1', { requestText: '' })]} activeId={null} />);
    expect(screen.getByText('（未填需求）')).toBeInTheDocument();
  });

  it('marks active row with is-active class', () => {
    const mirrors = [mk('r1'), mk('r2')];
    const { container } = render(<ConversationList mirrors={mirrors} activeId="r2" />);
    const rows = container.querySelectorAll('.conv-row');
    expect(rows[0].classList.contains('is-active')).toBe(false);
    expect(rows[1].classList.contains('is-active')).toBe(true);
    expect(rows[1].getAttribute('aria-current')).toBe('true');
  });

  it('clicking a non-active row dispatches SET_ACTIVE', () => {
    const mirrors = [mk('r1'), mk('r2')];
    render(<ConversationList mirrors={mirrors} activeId="r1" />);
    fireEvent.click(screen.getByText('需求 r2'));
    expect(sent).toContainEqual({ type: 'SET_ACTIVE', id: 'r2' });
  });

  it('clicking the active row does NOT dispatch SET_ACTIVE', () => {
    const mirrors = [mk('r1')];
    render(<ConversationList mirrors={mirrors} activeId="r1" />);
    fireEvent.click(screen.getByText('需求 r1'));
    expect(sent).toEqual([]);
  });

  it('clicking + 新对话 dispatches NEW_CONVERSATION', () => {
    render(<ConversationList mirrors={[mk('r1')]} activeId="r1" />);
    fireEvent.click(screen.getByRole('button', { name: '新对话' }));
    expect(sent).toContainEqual({ type: 'NEW_CONVERSATION' });
  });

  it('clicking × with confirm=true dispatches DELETE_CONVERSATION', () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);
    render(<ConversationList mirrors={[mk('r1')]} activeId="r1" />);
    fireEvent.click(screen.getByRole('button', { name: '删除对话' }));
    expect(confirmSpy).toHaveBeenCalled();
    expect(sent).toContainEqual({ type: 'DELETE_CONVERSATION', id: 'r1' });
  });

  it('clicking × with confirm=false does NOT dispatch', () => {
    vi.spyOn(window, 'confirm').mockReturnValue(false);
    render(<ConversationList mirrors={[mk('r1')]} activeId="r1" />);
    fireEvent.click(screen.getByRole('button', { name: '删除对话' }));
    expect(sent).toEqual([]);
  });

  it('× click does not bubble up to select the row', () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    render(<ConversationList mirrors={[mk('r1'), mk('r2')]} activeId="r1" />);
    // 点 r2 行上的删除按钮：只触发 DELETE_CONVERSATION，不触发 SET_ACTIVE
    const delButtons = screen.getAllByRole('button', { name: '删除对话' });
    fireEvent.click(delButtons[1]);
    const setActiveCalls = sent.filter((m) => (m as { type: string }).type === 'SET_ACTIVE');
    expect(setActiveCalls).toEqual([]);
  });
});
