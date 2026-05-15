// Plan 6 · Task 9: SettingsPanel 重写单测。
//
// 设计要点：
//   - SettingsPanel 不是首次引导，是日常修改入口；和 SetupWizardPanel 区别在于：
//       · 加载时先 loadConfig() 拿到本地表单回显（含 URL/token），
//       · 再用 AdminClient.getConfig() 拉服务端 model/path/_set 标识。
//   - 本地字段（orchestratorUrl / adminToken）只 saveConfig，不发 PUT。
//   - 服务端字段（model/key/path）只 diff PUT；未改字段不发出。
//   - API key 字段特别处理：服务端永远返回 null + _set bool；
//       · _set=true 且用户没输入新值 → 显示「已设置」徽标，PUT 不带该字段
//       · 用户输入新值 → PUT 带新值
//   - 409 弹对话框：两个按钮（丢弃 / 重试）
//   - chrome.* 已在 tests/setup.ts 全局 mock；这里只 fetch stub
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SettingsPanel } from '../src/ui/panels/SettingsPanel';

// ── 测试用 fetch mock 工厂 ─────────────────────────────────────
type RouteHandler = (init?: RequestInit) => Promise<Response> | Response;

interface RouteMap {
  getConfig?: RouteHandler;
  putConfig?: RouteHandler;
}

function installFetchMock(routes: RouteMap) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
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

const TOKEN = 'admin-token-1234567890abcdef'; // 长度 >= 20
const STORAGE_KEY = 'doskill_config_v2';

// 预置 chrome.storage 里的 config
function seedStorage(opts?: {
  orchestratorUrl?: string;
  adminToken?: string;
  configVersion?: number;
  lastSyncedAt?: string;
  server?: Record<string, unknown>;
}) {
  const o = opts ?? {};
  const cfg = {
    orchestratorUrl: o.orchestratorUrl ?? 'http://localhost:9000',
    adminToken: o.adminToken ?? TOKEN,
    configVersion: o.configVersion ?? 3,
    lastSyncedAt: o.lastSyncedAt,
    server: {
      devRunner: 'opencode',
      devModel: 'deepseek/deepseek-v4-flash',
      visionModel: 'qwen-vl-plus',
      demoRepoPath: '/opt/doskill/demo',
      previewBackendUrl: 'http://doskill-demo-backend:8000',
      ...(o.server ?? {}),
    },
  };
  (chrome.storage.local.set as ReturnType<typeof vi.fn>)({ [STORAGE_KEY]: cfg });
  return cfg;
}

function buildServerConfig(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    dev_runner: 'opencode',
    dev_model: 'deepseek/deepseek-v4-flash',
    vision_model: 'qwen-vl-plus',
    deepseek_api_key: null,
    dashscope_api_key: null,
    anthropic_api_key: null,
    demo_repo_path: '/opt/doskill/demo',
    preview_backend_url: 'http://doskill-demo-backend:8000',
    deepseek_api_key_set: false,
    dashscope_api_key_set: false,
    anthropic_api_key_set: false,
    ...overrides,
  };
}

function okGetConfig(server: Record<string, unknown>, version = 3): RouteHandler {
  return () =>
    new Response(
      JSON.stringify({ config: server, version }),
      { status: 200 },
    );
}

function okPutConfig(server: Record<string, unknown>, version = 4, restarted: string[] = []): RouteHandler {
  return () =>
    new Response(
      JSON.stringify({ config: server, version, restartedServices: restarted }),
      { status: 200 },
    );
}

