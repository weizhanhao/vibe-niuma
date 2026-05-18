// Plan 9 Task 7: 多项目模型 + chrome.storage CRUD + Legacy 迁移。
//
// 每个 project = 一台 orchestrator + 一组 git + 一组 key。chrome.storage.local 存：
//   vibe_niuma_projects: Project[]
//   vibe_niuma_active_project_id: string | null
//
// 启动时 migrateLegacyConfig() 把老的 vibe_niuma_config_v2（单 config）包成一个
// 「默认项目」存到 projects 数组，旧 key 保留作 fallback（不删）。

import { z } from 'zod';
import { ConfigSchema, type Config } from './config';

const PROJECTS_KEY = 'vibe_niuma_projects';
const ACTIVE_KEY = 'vibe_niuma_active_project_id';
const LEGACY_CONFIG_KEY = 'vibe_niuma_config_v2';

export const ProjectSchema = z.object({
  id: z.string().min(1),
  name: z.string().min(1).max(50),
  config: ConfigSchema,
  createdAt: z.number().int(),
});

export type Project = z.infer<typeof ProjectSchema>;

function newId(): string {
  const bytes = new Uint8Array(16);
  if (typeof crypto !== 'undefined' && crypto.getRandomValues) {
    crypto.getRandomValues(bytes);
  } else {
    for (let i = 0; i < 16; i++) bytes[i] = Math.floor(Math.random() * 256);
  }
  return Array.from(bytes).map((b) => b.toString(16).padStart(2, '0')).join('');
}

async function getRaw<T>(key: string): Promise<T | undefined> {
  if (!chrome?.storage?.local?.get) return undefined;
  const out = (await chrome.storage.local.get([key])) as Record<string, unknown>;
  return out[key] as T | undefined;
}

async function setRaw(items: Record<string, unknown>): Promise<void> {
  if (!chrome?.storage?.local?.set) return;
  await chrome.storage.local.set(items);
}

export async function loadProjects(): Promise<Project[]> {
  const raw = await getRaw<unknown[]>(PROJECTS_KEY);
  if (!Array.isArray(raw)) return [];
  const out: Project[] = [];
  for (const item of raw) {
    const parsed = ProjectSchema.safeParse(item);
    if (parsed.success) out.push(parsed.data);
  }
  return out;
}

export async function loadActiveProjectId(): Promise<string | null> {
  const id = await getRaw<string>(ACTIVE_KEY);
  return typeof id === 'string' && id ? id : null;
}

export async function loadActiveProject(): Promise<Project | null> {
  const id = await loadActiveProjectId();
  if (!id) return null;
  const projects = await loadProjects();
  return projects.find((p) => p.id === id) ?? null;
}

export async function saveProject(project: Project): Promise<void> {
  const projects = await loadProjects();
  const idx = projects.findIndex((p) => p.id === project.id);
  if (idx >= 0) projects[idx] = project;
  else projects.push(project);
  await setRaw({ [PROJECTS_KEY]: projects });
}

export async function setActiveProject(id: string | null): Promise<void> {
  // 同步把 active project 的 config 写到 vibe_niuma_config_v2 ——
  // 让 service worker + SettingsPanel 无感复用现有代码，不必改动它们的读路径。
  const items: Record<string, unknown> = { [ACTIVE_KEY]: id };
  if (id) {
    const projects = await loadProjects();
    const p = projects.find((x) => x.id === id);
    if (p) items[LEGACY_CONFIG_KEY] = p.config;
  }
  await setRaw(items);
}

export async function deleteProject(id: string): Promise<void> {
  const projects = (await loadProjects()).filter((p) => p.id !== id);
  await setRaw({ [PROJECTS_KEY]: projects });
  const active = await loadActiveProjectId();
  if (active === id) {
    await setActiveProject(projects[0]?.id ?? null);
  }
}

export async function createProject(name: string, config: Config): Promise<Project> {
  const project: Project = {
    id: newId(),
    name: name.trim() || '未命名项目',
    config,
    createdAt: Date.now(),
  };
  await saveProject(project);
  return project;
}

// 老 vibe_niuma_config_v2 → 默认项目（一次性，幂等）。返回是否触发了迁移。
export async function migrateLegacyConfig(): Promise<boolean> {
  const existing = await loadProjects();
  if (existing.length > 0) return false; // 已有项目，不迁移
  const legacyConfig = await getRaw<unknown>(LEGACY_CONFIG_KEY);
  if (!legacyConfig) return false;
  const parsed = ConfigSchema.safeParse(legacyConfig);
  if (!parsed.success) return false;
  const project = await createProject('默认项目', parsed.data);
  await setActiveProject(project.id);
  return true;
}
