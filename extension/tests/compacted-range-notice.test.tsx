// Plan 9 Task 11: CompactedRangeNotice 测试 —— 折叠条 UX
//
// 业务员看到的：
//   chat 里有一条折叠条「已折叠 47 条历史（~12k tokens）▾」
//   点 → 展开 drawer 看完整老 ai 消息
//   再点 → 收回
//
// 不变量：
//   - 默认折叠
//   - replacesCount + replacesTokenEstimate 必须显示出来
//   - 点击后 drawer 出现 + aria-expanded 切换
//   - 默认 initiallyOpen=false；可显式 true 直接展开

import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { CompactedRangeNotice } from '../src/ui/components/CompactedRangeNotice';

describe('CompactedRangeNotice', () => {
  it('默认折叠：show 折叠条但不渲染 children', () => {
    render(
      <CompactedRangeNotice replacesCount={47} replacesTokenEstimate={12345}>
        <div data-testid="hidden-history">老的 AI 回复 #1</div>
      </CompactedRangeNotice>,
    );
    expect(screen.getByText(/已折叠 47 条历史/)).toBeInTheDocument();
    expect(screen.getByText(/~12.3k tokens/i)).toBeInTheDocument();
    expect(screen.queryByTestId('hidden-history')).toBeNull();
    const btn = screen.getByRole('button', { name: /已折叠 47 条历史/ });
    expect(btn).toHaveAttribute('aria-expanded', 'false');
  });

  it('点击 → 展开 children + aria-expanded=true', () => {
    render(
      <CompactedRangeNotice replacesCount={47} replacesTokenEstimate={12345}>
        <div data-testid="hidden-history">老的 AI 回复 #1</div>
      </CompactedRangeNotice>,
    );
    const btn = screen.getByRole('button', { name: /已折叠 47 条历史/ });
    fireEvent.click(btn);
    expect(screen.getByTestId('hidden-history')).toBeInTheDocument();
    expect(btn).toHaveAttribute('aria-expanded', 'true');
  });

  it('再点 → 收回', () => {
    render(
      <CompactedRangeNotice replacesCount={47} replacesTokenEstimate={12345}>
        <div data-testid="hidden-history">老的 AI 回复 #1</div>
      </CompactedRangeNotice>,
    );
    const btn = screen.getByRole('button', { name: /已折叠 47 条历史/ });
    fireEvent.click(btn);
    fireEvent.click(btn);
    expect(screen.queryByTestId('hidden-history')).toBeNull();
    expect(btn).toHaveAttribute('aria-expanded', 'false');
  });

  it('replacesCount = 1 时显示「1 条」', () => {
    render(<CompactedRangeNotice replacesCount={1} replacesTokenEstimate={50} />);
    expect(screen.getByText(/已折叠 1 条历史/)).toBeInTheDocument();
  });

  it('replacesTokenEstimate = 0 时只显条数不显 token', () => {
    render(<CompactedRangeNotice replacesCount={3} replacesTokenEstimate={0} />);
    expect(screen.getByText(/已折叠 3 条历史/)).toBeInTheDocument();
    expect(screen.queryByText(/tokens/i)).toBeNull();
  });

  it('token 数 < 1000 时显原始数字而非 k 单位', () => {
    render(<CompactedRangeNotice replacesCount={5} replacesTokenEstimate={420} />);
    expect(screen.getByText(/~420 tokens/i)).toBeInTheDocument();
  });

  it('initiallyOpen=true 时直接展开', () => {
    render(
      <CompactedRangeNotice
        replacesCount={3}
        replacesTokenEstimate={100}
        initiallyOpen={true}
      >
        <div data-testid="hidden-history">x</div>
      </CompactedRangeNotice>,
    );
    expect(screen.getByTestId('hidden-history')).toBeInTheDocument();
  });
});
