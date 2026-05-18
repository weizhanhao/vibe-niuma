// Plan 6 · Task 8: SetupWizardPanel 4 步首次引导单测。
//
// 设计要点：
//   - 用 vi.stubGlobal('fetch', ...) 拦截 AdminClient 的 HTTP 调用
//   - chrome.* 已在 tests/setup.ts 全局 mock；这里只用断言「set 被调用」
//   - 每个 test 跑前重置 fetch 反应 + 重新 render
//   - 不直接断言 markdown 文案，避免帮助文档改了就挂；用 ariaLabel / role 选元素
//
// 调用方：vitest test runner（tests/**/*.test.{ts,tsx} glob）
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import {
  afterEach,
  describe,
  expect,
  it,
  vi,
} from 'vitest';
import { SetupWizardPanel } from '../src/ui/panels/SetupWizardPanel';

// ── 测试用 fetch mock 工厂 ─────────────────────────────────────
// 每个 case 把「URL match → 返回的 Response」配进来；未配中默认 500。
type RouteHandler = (init?: RequestInit) => Promise<Response> | Response;

interface RouteMap {
  health?: RouteHandler;
  getConfig?: RouteHandler;
  putConfig?: RouteHandler;
}

function installFetchMock(routes: RouteMap) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith('/health') && routes.health) {
      return routes.health(init);
    }
    if (url.endsWith('/admin/config')) {
      const method = (init?.method ?? 'GET').toUpperCase();
      if (method === 'GET' && routes.getConfig) return routes.getConfig(init);
      if (method === 'PUT' && routes.putConfig) return routes.putConfig(init);
    }
    return new Response(JSON.stringify({ detail: 'no route' }), { status: 500 });
  });
  vi.stubGlobal('fetch', fetchMock as unknown as typeof fetch);
  return fetchMock;
}

const OK_HEALTH: RouteHandler = () =>
  new Response(JSON.stringify({ ok: true }), { status: 200 });

const OK_GET_CONFIG: RouteHandler = () =>
  new Response(
    JSON.stringify({
      config: {
        dev_runner: 'opencode',
        dev_model: 'deepseek/deepseek-v4-flash',
        vision_model: 'qwen-vl-plus',
        deepseek_api_key: null,
        dashscope_api_key: null,
        anthropic_api_key: null,
        demo_repo_path: '/opt/vibe-niuma/demo',
        preview_backend_url: 'http://x:8000',
        deepseek_api_key_set: false,
        dashscope_api_key_set: false,
        anthropic_api_key_set: false,
      },
      version: 0,
    }),
    { status: 200 },
  );

const OK_PUT_CONFIG: RouteHandler = () =>
  new Response(
    JSON.stringify({
      config: {
        dev_runner: 'opencode',
        dev_model: 'deepseek/deepseek-v4-flash',
        vision_model: 'qwen-vl-plus',
        deepseek_api_key: null,
        dashscope_api_key: null,
        anthropic_api_key: null,
        demo_repo_path: '/opt/vibe-niuma/demo',
        preview_backend_url: 'http://x:8000',
        deepseek_api_key_set: true,
        dashscope_api_key_set: true,
        anthropic_api_key_set: false,
      },
      version: 1,
      restartedServices: ['vibe-niuma-orchestrator'],
    }),
    { status: 200 },
  );

const TOKEN = 'admin-token-1234567890abcdef'; // 长度 >= 20

