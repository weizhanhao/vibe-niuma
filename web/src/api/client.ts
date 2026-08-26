/** 后端客户端。所有请求带 X-User —— 租户隔离在服务端强制（api/deps.py）。 */

export interface Project {
  id: string; name: string; slug: string; target_branch: string;
  repos: string[]; quota_parallel_runs: number;
  active_requirements: number; awaiting_review: number;
}
export interface Task {
  id: string; key: string; title: string; repos: string[];
  depends_on: string[]; touches: string[]; sequence: string | null; state: string;
  fail_reason: string; attempts: number;
}
export interface Requirement {
  id: string; ref: string; title: string; body: string; requested_by: string;
  stage: string; state: string; contracts: string[];
  sequence_kind: string | null; tasks: Task[];
  awaiting_answer: boolean; created_at: string;
}
export interface Finding {
  id: string; axis: string; severity: string; category: string; path: string;
  start_line: number; claim: string; failure_scenario: string;
  kept: boolean; confidence: string; verdict_reason: string;
}
export interface MergeJob {
  id: string; requirement_ref: string; repo_name: string;
  position: number; state: string; conflict_ladder: { stage: string; ok: boolean; detail: string }[];
}
export interface Message {
  id: string; role: "user" | "agent" | "system"; author: string;
  body: string; stage: string; awaiting_answer: boolean;
  /** 这条消息背后 agent 的思考过程 —— 可展开看 */
  trace: import("./stream").Step[];
  created_at: string;
}
export interface Activity {
  id: number; kind: string; stage: string; state: string;
  detail: string; created_at: string;
}
export interface Preview { branch: string; task_key: string; url: string }
export interface Repo {
  id: string; name: string; url: string; host_kind: string; default_branch: string;
}
export interface EnvRow { env: string; state: string; url: string | null; finished_at: string | null }
export interface StageDef {
  key: string;
  /** 给人看的名字（中文）。key 是标识符，label 是显示名。 */
  label: string;
  human_gate: boolean; skill: string | null;
  adapter: string | null; env: string | null;
}
export interface PipelineDef { stages: StageDef[]; required_skills: string[]; target_branch: string }

export class ApiError extends Error {
  constructor(readonly status: number, message: string) {
    super(message);
    this.name = "ApiError";
  }
}

/** 凭证。生产走 bearer token；X-User 只在服务端开了 VP_DEV_AUTH 时有效。 */
export interface Credential { token?: string; devUser?: string }

const STORAGE_KEY = "vp.token";

export function loadCredential(): Credential {
  try {
    const t = localStorage.getItem(STORAGE_KEY);
    if (t) return { token: t };
  } catch { /* 隐私模式 / 禁用存储 */ }
  return {};
}

export function saveCredential(token: string): void {
  try { localStorage.setItem(STORAGE_KEY, token); } catch { /* 忽略 */ }
}

export function clearCredential(): void {
  try { localStorage.removeItem(STORAGE_KEY); } catch { /* 忽略 */ }
}

