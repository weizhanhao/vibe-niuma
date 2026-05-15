// Plan 6 · Task 12: 端到端集成测试。
//
// 跟现有测试互补：
// - tests/setup-wizard.test.tsx 单测 SetupWizardPanel 组件孤立行为
// - tests/onboarding-routing.test.tsx 测「storage 状态 → App 路由分支」
// - tests/settings-panel.test.tsx 单测 SettingsPanel 组件
// - 这里测「render <App /> 空 storage → 模拟用户走完 4 步 wizard → 落地 storage
//   + 后端 PUT → 路由自动切到 CapturePanel」全链条，验证 wizard / config /
//   admin-client / App 路由 4 个模块协作正确。
//
// 不动任何被测组件；不引入新依赖（沿用现有 fireEvent 模式，package.json 没装
// @testing-library/user-event，硬约束「不引入新依赖」优先）。
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { App } from '../src/ui/App';

const STORAGE_KEY = 'doskill_config_v2';
const ORCH_URL = 'http://example.com:9000';
const TOKEN = 'a'.repeat(32);

// ── Fetch mock：按 method + URL 派发 ────────────────────────────────────
//
// wizard 闭环 fetch 命中：
//   Step 1 测试连接 → GET /health
//   Step 2 验证 token → GET /admin/config（带 X-Admin-Token）
//   Step 3 保存 API key → PUT /admin/config（带 X-Admin-Token + body）
//
// 把每次调用记进 calls，方便测试后断言。

interface FetchHarness {
  fetchMock: ReturnType<typeof vi.fn>;
  calls: Array<{ url: string; method: string; headers: Headers; body: string | null }>;
}

function setupFetchMock(opts: { getReturnsVersion?: number; putReturnsVersion?: number } = {}): FetchHarness {
  const calls: FetchHarness['calls'] = [];
  const getVersion = opts.getReturnsVersion ?? 0;
  const putVersion = opts.putReturnsVersion ?? getVersion + 1;

  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method ?? 'GET').toUpperCase();
    const headers = new Headers(init?.headers);
    const body = typeof init?.body === 'string' ? init.body : null;
    calls.push({ url, method, headers, body });

    if (url.endsWith('/health')) {
      return new Response(JSON.stringify({ ok: true }), { status: 200 });
    }
    if (url.endsWith('/admin/config') && method === 'GET') {
      return new Response(
        JSON.stringify({
          config: {
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
          },
          version: getVersion,
        }),
        { status: 200 },
      );
    }
    if (url.endsWith('/admin/config') && method === 'PUT') {
      return new Response(
        JSON.stringify({
          config: {
            dev_runner: 'opencode',
            dev_model: 'deepseek/deepseek-v4-flash',
            vision_model: 'qwen-vl-plus',
            deepseek_api_key: null,
            dashscope_api_key: null,
            anthropic_api_key: null,
            demo_repo_path: '/opt/doskill/demo',
            preview_backend_url: 'http://doskill-demo-backend:8000',
            deepseek_api_key_set: true,
            dashscope_api_key_set: true,
            anthropic_api_key_set: false,
          },
          version: putVersion,
          restartedServices: ['litellm'],
        }),
        { status: 200 },
      );
    }
    return new Response(JSON.stringify({ detail: 'no route' }), { status: 500 });
  });

  vi.stubGlobal('fetch', fetchMock as unknown as typeof fetch);
  return { fetchMock, calls };
}