// 把 Step 1 → Step 2 → Step 3 一路推过去，留 onComplete spy 给后续断言
async function advanceToStep3(onComplete = vi.fn()) {
  const fetchMock = installFetchMock({
    health: OK_HEALTH,
    getConfig: OK_GET_CONFIG,
    putConfig: OK_PUT_CONFIG,
  });
  render(<SetupWizardPanel onComplete={onComplete} />);

  // Step 1：测试连接 → 下一步
  fireEvent.click(screen.getByRole('button', { name: /测试连接/ }));
  await waitFor(() => screen.getByRole('button', { name: /连接成功/ }));
  fireEvent.click(screen.getByRole('button', { name: /下一步/ }));

  // Step 2：填 token → 验证 → 下一步
  fireEvent.change(screen.getByLabelText('Admin Token'), { target: { value: TOKEN } });
  fireEvent.click(screen.getByRole('button', { name: /^验证$/ }));
  await waitFor(() => screen.getByRole('button', { name: /令牌有效/ }));
  fireEvent.click(screen.getByRole('button', { name: /下一步/ }));

  return { fetchMock, onComplete };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('SetupWizardPanel · initial render', () => {
  it('test_initial_render_shows_step_1', () => {
    render(<SetupWizardPanel onComplete={vi.fn()} />);
    expect(screen.getByRole('heading', { name: '服务器地址' })).toBeInTheDocument();
    // URL 默认填好 localhost
    const input = screen.getByLabelText('Orchestrator URL') as HTMLInputElement;
    expect(input.value).toBe('http://localhost:9000');
  });
});

describe('SetupWizardPanel · Step 1 (URL + testConnection)', () => {
  it('test_step1_url_test_button_calls_health_endpoint', async () => {
    const fetchMock = installFetchMock({ health: OK_HEALTH });
    render(<SetupWizardPanel onComplete={vi.fn()} />);

    fireEvent.click(screen.getByRole('button', { name: /测试连接/ }));
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /连接成功/ })).toBeInTheDocument();
    });
    expect(fetchMock).toHaveBeenCalledWith(
      'http://localhost:9000/health',
      expect.objectContaining({ method: 'GET' }),
    );
  });

  it('test_step1_test_failure_shows_error_and_disables_next', async () => {
    installFetchMock({
      health: () => new Response('boom', { status: 500 }),
    });
    render(<SetupWizardPanel onComplete={vi.fn()} />);

    fireEvent.click(screen.getByRole('button', { name: /测试连接/ }));
    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent(/连接失败/);
    });
    // 下一步按钮 disabled —— canGoNext=step1Tested=false
    expect(screen.getByRole('button', { name: /下一步/ })).toBeDisabled();
  });

  it('test_step1_invalid_url_disables_test_button', () => {
    installFetchMock({});
    render(<SetupWizardPanel onComplete={vi.fn()} />);

    fireEvent.change(screen.getByLabelText('Orchestrator URL'), {
      target: { value: 'not-a-url' },
    });
    expect(screen.getByRole('button', { name: /测试连接/ })).toBeDisabled();
  });
});

describe('SetupWizardPanel · Step 2 (admin token)', () => {
  it('test_step2_validates_token_via_get_admin_config', async () => {
    const fetchMock = installFetchMock({
      health: OK_HEALTH,
      getConfig: OK_GET_CONFIG,
    });
    render(<SetupWizardPanel onComplete={vi.fn()} />);

    fireEvent.click(screen.getByRole('button', { name: /测试连接/ }));
    await waitFor(() => screen.getByRole('button', { name: /连接成功/ }));
    fireEvent.click(screen.getByRole('button', { name: /下一步/ }));

    fireEvent.change(screen.getByLabelText('Admin Token'), { target: { value: TOKEN } });
    fireEvent.click(screen.getByRole('button', { name: /^验证$/ }));

    await waitFor(() => {
      expect(screen.getByRole('status')).toHaveTextContent(/v0/);
    });

    // fetch 调到 /admin/config 时带 X-Admin-Token
    const getCall = fetchMock.mock.calls.find((c) =>
      String(c[0]).endsWith('/admin/config'),
    );
    expect(getCall).toBeDefined();
    const headers = new Headers(getCall![1]?.headers);
    expect(headers.get('X-Admin-Token')).toBe(TOKEN);
  });

  it('test_step2_wrong_token_shows_401_message', async () => {
    installFetchMock({
      health: OK_HEALTH,
      getConfig: () =>
        new Response(JSON.stringify({ detail: 'invalid token' }), { status: 401 }),
    });
    render(<SetupWizardPanel onComplete={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: /测试连接/ }));
    await waitFor(() => screen.getByRole('button', { name: /连接成功/ }));
    fireEvent.click(screen.getByRole('button', { name: /下一步/ }));

    fireEvent.change(screen.getByLabelText('Admin Token'), { target: { value: TOKEN } });
    fireEvent.click(screen.getByRole('button', { name: /^验证$/ }));

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent(/令牌错误/);
    });
  });
});

