import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import type { OrderSummary } from '../src/api/client';
import { OrderTable } from '../src/components/OrderTable';

const ORDERS: OrderSummary[] = [
  { id: 1, customer_name: '张三', status: 'paid', total_amount: 398 },
  { id: 2, customer_name: '李四', status: 'pending', total_amount: 2598 },
];

describe('OrderTable', () => {
  it('每条订单渲染一行，含客户名与金额', () => {
    render(
      <MemoryRouter>
        <OrderTable orders={ORDERS} />
      </MemoryRouter>,
    );
    expect(screen.getByText('张三')).toBeInTheDocument();
    expect(screen.getByText('李四')).toBeInTheDocument();
    expect(screen.getByText('¥398.00')).toBeInTheDocument();
  });

  it('客户名是指向详情页的链接', () => {
    render(
      <MemoryRouter>
        <OrderTable orders={ORDERS} />
      </MemoryRouter>,
    );
    const link = screen.getByRole('link', { name: '张三' });
    expect(link).toHaveAttribute('href', '/orders/1');
  });

  it('空列表时渲染空状态文案', () => {
    render(
      <MemoryRouter>
        <OrderTable orders={[]} />
      </MemoryRouter>,
    );
    expect(screen.getByText('暂无订单')).toBeInTheDocument();
  });
});
