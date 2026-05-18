// Plan 6 · Task 4: chrome.storage.local + zod 校验封装的契约测试。
// 行为约定：
//   - 首次安装（storage 空）→ loadConfig 返回 null
//   - storage 里有合法 config → 解析返回完整对象
//   - storage 里有非法 config（URL 不是 url 形如） → loadConfig 返回 null（错误不抛出，UI 自己处理首次引导）
//   - saveConfig 接受 Partial，深 merge 已有值再写回，未传字段不丢失
import { describe, expect, it } from 'vitest';
import { isConfigured, loadConfig, saveConfig } from '../src/lib/config';

const STORAGE_KEY = 'vibe_niuma_config_v2';

function fakeStorage(): Record<string, unknown> {
  return (globalThis as unknown as { __fakeChrome: { storage: { area: Record<string, unknown> } } })
    .__fakeChrome.storage.area;
}

const VALID = {
  orchestratorUrl: 'http://ecs.example.com:9000',
  adminToken: 'abcdefghijklmnopqrstuvwxyz', // 26 chars，过 >=20
  configVersion: 3,
  server: {
    devRunner: 'opencode',
    devModel: 'deepseek/deepseek-v4-flash',
    visionModel: 'qwen-vl-plus',
    deepseekApiKey: 'sk-test',
    dashscopeApiKey: undefined,
    anthropicApiKey: undefined,
    demoRepoPath: '/opt/vibe-niuma/demo',
    previewBackendUrl: 'http://vibe-niuma-demo-backend:8000',
  },
};

describe('config schema · loadConfig', () => {
  it('returns null on first install when storage is empty', async () => {
    const got = await loadConfig();
    expect(got).toBeNull();
  });

  it('parses valid stored config into typed object', async () => {
    fakeStorage()[STORAGE_KEY] = VALID;
    const got = await loadConfig();
    expect(got).not.toBeNull();
    expect(got?.orchestratorUrl).toBe('http://ecs.example.com:9000');
    expect(got?.adminToken).toBe('abcdefghijklmnopqrstuvwxyz');
    expect(got?.configVersion).toBe(3);
    expect(got?.server.devRunner).toBe('opencode');
    expect(got?.server.demoRepoPath).toBe('/opt/vibe-niuma/demo');
  });

  it('returns null when stored URL is invalid (zod rejects)', async () => {
    fakeStorage()[STORAGE_KEY] = { ...VALID, orchestratorUrl: 'not-a-url' };
    const got = await loadConfig();
    expect(got).toBeNull();
  });

  it('returns null when admin token is too short', async () => {
    fakeStorage()[STORAGE_KEY] = { ...VALID, adminToken: 'short' };
    const got = await loadConfig();
    expect(got).toBeNull();
  });
});

describe('config schema · saveConfig', () => {
  it('saves a brand-new config with defaults for missing server fields', async () => {
    const saved = await saveConfig({
      orchestratorUrl: 'http://localhost:9000',
      adminToken: 'aaaaaaaaaaaaaaaaaaaaaaaa', // 24 chars
    });
    expect(saved.orchestratorUrl).toBe('http://localhost:9000');
    expect(saved.adminToken).toBe('aaaaaaaaaaaaaaaaaaaaaaaa');
    // server 拿默认值
    expect(saved.server.devRunner).toBe('opencode');
    expect(saved.server.demoRepoPath).toBe('/opt/vibe-niuma/demo');
    expect(saved.configVersion).toBe(0);
    expect(fakeStorage()[STORAGE_KEY]).toBeDefined();
  });

  it('partial update preserves other top-level fields', async () => {
    fakeStorage()[STORAGE_KEY] = VALID;
    const updated = await saveConfig({
      orchestratorUrl: 'http://new.example.com:9000',
    });
    expect(updated.orchestratorUrl).toBe('http://new.example.com:9000');
    expect(updated.adminToken).toBe(VALID.adminToken); // 未传 → 保留
    expect(updated.configVersion).toBe(VALID.configVersion);
    expect(updated.server.devModel).toBe(VALID.server.devModel);
  });

  it('partial update deep-merges nested server fields', async () => {
    fakeStorage()[STORAGE_KEY] = VALID;
    const updated = await saveConfig({
      server: { deepseekApiKey: 'sk-new-key' },
    });
    expect(updated.server.deepseekApiKey).toBe('sk-new-key');
    // 其它 server 字段不被丢
    expect(updated.server.devRunner).toBe(VALID.server.devRunner);
    expect(updated.server.visionModel).toBe(VALID.server.visionModel);
    expect(updated.server.demoRepoPath).toBe(VALID.server.demoRepoPath);
  });

  it('rejects invalid patches by throwing', async () => {
    await expect(
      saveConfig({
        orchestratorUrl: 'not-a-url',
        adminToken: 'aaaaaaaaaaaaaaaaaaaaaaaa',
      }),
    ).rejects.toThrow();
  });
});

