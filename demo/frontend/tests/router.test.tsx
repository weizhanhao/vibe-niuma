import { describe, expect, it } from 'vitest';
import { routes } from '../src/router';

describe('routes 配置', () => {
  it('暴露 4 条路由路径', () => {
    const paths = routes.map((r) => r.path);
    expect(paths).toEqual(['/', '/orders', '/orders/:id', '/settings']);
  });

  it('每条路由都有 element', () => {
    for (const route of routes) {
      expect(route.element).toBeDefined();
    }
  });
});