beforeEach(() => {
  // 把 chrome.storage.local 重置（setup.ts 已 beforeEach 清空 _storage.area）
  vi.clearAllMocks();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

// ─────────────────────────────────────────────────────────────────
// Tests
// ─────────────────────────────────────────────────────────────────

describe('SettingsPanel · 加载', () => {
  it('test_loads_existing_config_into_form', async () => {
    seedStorage({
      server: {
        devModel: 'qwen-coder-x',
        visionModel: 'qwen-vl-max',
        demoRepoPath: '/srv/doskill/demo',
      },
    });
    installFetchMock({
      getConfig: okGetConfig(
        buildServerConfig({
          dev_model: 'qwen-coder-x',
          vision_model: 'qwen-vl-max',
          demo_repo_path: '/srv/doskill/demo',
        }),
        3,
      ),
    });

    render(<SettingsPanel onClose={vi.fn()} />);

    // URL 回显
    await waitFor(() => {
      const input = screen.getByLabelText('Orchestrator URL') as HTMLInputElement;
      expect(input.value).toBe('http://localhost:9000');
    });
    const tokenInput = screen.getByLabelText('Admin Token') as HTMLInputElement;
    expect(tokenInput.value).toBe(TOKEN);

    // 服务端字段回显（getConfig 后）
    await waitFor(() => {
      const devModel = screen.getByLabelText('Dev Model') as HTMLInputElement;
      expect(devModel.value).toBe('qwen-coder-x');
    });
    const vision = screen.getByLabelText('Vision Model') as HTMLInputElement;
    expect(vision.value).toBe('qwen-vl-max');
    const repo = screen.getByLabelText('Demo Repo Path') as HTMLInputElement;
    expect(repo.value).toBe('/srv/doskill/demo');
  });

  it('test_failed_admin_client_load_shows_error_banner', async () => {
    seedStorage();
    installFetchMock({
      getConfig: () =>
        new Response(JSON.stringify({ detail: 'bad token' }), { status: 401 }),
    });

    render(<SettingsPanel onClose={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent(/无法连接服务器/);
    });
  });
});

describe('SettingsPanel · API key 标识', () => {
  it('test_api_key_set_flag_shows_indicator', async () => {
    seedStorage();
    installFetchMock({
      getConfig: okGetConfig(
        buildServerConfig({ deepseek_api_key_set: true }),
        3,
      ),
    });

    render(<SettingsPanel onClose={vi.fn()} />);
    await waitFor(() => screen.getByLabelText('DeepSeek API Key'));

    // 已设置标识应出现在 DeepSeek 字段附近
    const indicator = await screen.findByTestId('deepseek-set-indicator');
    expect(indicator).toHaveTextContent(/已设置/);
  });
});

describe('SettingsPanel · 保存', () => {
  it('test_unchanged_form_save_button_disabled', async () => {
    seedStorage();
    installFetchMock({ getConfig: okGetConfig(buildServerConfig(), 3) });
    render(<SettingsPanel onClose={vi.fn()} />);
    await waitFor(() => screen.getByLabelText('Dev Model'));

    expect(screen.getByRole('button', { name: /保存并同步/ })).toBeDisabled();
  });

  it('test_local_fields_save_immediately_without_admin_api_call', async () => {
    seedStorage();
    const fetchMock = installFetchMock({ getConfig: okGetConfig(buildServerConfig(), 3) });
    render(<SettingsPanel onClose={vi.fn()} />);
    await waitFor(() => screen.getByLabelText('Dev Model'));

    fireEvent.change(screen.getByLabelText('Orchestrator URL'), {
      target: { value: 'http://ecs.example:9000' },
    });
    fireEvent.click(screen.getByRole('button', { name: /保存并同步/ }));

    await waitFor(() => {
      expect(screen.getByText(/已同步/)).toBeInTheDocument();
    });

    // 没有 PUT 调用 —— 服务端字段未改
    const putCalls = fetchMock.mock.calls.filter(
      (c) => String(c[0]).endsWith('/admin/config') && (c[1]?.method ?? 'GET').toUpperCase() === 'PUT',
    );
    expect(putCalls).toHaveLength(0);

    // chrome.storage.local.set 被调（saveConfig）
    expect(chrome.storage.local.set).toHaveBeenCalled();
  });

  it('test_server_fields_diff_only_pushed_in_put', async () => {
    seedStorage();
    const fetchMock = installFetchMock({
      getConfig: okGetConfig(buildServerConfig(), 3),
      putConfig: okPutConfig(buildServerConfig({ dev_model: 'qwen-coder-y' }), 4),
    });
    render(<SettingsPanel onClose={vi.fn()} />);
    await waitFor(() => screen.getByLabelText('Dev Model'));

    fireEvent.change(screen.getByLabelText('Dev Model'), { target: { value: 'qwen-coder-y' } });
    fireEvent.click(screen.getByRole('button', { name: /保存并同步/ }));

    await waitFor(() => {
      expect(screen.getByText(/已同步/)).toBeInTheDocument();
    });

    const putCall = fetchMock.mock.calls.find(
      (c) => String(c[0]).endsWith('/admin/config') && c[1]?.method === 'PUT',
    );
    expect(putCall).toBeDefined();
    const body = JSON.parse(String(putCall![1]!.body));
    expect(body.expectedVersion).toBe(3);
    expect(body.config.dev_model).toBe('qwen-coder-y');
    // vision_model 没改，不应在 patch 里
    expect(body.config).not.toHaveProperty('vision_model');
  });

  it('test_empty_api_key_input_not_pushed_when_set_true', async () => {
    seedStorage();
    const fetchMock = installFetchMock({
      getConfig: okGetConfig(buildServerConfig({ deepseek_api_key_set: true }), 3),
      putConfig: okPutConfig(buildServerConfig({ dev_model: 'qwen-coder-y' }), 4),
    });
    render(<SettingsPanel onClose={vi.fn()} />);
    await waitFor(() => screen.getByLabelText('Dev Model'));

    // 只改 devModel，不动 deepseek key
    fireEvent.change(screen.getByLabelText('Dev Model'), { target: { value: 'qwen-coder-y' } });
    fireEvent.click(screen.getByRole('button', { name: /保存并同步/ }));

    await waitFor(() => screen.getByText(/已同步/));

    const putCall = fetchMock.mock.calls.find(
      (c) => String(c[0]).endsWith('/admin/config') && c[1]?.method === 'PUT',
    );
    const body = JSON.parse(String(putCall![1]!.body));
    expect(body.config).not.toHaveProperty('deepseek_api_key');
  });

  it('test_new_api_key_input_pushed', async () => {
    seedStorage();
    const fetchMock = installFetchMock({
      getConfig: okGetConfig(buildServerConfig(), 3),
      putConfig: okPutConfig(buildServerConfig({ deepseek_api_key_set: true }), 4),
    });
    render(<SettingsPanel onClose={vi.fn()} />);
    await waitFor(() => screen.getByLabelText('DeepSeek API Key'));

    fireEvent.change(screen.getByLabelText('DeepSeek API Key'), {
      target: { value: 'new-key' },
    });
    fireEvent.click(screen.getByRole('button', { name: /保存并同步/ }));

    await waitFor(() => screen.getByText(/已同步/));

    const putCall = fetchMock.mock.calls.find(
      (c) => String(c[0]).endsWith('/admin/config') && c[1]?.method === 'PUT',
    );
    const body = JSON.parse(String(putCall![1]!.body));
    expect(body.config.deepseek_api_key).toBe('new-key');
  });

  it('test_save_with_invalid_field_shows_inline_error', async () => {
    seedStorage();
    installFetchMock({
      getConfig: okGetConfig(buildServerConfig(), 3),
      putConfig: () =>
        new Response(
          JSON.stringify({ detail: 'dev_model: must not be empty' }),
          { status: 422 },
        ),
    });
    render(<SettingsPanel onClose={vi.fn()} />);
    await waitFor(() => screen.getByLabelText('Dev Model'));

    fireEvent.change(screen.getByLabelText('Dev Model'), { target: { value: 'x' } });
    fireEvent.click(screen.getByRole('button', { name: /保存并同步/ }));

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent(/dev_model/);
    });
  });
});

