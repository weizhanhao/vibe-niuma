// Plan 11 · M3.T19：HealthDashboard 单测。
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { HealthDashboard } from '../src/ui/components/HealthDashboard';

const PROPS = {
  orchestratorUrl: 'http://test.example.com:9000',
  adminToken: 'test-admin-token',
};

function installFetchMock(reply: () => Response | Promise<Response>) {
  const fn = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => reply());
  vi.stubGlobal('fetch', fn as unknown as typeof fetch);
  return fn;
}

// 默认用 real timers（waitFor 友好）；polling test 自己开 fake timers
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('HealthDashboard', () => {
  it('显示绿灯（status=ok）且初始无横幅', async () => {
    installFetchMock(() =>
      new Response(JSON.stringify({
        status: 'ok',
        services: { orchestrator: 'ok', mysql: 'ok', llm_proxy: 'ok', main_demo: 'ok' },
        uptime_seconds: 3600,
        last_cr_at: '2026-05-18T17:00:00',
      }), { status: 200 }),
    );

    render(<HealthDashboard {...PROPS} pollIntervalMs={9999999} />);
    await waitFor(() => {
      expect(screen.getByRole('status', { name: /系统状态/ })).toHaveAttribute(
        'data-status', 'ok',
      );
    });
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('显示红灯 + 横幅当 mysql down', async () => {
    installFetchMock(() =>
      new Response(JSON.stringify({
        status: 'red',
        services: { orchestrator: 'ok', mysql: 'down', llm_proxy: 'ok', main_demo: 'ok' },
        uptime_seconds: 60,
        last_cr_at: null,
      }), { status: 200 }),
    );

    render(<HealthDashboard {...PROPS} pollIntervalMs={9999999} />);
    await waitFor(() => {
      expect(screen.getByRole('status', { name: /系统状态/ })).toHaveAttribute(
        'data-status', 'red',
      );
    });
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(/mysql/i);
  });

  it('黄灯不弹横幅但展示降级提示', async () => {
    installFetchMock(() =>
      new Response(JSON.stringify({
        status: 'yellow',
        services: { orchestrator: 'ok', mysql: 'ok', llm_proxy: 'down', main_demo: 'ok' },
        uptime_seconds: 60,
        last_cr_at: null,
      }), { status: 200 }),
    );

    render(<HealthDashboard {...PROPS} pollIntervalMs={9999999} />);
    await waitFor(() => {
      expect(screen.getByRole('status', { name: /系统状态/ })).toHaveAttribute(
        'data-status', 'yellow',
      );
    });
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('/health 5xx 退化成 red 不死锁', async () => {
    installFetchMock(() =>
      new Response(JSON.stringify({ detail: 'down' }), { status: 503 }),
    );

    render(<HealthDashboard {...PROPS} pollIntervalMs={9999999} />);
    await waitFor(() => {
      expect(screen.getByRole('status', { name: /系统状态/ })).toHaveAttribute(
        'data-status', 'red',
      );
    });
  });

  it('hover 弹 detail tooltip 含 services + uptime', async () => {
    installFetchMock(() =>
      new Response(JSON.stringify({
        status: 'ok',
        services: { orchestrator: 'ok', mysql: 'ok', llm_proxy: 'unknown', main_demo: 'unknown' },
        uptime_seconds: 7200,
        last_cr_at: '2026-05-18T16:00:00',
      }), { status: 200 }),
    );

    render(<HealthDashboard {...PROPS} pollIntervalMs={9999999} />);
    const indicator = await screen.findByRole('status', { name: /系统状态/ });
    fireEvent.mouseEnter(indicator);
    expect(await screen.findByText(/mysql/i)).toBeInTheDocument();
    expect(screen.getByText(/orchestrator/i)).toBeInTheDocument();
    expect(screen.getByText(/uptime/i)).toBeInTheDocument();
  });

  it('每 pollIntervalMs 轮询一次', async () => {
    const fetchFn = installFetchMock(() =>
      new Response(JSON.stringify({
        status: 'ok',
        services: { orchestrator: 'ok', mysql: 'ok', llm_proxy: 'unknown', main_demo: 'unknown' },
        uptime_seconds: 1,
        last_cr_at: null,
      }), { status: 200 }),
    );

    // 这个 test 用 fake timers 控制轮询节奏
    vi.useFakeTimers();
    render(<HealthDashboard {...PROPS} pollIntervalMs={1000} />);
    // 让首次 fetch 兑现（useEffect 同步发出去，但 fetch 是异步的）
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    expect(fetchFn).toHaveBeenCalledTimes(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(fetchFn).toHaveBeenCalledTimes(2);
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(fetchFn).toHaveBeenCalledTimes(3);
  });
});