export function createClient(base: string, cred: Credential) {
  async function req<T>(path: string, init?: RequestInit): Promise<T> {
    const auth: Record<string, string> = cred.token
      ? { Authorization: `Bearer ${cred.token}` }
      : cred.devUser
      ? { "X-User": cred.devUser }
      : {};
    const r = await fetch(`${base}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...auth, ...(init?.headers ?? {}) },
    });
    if (!r.ok) {
      // 把服务端的说明带出来 —— "请求失败" 对排查毫无价值
      let detail = r.statusText;
      try { const b = await r.json(); detail = b.detail ?? detail; } catch { /* 非 JSON 响应 */ }
      throw new ApiError(r.status, detail);
    }
    return r.status === 204 ? (undefined as T) : r.json();
  }

  return {
    projects: () => req<Project[]>("/projects"),
    pipeline: (slug: string) => req<PipelineDef>(`/projects/${slug}/pipeline`),
    intake: (slug: string, opening: string) =>
      req<Requirement>(`/projects/${slug}/intake`,
        { method: "POST", body: JSON.stringify({ opening }) }),
    drafts: (slug: string) =>
      req<Requirement[]>(`/projects/${slug}/requirements?drafts=true`),
    editDraft: (slug: string, id: string, body: { title?: string; body?: string }) =>
      req<Requirement>(`/projects/${slug}/requirements/${id}`,
        { method: "PATCH", body: JSON.stringify(body) }),
    submitDraft: (slug: string, id: string) =>
      req<Requirement>(`/projects/${slug}/requirements/${id}/submit`,
        { method: "POST" }),
    requirements: (slug: string, stage?: string) =>
      req<Requirement[]>(`/projects/${slug}/requirements${stage ? `?stage=${stage}` : ""}`),
    requirement: (slug: string, id: string) =>
      req<Requirement>(`/projects/${slug}/requirements/${id}`),
    /** 直接建需求，跳过「立需求」那段对话。留给脚本/集成用，界面走 intake。 */
    createRequirement: (slug: string, body: { title: string; body: string }) =>
      req<Requirement>(`/projects/${slug}/requirements`, { method: "POST", body: JSON.stringify(body) }),
    findings: (slug: string, id: string, includeDropped = false) =>
      req<Finding[]>(`/projects/${slug}/requirements/${id}/findings?include_dropped=${includeDropped}`),
    review: (slug: string, id: string, decision: string, comment = "") =>
      req<{ ok: boolean }>(`/projects/${slug}/requirements/${id}/review`,
        { method: "POST", body: JSON.stringify({ decision, comment }) }),
    previews: (slug: string, id: string) =>
      req<Preview[]>(`/projects/${slug}/requirements/${id}/previews`),
    retry: (slug: string, id: string) =>
      req<{ ok: boolean; stage: string; attempt: number }>(
        `/projects/${slug}/requirements/${id}/retry`, { method: "POST" }),
    activity: (slug: string, id: string) =>
      req<Activity[]>(`/projects/${slug}/requirements/${id}/activity`),
    messages: (slug: string, id: string) =>
      req<Message[]>(`/projects/${slug}/requirements/${id}/messages`),
    say: (slug: string, id: string, body: string, proceed = false) =>
      req<Message>(`/projects/${slug}/requirements/${id}/messages`,
        { method: "POST", body: JSON.stringify({ body, proceed }) }),
    mergeQueue: (slug: string) => req<MergeJob[]>(`/projects/${slug}/merge-queue`),

    // ── 管理端 ──
    createProject: (body: {
      name: string; slug: string; org_id: string;
      target_branch?: string; llm_secret_ref?: string;
    }) => req<{ id: string; slug: string; name: string }>("/admin/projects",
      { method: "POST", body: JSON.stringify(body) }),
    repos: (slug: string) => req<Repo[]>(`/admin/projects/${slug}/repos`),
    addRepo: (slug: string, body: {
      name: string; url: string; default_branch?: string; pat_ref?: string;
    }) =>
      req<{ id: string; name: string }>(`/admin/projects/${slug}/repos`,
        { method: "POST", body: JSON.stringify(body) }),
    addMember: (slug: string, body: { user_id: string; role: string }) =>
      req<{ id: string }>(`/admin/projects/${slug}/members`,
        { method: "POST", body: JSON.stringify(body) }),
    issueToken: (user_id: string) =>
      req<{ token: string; user_id: string }>("/admin/tokens",
        { method: "POST", body: JSON.stringify({ user_id }) }),
    environments: (slug: string) => req<EnvRow[]>(`/projects/${slug}/environments`),
    eventsUrl: (slug: string, id: string, lastEventId = 0) =>
      // EventSource 不支持自定义头 —— token 只能走 query。
      // 服务端两种都认（见 api/deps.py）。
      `${base}/projects/${slug}/requirements/${id}/events?lastEventId=${lastEventId}`
      + (cred.token ? `&token=${encodeURIComponent(cred.token)}` : "")
      + (cred.devUser ? `&devUser=${encodeURIComponent(cred.devUser)}` : ""),
  };
}

export type Client = ReturnType<typeof createClient>;
