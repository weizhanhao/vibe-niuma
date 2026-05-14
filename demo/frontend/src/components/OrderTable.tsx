import { Link } from 'react-router-dom';
import type { OrderSummary } from '../api/client';

const STATUS_LABEL: Record<string, string> = {
  paid: '已支付',
  pending: '待支付',
  shipped: '已发货',
  cancelled: '已取消',
};

const STATUS_COLOR: Record<string, string> = {
  paid: 'var(--status-paid)',
  pending: 'var(--status-pending)',
  shipped: 'var(--status-shipped)',
  cancelled: 'var(--status-cancelled)',
};

interface OrderTableProps {
  orders: OrderSummary[];
}

export function OrderTable({ orders }: OrderTableProps) {
  if (orders.length === 0) {
    return (
      <div style={{ color: 'var(--color-text-muted)', padding: 'var(--space-4)' }}>
        暂无订单
      </div>
    );
  }

  return (
    <table style={{ width: '100%', borderCollapse: 'collapse' }}>
      <thead>
        <tr style={{ textAlign: 'left', color: 'var(--color-text-muted)' }}>
          <th style={{ padding: 'var(--space-2)' }}>订单号</th>
          <th style={{ padding: 'var(--space-2)' }}>客户</th>
          <th style={{ padding: 'var(--space-2)' }}>状态</th>
          <th style={{ padding: 'var(--space-2)' }}>金额</th>
        </tr>
      </thead>
      <tbody>
        {orders.map((order) => (
          <tr key={order.id} style={{ borderTop: '1px solid var(--color-border)' }}>
            <td style={{ padding: 'var(--space-2)' }}>#{order.id}</td>
            <td style={{ padding: 'var(--space-2)' }}>
              <Link to={`/orders/${order.id}`} style={{ color: 'var(--color-accent)' }}>
                {order.customer_name}
              </Link>
            </td>
            <td style={{ padding: 'var(--space-2)' }}>
              <span style={{ color: STATUS_COLOR[order.status] ?? 'var(--color-text)' }}>
                {STATUS_LABEL[order.status] ?? order.status}
              </span>
            </td>
            <td style={{ padding: 'var(--space-2)' }}>
              ¥{order.total_amount.toFixed(2)}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
