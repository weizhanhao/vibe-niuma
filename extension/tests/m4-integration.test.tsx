// Plan 11 · M4.T30：M4 端到端集成测试 + Plan 11 整体验收。
//
// 模拟两个浏览器实例（业务员 A 和 B）：
// 1. A 配好 config（含 alertWebhookUrl + repos） → ConfigExportImport 加密导出 → 拿到密文
// 2. B 是空 chrome.storage → 粘密文 + 同 passphrase → import → A 的整套 config 落盘
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';
import { decryptConfig, encryptConfig } from '../src/lib/config-crypto';
import { ConfigExportImport } from '../src/ui/components/ConfigExportImport';

const CONFIG_KEY = 'vibe_niuma_config_v2';
const PASSPHRASE = 'plan11-shared-pass';

const SAMPLE_CONFIG = {
  orchestratorUrl: 'https://1-2-3-4.sslip.io',
  adminToken: 'tokentokentokentokentoken',
  configVersion: 0,
  repos: [
    { url: 'https://github.com/biz/orders.git', mainBranch: 'main', targetBranch: 'vibe-niuma/dev' },
    { url: 'https://github.com/biz/admin.git', mainBranch: 'develop', targetBranch: 'vibe-niuma/dev' },
  ],
  alertWebhookUrl: 'https://oapi.dingtalk.com/robot/send?access_token=fake',
  server: {
    devRunner: 'opencode',
    devModel: 'deepseek/deepseek-v4-flash',
    visionModel: 'qwen-vl-plus',
    demoRepoPath: '/opt/vibe-niuma/demo',
    previewBackendUrl: 'http://vibe-niuma-demo-backend:8000',
  },
};

beforeEach(async () => {
  await chrome.storage.local.clear();
  await chrome.storage.session.clear();
});

describe('M4 端到端：业务员之间私分享 config', () => {
  it('A 加密导出 → B 解密导入，整个 config 一致落盘', async () => {
    // ── A 端 ──
    await chrome.storage.local.set({ [CONFIG_KEY]: SAMPLE_CONFIG });
    const aRender = render(<ConfigExportImport />);
    fireEvent.click(aRender.getByRole('tab', { name: /导出/ }));
    fireEvent.change(aRender.getByLabelText(/Passphrase/), { target: { value: PASSPHRASE } });
    fireEvent.click(aRender.getByRole('button', { name: /生成/ }));

    const out = await aRender.findByLabelText(/加密后的 config/);
    const exported = (out as HTMLTextAreaElement).value;
    expect(exported).toMatch(/"v":1/);

    aRender.unmount();
    await chrome.storage.local.clear();

    // ── B 端 ──
    const bRender = render(<ConfigExportImport />);
    fireEvent.click(bRender.getByRole('tab', { name: /导入/ }));
    fireEvent.change(bRender.getByLabelText(/加密文本/), { target: { value: exported } });
    fireEvent.change(bRender.getByLabelText(/Passphrase/), { target: { value: PASSPHRASE } });
    fireEvent.click(bRender.getByRole('button', { name: /导入/ }));

    await waitFor(() => {
      expect(bRender.getByRole('status')).toHaveTextContent(/已导入/);
    });

    const stored = await chrome.storage.local.get([CONFIG_KEY]);
    expect(stored[CONFIG_KEY]).toEqual(SAMPLE_CONFIG);
  });

  it('攻击者只拿到密文 + 错 passphrase → 解不开', async () => {
    const enc = await encryptConfig(SAMPLE_CONFIG, PASSPHRASE);
    await expect(decryptConfig(enc, 'attacker-guess')).rejects.toThrow(/passphrase 错误/);
  });

  it('Plan 11 端到端验收：config schema 含所有 M1-M4 新增字段', async () => {
    const { ConfigSchema } = await import('../src/lib/config');
    const parsed = ConfigSchema.parse(SAMPLE_CONFIG);

    // M1：repos
    expect(parsed.repos).toHaveLength(2);
    expect(parsed.repos[0].targetBranch).toBe('vibe-niuma/dev');

    // M3：alertWebhookUrl
    expect(parsed.alertWebhookUrl).toMatch(/dingtalk\.com/);

    // M4：HTTPS URL
    expect(parsed.orchestratorUrl).toMatch(/^https:\/\//);
    expect(parsed.orchestratorUrl).toContain('sslip.io');
  });

  it('M4 加密 round-trip 不损失 Unicode（业务员中文项目名、emoji）', async () => {
    const cfgWithUnicode = {
      ...SAMPLE_CONFIG,
      repos: [{ url: '业务-中文-repo', mainBranch: 'main', targetBranch: 'vibe-niuma/dev' }],
      alertWebhookUrl: SAMPLE_CONFIG.alertWebhookUrl + '&note=🐂',
    };
    const enc = await encryptConfig(cfgWithUnicode, PASSPHRASE);
    const dec = await decryptConfig(enc, PASSPHRASE);
    expect(dec).toEqual(cfgWithUnicode);
  });
});
