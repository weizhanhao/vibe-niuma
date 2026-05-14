import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import { Layout } from '../src/components/Layout';

describe('Layout', () => {
  it('渲染三个导航链接', () => {
    render(
      <MemoryRouter>
        <Layout>
          <div>内容</div>
        </Layout>
      </MemoryRouter>,
    );
    expect(screen.getByRole('link', { name: '看板' })).toHaveAttribute('href', '/');
    expect(screen.getByRole('link', { name: '订单' })).toHaveAttribute('href', '/orders');
    expect(screen.getByRole('link', { name: '设置' })).toHaveAttribute(
      'href',
      '/settings',
    );
  });

  it('渲染传入的子内容', () => {
    render(
      <MemoryRouter>
        <Layout>
          <div>内容区文本</div>
        </Layout>
      </MemoryRouter>,
    );
    expect(screen.getByText('内容区文本')).toBeInTheDocument();
  });
});