describe('SetupWizardPanel · Step 3 (api keys)', () => {
  it('test_step3_save_pushes_to_admin_config_with_correct_version', async () => {
    const { fetchMock } = await advanceToStep3();

    fireEvent.change(screen.getByLabelText('DeepSeek API Key'), {
      target: { value: 'sk-ds-xxx' },
    });
    fireEvent.change(screen.getByLabelText('DashScope API Key'), {
      target: { value: 'sk-aq-yyy' },
    });
    fireEvent.click(screen.getByRole('button', { name: /保存到服务器/ }));

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: '一切就绪' })).toBeInTheDocument();
    });

    const putCall = fetchMock.mock.calls.find(
      (c) => String(c[0]).endsWith('/admin/config') && c[1]?.method === 'PUT',
    );
    expect(putCall).toBeDefined();
    const body = JSON.parse(String(putCall![1]!.body));
    expect(body.expectedVersion).toBe(0);
    expect(body.config.deepseek_api_key).toBe('sk-ds-xxx');
    expect(body.config.dashscope_api_key).toBe('sk-aq-yyy');
  });

  it('test_step3_optional_anthropic_key_not_required', async () => {
    await advanceToStep3();

    fireEvent.change(screen.getByLabelText('DeepSeek API Key'), {
      target: { value: 'sk-ds-xxx' },
    });
    fireEvent.change(screen.getByLabelText('DashScope API Key'), {
      target: { value: 'sk-aq-yyy' },
    });
    expect(screen.getByRole('button', { name: /保存到服务器/ })).not.toBeDisabled();
  });

  it('test_step3_409_shows_stale_config_message', async () => {
    installFetchMock({
      health: OK_HEALTH,
      getConfig: OK_GET_CONFIG,
      putConfig: () =>
        new Response(
          JSON.stringify({ detail: 'stale version: expected 0, got 5' }),
          { status: 409 },
        ),
    });
    render(<SetupWizardPanel onComplete={vi.fn()} />);

    fireEvent.click(screen.getByRole('button', { name: /测试连接/ }));
    await waitFor(() => screen.getByRole('button', { name: /连接成功/ }));
    fireEvent.click(screen.getByRole('button', { name: /下一步/ }));

    fireEvent.change(screen.getByLabelText('Admin Token'), { target: { value: TOKEN } });
    fireEvent.click(screen.getByRole('button', { name: /^验证$/ }));
    await waitFor(() => screen.getByRole('button', { name: /令牌有效/ }));
    fireEvent.click(screen.getByRole('button', { name: /下一步/ }));

    fireEvent.change(screen.getByLabelText('DeepSeek API Key'), {
      target: { value: 'sk-ds-xxx' },
    });
    fireEvent.change(screen.getByLabelText('DashScope API Key'), {
      target: { value: 'sk-aq-yyy' },
    });
    fireEvent.click(screen.getByRole('button', { name: /保存到服务器/ }));

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent(/已被其他人修改/);
    });
  });
});

describe('SetupWizardPanel · Step 4 (complete)', () => {
  it('test_step4_complete_button_saves_to_chrome_storage', async () => {
    await advanceToStep3();
    fireEvent.change(screen.getByLabelText('DeepSeek API Key'), {
      target: { value: 'sk-ds' },
    });
    fireEvent.change(screen.getByLabelText('DashScope API Key'), {
      target: { value: 'sk-aq' },
    });
    fireEvent.click(screen.getByRole('button', { name: /保存到服务器/ }));
    await waitFor(() => screen.getByRole('heading', { name: '一切就绪' }));

    fireEvent.click(screen.getByRole('button', { name: /开始使用 vibe-niuma/ }));

    await waitFor(() => {
      expect(chrome.storage.local.set).toHaveBeenCalled();
    });
    const setCalls = (chrome.storage.local.set as ReturnType<typeof vi.fn>).mock.calls;
    const lastCall = setCalls[setCalls.length - 1][0] as Record<string, unknown>;
    const stored = lastCall.vibe_niuma_config_v2 as {
      orchestratorUrl: string;
      adminToken: string;
      configVersion: number;
    };
    expect(stored.orchestratorUrl).toBe('http://localhost:9000');
    expect(stored.adminToken).toBe(TOKEN);
    // putConfig 返回 version: 1，应被透传
    expect(stored.configVersion).toBe(1);
  });

  it('test_step4_complete_calls_on_complete_callback', async () => {
    const onComplete = vi.fn();
    await advanceToStep3(onComplete);
    fireEvent.change(screen.getByLabelText('DeepSeek API Key'), {
      target: { value: 'sk-ds' },
    });
    fireEvent.change(screen.getByLabelText('DashScope API Key'), {
      target: { value: 'sk-aq' },
    });
    fireEvent.click(screen.getByRole('button', { name: /保存到服务器/ }));
    await waitFor(() => screen.getByRole('heading', { name: '一切就绪' }));

    fireEvent.click(screen.getByRole('button', { name: /开始使用 vibe-niuma/ }));
    await waitFor(() => expect(onComplete).toHaveBeenCalled());
  });
});

