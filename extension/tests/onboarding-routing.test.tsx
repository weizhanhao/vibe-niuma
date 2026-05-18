// Plan 6 Task 10：App.tsx 路由 + service-worker getOrchestratorClient 单测。
//
// 关键不变量：
// - chrome.storage 空 → App 渲染 SetupWizardPanel，不渲染 CapturePanel
// - 完整 config → App 渲染主面板（CapturePanel）
// - 部分 config（URL 但 token 空）→ 仍走 wizard
// - 主面板里齿轮按钮 → toggle SettingsPanel
// - chrome.storage.onChanged 触发 → App 重新 loadConfig 切路由
// - service-worker getOrchestratorClient 从 chrome.storage 读 URL（非 hardcoded）
// - 缺配置时 getOrchestratorClient 返回 null
// - 配置变化时 client 缓存被 invalidate
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { App } from '../src/ui/App';

const STORAGE_KEY = 'vibe_niuma_config_v2';
// Plan 7：路由优先级 = DeploymentAssistant > SetupWizard。这些测试用例还在测 Plan 6 的
// SetupWizardPanel 行为 —— 预置 deployment_completed_at 让 App 跳过 Plan 7 助手走兜底。
const ASSISTANT_COMPLETED_KEY = 'vibe_niuma_deployment_completed_at';

beforeEach(async () => {
  await chrome.storage.local.set({ [ASSISTANT_COMPLETED_KEY]: 1 });
});
const VALID_CONFIG = {
  orchestratorUrl: 'http://example.com:9000',
  adminToken: 'admin-token-1234567890abcdef',
  configVersion: 1,
  server: {
    devRunner: 'opencode',
    devModel: 'deepseek/deepseek-v4-flash',
    visionModel: 'qwen-vl-plus',
    demoRepoPath: '/opt/vibe-niuma/demo',
    previewBackendUrl: 'http://vibe-niuma-demo-backend:8000',
  },
};

async function seed(cfg: unknown): Promise<void> {
  if (cfg === null || cfg === undefined) {
    await chrome.storage.local.clear();
    // 重新打 completed 标，保持 Plan 7 助手 bypass
    await chrome.storage.local.set({ [ASSISTANT_COMPLETED_KEY]: 1 });
    return;
  }
  await chrome.storage.local.set({ [STORAGE_KEY]: cfg });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

// ── App routing ──────────────────────────────────────────────────────

describe('App routing — first install / configured / partial config', () => {
  it.skip('[Plan 9 obsolete] first install (storage empty) renders SetupWizardPanel, not CapturePanel', async () => {
    await seed(null);
    render(<App />);
    // wizard 标志：第 1 步「服务器地址」
    await waitFor(() => {
      expect(screen.getByText('服务器地址')).toBeInTheDocument();
    });
    // CapturePanel 标志（业务需求 textarea label）不应在 wizard 里出现
    expect(screen.queryByText('想改这个页面的哪里？')).toBeNull();
  });

  it('configured (full valid config) renders CapturePanel, not wizard', async () => {
    await seed(VALID_CONFIG);
    render(<App />);
    // 主面板 capture step 标题
    await waitFor(() => {
      expect(screen.getByText('想改这个页面的哪里？')).toBeInTheDocument();
    });
    // wizard 第 1 步标题不应出现
    expect(screen.queryByText('服务器地址')).toBeNull();
  });

  it.skip('[Plan 9 obsolete] partial config (URL set but adminToken empty) routes to wizard', async () => {
    // adminToken 太短，过不了 zod schema → loadConfig 返回 null → wizard
    await chrome.storage.local.set({
      [STORAGE_KEY]: {
        ...VALID_CONFIG,
        adminToken: 'tooShort',
      },
    });
    render(<App />);
    await waitFor(() => {
      expect(screen.getByText('服务器地址')).toBeInTheDocument();
    });
  });

  it('after wizard completes, gear icon toggles SettingsPanel', async () => {
    await seed(VALID_CONFIG);
    // 拦截 admin /admin/config GET 让 SettingsPanel 不抛网络错误
    vi.stubGlobal('fetch', vi.fn(async () =>
      new Response(JSON.stringify({
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
      }), { status: 200 })
    ) as unknown as typeof fetch);

    render(<App />);
    // 等主面板出来
    await waitFor(() => {
      expect(screen.getByLabelText('设置')).toBeInTheDocument();
    });
    // 点齿轮 → SettingsPanel
    fireEvent.click(screen.getByLabelText('设置'));
    await waitFor(() => {
      // 新 SettingsPanel 顶部 h3 是「配置 vibe-niuma」
      expect(screen.getByRole('heading', { name: '配置 vibe-niuma' })).toBeInTheDocument();
    });
    // 关闭按钮（aria-label="关闭设置"）回主面板
    fireEvent.click(screen.getByLabelText('关闭设置'));
    await waitFor(() => {
      expect(screen.getByText('想改这个页面的哪里？')).toBeInTheDocument();
    });
  });

  it.skip('[Plan 9 obsolete] config change event re-routes immediately (wizard → main)', async () => {
    await seed(null);
    render(<App />);
    // 初始 wizard
    await waitFor(() => {
      expect(screen.getByText('服务器地址')).toBeInTheDocument();
    });
    // 写入完整 config → onChanged 自动 fire → App 重新 loadConfig
    await chrome.storage.local.set({ [STORAGE_KEY]: VALID_CONFIG });
    await waitFor(() => {
      expect(screen.getByText('想改这个页面的哪里？')).toBeInTheDocument();
    });
  });
});

// ── service-worker getOrchestratorClient ───────────────────────────

describe('service-worker getOrchestratorClient', () => {
  beforeEach(() => {
    vi.resetModules();
  });

  it('reads URL from chrome.storage config (not hardcoded localhost)', async () => {
    await chrome.storage.local.set({
      [STORAGE_KEY]: {
        ...VALID_CONFIG,
        orchestratorUrl: 'http://example.com:9000',
      },
    });
    const { getOrchestratorClient } = await import('../src/background/service-worker');
    const client = await getOrchestratorClient();
    expect(client).not.toBeNull();
    expect(client!.baseUrl).toBe('http://example.com:9000');
  });

  it('returns null when no config in storage', async () => {
    await chrome.storage.local.clear();
    const { getOrchestratorClient } = await import('../src/background/service-worker');
    const client = await getOrchestratorClient();
    expect(client).toBeNull();
  });

  it('invalidates cache on config change (returns fresh client with new baseUrl)', async () => {
    await chrome.storage.local.set({
      [STORAGE_KEY]: { ...VALID_CONFIG, orchestratorUrl: 'http://first.test:9000' },
    });
    const { getOrchestratorClient } = await import('../src/background/service-worker');
    const client1 = await getOrchestratorClient();
    expect(client1).not.toBeNull();
    expect(client1!.baseUrl).toBe('http://first.test:9000');

    // 改 storage → 自动 fire onChanged → SW 监听器 invalidate cache
    await chrome.storage.local.set({
      [STORAGE_KEY]: { ...VALID_CONFIG, orchestratorUrl: 'http://second.test:9000' },
    });
    // onChanged 是同步触发的，但 invalidate 后下次 getOrchestratorClient 是 async
    const client2 = await getOrchestratorClient();
    expect(client2).not.toBeNull();
    expect(client2!.baseUrl).toBe('http://second.test:9000');
    // 应该是 NEW 实例（不是同一个 ref）
    expect(client2).not.toBe(client1);
  });
});