describe('SettingsPanel · 重启提示', () => {
  it('test_changing_api_key_triggers_litellm_restart_hint', async () => {
    seedStorage();
    installFetchMock({
      getConfig: okGetConfig(buildServerConfig(), 3),
      putConfig: okPutConfig(
        buildServerConfig({ deepseek_api_key_set: true }),
        4,
        ['doskill-llm-proxy'],
      ),
    });
    render(<SettingsPanel onClose={vi.fn()} />);
    await waitFor(() => screen.getByLabelText('DeepSeek API Key'));

    fireEvent.change(screen.getByLabelText('DeepSeek API Key'), {
      target: { value: 'new-key' },
    });
    fireEvent.click(screen.getByRole('button', { name: /保存并同步/ }));

    await waitFor(() => {
      expect(screen.getByText(/LiteLLM 正在重启/)).toBeInTheDocument();
    });
  });

  it('test_changing_model_no_restart_hint', async () => {
    seedStorage();
    installFetchMock({
      getConfig: okGetConfig(buildServerConfig(), 3),
      putConfig: okPutConfig(buildServerConfig({ dev_model: 'qwen-coder-y' }), 4, []),
    });
    render(<SettingsPanel onClose={vi.fn()} />);
    await waitFor(() => screen.getByLabelText('Dev Model'));

    fireEvent.change(screen.getByLabelText('Dev Model'), { target: { value: 'qwen-coder-y' } });
    fireEvent.click(screen.getByRole('button', { name: /保存并同步/ }));

    await waitFor(() => screen.getByText(/已同步/));

    expect(screen.queryByText(/LiteLLM 正在重启/)).not.toBeInTheDocument();
  });
});

