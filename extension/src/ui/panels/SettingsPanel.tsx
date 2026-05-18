// Plan 6 · Task 9: 日常配置面板（首次引导用 SetupWizardPanel）。
//
// 设计要点：
//   - 加载流程：
//       1) loadConfig() 拿到本地 URL / token / 本地缓存的 server 字段 + version
//       2) 用 AdminClient.getConfig() 拉服务端 wire（snake_case + _set bool）
//       3) wire → form（snake_case ↔ camelCase 单点 map）
//   - 字段类型分两类：
//       · 本地字段（orchestratorUrl / adminToken）：saveConfig，不 PUT
//       · 服务端字段：diff PUT（用 baseline 比较；未改的不进 patch）
//   - API key 字段：
//       · 服务端 wire.deepseek_api_key 永远 null；deepseek_api_key_set 表示是否已配置
//       · UI：未输入新值 + _set=true → 显示「✓ 已设置」标识 + placeholder
//       · 用户输入新值 才会 PUT 上去（避免误清空）
//   - 409 处理：弹对话框，两个按钮
//       · 「丢弃我的改动」→ 再次 GET，覆盖表单
//       · 「保留我的改动」→ 用最新 currentVersion 再 PUT 一次
//   - 422：把 detail 文案丢到 alert role 节点里
//
// 调用方：暂时无（Task 10 接到 App.tsx）；单测在 extension/tests/settings-panel.test.tsx
import { useEffect, useMemo, useRef, useState } from 'react';
import {
  AdminClient,
  AuthError,
  NetworkError,
  StaleVersionError,
  ValidationError,
  type ServerConfig as WireServerConfig,
} from '../../lib/admin-client';
import { loadConfig, saveConfig, type ServerConfig as LocalServerConfig } from '../../lib/config';
import { HelpBubble } from '../components/HelpBubble';

// 帮助文档 markdown
import orchestratorUrlHelp from '../helpContent/orchestrator-url.md?raw';
import adminTokenHelp from '../helpContent/admin-token.md?raw';
import deepseekKeyHelp from '../helpContent/deepseek-key.md?raw';
import dashscopeKeyHelp from '../helpContent/dashscope-key.md?raw';
import gitRepoPathHelp from '../helpContent/git-repo-path.md?raw';
import ecsSetupHelp from '../helpContent/ecs-setup.md?raw';

// ── Types ──────────────────────────────────────────────────────────

export interface SettingsPanelProps {
  onClose: () => void;
}

// 表单内部状态：camelCase；_set 是服务端给的「已经存了 key」标记；
// 三个 key 字段单独存「用户输入」，未输入空串表示「保持服务端现状」。
interface FormState {
  // 本地（不 PUT）
  orchestratorUrl: string;
  adminToken: string;

  // 服务端（diff PUT）
  devRunner: 'opencode' | 'claude-code';
  devModel: string;
  visionModel: string;
  demoRepoPath: string;
  previewBackendUrl: string;

  // API key 「用户输入新值」（不与 _set 重叠）
  deepseekKeyInput: string;
  dashscopeKeyInput: string;
  anthropicKeyInput: string;
}

interface ServerSnapshot {
  devRunner: 'opencode' | 'claude-code';
  devModel: string;
  visionModel: string;
  demoRepoPath: string;
  previewBackendUrl: string;
  deepseekSet: boolean;
  dashscopeSet: boolean;
  anthropicSet: boolean;
}

// ── snake_case wire ↔ camelCase form ──────────────────────────────

function wireToSnapshot(wire: WireServerConfig): ServerSnapshot {
  return {
    devRunner: wire.dev_runner,
    devModel: wire.dev_model,
    visionModel: wire.vision_model,
    demoRepoPath: wire.demo_repo_path,
    previewBackendUrl: wire.preview_backend_url,
    deepseekSet: wire.deepseek_api_key_set,
    dashscopeSet: wire.dashscope_api_key_set,
    anthropicSet: wire.anthropic_api_key_set,
  };
}

