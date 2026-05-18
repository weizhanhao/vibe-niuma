// Plan 11 · M2.T13：AliyunProvisionStep 单测。
//
// 业务员粘 Aliyun access key → 点「自动开机」→ 走 /admin/provision-ecs ＋
// bootstrap=true，拿回 admin_token + orchestrator_url。
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { AliyunProvisionStep } from '../src/ui/components/AliyunProvisionStep';

function installFetchMock(response: Response | (() => Response)) {
  const fn = vi.fn(
    async (_input: RequestInfo | URL, _init?: RequestInit) =>
      typeof response === 'function' ? response() : response,
  );
  vi.stubGlobal('fetch', fn as unknown as typeof fetch);
  return fn;
}

const PROPS_BASE = {
  bootstrapOrchestratorUrl: 'http://bootstrap.example.com:9000',
  bootstrapAdminToken: 'bootstrap-admin-token-xyz',
};

afterEach(() => vi.unstubAllGlobals());

describe('AliyunProvisionStep', () => {
  it('禁用「开机」按钮直到必填都填完', () => {
    render(<AliyunProvisionStep {...PROPS_BASE} onComplete={vi.fn()} />);
    const btn = screen.getByRole('button', { name: /自动开机/ });
    expect(btn).toBeDisabled();

    fireEvent.change(screen.getByLabelText(/Access Key ID/i), {
      target: { value: 'LTAIxxx' },
    });
    expect(btn).toBeDisabled();

    fireEvent.change(screen.getByLabelText(/Access Key Secret/i), {
      target: { value: 'secretxxx' },
    });
    expect(btn).toBeDisabled();

    fireEvent.change(screen.getByLabelText(/DeepSeek/i), {
      target: { value: 'sk-deepseek-xxx' },
    });
    expect(btn).not.toBeDisabled();
  });

  it('成功路径：POST /admin/provision-ecs 然后 onComplete 拿到 admin_token', async () => {
    const fetchMock = installFetchMock(
      new Response(
        JSON.stringify({
          instance_id: 'i-fake-001',
          public_ip: '47.96.1.2',
          region_id: 'cn-hangzhou',
          root_password: 'TempPass123!',
          open_ports: [22, 9000],
          bootstrap: {
            ok: true,
            admin_token: 'new-admin-token-abc',
            orchestrator_url: 'http://47.96.1.2:9000',
          },
          next_step: '完成',
        }),
        { status: 200 },
      ),
    );

    const onComplete = vi.fn();
    render(<AliyunProvisionStep {...PROPS_BASE} onComplete={onComplete} />);

    fireEvent.change(screen.getByLabelText(/Access Key ID/i), {
      target: { value: 'LTAIxxx' },
    });
    fireEvent.change(screen.getByLabelText(/Access Key Secret/i), {
      target: { value: 'secretxxx' },
    });
    fireEvent.change(screen.getByLabelText(/DeepSeek/i), {
      target: { value: 'sk-deepseek-xxx' },
    });
    fireEvent.change(screen.getByLabelText(/通义.*视觉/i), {
      target: { value: 'sk-dashscope-yyy' },
    });

    fireEvent.click(screen.getByRole('button', { name: /自动开机/ }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toBe('http://bootstrap.example.com:9000/admin/provision-ecs');
    expect(init?.method).toBe('POST');
    expect(init?.headers).toMatchObject({
      'X-Admin-Token': 'bootstrap-admin-token-xyz',
      'Content-Type': 'application/json',
    });
    const body = JSON.parse(init?.body as string);
    expect(body).toMatchObject({
      access_key_id: 'LTAIxxx',
      access_key_secret: 'secretxxx',
      region_id: 'cn-hangzhou',
      bootstrap: true,
      deepseek_api_key: 'sk-deepseek-xxx',
      dashscope_api_key: 'sk-dashscope-yyy',
    });

    await waitFor(() =>
      expect(onComplete).toHaveBeenCalledWith({
        publicIp: '47.96.1.2',
        orchestratorUrl: 'http://47.96.1.2:9000',
        adminToken: 'new-admin-token-abc',
      }),
    );
  });

  it('阿里云 5xx 失败：展示错误，不 onComplete', async () => {
    installFetchMock(
      new Response(JSON.stringify({ detail: '配额不足' }), { status: 500 }),
    );
    const onComplete = vi.fn();
    render(<AliyunProvisionStep {...PROPS_BASE} onComplete={onComplete} />);

    fireEvent.change(screen.getByLabelText(/Access Key ID/i), { target: { value: 'x' } });
    fireEvent.change(screen.getByLabelText(/Access Key Secret/i), { target: { value: 'y' } });
    fireEvent.change(screen.getByLabelText(/DeepSeek/i), { target: { value: 'z' } });
    fireEvent.click(screen.getByRole('button', { name: /自动开机/ }));

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent('配额不足');
    });
    expect(onComplete).not.toHaveBeenCalled();
  });

  it('bootstrap 部分失败：展示 public_ip + 错误，不 onComplete', async () => {
    installFetchMock(
      new Response(
        JSON.stringify({
          instance_id: 'i-fake',
          public_ip: '47.96.88.1',
          region_id: 'cn-hangzhou',
          root_password: 'Temp!',
          open_ports: [22],
          bootstrap: { ok: false, error: 'apt-get failed: 网络不通' },
        }),
        { status: 200 },
      ),
    );
    const onComplete = vi.fn();
    render(<AliyunProvisionStep {...PROPS_BASE} onComplete={onComplete} />);

    fireEvent.change(screen.getByLabelText(/Access Key ID/i), { target: { value: 'x' } });
    fireEvent.change(screen.getByLabelText(/Access Key Secret/i), { target: { value: 'y' } });
    fireEvent.change(screen.getByLabelText(/DeepSeek/i), { target: { value: 'z' } });
    fireEvent.click(screen.getByRole('button', { name: /自动开机/ }));

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent('apt-get failed');
    });
    // IP 同时出现在 alert + 「ssh root@<ip>」小提示里，匹配 ≥1
    expect(screen.getAllByText(/47\.96\.88\.1/).length).toBeGreaterThanOrEqual(1);
    expect(onComplete).not.toHaveBeenCalled();
  });

  it('network error: 给业务员看得懂的错', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('Failed to fetch');
      }) as unknown as typeof fetch,
    );
    const onComplete = vi.fn();
    render(<AliyunProvisionStep {...PROPS_BASE} onComplete={onComplete} />);

    fireEvent.change(screen.getByLabelText(/Access Key ID/i), { target: { value: 'x' } });
    fireEvent.change(screen.getByLabelText(/Access Key Secret/i), { target: { value: 'y' } });
    fireEvent.change(screen.getByLabelText(/DeepSeek/i), { target: { value: 'z' } });
    fireEvent.click(screen.getByRole('button', { name: /自动开机/ }));

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent(/连不上/);
    });
    expect(onComplete).not.toHaveBeenCalled();
  });
});
