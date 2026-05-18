// Plan 11 M1.T8：新建项目向导扩展成 4 步：
//   1. 项目名
//   2. DeploymentAssistant（Plan 7 部署助手，跑完 chrome.storage 有 orchestrator
//      URL + admin token）
//   3. GitHub PAT 收集（Plan 11 M1.T2 GitHubAuthStep；可跳过）
//   4. Repo 列表配置（Plan 11 M1.T8 RepoListEditor）+ 触发 sync-repos →
//      saveConfig 含 repos → createProject + setActive + onDone

import React, { useState } from 'react';
import { loadConfig, saveConfig, type RepoConfig } from '../../lib/config';
import { loadGitHubAuth, type GitHubAuth } from '../../lib/github-auth';
import { createProject, setActiveProject } from '../../lib/projects';
import { AlertWebhookStep } from '../components/AlertWebhookStep';
import { GitHubAuthStep } from '../components/GitHubAuthStep';
import { RepoListEditor } from '../components/RepoListEditor';
import { DeploymentAssistantPanel } from './DeploymentAssistantPanel';

interface Props {
  onDone: () => void;
  onCancel: () => void;
}

type Step = 1 | 2 | 3 | 4 | 5;

export function CreateProjectPanel({ onDone, onCancel }: Props) {
  const [step, setStep] = useState<Step>(1);
  const [name, setName] = useState('');
  const [auth, setAuth] = useState<GitHubAuth | undefined>(undefined);
  const [repos, setRepos] = useState<RepoConfig[]>([]);
  // Plan 11 M4.T27：业务员可选配的告警 webhook URL
  const [alertWebhookUrl, setAlertWebhookUrl] = useState<string>('');
  const [syncStatus, setSyncStatus] = useState<
    | { kind: 'idle' }
    | { kind: 'syncing' }
    | { kind: 'ok'; synced: number; failed: number }
    | { kind: 'error'; message: string }
  >({ kind: 'idle' });

  // 进 step 2 前清掉上次未完成的 wizard state，让新项目从 deepseek key 卡片开始
  // 不被上次留下的 phase=choosing_path 之类污染。
  const goToStep2 = async () => {
    if (chrome?.storage?.local?.remove) {
      try {
        await chrome.storage.local.remove([
          'vibe_niuma_deployment_state',
          'vibe_niuma_deployment_history',
        ]);
      } catch {
        /* 清不掉也别拦着用户进 step 2 */
      }
    }
    setStep(2);
  };

  // DeploymentAssistant 完成 → step 3（GitHub）
  const onAssistantComplete = () => setStep(3);

  // GitHub auth 完成 → step 4（repos）。可能 auth 为 undefined（业务员跳过 / 只用 public）
  const onAuthComplete = async (a: GitHubAuth | undefined) => {
    if (a) setAuth(a);
    // 同时从 session 重新拿一遍，万一 saveGitHubAuth 还在 in-flight
    const fromSess = await loadGitHubAuth();
    if (fromSess) setAuth(fromSess);
    setStep(4);
  };

  // step 4 RepoList 完成 → step 5 AlertWebhook（可跳过）
  const onReposComplete = () => setStep(5);

  // step 5 AlertWebhook 完成 → 保存 + sync-repos + 完成
  const onAlertWebhookComplete = (url: string) => {
    setAlertWebhookUrl(url);
    void onSaveAndFinish(url);
  };

  // step 5：保存 + 触发 sync-repos + 完成
  // Plan 11 M4.T27：alertUrl 由 onAlertWebhookComplete 直传，避免 setState 异步窗口
  const onSaveAndFinish = async (alertUrl: string) => {
    // 验空 URL
    const cleaned = repos
      .map((r) => ({
        url: r.url.trim(),
        mainBranch: (r.mainBranch ?? 'main').trim() || 'main',
        targetBranch: (r.targetBranch ?? 'vibe-niuma/dev').trim() || 'vibe-niuma/dev',
      }))
      .filter((r) => r.url.length > 0);

    // 写 config（含 repos + alertWebhookUrl）
    await saveConfig({ repos: cleaned, alertWebhookUrl: alertUrl });

    const cfg = await loadConfig();
    if (!cfg) {
      setSyncStatus({ kind: 'error', message: '配置保存失败，请重试' });
      return;
    }

    const project = await createProject(name, cfg);
    await setActiveProject(project.id);

    // 触发 sync-repos（best-effort —— 失败不阻塞项目落地）
    if (cleaned.length > 0) {
      setSyncStatus({ kind: 'syncing' });
      try {
        const resp = await fetch(`${cfg.orchestratorUrl.replace(/\/$/, '')}/projects/${project.id}/sync-repos`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            repos: cleaned.map((r) => ({
              url: r.url,
              main_branch: r.mainBranch,
              target_branch: r.targetBranch,
            })),
            pat: auth?.token ?? null,
          }),
        });
        if (resp.ok) {
          const body = (await resp.json()) as { synced: unknown[]; failed: unknown[] };
          setSyncStatus({ kind: 'ok', synced: body.synced.length, failed: body.failed.length });
          // sync 后给业务员看 1-2 秒结果再走
          setTimeout(() => onDone(), 1500);
          return;
        }
        setSyncStatus({ kind: 'error', message: `sync 失败：HTTP ${resp.status}` });
      } catch (err) {
        setSyncStatus({
          kind: 'error',
          message: `sync 失败：${err instanceof Error ? err.message : String(err)}`,
        });
      }
      // 出错也允许进项目主界面（业务员可以在 Settings 重新同步）
      setTimeout(() => onDone(), 2500);
    } else {
      // 没绑仓 → 直接完成
      onDone();
    }
  };

  if (step === 1) {
    return (
      <div className="app-body">
        <section>
          <h3 className="title">给新项目起个名字</h3>
          <p className="help">业务上你会怎么叫这套东西？</p>
          <label className="field">
            <span className="label"><span>项目名</span><span className="count">{name.length} / 50</span></span>
            <input
              type="text"
              aria-label="项目名"
              value={name}
              maxLength={50}
              onChange={(e) => setName(e.target.value)}
              placeholder="订单管理 / 内部工具 ..."
            />
          </label>
          <div className="btn-row">
            <button className="btn btn-ghost" onClick={onCancel}>取消</button>
            <button
              className="btn btn-primary"
              onClick={() => void goToStep2()}
              disabled={!name.trim()}
            >下一步 →</button>
          </div>
        </section>
      </div>
    );
  }

  if (step === 2) {
    return <DeploymentAssistantPanel onComplete={onAssistantComplete} />;
  }

  if (step === 3) {
    return (
      <div className="app-body">
        <GitHubAuthStep
          allowSkip
          onComplete={(a) => void onAuthComplete(a)}
        />
        <div className="btn-row">
          <button className="btn btn-ghost" onClick={() => setStep(2)}>← 上一步</button>
        </div>
      </div>
    );
  }

  if (step === 4) {
    return (
      <div className="app-body">
        <section>
          <h3 className="title">关联 GitHub 仓库</h3>
          {auth && <p className="hint">✓ 已绑定 @{auth.username}（PAT 在 session 内存里）</p>}
          <RepoListEditor value={repos} onChange={setRepos} />
          <div className="btn-row">
            <button className="btn btn-ghost" onClick={() => setStep(3)}>← 上一步</button>
            <button
              className="btn btn-primary"
              onClick={onReposComplete}
            >
              {repos.length === 0 ? '跳过仓库 →' : '下一步 →'}
            </button>
          </div>
        </section>
      </div>
    );
  }

  // step === 5：alert webhook（可跳过）+ 实际保存 + sync
  return (
    <div className="app-body">
      <AlertWebhookStep
        initialUrl={alertWebhookUrl}
        onComplete={onAlertWebhookComplete}
      />
      {syncStatus.kind === 'syncing' && (
        <div className="alert alert-info" role="status">⏳ 正在 sync 仓库到 ECS...</div>
      )}
      {syncStatus.kind === 'ok' && (
        <div className="alert alert-ok" role="status">
          ✓ 已同步 {syncStatus.synced} 个仓
          {syncStatus.failed > 0 && `（${syncStatus.failed} 个失败，进主界面后可在设置里重试）`}
        </div>
      )}
      {syncStatus.kind === 'error' && (
        <div className="alert alert-error" role="alert">{syncStatus.message}</div>
      )}
      <div className="btn-row">
        <button
          className="btn btn-ghost"
          onClick={() => setStep(4)}
          disabled={syncStatus.kind === 'syncing'}
        >← 上一步</button>
      </div>
    </div>
  );
}
