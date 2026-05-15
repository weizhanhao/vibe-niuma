// Plan 6 · Task 4: 扩展端唯一配置入口。
// 设计要点：
//   - 所有 UI / SW 都通过 loadConfig / saveConfig 操作，不直接碰 chrome.storage
//   - 用 zod 校验 storage 里的数据，损坏 → loadConfig 返回 null（UI 走首次引导）
//   - saveConfig 深 merge，传 Partial 不丢失未传字段
//   - configVersion 是后端乐观锁版本号（Task 3 后端会维护），扩展只读 + 透传
import { z } from 'zod';

// ── Storage key ─────────────────────────────────────────────────────
// v2 后缀：和老的 `doskill.orchestratorBaseUrl`（仅存单个 URL 字符串）区分开。
// 升级时不读老 key，让用户用 SetupWizard 重新走一遍——比 schema 迁移省事且更显式。
const STORAGE_KEY = 'doskill_config_v2';

// ── zod schema ──────────────────────────────────────────────────────
// 严格按照 Plan 6 task 4 的定义；server 子对象的默认值在保存时由 zod 自己补上。
export const ConfigSchema = z.object({
  orchestratorUrl: z.string().url(),
  adminToken: z.string().min(20),
  configVersion: z.number().int().default(0),
  server: z.object({
    devRunner: z.enum(['opencode', 'claude-code']).default('opencode'),
    devModel: z.string().default('deepseek/deepseek-v4-flash'),
    visionModel: z.string().default('qwen-vl-plus'),
    deepseekApiKey: z.string().optional(),
    dashscopeApiKey: z.string().optional(),
    anthropicApiKey: z.string().optional(),
    demoRepoPath: z.string().default('/opt/doskill/demo'),
    previewBackendUrl: z.string().default('http://doskill-demo-backend:8000'),
  }),
});

export type Config = z.infer<typeof ConfigSchema>;
export type ServerConfig = Config['server'];

// ── Deep partial (for saveConfig patch arg) ─────────────────────────
// 只有两层（顶层 + server），不引入泛型 DeepPartial 库，手写更清晰。
export interface ConfigPatch {
  orchestratorUrl?: string;
  adminToken?: string;
  configVersion?: number;
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
    server: mergedServer,
  };

  const validated = ConfigSchema.parse(merged);
  await chrome.storage.local.set({ [STORAGE_KEY]: validated });
  return validated;
}

/** 用于 SetupWizard 判断是否要弹首次引导。最低条件：URL + token 都存在且能过 schema。 */
export async function isConfigured(): Promise<boolean> {
  const cfg = await loadConfig();
  if (!cfg) return false;
  return cfg.orchestratorUrl.length > 0 && cfg.adminToken.length >= 20;
}