// Plan 11 · M1.T1
describe('config schema · repos field (multi-repo per project)', () => {
  it('defaults to empty array when legacy config lacks repos field', async () => {
    // 老 storage（没 repos 字段）
    fakeStorage()[STORAGE_KEY] = VALID;
    const got = await loadConfig();
    expect(got?.repos).toEqual([]);
  });

  it('preserves repos array with multiple entries', async () => {
    fakeStorage()[STORAGE_KEY] = {
      ...VALID,
      repos: [
        { url: 'https://github.com/org/frontend.git', mainBranch: 'main', targetBranch: 'vibe-niuma/dev' },
        { url: 'git@github.com:org/backend.git', mainBranch: 'master', targetBranch: 'sales/dev' },
      ],
    };
    const got = await loadConfig();
    expect(got?.repos).toHaveLength(2);
    expect(got?.repos[0].url).toBe('https://github.com/org/frontend.git');
    expect(got?.repos[1].mainBranch).toBe('master');
    expect(got?.repos[1].targetBranch).toBe('sales/dev');
  });

  it('applies branch defaults when only url is given', async () => {
    fakeStorage()[STORAGE_KEY] = {
      ...VALID,
      repos: [{ url: 'https://github.com/org/foo.git' }],
    };
    const got = await loadConfig();
    expect(got?.repos[0].mainBranch).toBe('main');
    expect(got?.repos[0].targetBranch).toBe('vibe-niuma/dev');
  });

  it('rejects repo with empty url', async () => {
    fakeStorage()[STORAGE_KEY] = {
      ...VALID,
      repos: [{ url: '', mainBranch: 'main', targetBranch: 'vibe-niuma/dev' }],
    };
    expect(await loadConfig()).toBeNull();
  });

  it('saveConfig replaces repos array when provided', async () => {
    fakeStorage()[STORAGE_KEY] = {
      ...VALID,
      repos: [{ url: 'https://github.com/org/old.git', mainBranch: 'main', targetBranch: 'vibe-niuma/dev' }],
    };
    const updated = await saveConfig({
      repos: [
        { url: 'https://github.com/org/new1.git', mainBranch: 'main', targetBranch: 'vibe-niuma/dev' },
        { url: 'https://github.com/org/new2.git', mainBranch: 'main', targetBranch: 'vibe-niuma/dev' },
      ],
    });
    expect(updated.repos).toHaveLength(2);
    expect(updated.repos[0].url).toBe('https://github.com/org/new1.git');
    expect(updated.repos[1].url).toBe('https://github.com/org/new2.git');
  });

  it('saveConfig with patch lacking repos preserves existing repos', async () => {
    fakeStorage()[STORAGE_KEY] = {
      ...VALID,
      repos: [{ url: 'https://github.com/org/kept.git', mainBranch: 'main', targetBranch: 'vibe-niuma/dev' }],
    };
    const updated = await saveConfig({
      orchestratorUrl: 'http://new.example.com:9000',
    });
    expect(updated.repos).toHaveLength(1);
    expect(updated.repos[0].url).toBe('https://github.com/org/kept.git');
  });
});

describe('config schema · isConfigured', () => {
  it('returns false when nothing is stored', async () => {
    expect(await isConfigured()).toBe(false);
  });

  it('returns true when URL + token are present and valid', async () => {
    fakeStorage()[STORAGE_KEY] = VALID;
    expect(await isConfigured()).toBe(true);
  });

  it('returns false when only URL is stored but token is missing', async () => {
    // 不合法 → loadConfig 返回 null → isConfigured false
    fakeStorage()[STORAGE_KEY] = { ...VALID, adminToken: '' };
    expect(await isConfigured()).toBe(false);
  });
});