describe('SettingsPanel · 409 处理', () => {
  it('test_save_with_stale_version_shows_409_dialog_offering_reload', async () => {
    seedStorage();
    installFetchMock({
      getConfig: okGetConfig(buildServerConfig(), 3),
      putConfig: () =>
        new Response(
          JSON.stringify({ detail: 'stale version: expected 3, got 5' }),
          { status: 409 },
        ),
    });
    render(<SettingsPanel onClose={vi.fn()} />);
    await waitFor(() => screen.getByLabelText('Dev Model'));

    fireEvent.change(screen.getByLabelText('Dev Model'), { target: { value: 'qwen-coder-y' } });
    fireEvent.click(screen.getByRole('button', { name: /保存并同步/ }));

    await waitFor(() => {
      expect(screen.getByRole('dialog')).toBeInTheDocument();
    });
    const dialog = screen.getByRole('dialog');
    expect(dialog).toHaveTextContent(/v5/);
    expect(dialog).toHaveTextContent(/v3/);
    expect(screen.getByRole('button', { name: /丢弃我的改动/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /保留我的改动/ })).toBeInTheDocument();
  });

  it('test_409_dialog_reload_button_pulls_latest', async () => {
    seedStorage();
    // 第一次 GET 返回 v3；409 后再 GET 返回 v5 + 新 dev_model
    let getCallCount = 0;
    installFetchMock({
      getConfig: () => {
        getCallCount += 1;
        if (getCallCount === 1) {
          return new Response(
            JSON.stringify({ config: buildServerConfig(), version: 3 }),
            { status: 200 },
          );
        }
        return new Response(
          JSON.stringify({
            config: buildServerConfig({ dev_model: 'server-pushed-model' }),
            version: 5,
          }),
          { status: 200 },
        );
      },
      putConfig: () =>
        new Response(
          JSON.stringify({ detail: 'stale version: expected 3, got 5' }),
          { status: 409 },
        ),
    });
    render(<SettingsPanel onClose={vi.fn()} />);
    await waitFor(() => screen.getByLabelText('Dev Model'));

    fireEvent.change(screen.getByLabelText('Dev Model'), { target: { value: 'my-change' } });
    fireEvent.click(screen.getByRole('button', { name: /保存并同步/ }));

    await waitFor(() => screen.getByRole('dialog'));
    fireEvent.click(screen.getByRole('button', { name: /丢弃我的改动/ }));

    // 表单覆盖为服务端版本
    await waitFor(() => {
      const devModel = screen.getByLabelText('Dev Model') as HTMLInputElement;
      expect(devModel.value).toBe('server-pushed-model');
    });
    // 对话框关闭
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
});

describe('SettingsPanel · 帮助气泡', () => {
  it('test_help_bubble_visible_for_every_field', async () => {
    seedStorage();
    installFetchMock({ getConfig: okGetConfig(buildServerConfig(), 3) });
    render(<SettingsPanel onClose={vi.fn()} />);
    await waitFor(() => screen.getByLabelText('Dev Model'));

    // 每个字段都有专属帮助 button（aria-label 含「帮助」）
    const labels = [
      'Orchestrator URL 帮助',
      'Admin Token 帮助',
      'Dev Runner 帮助',
      'Dev Model 帮助',
      'Vision Model 帮助',
      'DeepSeek Key 帮助',
      'DashScope Key 帮助',
      'Anthropic Key 帮助',
      'Demo Repo Path 帮助',
      'Preview Backend URL 帮助',
    ];
    for (const label of labels) {
      expect(screen.getByLabelText(label)).toBeInTheDocument();
    }
  });
});

describe('SettingsPanel · 重置 / 关闭', () => {
  it('test_reset_to_server_version_button_pulls_latest', async () => {
    seedStorage();
    let getCallCount = 0;
    installFetchMock({
      getConfig: () => {
        getCallCount += 1;
        if (getCallCount === 1) {
          return new Response(
            JSON.stringify({ config: buildServerConfig(), version: 3 }),
            { status: 200 },
          );
        }
        return new Response(
          JSON.stringify({
            config: buildServerConfig({ dev_model: 'fresh-server-model' }),
            version: 7,
          }),
          { status: 200 },
        );
      },
    });
    render(<SettingsPanel onClose={vi.fn()} />);
    await waitFor(() => screen.getByLabelText('Dev Model'));

    fireEvent.change(screen.getByLabelText('Dev Model'), { target: { value: 'dirty' } });

    fireEvent.click(screen.getByRole('button', { name: /重置为服务器版本/ }));

    await waitFor(() => {
      const devModel = screen.getByLabelText('Dev Model') as HTMLInputElement;
      expect(devModel.value).toBe('fresh-server-model');
    });
    // dirty 被重置 → 保存按钮 disabled
    expect(screen.getByRole('button', { name: /保存并同步/ })).toBeDisabled();
  });

  it('test_close_button_calls_on_close', async () => {
    seedStorage();
    installFetchMock({ getConfig: okGetConfig(buildServerConfig(), 3) });
    const onClose = vi.fn();
    render(<SettingsPanel onClose={onClose} />);
    await waitFor(() => screen.getByLabelText('Dev Model'));

    fireEvent.click(screen.getByRole('button', { name: /关闭设置/ }));
    expect(onClose).toHaveBeenCalled();
  });
});
