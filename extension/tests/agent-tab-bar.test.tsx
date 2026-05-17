// Plan 10 Task 14: AgentTabBar 组件 —— cursor-like 顶部对话 tab 栏。
import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AgentTabBar } from '../src/ui/components/AgentTabBar';

beforeEach(async () => {
  await chrome.storage.local.clear();
  vi.clearAllMocks();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('AgentTabBar', () => {
  it('renders empty state hint when no tabs', () => {
    render(
      <AgentTabBar
        tabs={[]}
        activeTabId={null}
        onActivate={() => {}}
        onClose={() => {}}
        onNew={() => {}}
        onShowHistory={() => {}}
      />,
    );
    expect(screen.getByLabelText('新建对话')).toBeInTheDocument();
    expect(screen.getByLabelText('历史对话')).toBeInTheDocument();
  });

  it('renders one tab per id with title', () => {
    render(
      <AgentTabBar
        tabs={[
          { id: 'a', title: '加搜索' },
          { id: 'b', title: '改字号' },
        ]}
        activeTabId="a"
        onActivate={() => {}}
        onClose={() => {}}
        onNew={() => {}}
        onShowHistory={() => {}}
      />,
    );
    expect(screen.getByText('加搜索')).toBeInTheDocument();
    expect(screen.getByText('改字号')).toBeInTheDocument();
  });

  it('marks active tab with aria-current', () => {
    render(
      <AgentTabBar
        tabs={[
          { id: 'a', title: 'A' },
          { id: 'b', title: 'B' },
        ]}
        activeTabId="b"
        onActivate={() => {}}
        onClose={() => {}}
        onNew={() => {}}
        onShowHistory={() => {}}
      />,
    );
    const btnB = screen.getByRole('tab', { name: /B/ });
    expect(btnB).toHaveAttribute('aria-current', 'page');
  });

  it('clicking inactive tab fires onActivate', () => {
    const onActivate = vi.fn();
    render(
      <AgentTabBar
        tabs={[
          { id: 'a', title: 'A' },
          { id: 'b', title: 'B' },
        ]}
        activeTabId="a"
        onActivate={onActivate}
        onClose={() => {}}
        onNew={() => {}}
        onShowHistory={() => {}}
      />,
    );
    fireEvent.click(screen.getByRole('tab', { name: /B/ }));
    expect(onActivate).toHaveBeenCalledWith('b');
  });

  it('clicking × on a tab fires onClose with that id (without onActivate)', () => {
    const onActivate = vi.fn();
    const onClose = vi.fn();
    render(
      <AgentTabBar
        tabs={[{ id: 'a', title: 'A' }, { id: 'b', title: 'B' }]}
        activeTabId="a"
        onActivate={onActivate}
        onClose={onClose}
        onNew={() => {}}
        onShowHistory={() => {}}
      />,
    );
    const closeBtnB = screen.getByLabelText('关闭 B');
    fireEvent.click(closeBtnB);
    expect(onClose).toHaveBeenCalledWith('b');
    expect(onActivate).not.toHaveBeenCalled();
  });

  it('clicking + fires onNew', () => {
    const onNew = vi.fn();
    render(
      <AgentTabBar
        tabs={[]}
        activeTabId={null}
        onActivate={() => {}}
        onClose={() => {}}
        onNew={onNew}
        onShowHistory={() => {}}
      />,
    );
    fireEvent.click(screen.getByLabelText('新建对话'));
    expect(onNew).toHaveBeenCalled();
  });

  it('clicking history icon fires onShowHistory', () => {
    const onShowHistory = vi.fn();
    render(
      <AgentTabBar
        tabs={[]}
        activeTabId={null}
        onActivate={() => {}}
        onClose={() => {}}
        onNew={() => {}}
        onShowHistory={onShowHistory}
      />,
    );
    fireEvent.click(screen.getByLabelText('历史对话'));
    expect(onShowHistory).toHaveBeenCalled();
  });

  it('renders untitled hint when tab.title is empty', () => {
    render(
      <AgentTabBar
        tabs={[{ id: 'a', title: '' }]}
        activeTabId="a"
        onActivate={() => {}}
        onClose={() => {}}
        onNew={() => {}}
        onShowHistory={() => {}}
      />,
    );
    expect(screen.getByText(/未命名/)).toBeInTheDocument();
  });
});
