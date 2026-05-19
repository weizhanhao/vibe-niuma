// 熟手快速通道 —— 一张表填完全部配置，一键 createProject 进项目。
//
// 设计原则：跳过 AI 引导 5 步（步骤 2-5），把所有配置塞进一张大表：
//   orchestrator URL / admin token / DeepSeek / DashScope（可选）/
//   GitHub PAT（可选）/ 多仓列表 / Alert webhook（可选）
// 点保存：saveConfig + saveGitHubAuth + createProject + setActive + sync-repos
//   + onDone。一气流到项目主界面。
//
// 业务员第一次安装还是走 AI 引导（CreateProjectPanel step 2-5 默认路径）。
import { useEffect, useState } from 'react';
import { loadConfig, saveConfig, type RepoConfig } from '../../lib/config';
import { loadGitHubAuth, saveGitHubAuth, type GitHubAuth } from '../../lib/github-auth';
import { createProject, setActiveProject } from '../../lib/projects';
import { HelpBubble } from '../components/HelpBubble';
import { RepoListEditor } from '../components/RepoListEditor';

// 帮助文档 markdown（vite 构建时打包成字符串，HelpBubble 点开显示）
import orchestratorUrlHelp from '../helpContent/orchestrator-url.md?raw';
import adminTokenHelp from '../helpContent/admin-token.md?raw';
import deepseekKeyHelp from '../helpContent/deepseek-key.md?raw';
import dashscopeKeyHelp from '../helpContent/dashscope-key.md?raw';
import githubPatHelp from '../helpContent/github-pat.md?raw';
import alertWebhookHelp from '../helpContent/alert-webhook.md?raw';

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
  /** 步骤 1 已经收集的项目名 —— 由父传入，这里不再让用户改。 */
  name: string;
  /** 全部配置 + createProject 完成。父用它退出 wizard 进项目主界面。 */
  onDone: () => void;
  /** 回步骤 1（改项目名 / 切回 AI 引导）。 */
  onCancel: () => void;
}

interface FormState {
  orchestratorUrl: string;
  adminToken: string;
  deepseekKey: string;
  dashscopeKey: string;
  githubPat: string;
  alertWebhookUrl: string;
}

const EMPTY: FormState = {
  orchestratorUrl: '',
  adminToken: '',
  deepseekKey: '',
  dashscopeKey: '',
  githubPat: '',
  alertWebhookUrl: '',
};

