import { afterEach, describe, expect, it, vi } from 'vitest';
import { getOrder, getSettings, listOrders, updateSetting } from '../src/api/client';

afterEach(() => {
  vi.restoreAllMocks();
});

function mockFetch(body: unknown, ok = true, status = 200) {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue({
      ok,
      status,
      json: async () => body,
    }),
  );
}

describe('api client', () => {
  it('listOrders 请求 /api/orders 并返回数组', async () => {
    mockFetch([{ id: 1, customer_name: '张三', status: 'paid', total_amount: 100 }]);
    const orders = await listOrders();
    expect(fetch).toHaveBeenCalledWith('/api/orders');
    expect(orders[0].customer_name).toBe('张三');
  });

  it('getOrder 请求 /api/orders/:id', async () => {
    mockFetch({
      id: 7,
      customer_name: '李四',
      status: 'paid',
      total_amount: 50,
      items: [],
    });
    const order = await getOrder(7);
    expect(fetch).toHaveBeenCalledWith('/api/orders/7');
    expect(order.id).toBe(7);
  });

  it('getOrder 在 404 时抛错', async () => {
    mockFetch({ detail: 'not found' }, false, 404);
    await expect(getOrder(999)).rejects.toThrow();
  });

  it('getSettings 请求 /api/settings', async () => {
    mockFetch([{ key: 'page_title', value: '订单管理' }]);
    const settings = await getSettings();
    expect(fetch).toHaveBeenCalledWith('/api/settings');
    expect(settings[0].key).toBe('page_title');
  });

  it('updateSetting 用 PUT 提交 value', async () => {
    mockFetch({ key: 'page_title', value: '订单中心' });
    const updated = await updateSetting('page_title', '订单中心');
    expect(fetch).toHaveBeenCalledWith('/api/settings/page_title', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ value: '订单中心' }),
    });
    expect(updated.value).toBe('订单中心');
  });
});
