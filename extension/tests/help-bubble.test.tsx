// Plan 6 · Task 6: HelpBubble 通用组件。
// 行为：
//   - 渲染圆形 `?` 触发器（aria-label 来自 props）
//   - 点击展开 floating popover，里面用 react-markdown 渲染 content（含外链）
//   - 外链必须有 target="_blank" + rel 安全属性
//   - 点击 popover 外部 / 按 Esc 关闭
//   - 默认折叠（popover 不挂载在 DOM）
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { HelpBubble } from '../src/ui/components/HelpBubble';

describe('HelpBubble', () => {
  it('renders question mark icon with aria-label', () => {
    render(<HelpBubble content="hi" ariaLabel="什么是 X" />);
    const btn = screen.getByRole('button', { name: '什么是 X' });
    expect(btn).toBeInTheDocument();
    // 折叠态：popover 不挂载
    expect(screen.queryByRole('tooltip')).toBeNull();
  });

  it('clicking shows popover with markdown rendered as elements', () => {
    render(
      <HelpBubble
        content={'**粗体** 和 [外链](https://example.com) 在这里'}
        ariaLabel="说明"
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: '说明' }));
    const tooltip = screen.getByRole('tooltip');
    // 粗体应该被 react-markdown 渲染成 <strong>
    expect(tooltip.querySelector('strong')).not.toBeNull();
    // 外链应该被渲染成 <a>
    const a = tooltip.querySelector('a');
    expect(a).not.toBeNull();
    expect(a?.textContent).toBe('外链');
    expect(a?.getAttribute('href')).toBe('https://example.com');
  });

  it('external links open in a new tab with safe rel', () => {
    render(
      <HelpBubble
        content={'[外链](https://example.com)'}
        ariaLabel="说明"
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: '说明' }));
    const a = screen.getByRole('tooltip').querySelector('a')!;
    expect(a.getAttribute('target')).toBe('_blank');
    const rel = a.getAttribute('rel') ?? '';
    expect(rel).toContain('noopener');
    expect(rel).toContain('noreferrer');
  });

  it('clicking outside closes popover', () => {
    render(
      <div>
        <HelpBubble content="hi" ariaLabel="说明" />
        <div data-testid="outside">outside</div>
      </div>,
    );
    fireEvent.click(screen.getByRole('button', { name: '说明' }));
    expect(screen.getByRole('tooltip')).toBeInTheDocument();

    // 模拟点击外部
    fireEvent.mouseDown(screen.getByTestId('outside'));
    expect(screen.queryByRole('tooltip')).toBeNull();
  });

  it('pressing Escape closes the popover (a11y keyboard)', () => {
    render(<HelpBubble content="hi" ariaLabel="说明" />);
    fireEvent.click(screen.getByRole('button', { name: '说明' }));
    expect(screen.getByRole('tooltip')).toBeInTheDocument();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByRole('tooltip')).toBeNull();
  });

  it('clicking the trigger again toggles popover closed', () => {
    render(<HelpBubble content="hi" ariaLabel="说明" />);
    const btn = screen.getByRole('button', { name: '说明' });
    fireEvent.click(btn);
    expect(screen.getByRole('tooltip')).toBeInTheDocument();
    fireEvent.click(btn);
    expect(screen.queryByRole('tooltip')).toBeNull();
  });
});
