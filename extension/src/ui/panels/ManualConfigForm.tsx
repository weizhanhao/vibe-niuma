// 熟手快速通道 —— 跳过 AI 引导，4 个 input 填配置一气保存。
//
// 给已经知道 orchestrator URL / admin token / deepseek key 的开发者用。
// 业务员第一次还是走 DeploymentAssistantPanel 的 AI 聊天引导。
import { useEffect, useState } from 'react';
import { loadConfig, saveConfig } from '../../lib/config';

// 跟 DeploymentAssistantPanel 同源的 chrome.storage key 名 —— wizard 状态机 / 此表单
// 共享 deepseek key 落点。
const DEEPSEEK_KEY_KEY = 'vibe_niuma_deepseek_key';
const DASHSCOPE_KEY_KEY = 'vibe_niuma_dashscope_key';

async function readStorageString(key: string): Promise<string> {
  if (!chrome?.storage?.local?.get) return '';
  const out = (await chrome.storage.local.get([key])) as Record<string, unknown>;
  return typeof out[key] === 'string' ? (out[key] as string) : '';
}

async function writeStorageString(key: string, value: string): Promise<void> {
  if (!chrome?.storage?.local?.set) return;
  await chrome.storage.local.set({ [key]: value });
}

interface ManualConfigFormProps {
  onSave: () => void;
  onCancel: () => void;
}

interface FormState {
  orchestratorUrl: string;
  adminToken: string;
  deepseekKey: string;
  dashscopeKey: string;
}

const EMPTY: FormState = {
  orchestratorUrl: '',
  adminToken: '',
  deepseekKey: '',
  dashscopeKey: '',
};

export function ManualConfigForm({ onSave, onCancel }: ManualConfigFormProps) {
  const [form, setForm] = useState<FormState>(EMPTY);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // 预填现有 storage 值（让用户改局部、不必从头）
  useEffect(() => {
    void (async () => {
      const cfg = await loadConfig();
      const [dsKey, dashKey] = await Promise.all([
        readStorageString(DEEPSEEK_KEY_KEY),
        readStorageString(DASHSCOPE_KEY_KEY),
      ]);
      setForm({
        orchestratorUrl: cfg?.orchestratorUrl ?? '',
        adminToken: cfg?.adminToken ?? '',
        deepseekKey: dsKey,
        dashscopeKey: dashKey,
      });
    })();
  }, []);

  const canSave =
    form.orchestratorUrl.trim().length > 0 &&
    form.adminToken.trim().length >= 20 &&
    form.deepseekKey.trim().startsWith('sk-');

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    try {
      await saveConfig({
        orchestratorUrl: form.orchestratorUrl.trim(),
        adminToken: form.adminToken.trim(),
      });
      await writeStorageString(DEEPSEEK_KEY_KEY, form.deepseekKey.trim());
      if (form.dashscopeKey.trim()) {
        await writeStorageString(DASHSCOPE_KEY_KEY, form.dashscopeKey.trim());
      }
      onSave();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className="manual-config-form">
      <p className="help">熟手通道：一次填完直接保存，不走 AI 引导。</p>

      <label className="field">
        <span className="label">Orchestrator URL</span>
        <input
          type="url"
          placeholder="https://114-55-171-64.sslip.io 或 http://localhost:9000"
          value={form.orchestratorUrl}
          onChange={(e) => setForm({ ...form, orchestratorUrl: e.target.value })}
        />
      </label>

      <label className="field">
        <span className="label">Admin Token</span>
        <input
          type="password"
          placeholder="ECS 上 /opt/vibe-niuma/admin.token 那一串"
          value={form.adminToken}
          onChange={(e) => setForm({ ...form, adminToken: e.target.value })}
        />
      </label>

      <label className="field">
        <span className="label">DeepSeek API Key</span>
        <input
          type="password"
          placeholder="sk-..."
          value={form.deepseekKey}
          onChange={(e) => setForm({ ...form, deepseekKey: e.target.value })}
        />
      </label>

      <label className="field">
        <span className="label">DashScope API Key（可选）</span>
        <input
          type="password"
          placeholder="sk-...（看截图要它，不填只走文字模型）"
          value={form.dashscopeKey}
          onChange={(e) => setForm({ ...form, dashscopeKey: e.target.value })}
        />
      </label>

      {error && <p className="wizard-error">{error}</p>}

      <div className="btn-row">
        <button className="btn btn-ghost" onClick={onCancel} disabled={saving}>
          回引导
        </button>
        <button
          className="btn btn-primary"
          onClick={() => void handleSave()}
          disabled={!canSave || saving}
        >
          {saving ? '保存中…' : '保存并进入'}
        </button>
      </div>
    </section>
  );
}
