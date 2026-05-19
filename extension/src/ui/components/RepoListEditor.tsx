// Plan 11 M1.T8：业务员配项目时填关联的 GitHub 仓库列表。
//
// 每行一条仓：URL + mainBranch（默认 main）+ targetBranch（默认 vibe-niuma/dev）。
// 业务员可加可删；空列表 = 走老的单一 demoRepoPath 单仓行为。

import React from 'react';
import type { RepoConfig } from '../../lib/config';
import repoListHelp from '../helpContent/repo-list.md?raw';
import { HelpBubble } from './HelpBubble';

interface Props {
  value: RepoConfig[];
  onChange: (next: RepoConfig[]) => void;
}

function defaultRepo(): RepoConfig {
  return { url: '', mainBranch: 'main', targetBranch: 'vibe-niuma/dev' };
}

function looksLikeGitUrl(url: string): boolean {
  const u = url.trim();
  return /^https:\/\/github\.com\/[^/]+\/[^/]+/.test(u) || /^git@github\.com:[^/]+\/[^/]+/.test(u);
}

export function RepoListEditor({ value, onChange }: Props) {
  const updateRow = (i: number, patch: Partial<RepoConfig>) => {
    const next = value.map((r, idx) => (idx === i ? { ...r, ...patch } : r));
    onChange(next);
  };

  const addRow = () => onChange([...value, defaultRepo()]);

  const removeRow = (i: number) => onChange(value.filter((_, idx) => idx !== i));

  return (
    <section className="repo-list-editor">
      <div className="title-with-help">
        <p className="help">
          告诉 vibe-niuma 这个项目要管哪些代码托管仓库（GitHub / Gitee / 云效都行）。
          业务员合并的代码会自动 push 到每个仓的 <code>vibe-niuma/dev</code> 分支（不动你们
          main），程序员从那里 review。
        </p>
        <HelpBubble content={repoListHelp} ariaLabel="关联仓库 帮助" />
      </div>

      {value.length === 0 && (
        <div className="empty-state">
          <p>还没绑任何仓库。点下面「+ 添加仓库」开始。</p>
        </div>
      )}

      {value.map((repo, i) => {
        const urlInvalid = repo.url.trim().length > 0 && !looksLikeGitUrl(repo.url);
        return (
          <div key={i} className="repo-row">
            <div className="repo-row-header">
              <span className="repo-row-num">仓库 #{i + 1}</span>
              {urlInvalid && <span className="badge-error">URL 格式不对</span>}
              <button
                type="button"
                className="repo-row-remove"
                onClick={() => removeRow(i)}
                aria-label={`删除仓库 ${i + 1}`}
                title="删除这条"
              >×</button>
            </div>
            <label className="field">
              <span className="label">URL</span>
              <input
                type="text"
                aria-label={`仓库 ${i + 1} URL`}
                value={repo.url}
                onChange={(e) => updateRow(i, { url: e.target.value })}
                placeholder="https://github.com/myorg/frontend.git"
                autoComplete="off"
              />
            </label>
            <div className="repo-branches">
              <label className="field field-half">
                <span className="label">主分支</span>
                <input
                  type="text"
                  aria-label={`仓库 ${i + 1} 主分支`}
                  value={repo.mainBranch ?? 'main'}
                  onChange={(e) => updateRow(i, { mainBranch: e.target.value })}
                  placeholder="main"
                />
              </label>
              <label className="field field-half">
                <span className="label">合并目标</span>
                <input
                  type="text"
                  aria-label={`仓库 ${i + 1} 目标分支`}
                  value={repo.targetBranch ?? 'vibe-niuma/dev'}
                  onChange={(e) => updateRow(i, { targetBranch: e.target.value })}
                  placeholder="vibe-niuma/dev"
                />
              </label>
            </div>
          </div>
        );
      })}

      <button
        type="button"
        className="btn btn-ghost"
        onClick={addRow}
      >
        + 添加仓库
      </button>

      <details className="help-collapse">
        <summary>什么是「业务员合并目标」？</summary>
        <p>
          业务员每合一条 CR，代码不会直接进 main —— 而是先合到 <code>vibe-niuma/dev</code>
          分支（每个仓单独一条）。程序员从这条分支提 PR、review、合到 main。
          这一层闸门保护客户的主分支不被业务员误操作污染。
        </p>
        <p>
          默认名字 <code>vibe-niuma/dev</code> 不会撞客户已有的 <code>dev</code> 分支。
          如果想换（比如 <code>your-company/auto-dev</code>），改这一列即可。
        </p>
      </details>
    </section>
  );
}