beforeEach(async () => {
  await chrome.storage.local.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

// ── ① 全流程：空 storage → 4 步 wizard → 主面板 ──────────────────────

describe('Plan 6 全流程：全新安装 → wizard → 主面板', () => {
  it('从空 storage 走完 4 步 wizard，最终路由到 CapturePanel + storage 落地 + PUT body 正确', async () => {
    const { fetchMock, calls } = setupFetchMock({ getReturnsVersion: 0, putReturnsVersion: 1 });

    render(<App />);

    // 初始：wizard Step 1 渲染（h2「服务器地址」）
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: '服务器地址' })).toBeInTheDocument();
    });

    // ── Step 1：输入 URL + 测试连接 + 下一步 ──
    fireEvent.change(screen.getByLabelText('Orchestrator URL'), { target: { value: ORCH_URL } });
    fireEvent.click(screen.getByRole('button', { name: /测试连接/ }));
    await waitFor(() => screen.getByRole('button', { name: /连接成功/ }));
    fireEvent.click(screen.getByRole('button', { name: /下一步/ }));

    // ── Step 2：填 token + 验证 + 下一步 ──
    await waitFor(() => screen.getByRole('heading', { name: '管理员令牌' }));
    fireEvent.change(screen.getByLabelText('Admin Token'), { target: { value: TOKEN } });
    fireEvent.click(screen.getByRole('button', { name: /^验证$/ }));
    await waitFor(() => screen.getByRole('button', { name: /令牌有效/ }));
    fireEvent.click(screen.getByRole('button', { name: /下一步/ }));

    // ── Step 3：填两个必填 API key + 保存到服务器（成功后 SetupWizardPanel 内部 setStep(4)）──
    await waitFor(() => screen.getByRole('heading', { name: '模型 API Key' }));
    fireEvent.change(screen.getByLabelText('DeepSeek API Key'), { target: { value: 'sk-deepseek-xxx' } });
    fireEvent.change(screen.getByLabelText('DashScope API Key'), { target: { value: 'sk-dashscope-xxx' } });
    fireEvent.click(screen.getByRole('button', { name: /保存到服务器/ }));

    // ── Step 4：「一切就绪」→ 点最终按钮触发 onFinish → saveConfig 写 storage ──
    await waitFor(() => screen.getByRole('heading', { name: '一切就绪' }));
    // SetupWizardPanel 用 WizardStep.isLastStep 渲染最终按钮；按文案宽匹配兼容
    // 「下一步 / 完成 / 开始使用」中任一种实现。
    const finishBtn = screen.getByRole('button', { name: /下一步|完成|开始使用/ });
    fireEvent.click(finishBtn);

    // ── 断言 1：chrome.storage 落地完整 config（URL + token + version=1）──
    await waitFor(async () => {
      const stored = await chrome.storage.local.get(STORAGE_KEY);
      expect(stored[STORAGE_KEY]).toMatchObject({
        orchestratorUrl: ORCH_URL,
        adminToken: TOKEN,
        configVersion: 1,
      });
    });

    // ── 断言 2：PUT /admin/config 被调，body 含两个 API key + expectedVersion=0 + 带 X-Admin-Token ──
    const putCall = calls.find((c) => c.url.endsWith('/admin/config') && c.method === 'PUT');
    expect(putCall).toBeDefined();
    expect(putCall!.headers.get('X-Admin-Token')).toBe(TOKEN);
    const putBody = JSON.parse(putCall!.body!) as {
      expectedVersion: number;
      config: { deepseek_api_key: string; dashscope_api_key: string };
    };
    expect(putBody.expectedVersion).toBe(0);
    expect(putBody.config.deepseek_api_key).toBe('sk-deepseek-xxx');
    expect(putBody.config.dashscope_api_key).toBe('sk-dashscope-xxx');

    // ── 断言 3：storage onChanged 触发 App 重路由 → CapturePanel 出现 ──
    await waitFor(() => {
      expect(screen.getByText('想改这个页面的哪里？')).toBeInTheDocument();
    });
    expect(screen.queryByRole('heading', { name: '服务器地址' })).toBeNull();

    // ── 断言 4：fetch 总共命中 3 个路由（health + GET + PUT 各至少一次）──
    const healthCalls = fetchMock.mock.calls.filter((c) => String(c[0]).endsWith('/health'));
    const getCfgCalls = fetchMock.mock.calls.filter(
      (c) => String(c[0]).endsWith('/admin/config')
        && ((c[1] as RequestInit | undefined)?.method ?? 'GET').toUpperCase() === 'GET',
    );
    const putCfgCalls = fetchMock.mock.calls.filter(
      (c) => String(c[0]).endsWith('/admin/config')
        && ((c[1] as RequestInit | undefined)?.method ?? '').toUpperCase() === 'PUT',
    );
    expect(healthCalls.length).toBeGreaterThanOrEqual(1);
    expect(getCfgCalls.length).toBeGreaterThanOrEqual(1);
    expect(putCfgCalls).toHaveLength(1);
  });
});

// ── ② 已配置时直接进主面板 ────────────────────────────────────────────

describe('Plan 6 全流程：已配置 → 主面板（跳过 wizard）', () => {
  it('已有完整 config 时 App 渲染 CapturePanel，不渲染 wizard', async () => {
    setupFetchMock();
    await chrome.storage.local.set({
      [STORAGE_KEY]: {
        orchestratorUrl: ORCH_URL,
        adminToken: TOKEN,
        configVersion: 5,
        server: {
          devRunner: 'opencode',
          devModel: 'deepseek/deepseek-v4-flash',
          visionModel: 'qwen-vl-plus',
          demoRepoPath: '/opt/doskill/demo',
          previewBackendUrl: 'http://doskill-demo-backend:8000',
        },
      },
    });

    render(<App />);

    await waitFor(() => {
      expect(screen.getByText('想改这个页面的哪里？')).toBeInTheDocument();
    });
    expect(screen.queryByRole('heading', { name: '服务器地址' })).toBeNull();
  });
});

// ── ③ 配置后齿轮切到 SettingsPanel + 关闭回主面板 ──────────────────────

describe('Plan 6 全流程：主面板齿轮 → SettingsPanel → 关闭回主面板', () => {
  it('已配置时点齿轮进 SettingsPanel；点关闭按钮回 CapturePanel', async () => {
    setupFetchMock();
    await chrome.storage.local.set({
      [STORAGE_KEY]: {
        orchestratorUrl: ORCH_URL,
        adminToken: TOKEN,
        configVersion: 5,
        server: {
          devRunner: 'opencode',
          devModel: 'deepseek/deepseek-v4-flash',
          visionModel: 'qwen-vl-plus',
          demoRepoPath: '/opt/doskill/demo',
          previewBackendUrl: 'http://doskill-demo-backend:8000',
        },
      },
    });

    render(<App />);

    await waitFor(() => {
      expect(screen.getByLabelText('设置')).toBeInTheDocument();
    });
    fireEvent.click(screen.getByLabelText('设置'));

    // SettingsPanel 顶部 h3「配置 doskill」（panels/SettingsPanel.tsx 标题）
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: '配置 doskill' })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByLabelText('关闭设置'));
    await waitFor(() => {
      expect(screen.getByText('想改这个页面的哪里？')).toBeInTheDocument();
    });
  });
});
