// Plan 11 M1.T2: github-auth lib 测试。
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  clearGitHubAuth,
  loadGitHubAuth,
  saveGitHubAuth,
  validateGitHubPAT,
} from '../src/lib/github-auth';

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn());
});

afterEach(() => {
  vi.unstubAllGlobals();
});

// ── session 存储 ────────────────────────────────────────────────────

describe('github-auth · session storage', () => {
  it('returns null when no auth stored', async () => {
    expect(await loadGitHubAuth()).toBeNull();
  });

  it('round-trips save → load', async () => {
    const auth = { token: 'ghp_xxxx', username: 'alice', validatedAt: 12345 };
    await saveGitHubAuth(auth);
    const got = await loadGitHubAuth();
    expect(got).toEqual(auth);
  });

  it('clearGitHubAuth removes the entry', async () => {
    await saveGitHubAuth({ token: 'ghp_x', username: 'a', validatedAt: 1 });
    await clearGitHubAuth();
    expect(await loadGitHubAuth()).toBeNull();
  });

  it('returns null when stored entry has empty token', async () => {
    // 篡改 session 模拟损坏数据
    await chrome.storage.session.set({ vibe_niuma_github_pat: { token: '', username: 'x', validatedAt: 0 } });
    expect(await loadGitHubAuth()).toBeNull();
  });
});

// ── validateGitHubPAT ──────────────────────────────────────────────

describe('github-auth · validateGitHubPAT', () => {
  it('rejects empty / too-short PAT without calling fetch', async () => {
    const r = await validateGitHubPAT('');
    expect(r.ok).toBe(false);
    expect(fetch).not.toHaveBeenCalled();
  });

  it('returns ok + username on 200 response', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(
      new Response(JSON.stringify({ login: 'weizhanhao' }), { status: 200 }) as Response,
    );
    const r = await validateGitHubPAT('ghp_validlookingtoken');
    expect(r.ok).toBe(true);
    expect(r.username).toBe('weizhanhao');
  });

  it('returns 401 error on Bad credentials', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(
      new Response(JSON.stringify({ message: 'Bad credentials' }), { status: 401 }) as Response,
    );
    const r = await validateGitHubPAT('ghp_xxxxxxxxxx');
    expect(r.ok).toBe(false);
    expect(r.error).toMatch(/401/);
  });

  it('returns 403 with scope-fix hint', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(
      new Response(JSON.stringify({ message: 'Forbidden' }), { status: 403 }) as Response,
    );
    const r = await validateGitHubPAT('ghp_xxxxxxxxxx');
    expect(r.ok).toBe(false);
    expect(r.error).toMatch(/repo scope/);
  });

  it('sends bearer auth + version header', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(
      new Response(JSON.stringify({ login: 'a' }), { status: 200 }) as Response,
    );
    await validateGitHubPAT('ghp_xxxxxxxxxx');
    const [url, init] = vi.mocked(fetch).mock.calls[0]!;
    expect(url).toBe('https://api.github.com/user');
    const headers = (init?.headers ?? {}) as Record<string, string>;
    expect(headers.Authorization).toBe('Bearer ghp_xxxxxxxxxx');
    expect(headers['X-GitHub-Api-Version']).toBe('2022-11-28');
  });

  it('returns network-error message on fetch throw', async () => {
    vi.mocked(fetch).mockRejectedValueOnce(new Error('connection refused'));
    const r = await validateGitHubPAT('ghp_xxxxxxxxxx');
    expect(r.ok).toBe(false);
    expect(r.error).toMatch(/connection refused/);
  });
});