export function ManualConfigForm({ name, onDone, onCancel }: ManualConfigFormProps) {
  const [form, setForm] = useState<FormState>(EMPTY);
  const [repos, setRepos] = useState<RepoConfig[]>([]);
  const [saving, setSaving] = useState(false);
  const [progress, setProgress] = useState<string>('');
  const [error, setError] = useState<string | null>(null);

  // 预填现有 storage 值（让用户改局部、不必从头）
  useEffect(() => {
    void (async () => {
      const cfg = await loadConfig();
      const [dsKey, dashKey, gh] = await Promise.all([
        readStorageString(DEEPSEEK_KEY_KEY),
        readStorageString(DASHSCOPE_KEY_KEY),
        loadGitHubAuth(),
      ]);
      setForm({
        orchestratorUrl: cfg?.orchestratorUrl ?? '',
        adminToken: cfg?.adminToken ?? '',
        deepseekKey: dsKey,
        dashscopeKey: dashKey,
        githubPat: gh?.token ?? '',
        alertWebhookUrl: cfg?.alertWebhookUrl ?? '',
      });
      if (cfg?.repos) setRepos(cfg.repos);
    })();
  }, []);

  const canSave =
    name.trim().length > 0 &&
    form.orchestratorUrl.trim().length > 0 &&
    form.adminToken.trim().length >= 20 &&
    form.deepseekKey.trim().startsWith('sk-');

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    setProgress('保存配置...');

    try {
      // 1. orchestrator + 多仓 + alert webhook 都进 chrome.storage 顶层 config
      const cleanedRepos = repos
        .map((r) => ({
          url: r.url.trim(),
          mainBranch: (r.mainBranch ?? 'main').trim() || 'main',
          targetBranch: (r.targetBranch ?? 'vibe-niuma/dev').trim() || 'vibe-niuma/dev',
        }))
        .filter((r) => r.url.length > 0);
      await saveConfig({
        orchestratorUrl: form.orchestratorUrl.trim(),
        adminToken: form.adminToken.trim(),
        repos: cleanedRepos,
        alertWebhookUrl: form.alertWebhookUrl.trim(),
      });

      // 2. LLM keys 单存（chrome.storage.local 顶层独立 key）
      await writeStorageString(DEEPSEEK_KEY_KEY, form.deepseekKey.trim());
      if (form.dashscopeKey.trim()) {
        await writeStorageString(DASHSCOPE_KEY_KEY, form.dashscopeKey.trim());
      }

      // 3. GitHub PAT（session 存活，关浏览器即清；username 设占位，业务员后续真用时
      //    /admin/* 端点会跟着验证刷新）
      if (form.githubPat.trim()) {
        const auth: GitHubAuth = {
          token: form.githubPat.trim(),
          username: '(manual)',
          validatedAt: Date.now(),
        };
        await saveGitHubAuth(auth);
      }

      // 4. createProject —— 这一步真把项目落地到 vibe_niuma_projects
      setProgress('创建项目...');
      const cfg = await loadConfig();
      if (!cfg) throw new Error('保存后读不到 config（schema 校验失败？）');
      const project = await createProject(name.trim(), cfg);
      await setActiveProject(project.id);

      // 5. best-effort 同步多仓到 ECS（失败不阻塞进项目，业务员可在设置里重试）
      if (cleanedRepos.length > 0) {
        setProgress('同步多仓到 ECS...');
        try {
          const resp = await fetch(
            `${cfg.orchestratorUrl.replace(/\/$/, '')}/projects/${project.id}/sync-repos`,
            {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                repos: cleanedRepos.map((r) => ({
                  url: r.url,
                  main_branch: r.mainBranch,
                  target_branch: r.targetBranch,
                })),
                pat: form.githubPat.trim() || null,
              }),
            },
          );
          if (!resp.ok) {
            setProgress(`sync-repos 失败 HTTP ${resp.status}（项目已创建，可在设置里重试）`);
            // 给 1 秒看到提示再走
            await new Promise((r) => setTimeout(r, 1200));
          }
        } catch (e) {
          setProgress(`sync-repos 网络错误（项目已创建，可在设置里重试）: ${e instanceof Error ? e.message : String(e)}`);
          await new Promise((r) => setTimeout(r, 1500));
        }
      }

      // 6. 全部完成 —— 父组件 onDone 触发 App.tsx reload 进 has-active
      onDone();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setProgress('');
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className="manual-config-form">
      <h3 className="title">直接填表（熟手通道）</h3>
      <p className="help">
        项目名：<strong>{name || '(未填)'}</strong> · 一张表填完全部，一键 createProject 进项目。
      </p>

      <fieldset className="manual-config-fieldset">
        <legend>Orchestrator</legend>
        <label className="field">
          <div className="field-label-row">
            <span className="label">Orchestrator URL</span>
            <HelpBubble content={orchestratorUrlHelp} ariaLabel="Orchestrator URL 帮助" />
          </div>
          <input
            type="url"
            placeholder="https://114-55-171-64.sslip.io"
            value={form.orchestratorUrl}
            onChange={(e) => setForm({ ...form, orchestratorUrl: e.target.value })}
          />
        </label>
        <label className="field">
          <div className="field-label-row">
            <span className="label">Admin Token</span>
            <HelpBubble content={adminTokenHelp} ariaLabel="Admin Token 帮助" />
          </div>
          <input
            type="password"
            placeholder="ECS 上 /opt/vibe-niuma/admin.token 那一串"
            value={form.adminToken}
            onChange={(e) => setForm({ ...form, adminToken: e.target.value })}
          />
        </label>
      </fieldset>

      <fieldset className="manual-config-fieldset">
        <legend>LLM Keys</legend>
        <label className="field">
          <div className="field-label-row">
            <span className="label">DeepSeek API Key</span>
            <HelpBubble content={deepseekKeyHelp} ariaLabel="DeepSeek API Key 帮助" />
          </div>
          <input
            type="password"
            placeholder="sk-..."
            value={form.deepseekKey}
            onChange={(e) => setForm({ ...form, deepseekKey: e.target.value })}
          />
        </label>
        <label className="field">
          <div className="field-label-row">
            <span className="label">DashScope API Key（可选）</span>
            <HelpBubble content={dashscopeKeyHelp} ariaLabel="DashScope API Key 帮助" />
          </div>
          <input
            type="password"
            placeholder="sk-...（看截图模型用，不填只走文字）"
            value={form.dashscopeKey}
            onChange={(e) => setForm({ ...form, dashscopeKey: e.target.value })}
          />
        </label>
      </fieldset>

      <fieldset className="manual-config-fieldset">
        <legend>代码托管 PAT（可选）</legend>
        <label className="field">
          <div className="field-label-row">
            <span className="label">Personal Access Token</span>
            <HelpBubble content={githubPatHelp} ariaLabel="代码托管 PAT 帮助（GitHub / Gitee / 云效）" />
          </div>
          <input
            type="password"
            placeholder="GitHub ghp_… / Gitee / 阿里云云效 token 都行（session 内存，永远不入 DB）"
            value={form.githubPat}
            onChange={(e) => setForm({ ...form, githubPat: e.target.value })}
          />
        </label>
      </fieldset>

      <fieldset className="manual-config-fieldset">
        <legend>多仓库</legend>
        <RepoListEditor value={repos} onChange={setRepos} />
      </fieldset>

      <fieldset className="manual-config-fieldset">
        <legend>告警 Webhook（可选）</legend>
        <label className="field">
          <div className="field-label-row">
            <span className="label">钉钉 / 飞书 / Discord webhook URL</span>
            <HelpBubble content={alertWebhookHelp} ariaLabel="告警 Webhook 帮助" />
          </div>
          <input
            type="url"
            placeholder="https://oapi.dingtalk.com/robot/send?access_token=… 或飞书 / Discord"
            value={form.alertWebhookUrl}
            onChange={(e) => setForm({ ...form, alertWebhookUrl: e.target.value })}
          />
        </label>
      </fieldset>

      {progress && <p className="hint">{progress}</p>}
      {error && <p className="wizard-error">{error}</p>}

      <div className="btn-row">
        <button className="btn btn-ghost" onClick={onCancel} disabled={saving}>
          ← 回上一步
        </button>
        <button
          className="btn btn-primary"
          onClick={() => void handleSave()}
          disabled={!canSave || saving}
        >
          {saving ? '保存中…' : '保存并创建项目'}
        </button>
      </div>
    </section>
  );
}
