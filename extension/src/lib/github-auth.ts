// Plan 11 · M1.T2：GitHub PAT 鉴权信息只在 chrome.storage.session 里活，
// 关浏览器 / 重启扩展即清。**永远不入 chrome.storage.local，永远不入 DB**。
//
// session 的「session」= chrome service worker 生命周期。对业务员来说就是
// 浏览器开着的这段时间。这跟 SSH 私钥的安全模型一致。

const STORAGE_KEY = 'vibe_niuma_github_pat';

export interface GitHubAuth {
  /** Personal Access Token，业务员从 GitHub Settings → Developer settings 创建 */
  token: string;
  /** GET /user 拿到的 login（业务员的 GitHub 用户名），存它为了 UI 上显示「✓ 已绑定 @xxx」 */
  username: string;
  /** Unix ms timestamp，验证通过的时刻 */
  validatedAt: number;
}

interface ChromeSessionLike {
  get(keys: string[]): Promise<Record<string, unknown>>;
  set(items: Record<string, unknown>): Promise<void>;
  remove(keys: string[]): Promise<void>;
}

function getSession(): ChromeSessionLike | null {
  const g = (globalThis as unknown as {
    chrome?: { storage?: { session?: ChromeSessionLike } };
  }).chrome;
  return g?.storage?.session ?? null;
}

export async function loadGitHubAuth(): Promise<GitHubAuth | null> {
  const sess = getSession();
  if (!sess) return null;
  try {
    const got = await sess.get([STORAGE_KEY]);
    const raw = got[STORAGE_KEY] as Partial<GitHubAuth> | undefined;
    if (!raw || typeof raw.token !== 'string' || raw.token.length === 0) return null;
    return {
      token: raw.token,
      username: typeof raw.username === 'string' ? raw.username : '',
      validatedAt: typeof raw.validatedAt === 'number' ? raw.validatedAt : Date.now(),
    };
  } catch {
    return null;
  }
}

export async function saveGitHubAuth(auth: GitHubAuth): Promise<void> {
  const sess = getSession();
  if (!sess) return;
  await sess.set({ [STORAGE_KEY]: auth });
}

export async function clearGitHubAuth(): Promise<void> {
  const sess = getSession();
  if (!sess) return;
  await sess.remove([STORAGE_KEY]);
}

// ── PAT 校验：打 GitHub /user 看 200 + 拿 login ─────────────────────

export interface ValidatePATResult {
  ok: boolean;
  username?: string;
  /** 失败时的人话错误，UI 直接展示 */
  error?: string;
}

export async function validateGitHubPAT(token: string): Promise<ValidatePATResult> {
  if (!token || token.length < 10) {
    return { ok: false, error: 'PAT 看起来不对（太短）' };
  }
  try {
    const resp = await fetch('https://api.github.com/user', {
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
      },
    });
    if (resp.status === 200) {
      const body = (await resp.json()) as { login?: string };
      const username = body.login ?? '';
      return { ok: true, username };
    }
    if (resp.status === 401) {
      return { ok: false, error: 'PAT 无效或已过期（401）' };
    }
    if (resp.status === 403) {
      return { ok: false, error: 'PAT 权限不足（403）。请重新创建一个 repo scope 全开的 PAT。' };
    }
    return { ok: false, error: `GitHub API 返 ${resp.status}` };
  } catch (err) {
    return { ok: false, error: `网络错误：${err instanceof Error ? err.message : String(err)}` };
  }
}
