// Plan 10 Task 14: HistoryDropdown 组件 —— 时钟图标点击后弹的历史会话选择面板。
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { HistoryDropdown } from '../src/ui/components/HistoryDropdown';
import type { Conversation } from '../src/lib/conversations';

beforeEach(() => {
  vi.clearAllMocks();
});

afterEach(() => {
  vi.restoreAllMocks();
});

function mk(id: string, opts: Partial<Conversation> = {}): Conversation {
  return {
    id,
    title: `对话 ${id}`,
    created_at: '2026-05-17T10:00:00Z',
    updated_at: '2026-05-17T11:00:00Z',
    archived_at: null,
    messages: [],
    ...opts,
  };
}

describe('HistoryDropdown', () => {
  it('shows loading hint while items=null', () => {
    render(
      <HistoryDropdown items={null} onPick={() => {}} onClose={() => {}} />,
    );
    expect(screen.getByText(/加载中/)).toBeInTheDocument();
  });

  it('shows empty hint when items=[]', () => {
    render(
      <HistoryDropdown items={[]} onPick={() => {}} onClose={() => {}} />,
    );
    expect(screen.getByText(/还没有历史/)).toBeInTheDocument();
  });

  it('renders one item per conversation with title', () => {
    render(
      <HistoryDropdown
        items={[mk('a', { title: '加搜索' }), mk('b', { title: '改字号' })]}
        onPick={() => {}}
        onClose={() => {}}
      />,
    );
    expect(screen.getByText('加搜索')).toBeInTheDocument();
    expect(screen.getByText('改字号')).toBeInTheDocument();
  });

  it('clicking an item fires onPick(id) then onClose', async () => {
    const onPick = vi.fn();
    const onClose = vi.fn();
    render(
      <HistoryDropdown
        items={[mk('a', { title: '加搜索' })]}
        onPick={onPick}
        onClose={onClose}
      />,
    );
    fireEvent.click(screen.getByText('加搜索'));
    await waitFor(() => expect(onPick).toHaveBeenCalledWith('a'));
    expect(onClose).toHaveBeenCalled();
  });

  it('renders untitled fallback when title is empty', () => {
    render(
      <HistoryDropdown
        items={[mk('a', { title: '' })]}
        onPick={() => {}}
        onClose={() => {}}
      />,
    );
    expect(screen.getByText(/未命名/)).toBeInTheDocument();
  });
});
