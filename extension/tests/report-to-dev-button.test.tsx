// Plan 11 · M3.T22 ReportToDevButton 单测。
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ReportToDevButton } from '../src/ui/components/ReportToDevButton';

const PROPS_BASE = {
  orchestratorUrl: 'http://orch.example.com:9000',
  adminToken: 'admin-token-xxx',
  webhookUrl: 'https://oapi.dingtalk.com/robot/send?access_token=fake',
  context: {
    lastCrId: 'cr-abc-123',
    consoleErrors: ['TypeError: x is undefined', '404 /api/foo'],
  },
};

function installFetchMock(fn?: ReturnType<typeof vi.fn>) {
  const f = fn ?? vi.fn(async () => new Response(JSON.stringify({ ok: true }), { status: 200 }));
  vi.stubGlobal('fetch', f as unknown as typeof fetch);
  return f;
}

afterEach(() => vi.unstubAllGlobals());

describe('ReportToDevButton', () => {
  it('点按钮 → 自动调用 /admin/alert 带上下文', async () => {
    const fetchFn = installFetchMock();
    render(<ReportToDevButton {...PROPS_BASE} />);
    const btn = await screen.findByRole('button', { name: /报告给程序员/ });
    fireEvent.click(btn);

    await waitFor(() => expect(fetchFn).toHaveBeenCalledTimes(1));
    const [url, init] = fetchFn.mock.calls[0];
    expect(String(url)).toBe('http://orch.example.com:9000/admin/alert');
    expect(init?.method).toBe('POST');
    expect(init?.headers).toMatchObject({
      'X-Admin-Token': 'admin-token-xxx',
      'Content-Type': 'application/json',
    });
    const body = JSON.parse(init?.body as string);
    expect(body.webhook_url).toBe(PROPS_BASE.webhookUrl);
    expect(body.title).toMatch(/vibe-niuma/i);
    expect(body.body).toContain('cr-abc-123');
    expect(body.body).toContain('TypeError: x is undefined');
  });

  it('业务员补的留言会进 body', async () => {
    const fetchFn = installFetchMock();
    render(<ReportToDevButton {...PROPS_BASE} />);
    const textarea = await screen.findByRole('textbox', { name: /补充留言/ });
    fireEvent.change(textarea, { target: { value: '我点保存按钮 ECS 报红了' } });
    fireEvent.click(screen.getByRole('button', { name: /报告给程序员/ }));
    await waitFor(() => expect(fetchFn).toHaveBeenCalledTimes(1));
    const body = JSON.parse(fetchFn.mock.calls[0][1]?.body as string);
    expect(body.body).toContain('我点保存按钮 ECS 报红了');
  });

  it('成功 → 显示 confirm 反馈', async () => {
    installFetchMock();
    render(<ReportToDevButton {...PROPS_BASE} />);
    fireEvent.click(await screen.findByRole('button', { name: /报告给程序员/ }));
    await waitFor(() => {
      expect(screen.getByRole('status')).toHaveTextContent(/已发/);
    });
  });

  it('失败 → role=alert 显示错误，按钮可重试', async () => {
    installFetchMock(vi.fn(async () =>
      new Response(JSON.stringify({ detail: 'webhook keywords not in content' }), { status: 502 }),
    ));
    render(<ReportToDevButton {...PROPS_BASE} />);
    fireEvent.click(await screen.findByRole('button', { name: /报告给程序员/ }));
    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent(/keywords not in content/);
    });
    expect(screen.getByRole('button', { name: /报告给程序员/ })).not.toBeDisabled();
  });

  it('webhookUrl 为空时按钮禁用，提示业务员去配', () => {
    installFetchMock();
    render(<ReportToDevButton {...PROPS_BASE} webhookUrl="" />);
    expect(screen.getByRole('button', { name: /报告给程序员/ })).toBeDisabled();
    expect(screen.getByText(/先在设置里配 webhook/)).toBeInTheDocument();
  });
});