function emptyForm(): FormState {
  return {
    orchestratorUrl: 'http://localhost:9000',
    adminToken: '',
    devRunner: 'opencode',
    devModel: '',
    visionModel: '',
    demoRepoPath: '',
    previewBackendUrl: '',
    deepseekKeyInput: '',
    dashscopeKeyInput: '',
    anthropicKeyInput: '',
  };
}

function snapshotToForm(snap: ServerSnapshot, base: FormState): FormState {
  return {
    ...base,
    devRunner: snap.devRunner,
    devModel: snap.devModel,
    visionModel: snap.visionModel,
    demoRepoPath: snap.demoRepoPath,
    previewBackendUrl: snap.previewBackendUrl,
    // 用户输入清空：刚同步过来不该带新输入
    deepseekKeyInput: '',
    dashscopeKeyInput: '',
    anthropicKeyInput: '',
  };
}

// 比较 form 和 baseline，得出服务端 patch（snake_case partial）。
// API key 仅当 input 非空时才进 patch（避免清空已设置的 key）。
function diffPatch(form: FormState, baseline: ServerSnapshot): Partial<WireServerConfig> {
  const patch: Partial<WireServerConfig> = {};

  if (form.devRunner !== baseline.devRunner) patch.dev_runner = form.devRunner;
  if (form.devModel !== baseline.devModel) patch.dev_model = form.devModel;
  if (form.visionModel !== baseline.visionModel) patch.vision_model = form.visionModel;
  if (form.demoRepoPath !== baseline.demoRepoPath) patch.demo_repo_path = form.demoRepoPath;
  if (form.previewBackendUrl !== baseline.previewBackendUrl) {
    patch.preview_backend_url = form.previewBackendUrl;
  }

  if (form.deepseekKeyInput.length > 0) patch.deepseek_api_key = form.deepseekKeyInput;
  if (form.dashscopeKeyInput.length > 0) patch.dashscope_api_key = form.dashscopeKeyInput;
  if (form.anthropicKeyInput.length > 0) patch.anthropic_api_key = form.anthropicKeyInput;

  return patch;
}

// 是否有任何字段（含本地 + 服务端）改动 —— 用来决定「保存并同步」按钮 enable
function isDirty(
  form: FormState,
  baseline: ServerSnapshot,
  localBaseline: { orchestratorUrl: string; adminToken: string },
): boolean {
  if (form.orchestratorUrl !== localBaseline.orchestratorUrl) return true;
  if (form.adminToken !== localBaseline.adminToken) return true;
  return Object.keys(diffPatch(form, baseline)).length > 0;
}

// 错误归一化
function describeError(err: unknown): string {
  if (err instanceof AuthError) return '令牌错误（401）';
  if (err instanceof StaleVersionError) return '服务端版本过旧';
  if (err instanceof ValidationError) return err.message;
  if (err instanceof NetworkError) return `网络错误：${err.message}`;
  if (err instanceof Error) return err.message;
  return String(err);
}