describe('SetupWizardPanel · navigation', () => {
  it('test_prev_button_navigates_back', async () => {
    installFetchMock({ health: OK_HEALTH, getConfig: OK_GET_CONFIG });
    render(<SetupWizardPanel onComplete={vi.fn()} />);

    fireEvent.click(screen.getByRole('button', { name: /测试连接/ }));
    await waitFor(() => screen.getByRole('button', { name: /连接成功/ }));
    fireEvent.click(screen.getByRole('button', { name: /下一步/ }));

    expect(screen.getByRole('heading', { name: '管理员令牌' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /上一步/ }));
    expect(screen.getByRole('heading', { name: '服务器地址' })).toBeInTheDocument();
  });

  it('test_skip_button_confirms_then_calls_on_complete', () => {
    const onComplete = vi.fn();
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);
    render(<SetupWizardPanel onComplete={onComplete} />);

    fireEvent.click(screen.getByRole('button', { name: '跳过引导' }));
    expect(confirmSpy).toHaveBeenCalled();
    expect(onComplete).toHaveBeenCalled();
    confirmSpy.mockRestore();
  });

  it('test_skip_button_cancel_does_not_call_on_complete', () => {
    const onComplete = vi.fn();
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);
    render(<SetupWizardPanel onComplete={onComplete} />);

    fireEvent.click(screen.getByRole('button', { name: '跳过引导' }));
    expect(onComplete).not.toHaveBeenCalled();
    confirmSpy.mockRestore();
  });
});

describe('SetupWizardPanel · progress indicator', () => {
  it('test_progress_indicator_highlights_current_step', async () => {
    installFetchMock({ health: OK_HEALTH, getConfig: OK_GET_CONFIG });
    render(<SetupWizardPanel onComplete={vi.fn()} />);

    expect(screen.getByTestId('wizard-dot-1')).toHaveClass('is-current');
    expect(screen.getByTestId('wizard-dot-2')).toHaveClass('is-pending');

    fireEvent.click(screen.getByRole('button', { name: /测试连接/ }));
    await waitFor(() => screen.getByRole('button', { name: /连接成功/ }));
    fireEvent.click(screen.getByRole('button', { name: /下一步/ }));

    expect(screen.getByTestId('wizard-dot-1')).toHaveClass('is-done');
    expect(screen.getByTestId('wizard-dot-2')).toHaveClass('is-current');
  });
});

describe('SetupWizardPanel · full flow', () => {
  it('test_full_flow_4_steps_renders_complete_at_end', async () => {
    const onComplete = vi.fn();
    installFetchMock({
      health: OK_HEALTH,
      getConfig: OK_GET_CONFIG,
      putConfig: OK_PUT_CONFIG,
    });
    render(<SetupWizardPanel onComplete={onComplete} />);

    // Step 1
    fireEvent.click(screen.getByRole('button', { name: /测试连接/ }));
    await waitFor(() => screen.getByRole('button', { name: /连接成功/ }));
    expect(screen.getByRole('button', { name: /下一步/ })).not.toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: /下一步/ }));

    // Step 2
    expect(screen.getByRole('heading', { name: '管理员令牌' })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Admin Token'), { target: { value: 'short' } });
    expect(screen.getByRole('button', { name: /^验证$/ })).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Admin Token'), { target: { value: TOKEN } });
    expect(screen.getByRole('button', { name: /^验证$/ })).not.toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: /^验证$/ }));
    await waitFor(() => screen.getByRole('button', { name: /令牌有效/ }));
    fireEvent.click(screen.getByRole('button', { name: /下一步/ }));

    // Step 3
    expect(screen.getByRole('heading', { name: '模型 API Key' })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('DeepSeek API Key'), { target: { value: 'sk-1' } });
    expect(screen.getByRole('button', { name: /保存到服务器/ })).toBeDisabled();
    fireEvent.change(screen.getByLabelText('DashScope API Key'), { target: { value: 'sk-2' } });
    expect(screen.getByRole('button', { name: /保存到服务器/ })).not.toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: /保存到服务器/ }));

    // Step 4
    await waitFor(() => screen.getByRole('heading', { name: '一切就绪' }));
    fireEvent.click(screen.getByRole('button', { name: /开始使用 vibe-niuma/ }));
    await waitFor(() => expect(onComplete).toHaveBeenCalled());
  });
});
