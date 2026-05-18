// Plan 11 · M1.T2：业务员粘 GitHub PAT 的 wizard step。
//
// 业务员在 GitHub Settings → Developer settings → Personal access tokens
// 创建一个带 repo scope 的 PAT（fine-grained 或 classic 都行），粘进来。
// 我们打 GET /user 校验，成功就存进 chrome.storage.session。
//
// PAT 永远不入 chrome.storage.local，永远不入 server DB —— 关浏览器即清。

import React, { useState } from 'react';
import {
  GitHubAuth,
  saveGitHubAuth,
  validateGitHubPAT,
} from '../../lib/github-auth';

interface Props {
  /** 业务员完成（PAT 已存 session）后回调。可选 `auth` —— 跳过时为 undefined */
  onComplete: (auth?: GitHubAuth) => void;
  /** 业务员可点「跳过」（如果他只管 public repo），onComplete 不带 auth */
  allowSkip?: boolean;
}

type Status =
  | { kind: 'idle' }
  | { kind: 'checking' }
  | { kind: 'ok'; username: string }
  | { kind: 'error'; message: string };

export function GitHubAuthStep({ onComplete, allowSkip = false }: Props) {
  const [token, setToken] = useState('');
  const [status, setStatus] = useState<Status>({ kind: 'idle' });

  const validate = async () => {
    setStatus({ kind: 'checking' });
    const r = await validateGitHubPAT(token.trim());
    if (r.ok && r.username) {
      const auth: GitHubAuth = {
        token: token.trim(),
        username: r.username,
        validatedAt: Date.now(),
      };
      await saveGitHubAuth(auth);
      setStatus({ kind: 'ok', username: r.username });
    } else {
      setStatus({ kind: 'error', message: r.error ?? '校验失败' });
    }
  };

  const next = () => {
    if (status.kind !== 'ok') return;
    onComplete({
      token: token.trim(),
      username: status.username,
      validatedAt: Date.now(),
    });
  };

  return (
    <section className="github-auth-step">
      <h3 className="title">连接你的 GitHub</h3>
      <p className="help">
        业务员合并的代码会推回你公司的 GitHub 仓库（合到 <code>vibe-niuma/dev</code>
        分支，程序员从那里 review 后再合到 main）。需要一个 GitHub Personal Access Token。
      </p>

      <details className="help-collapse">
        <summary>怎么生成 PAT？（点开看图文步骤）</summary>
        <ol>
          <li>登录 GitHub → 头像 → Settings → Developer settings → Personal access tokens</li>
          <li>选 Tokens (classic) → Generate new token (classic)</li>
          <li>Note 写「vibe-niuma」；Expiration 选 90 days 或 No expiration</li>
          <li>勾选 <code>repo</code>（整个分组），其它不用勾</li>
          <li>Generate token → 复制开头是 <code>ghp_</code> 的字符串</li>
        </ol>
      </details>

      <label className="field">
        <span className="label">GitHub PAT</span>
        <input
          type="password"
          aria-label="GitHub PAT"
          value={token}
          onChange={(e) => {
            setToken(e.target.value);
            if (status.kind !== 'idle') setStatus({ kind: 'idle' });
          }}
          placeholder="ghp_..."
          autoComplete="off"
        />
      </label>

      {status.kind === 'error' && (
        <div className="alert alert-error" role="alert">{status.message}</div>
      )}
      {status.kind === 'ok' && (
        <div className="alert alert-ok" role="status">✓ 已绑定 @{status.username}</div>
      )}

      <div className="btn-row">
        {allowSkip && status.kind !== 'ok' && (
          <button
            type="button"
            className="btn btn-ghost"
            onClick={() => onComplete(undefined)}
          >
            跳过（我只用 public repo）
          </button>
        )}
        {status.kind !== 'ok' ? (
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => void validate()}
            disabled={!token.trim() || status.kind === 'checking'}
          >
            {status.kind === 'checking' ? '验证中...' : '验证 PAT'}
          </button>
        ) : (
          <button
            type="button"
            className="btn btn-primary"
            onClick={next}
          >
            下一步 →
          </button>
        )}
      </div>

      <p className="hint">
        ⚠ PAT 只存在你这台浏览器的 session 内存里，关掉浏览器就清掉。
        我们**永不**把它发到服务器存盘。
      </p>
    </section>
  );
}
