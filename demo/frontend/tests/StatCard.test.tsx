import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { StatCard } from '../src/components/StatCard';

describe('StatCard', () => {
  it('渲染标题与数值', () => {
    render(<StatCard label="订单总数" value="128" />);
    expect(screen.getByText('订单总数')).toBeInTheDocument();
    expect(screen.getByText('128')).toBeInTheDocument();
  });

  it('带 hint 时渲染 hint 文本', () => {
    render(<StatCard label="本月营收" value="¥9,800" hint="较上月 +12%" />);
    expect(screen.getByText('较上月 +12%')).toBeInTheDocument();
  });
});
