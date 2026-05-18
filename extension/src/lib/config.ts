// Plan 6 · Task 4: 扩展端唯一配置入口。
// 设计要点：
//   - 所有 UI / SW 都通过 loadConfig / saveConfig 操作，不直接碰 chrome.storage
//   - 用 zod 校验 storage 里的数据，损坏 → loadConfig 返回 null（UI 走首次引导）
//   - saveConfig 深 merge，传 Partial 不丢失未传字段
//   - configVersion 是后端乐观锁版本号（Task 3 后端会维护），扩展只读 + 透传
import { z } from 'zod';

// ── Storage key ─────────────────────────────────────────────────────
// v2 后缀：和老的 `vibe-niuma.orchestratorBaseUrl`（仅存单个 URL 字符串）区分开。
// 升级时不读老 key，让用户用 SetupWizard 重新走一遍——比 schema 迁移省事且更显式。
const STORAGE_KEY = 'vibe_niuma_config_v2';

// ── zod schema ──────────────────────────────────────────────────────
// Plan 11 · M1.T1：加 repos 字段。
//   - 一个项目可绑 N 个仓（前后端拆开 / 微服务多仓）。
//   - mainBranch = rebase 起点（默认 'main'）
//   - targetBranch = 业务员 CR 合并到的分支（默认 'vibe-niuma/dev'，
//     程序员从这条分支提 PR review 后合到 main，避免业务员直接污染 main）
//   - 旧项目（pre-Plan 11）storage 里没这个字段 → zod default 兜回 [] 数组。
//     空数组 = 走老的单一 demoRepoPath 单仓路径（git_manager.py 兼容）。
const RepoConfigSchema = z.object({
  url: z.string().min(1),  // git URL：'git@github.com:org/repo.git' 或 'https://github.com/org/repo.git'
  mainBranch: z.string().min(1).default('main'),
  targetBranch: z.string().min(1).default('vibe-niuma/dev'),
});

// 严格按照 Plan 6 task 4 的定义；server 子对象的默认值在保存时由 zod 自己补上。
export const ConfigSchema = z.object({
  orchestratorUrl: z.string().url(),
  adminToken: z.string().min(20),
  configVersion: z.number().int().default(0),
  repos: z.array(RepoConfigSchema).default([]),
  server: z.object({
    devRunner: z.enum(['opencode', 'claude-code']).default('opencode'),
    devModel: z.string().default('deepseek/deepseek-v4-flash'),
    visionModel: z.string().default('qwen-vl-plus'),
    deepseekApiKey: z.string().optional(),
    dashscopeApiKey: z.string().optional(),
    anthropicApiKey: z.string().optional(),
    demoRepoPath: z.string().default('/opt/vibe-niuma/demo'),
    previewBackendUrl: z.string().default('http://vibe-niuma-demo-backend:8000'),
  }),
});

export type Config = z.infer<typeof ConfigSchema>;
export type ServerConfig = Config['server'];
export type RepoConfig = z.infer<typeof RepoConfigSchema>;

// ── Deep partial (for saveConfig patch arg) ─────────────────────────
// 只有两层（顶层 + server），不引入泛型 DeepPartial 库，手写更清晰。
// repos 是数组：传整段替换（不做 element-level merge —— 数组语义就是「这是当前全集」）。
export interface ConfigPatch {
  orchestratorUrl?: string;
  adminToken?: string;
  configVersion?: number;
  repos?: RepoConfig[];
  server?: Partial<ServerConfig>;
}

// ── 内部：从 storage 读出原始 JSON，可能为 undefined ────────────────
async function readRaw(): Promise<unknown> {
  try {
    const got = await chrome.storage.local.get(STORAGE_KEY);
    return got?.[STORAGE_KEY];
  } catch {
    return undefined;
  }
}

/**
 * 读 chrome.storage 里的 config，并通过 zod 校验。
 * - storage 没有该 key 或读取异常 → null
 * - storage 有但 schema 校验失败（比如 URL 写错） → null（让 UI 触发引导，避免抛出炸 sidebar）
 */
export async function loadConfig(): Promise<Config | null> {
  const raw = await readRaw();
  if (raw === undefined || raw === null) return null;
  const parsed = ConfigSchema.safeParse(raw);
  if (!parsed.success) return null;
  return parsed.data;
}

/**
 * 写 chrome.storage：
 *   - 读现有 config（如果有）
 *   - 深 merge patch（顶层浅 merge，server 子对象浅 merge）
 *   - 没有现有 config 时，用 patch 起一份新的 → 走 schema 默认值
 *   - 用 zod 校验 → 失败抛错（让 UI 显式提示）
 */
export async function saveConfig(patch: ConfigPatch): Promise<Config> {
  const existing = (await readRaw()) as Record<string, unknown> | undefined;

  // server 子对象单独 merge：避免顶层 spread 把 patch.server 整个替掉
  const existingServer = (existing && typeof existing === 'object' && 'server' in existing
    ? ((existing as { server: unknown }).server as Record<string, unknown>)
    : undefined) ?? {};
  const mergedServer = {
    ...existingServer,
    ...(patch.server ?? {}),
  };

  const merged: Record<string, unknown> = {
    ...(existing ?? {}),
    ...(patch.orchestratorUrl !== undefined ? { orchestratorUrl: patch.orchestratorUrl } : {}),
    ...(patch.adminToken !== undefined ? { adminToken: patch.adminToken } : {}),
    ...(patch.configVersion !== undefined ? { configVersion: patch.configVersion } : {}),
    ...(patch.repos !== undefined ? { repos: patch.repos } : {}),
    server: mergedServer,
  };

  const validated = ConfigSchema.parse(merged);
  await chrome.storage.local.set({ [STORAGE_KEY]: validated });
  return validated;
}

/** 用于 SetupWizard 判断是否要弹首次引导。最低条件：URL + token 都存在且能过 schema。 */
export async function isConfigured(): Promise<boolean> {
  const cfg = await loadConfig();
  return isConfigSufficient(cfg);
}

/**
 * 同步版本：在已经 loadConfig 一次后，对 Config 对象做 sufficient 判断。
 * 给 App.tsx 路由用——避免每次 render 都再 await loadConfig。
 * 「足够」= URL 非空 + token >= 20 字符（zod schema 已保证这俩字段类型对，这里只检查长度）。
 */
export function isConfigSufficient(config: Config | null): boolean {
  if (!config) return false;
  return config.orchestratorUrl.length > 0 && config.adminToken.length >= 20;
}
