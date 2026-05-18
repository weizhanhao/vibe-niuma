// Plan 11 · M4.T26：业务员 / 程序员之间私分享 config 的 UI（无 OAuth、无 server backup）。
//
// 私有部署场景：业务员 A 把 config 加密 → 微信发给业务员 B，
// 业务员 B 在新机器扩展粘 + 同 passphrase → 解密导入。
//
// 两个 tab：
// - 导出：读 chrome.storage 当前 config → encryptConfig(pass) → 给业务员看 textarea
// - 导入：粘加密文本 + pass → decryptConfig → 写 chrome.storage（覆盖现有）
import { useState } from 'react';
import { decryptConfig, encryptConfig } from '../../lib/config-crypto';

const CONFIG_KEY = 'vibe_niuma_config_v2';

type Tab = 'export' | 'import';

export function ConfigExportImport() {
  const [tab, setTab] = useState<Tab>('export');
  return (
    <section className="config-export-import">
      <div className="tabs" role="tablist">
        <button
          role="tab"
          aria-selected={tab === 'export'}
          className={tab === 'export' ? 'tab tab-active' : 'tab'}
          onClick={() => setTab('export')}
        >导出 config</button>
        <button
          role="tab"
          aria-selected={tab === 'import'}
          className={tab === 'import' ? 'tab tab-active' : 'tab'}
          onClick={() => setTab('import')}
        >导入 config</button>
      </div>
      {tab === 'export' ? <ExportPane /> : <ImportPane />}
    </section>
  );
}

function ExportPane() {
  const [pass, setPass] = useState('');
  const [output, setOutput] = useState('');
  const [err, setErr] = useState('');

  const generate = async () => {
    setErr('');
    try {
      if (!chrome?.storage?.local?.get) {
        throw new Error('chrome.storage 不可用（在浏览器外？）');
      }
      const stored = (await chrome.storage.local.get([CONFIG_KEY])) as Record<string, unknown>;
      const cfg = stored[CONFIG_KEY];
      if (!cfg) {
        throw new Error('没有可导出的 config（先配 orchestrator + admin token）');
      }
      const enc = await encryptConfig(cfg, pass);
      setOutput(enc);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <div className="export-pane">
      <p className="help">把当前 config 加密成一段文本，发给同事让他导入即可同步整套配置。</p>
      <label className="field">
        <span className="label">Passphrase（解密时同事要输同一个）</span>
        <input
          type="password"
          aria-label="Passphrase"
          value={pass}
          onChange={(e) => setPass(e.target.value)}
          placeholder="至少 8 位，记到 1Password / Bitwarden"
        />
      </label>
      <div className="btn-row">
        <button
          type="button"
          className="btn btn-primary"
          onClick={() => void generate()}
          disabled={!pass}
        >生成加密文本</button>
      </div>
      {err && <div className="alert alert-error" role="alert">{err}</div>}
      {output && (
        <label className="field">
          <span className="label">加密后的 config（复制发给同事）</span>
          <textarea
            aria-label="加密后的 config"
            value={output}
            readOnly
            rows={8}
          />
        </label>
      )}
    </div>
  );
}

function ImportPane() {
  const [cipher, setCipher] = useState('');
  const [pass, setPass] = useState('');
  const [status, setStatus] = useState<
    | { kind: 'idle' }
    | { kind: 'importing' }
    | { kind: 'ok' }
    | { kind: 'error'; message: string }
  >({ kind: 'idle' });

  const importNow = async () => {
    setStatus({ kind: 'importing' });
    try {
      const decoded = await decryptConfig(cipher, pass);
      if (!chrome?.storage?.local?.set) {
        throw new Error('chrome.storage 不可用');
      }
      await chrome.storage.local.set({ [CONFIG_KEY]: decoded });
      setStatus({ kind: 'ok' });
    } catch (e) {
      setStatus({ kind: 'error', message: e instanceof Error ? e.message : String(e) });
    }
  };

  const canSubmit = cipher && pass && status.kind !== 'importing';

  return (
    <div className="import-pane">
      <p className="help">把同事发来的加密文本粘进来，输他给你的 passphrase。</p>
      <label className="field">
        <span className="label">加密文本</span>
        <textarea
          aria-label="加密文本"
          value={cipher}
          onChange={(e) => setCipher(e.target.value)}
          rows={6}
          placeholder='{"v":1,"salt":"...","iv":"...","ciphertext":"..."}'
        />
      </label>
      <label className="field">
        <span className="label">Passphrase</span>
        <input
          type="password"
          aria-label="Passphrase"
          value={pass}
          onChange={(e) => setPass(e.target.value)}
        />
      </label>
      {status.kind === 'ok' && (
        <div className="alert alert-ok" role="status">✓ 已导入。刷新页面或重启扩展即可生效。</div>
      )}
      {status.kind === 'error' && (
        <div className="alert alert-error" role="alert">{status.message}</div>
      )}
      <div className="btn-row">
        <button
          type="button"
          className="btn btn-primary"
          onClick={() => void importNow()}
          disabled={!canSubmit}
        >
          {status.kind === 'importing' ? '导入中…' : '导入'}
        </button>
      </div>
    </div>
  );
}
