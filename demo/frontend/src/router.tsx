import type { ReactElement } from 'react';
import { createBrowserRouter } from 'react-router-dom';
import { Layout } from './components/Layout';
import { Dashboard } from './pages/Dashboard';
import { OrderDetail } from './pages/OrderDetail';
import { OrderList } from './pages/OrderList';
import { Settings } from './pages/Settings';

interface RouteDef {
  path: string;
  element: ReactElement;
}

// routes：StackAdapter 的契约锚点。path 字符串与对应页面组件文件一一对应。
export const routes: RouteDef[] = [
  { path: '/', element: <Dashboard /> },
  { path: '/orders', element: <OrderList /> },
  { path: '/orders/:id', element: <OrderDetail /> },
  { path: '/settings', element: <Settings /> },
];

export const router = createBrowserRouter(
  routes.map((route) => ({
    path: route.path,
    element: <Layout>{route.element}</Layout>,
  })),
);
