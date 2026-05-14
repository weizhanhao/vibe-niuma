import { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { getOrder, type OrderDetail as OrderDetailData } from '../api/client';

export function OrderDetail() {
  const { id } = useParams<{ id: string }>();
  const [order, setOrder] = useState<OrderDetailData | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    if (!id) return;
    getOrder(Number(id))
      .then(setOrder)
      .catch(() => setError(true));
  }, [id]);

  if (error) {
    return <div>订单不存在</div>;
  }
  if (order === null) {
    return <div>加载中…</div>;
  }

  return (
    <section aria-labelledby="order-detail-heading">
      <h1 id="order-detail-heading" style={{ marginBottom: 'var(--space-4)' }}>
        订单 #{order.id}
      </h1>
      <div
        style={{
          background: 'var(--color-surface)',
          border: '1px solid var(--color-border)',
          borderRadius: 'var(--radius)',
          padding: 'var(--space-4)',
        }}
      >
        <p style={{ marginBottom: 'var(--space-2)' }}>客户：<span>{order.customer_name}</span></p>
        <p style={{ marginBottom: 'var(--space-2)' }}>状态：{order.status}</p>
        <p style={{ marginBottom: 'var(--space-3)' }}>
          总金额：¥{order.total_amount.toFixed(2)}
        </p>
        <h2 style={{ fontSize: 'var(--text-lg)', marginBottom: 'var(--space-2)' }}>
          商品明细
        </h2>
        <ul>
          {order.items.map((item) => (
            <li key={item.id}>
              <span>{item.product_name}</span> × {item.quantity} —— ¥{item.unit_price.toFixed(2)}
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}
