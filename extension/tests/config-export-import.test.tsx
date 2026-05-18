// Plan 11 · M4.T26：ConfigExportImport 组件 单测。
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ConfigExportImport } from '../src/ui/components/ConfigExportImport';
import { encryptConfig } from '../src/lib/config-crypto';

beforeEach(async () => {
  await chrome.storage.local.clear();
  await chrome.storage.session.clear();
});

afterEach(() => vi.unstubAllGlobals());

describe('ConfigExportImport', () => {
  it('export：填 passphrase + 点导出 → 出现加密文本框', async () => {
    await chrome.storage.local.set({
      vibe_niuma_config_v2: {
        orchestratorUrl: 'http://x:9000',
        adminToken: 'a'.repeat(20),
        configVersion: 0,
        repos: [],
        alertWebhookUrl: '',
        server: {
          devRunner: 'opencode', devModel: 'm', visionModel: 'v',
          demoRepoPath: '/d', previewBackendUrl: 'http://p',
        },
      },
    });

    render(<ConfigExportImport />);
    fireEvent.click(screen.getByRole('tab', { name: /导出/ }));
    fireEvent.change(screen.getByLabelText(/Passphrase/), { target: { value: 'my-pass' } });
    fireEvent.click(screen.getByRole('button', { name: /生成/ }));

    const out = await screen.findByLabelText(/加密后的 config/);
    await waitFor(() => {
      const text = (out as HTMLTextAreaElement).value;
      const parsed = JSON.parse(text);
      expect(parsed.v).toBe(1);
      expect(typeof parsed.salt).toBe('string');
    });
  });

  it('export：空 passphrase 时 export 按钮禁用', () => {
    render(<ConfigExportImport />);
    fireEvent.click(screen.getByRole('tab', { name: /导出/ }));
    expect(screen.getByRole('button', { name: /生成/ })).toBeDisabled();
  });

  it('import：粘加密文本 + 正确 passphrase → 写到 chrome.storage', async () => {
    const original = {
      orchestratorUrl: 'http://restored:9000',
      adminToken: 'r'.repeat(20),
      configVersion: 0,
      repos: [],
      alertWebhookUrl: '',
      server: {
        devRunner: 'opencode', devModel: 'm', visionModel: 'v',
        demoRepoPath: '/d', previewBackendUrl: 'http://p',
      },
    };
    const enc = await encryptConfig(original, 'shared-pass');

    render(<ConfigExportImport />);
    fireEvent.click(screen.getByRole('tab', { name: /导入/ }));
    fireEvent.change(screen.getByLabelText(/加密文本/), { target: { value: enc } });
    fireEvent.change(screen.getByLabelText(/Passphrase/), { target: { value: 'shared-pass' } });
    fireEvent.click(screen.getByRole('button', { name: /导入/ }));

    await waitFor(() => {
      expect(screen.getByRole('status')).toHaveTextContent(/已导入/);
    });
    const stored = await chrome.storage.local.get(['vibe_niuma_config_v2']);
    expect(stored.vibe_niuma_config_v2).toEqual(original);
  });

  it('import：错 passphrase → role=alert 显示错误', async () => {
    const enc = await encryptConfig({ x: 1 }, 'right');
    render(<ConfigExportImport />);
    fireEvent.click(screen.getByRole('tab', { name: /导入/ }));
    fireEvent.change(screen.getByLabelText(/加密文本/), { target: { value: enc } });
    fireEvent.change(screen.getByLabelText(/Passphrase/), { target: { value: 'wrong' } });
    fireEvent.click(screen.getByRole('button', { name: /导入/ }));

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent(/passphrase 错误/);
    });
  });

  it('import：非法 JSON 文本 → role=alert', async () => {
    render(<ConfigExportImport />);
    fireEvent.click(screen.getByRole('tab', { name: /导入/ }));
    fireEvent.change(screen.getByLabelText(/加密文本/), { target: { value: 'not json' } });
    fireEvent.change(screen.getByLabelText(/Passphrase/), { target: { value: 'p' } });
    fireEvent.click(screen.getByRole('button', { name: /导入/ }));

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent(/JSON/);
    });
  });
});