// 时间格式化：「YYYY-MM-DD HH:MM」
function formatSyncTime(iso: string | undefined): string {
  if (!iso) return '未同步';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '未同步';
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

// ── Component ───────────────────────────────────────────────────────

type SaveStatus =
  | { kind: 'idle' }
  | { kind: 'pending' }
  | { kind: 'success'; version: number; restarted: string[] }
  | { kind: 'error'; message: string };

interface StaleDialog {
  currentVersion: number;
  expectedVersion: number;
}

export function SettingsPanel({ onClose }: SettingsPanelProps) {
  const [form, setForm] = useState<FormState>(emptyForm());
  const [baseline, setBaseline] = useState<ServerSnapshot | null>(null);
  const localBaselineRef = useRef<{ orchestratorUrl: string; adminToken: string }>({
    orchestratorUrl: '',
    adminToken: '',
  });
  const [version, setVersion] = useState<number>(0);
  const [lastSyncedAt, setLastSyncedAt] = useState<string | undefined>(undefined);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saveStatus, setSaveStatus] = useState<SaveStatus>({ kind: 'idle' });
  const [staleDialog, setStaleDialog] = useState<StaleDialog | null>(null);

  // 加载：先本地，再服务端
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const cfg = await loadConfig();
      const localUrl = cfg?.orchestratorUrl ?? 'http://localhost:9000';
      const localToken = cfg?.adminToken ?? '';
      const localServer: LocalServerConfig | undefined = cfg?.server;

      if (cancelled) return;
      localBaselineRef.current = { orchestratorUrl: localUrl, adminToken: localToken };
      setForm((prev) => ({
        ...prev,
        orchestratorUrl: localUrl,
        adminToken: localToken,
        devRunner: localServer?.devRunner ?? prev.devRunner,
        devModel: localServer?.devModel ?? prev.devModel,
        visionModel: localServer?.visionModel ?? prev.visionModel,
        demoRepoPath: localServer?.demoRepoPath ?? prev.demoRepoPath,
        previewBackendUrl: localServer?.previewBackendUrl ?? prev.previewBackendUrl,
      }));
      setVersion(cfg?.configVersion ?? 0);

      // chrome.storage 里有可能存了 lastSyncedAt（loadConfig 不识别该字段，单独读一次原始 storage）
      try {
        const raw = await chrome.storage.local.get('doskill_config_v2');
        const stored = raw?.doskill_config_v2 as { lastSyncedAt?: string } | undefined;
        if (!cancelled) setLastSyncedAt(stored?.lastSyncedAt);
      } catch {
        // ignore
      }

      // 拉服务端
      if (!localToken || localToken.length < 20) {
        // 没 token 没法拉，让用户先填
        return;
      }
      try {
        const client = new AdminClient(localUrl, localToken);
        const resp = await client.getConfig();
        if (cancelled) return;
        const snap = wireToSnapshot(resp.config);
        setBaseline(snap);
        setVersion(resp.version);
        setForm((prev) => snapshotToForm(snap, {
          ...prev,
          orchestratorUrl: localUrl,
          adminToken: localToken,
        }));
      } catch (err) {
        if (cancelled) return;
        setLoadError(`无法连接服务器，检查地址和令牌：${describeError(err)}`);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  // dirty 判定（baseline 还没拉到时一律 false）
  const dirty = useMemo(() => {
    if (!baseline) return false;
    return isDirty(form, baseline, localBaselineRef.current);
  }, [form, baseline]);

  // ── 字段更新 helper ──
  const update = <K extends keyof FormState>(key: K, value: FormState[K]) => {
    setForm((prev) => ({ ...prev, [key]: value }));
    // 用户开始改 → 清掉上次的成功状态，让按钮重新可用
    if (saveStatus.kind === 'success' || saveStatus.kind === 'error') {
      setSaveStatus({ kind: 'idle' });
    }
  };

  // ── 保存并同步 ──
  const doSave = async (overrideExpectedVersion?: number): Promise<void> => {
    if (!baseline) return;
    setSaveStatus({ kind: 'pending' });

    // 1. 本地字段先 saveConfig
    const localBase = localBaselineRef.current;
    const localChanged =
      form.orchestratorUrl !== localBase.orchestratorUrl
      || form.adminToken !== localBase.adminToken;

    // 2. 服务端 diff
    const patch = diffPatch(form, baseline);
    const hasServerChange = Object.keys(patch).length > 0;

    let newVersion = version;
    let restarted: string[] = [];

    if (hasServerChange) {
      const expectedVersion = overrideExpectedVersion ?? version;
      try {
        const client = new AdminClient(form.orchestratorUrl, form.adminToken);
        const resp = await client.putConfig(patch, expectedVersion);
        newVersion = resp.version;
        restarted = resp.restartedServices;
        // 用服务端返回的 config 更新 baseline，user input 清空
        const snap = wireToSnapshot(resp.config);
        setBaseline(snap);
        setForm((prev) => snapshotToForm(snap, prev));
      } catch (err) {
        if (err instanceof StaleVersionError) {
          setStaleDialog({
            currentVersion: err.currentVersion,
            expectedVersion: err.expectedVersion,
          });
          setSaveStatus({ kind: 'idle' });
          return;
        }
        setSaveStatus({ kind: 'error', message: describeError(err) });
        return;
      }
    }

    // 3. saveConfig 写本地
    const now = new Date().toISOString();
    try {
      // saveConfig 会自动 merge；本地 server 字段也一起更新一下，避免下次打开短暂不一致
      await saveConfig({
        orchestratorUrl: form.orchestratorUrl,
        adminToken: form.adminToken,
        configVersion: newVersion,
        server: {
          devRunner: form.devRunner,
          devModel: form.devModel,
          visionModel: form.visionModel,
          demoRepoPath: form.demoRepoPath,
          previewBackendUrl: form.previewBackendUrl,
        },
      });
      // lastSyncedAt 不在 zod schema 里，单独 set
      const stored = await chrome.storage.local.get('doskill_config_v2');
      const cfg = (stored?.doskill_config_v2 ?? {}) as Record<string, unknown>;
      await chrome.storage.local.set({
        doskill_config_v2: { ...cfg, lastSyncedAt: now },
      });
    } catch (err) {
      setSaveStatus({ kind: 'error', message: describeError(err) });
      return;
    }

    setVersion(newVersion);
    setLastSyncedAt(now);
    localBaselineRef.current = {
      orchestratorUrl: form.orchestratorUrl,
      adminToken: form.adminToken,
    };
    // 即使只是本地字段变更，也给用户成功反馈
    void localChanged;
    setSaveStatus({ kind: 'success', version: newVersion, restarted });
  };

  // ── 重置为服务器版本 ──
  const doResetFromServer = async (): Promise<void> => {
    try {
      const client = new AdminClient(form.orchestratorUrl, form.adminToken);
      const resp = await client.getConfig();
      const snap = wireToSnapshot(resp.config);
      setBaseline(snap);
      setVersion(resp.version);
      setForm((prev) => snapshotToForm(snap, prev));
      setSaveStatus({ kind: 'idle' });
      setLoadError(null);
    } catch (err) {
      setSaveStatus({ kind: 'error', message: describeError(err) });
    }
  };

  // ── 409 对话框按钮 ──
  const onDiscardLocal = async () => {
    setStaleDialog(null);
    await doResetFromServer();
  };

  const onRetryWithLatest = async () => {
    if (!staleDialog) return;
    const newExpected = staleDialog.currentVersion;
    setStaleDialog(null);
    setVersion(newExpected);
    await doSave(newExpected);
  };

  // ── 渲染 ──

  return (
    <section className="settings-panel">
      <div className="eyebrow">设置</div>
      <h3 className="title">配置 vibe-niuma</h3>

      {/* 顶部状态栏（不能用 .card —— .card 有 overflow:hidden 会裁内容） */}
      <div className="settings-status-bar">
        <div>
          <div style={{ fontSize: '0.78rem', color: 'var(--ink-mute)' }}>上次同步</div>
          <div>{formatSyncTime(lastSyncedAt)}</div>
        </div>
        <div>
          <div style={{ fontSize: '0.78rem', color: 'var(--ink-mute)' }}>服务端版本</div>
          <div>v{version}</div>
        </div>
        <button
          type="button"
          className="btn btn-secondary btn-small"
          onClick={doResetFromServer}
          disabled={form.adminToken.length < 20}
        >
          重置为服务器版本
        </button>
      </div>

      {loadError && (
        <p className="wizard-error" role="alert">{loadError}</p>
      )}

      {/* 分组 1：服务器连接（Plan 9 起：分组永久展开，外层 .app-body 一个滚动条统一滚） */}
      <section className="settings-group">
        <h4 className="settings-group-title">服务器连接</h4>
        <div className="field">
          <div className="field-label-row">
            <span className="field-label">Orchestrator URL</span>
            <HelpBubble content={orchestratorUrlHelp} ariaLabel="Orchestrator URL 帮助" />
          </div>
          <input
            aria-label="Orchestrator URL"
            type="url"
            value={form.orchestratorUrl}
            onChange={(e) => update('orchestratorUrl', e.target.value)}
          />
        </div>
        <div className="field">
          <div className="field-label-row">
            <span className="field-label">Admin Token</span>
            <HelpBubble content={adminTokenHelp} ariaLabel="Admin Token 帮助" />
          </div>
          <input
            aria-label="Admin Token"
            type="password"
            value={form.adminToken}
            onChange={(e) => update('adminToken', e.target.value)}
          />
        </div>
      </section>

      {/* 分组 2：AI 模型 */}
      <section className="settings-group">
        <h4 className="settings-group-title">AI 模型</h4>
        <div className="field">
          <div className="field-label-row">
            <span className="field-label">Dev Runner</span>
            <HelpBubble content={ecsSetupHelp} ariaLabel="Dev Runner 帮助" />
          </div>
          <select
            aria-label="Dev Runner"
            value={form.devRunner}
            onChange={(e) => update('devRunner', e.target.value as 'opencode' | 'claude-code')}
          >
            <option value="opencode">opencode</option>
            <option value="claude-code">claude-code</option>
          </select>
        </div>
        <div className="field">
          <div className="field-label-row">
            <span className="field-label">Dev Model</span>
            <HelpBubble content={ecsSetupHelp} ariaLabel="Dev Model 帮助" />
          </div>
          <input
            aria-label="Dev Model"
            type="text"
            value={form.devModel}
            onChange={(e) => update('devModel', e.target.value)}
          />
        </div>
        <div className="field">
          <div className="field-label-row">
            <span className="field-label">Vision Model</span>
            <HelpBubble content={ecsSetupHelp} ariaLabel="Vision Model 帮助" />
          </div>
          <input
            aria-label="Vision Model"
            type="text"
            value={form.visionModel}
            onChange={(e) => update('visionModel', e.target.value)}
          />
        </div>
      </section>

      {/* 分组 3：API key */}
      <section className="settings-group">
        <h4 className="settings-group-title">API Key</h4>
        <ApiKeyField
          label="DeepSeek API Key"
          ariaLabel="DeepSeek API Key"
          helpAria="DeepSeek Key 帮助"
          helpContent={deepseekKeyHelp}
          isSet={baseline?.deepseekSet ?? false}
          value={form.deepseekKeyInput}
          onChange={(v) => update('deepseekKeyInput', v)}
          testId="deepseek-set-indicator"
        />
        <ApiKeyField
          label="DashScope API Key"
          ariaLabel="DashScope API Key"
          helpAria="DashScope Key 帮助"
          helpContent={dashscopeKeyHelp}
          isSet={baseline?.dashscopeSet ?? false}
          value={form.dashscopeKeyInput}
          onChange={(v) => update('dashscopeKeyInput', v)}
          testId="dashscope-set-indicator"
        />
        <ApiKeyField
          label="Anthropic API Key"
          ariaLabel="Anthropic API Key"
          helpAria="Anthropic Key 帮助"
          helpContent={ecsSetupHelp}
          isSet={baseline?.anthropicSet ?? false}
          value={form.anthropicKeyInput}
          onChange={(v) => update('anthropicKeyInput', v)}
          testId="anthropic-set-indicator"
        />
        {/* TODO(plan6 后续)：让用户主动清空已设置的 key（PUT null）。
            MVP 暂不支持，避免与「未输入 = 保持现状」语义冲突。 */}
      </section>

      {/* 分组 4：项目路径 */}
      <section className="settings-group">
        <h4 className="settings-group-title">项目路径</h4>
        <div className="field">
          <div className="field-label-row">
            <span className="field-label">Demo Repo Path</span>
            <HelpBubble content={gitRepoPathHelp} ariaLabel="Demo Repo Path 帮助" />
          </div>
          <input
            aria-label="Demo Repo Path"
            type="text"
            value={form.demoRepoPath}
            onChange={(e) => update('demoRepoPath', e.target.value)}
          />
        </div>
        <div className="field">
          <div className="field-label-row">
            <span className="field-label">Preview Backend URL</span>
            <HelpBubble content={ecsSetupHelp} ariaLabel="Preview Backend URL 帮助" />
          </div>
          <input
            aria-label="Preview Backend URL"
            type="text"
            value={form.previewBackendUrl}
            onChange={(e) => update('previewBackendUrl', e.target.value)}
          />
        </div>
      </section>

      {/* 同步状态文案 */}
      {saveStatus.kind === 'pending' && (
        <p className="wizard-success" role="status">同步中…</p>
      )}
      {saveStatus.kind === 'success' && (
        <>
          <p className="wizard-success" role="status">
            ✓ 已同步 · 服务端版本 v{saveStatus.version}
          </p>
          {saveStatus.restarted.includes('doskill-llm-proxy') && (
            <p className="wizard-success" role="status">
              ⏳ LiteLLM 正在重启，约 5s 后恢复服务
            </p>
          )}
        </>
      )}
      {saveStatus.kind === 'error' && (
        <p className="wizard-error" role="alert">
          ✗ 同步失败：{saveStatus.message}
        </p>
      )}

      {/* 底部按钮 */}
      <div className="btn-row">
        <button
          type="button"
          className="btn btn-secondary"
          onClick={onClose}
        >
          关闭设置
        </button>
        <button
          type="button"
          className="btn btn-primary"
          onClick={() => { void doSave(); }}
          disabled={!dirty || saveStatus.kind === 'pending'}
        >
          保存并同步
        </button>
      </div>

      {/* 409 对话框 */}
      {staleDialog && (
        <div className="modal-backdrop" role="presentation">
          <div className="modal" role="dialog" aria-labelledby="stale-dialog-title">
            <h4 id="stale-dialog-title">配置版本冲突</h4>
            <p>
              服务端配置已被其他人改过
              （当前 v{staleDialog.currentVersion}，本地 v{staleDialog.expectedVersion}）。
            </p>
            <div className="btn-row">
              <button
                type="button"
                className="btn btn-secondary"
                onClick={() => { void onDiscardLocal(); }}
              >
                丢弃我的改动，拉服务器版本
              </button>
              <button
                type="button"
                className="btn btn-primary"
                onClick={() => { void onRetryWithLatest(); }}
              >
                保留我的改动，再试一次
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}

// ── ApiKeyField 子组件：input + 「已设置」标识共用 label ──

interface ApiKeyFieldProps {
  label: string;
  ariaLabel: string;
  helpAria: string;
  helpContent: string;
  isSet: boolean;
  value: string;
  onChange: (v: string) => void;
  testId: string;
}

function ApiKeyField({
  label, ariaLabel, helpAria, helpContent, isSet, value, onChange, testId,
}: ApiKeyFieldProps) {
  return (
    <div className="field">
      <div className="field-label-row">
        <span className="field-label">{label}</span>
        <HelpBubble content={helpContent} ariaLabel={helpAria} />
        {isSet && (
          <span
            data-testid={testId}
            className="api-key-set-indicator"
            style={{ color: 'var(--accent, oklch(60% 0.15 145))', fontSize: '0.78rem' }}
          >
            ✓ 已设置
          </span>
        )}
      </div>
      <input
        aria-label={ariaLabel}
        type="password"
        value={value}
        placeholder={isSet ? '••• 已设置（输入新值会替换）' : ''}
        onChange={(e) => onChange(e.target.value)}
      />
    </div>
  );
}
