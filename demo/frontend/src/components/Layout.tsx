import type { ReactNode } from 'react';
import { NavLink } from 'react-router-dom';

const NAV = [
  { to: '/', label: '看板', end: true },
  { to: '/orders', label: '订单', end: false },
  { to: '/settings', label: '设置', end: false },
];

interface LayoutProps {
  children: ReactNode;
}

export function Layout({ children }: LayoutProps) {
  return (
    <div style={{ display: 'flex', minHeight: '100vh' }}>
      <nav
        aria-label="主导航"
        style={{
          width: 200,
          background: 'var(--color-surface)',
          borderRight: '1px solid var(--color-border)',
          padding: 'var(--space-4) var(--space-3)',
        }}
      >
        <div
          style={{
            fontSize: 'var(--text-lg)',
            fontWeight: 600,
            marginBottom: 'var(--space-4)',
          }}
        >
          订单管理
        </div>
        {NAV.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.end}
            style={({ isActive }) => ({
              display: 'block',
              padding: 'var(--space-2) var(--space-3)',
              borderRadius: 'var(--radius)',
              marginBottom: 'var(--space-1)',
              color: isActive ? 'var(--color-accent)' : 'var(--color-text)',
              background: isActive ? 'var(--color-bg)' : 'transparent',
              textDecoration: 'none',
            })}
          >
            {item.label}
          </NavLink>
        ))}
      </nav>
      <main style={{ flex: 1, padding: 'var(--space-5)' }}>{children}</main>
    </div>
  );
}
