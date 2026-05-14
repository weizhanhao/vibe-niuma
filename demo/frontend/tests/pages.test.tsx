import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { Dashboard } from '../src/pages/Dashboard';
import { OrderDetail } from '../src/pages/OrderDetail';
import { OrderList } from '../src/pages/OrderList';
import { Settings } from '../src/pages/Settings';

afterEach(() => {
  vi.restoreAllMocks();
});

function mockFetchSequence(...bodies: unknown[]) {
  const fn = vi.fn();
  for (const body of bodies) {
    fn.mockResolvedValueOnce({ ok: true, status: 200, json: async () => body });
  }
  vi.stubGlobal('fetch', fn);
}

describe('Dashboard', () => {
  it('加载订单后渲染统计卡片', async () => {
    mockFetchSequence([
      { id: 1, customer_name: '张三', status: 'paid', total_amount: 100 },
      { id: 2, customer_name: '李四', status: 'pending', total_amount: 200 },
    ]);
    render(
      <MemoryRouter>
        <Dashboard />
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByText('订单总数')).toBeInTheDocument());
    expect(screen.getByText('2')).toBeInTheDocument();
  });
});

describe('OrderList', () => {
  it('加载并渲染订单表格', async () => {
    mockFetchSequence([
      { id: 1, customer_name: '张三', status: 'paid', total_amount: 100 },
    ]);
    render(
      <MemoryRouter>
        <OrderList />
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByText('张三')).toBeInTheDocument());
  });
});

describe('OrderDetail', () => {
  it('按路由参数加载订单详情', async () => {
    mockFetchSequence({
      id: 7,
      customer_name: '王五',
      status: 'shipped',
      total_amount: 300,
      items: [{ id: 1, product_name: '显示器', quantity: 1, unit_price: 300 }],
    });
    render(
      <MemoryRouter initialEntries={['/orders/7']}>
        <Routes>
          <Route path="/orders/:id" element={<OrderDetail />} />
        </Routes>
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByText('王五')).toBeInTheDocument());
    expect(screen.getByText('显示器')).toBeInTheDocument();
  });
});

describe('Settings', () => {
  it('加载并渲染设置项', async () => {
    mockFetchSequence([{ key: 'page_title', value: '订单管理' }]);
    render(
      <MemoryRouter>
        <Settings />
      </MemoryRouter>,
    );
    await waitFor(() =>
      expect(screen.getByDisplayValue('订单管理')).toBeInTheDocument(),
    );
  });
});
