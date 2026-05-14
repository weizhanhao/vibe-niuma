export interface OrderSummary {
  id: number;
  customer_name: string;
  status: string;
  total_amount: number;
}

export interface OrderItem {
  id: number;
  product_name: string;
  quantity: number;
  unit_price: number;
}

export interface OrderDetail extends OrderSummary {
  items: OrderItem[];
}

export interface AppSetting {
  key: string;
  value: string;
}

async function parse<T>(resp: Response): Promise<T> {
  if (!resp.ok) {
    throw new Error(`请求失败：${resp.status}`);
  }
  return (await resp.json()) as T;
}

export async function listOrders(): Promise<OrderSummary[]> {
  return parse<OrderSummary[]>(await fetch('/api/orders'));
}

export async function getOrder(id: number): Promise<OrderDetail> {
  return parse<OrderDetail>(await fetch(`/api/orders/${id}`));
}

export async function getSettings(): Promise<AppSetting[]> {
  return parse<AppSetting[]>(await fetch('/api/settings'));
}

export async function updateSetting(key: string, value: string): Promise<AppSetting> {
  return parse<AppSetting>(
    await fetch(`/api/settings/${key}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ value }),
    }),
  );
}
