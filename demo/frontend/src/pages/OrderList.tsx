import { useEffect, useState } from 'react';
import { listOrders, type OrderSummary } from '../api/client';
import { OrderTable } from '../components/OrderTable';

export function OrderList() {
  const [orders, setOrders] = useState<OrderSummary[] | null>(null);

  useEffect(() => {
    listOrders()
      .then(setOrders)
      .catch(() => setOrders([]));
  }, []);

  return (
    <section aria-labelledby="orders-heading">
      <h1 id="orders-heading" style={{ marginBottom: 'var(--space-4)' }}>
        订单
      </h1>
      <div
        style={{
          background: 'var(--color-surface)',
          border: '1px solid var(--color-border)',
          borderRadius: 'var(--radius)',
          padding: 'var(--space-3)',
        }}
      >
        {orders === null ? <div>加载中…</div> : <OrderTable orders={orders} />}
      </div>
    </section>
  );
}
