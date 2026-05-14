import { useEffect, useState } from 'react';
import { listOrders, type OrderSummary } from '../api/client';
import { StatCard } from '../components/StatCard';

export function Dashboard() {
  const [orders, setOrders] = useState<OrderSummary[] | null>(null);

  useEffect(() => {
    listOrders()
      .then(setOrders)
      .catch(() => setOrders([]));
  }, []);

  if (orders === null) {
    return <div>加载中…</div>;
  }

  const total = orders.length;
  const revenue = orders
    .filter((o) => o.status === 'paid')
    .reduce((sum, o) => sum + o.total_amount, 0);
  const pending = orders.filter((o) => o.status === 'pending').length;

  return (
    <section aria-labelledby="dashboard-heading">
      <h1 id="dashboard-heading" style={{ marginBottom: 'var(--space-4)' }}>
        看板
      </h1>
      <div style={{ display: 'flex', gap: 'var(--space-3)', flexWrap: 'wrap' }}>
        <StatCard label="订单总数" value={String(total)} />
        <StatCard label="已支付营收" value={`¥${revenue.toFixed(2)}`} />
        <StatCard label="待支付订单" value={String(pending)} />
      </div>
    </section>
  );
}
