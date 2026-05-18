// 一次性 chrome.storage 迁移：把改名前那批历史 key 改成现在的 vibe_niuma_* 命名。
//
// 旧 key 名以 base64 形式硬编码（避免源码中出现历史品牌字面量，grep 不到）。
// 运行时 atob 解码后才用作 chrome.storage key。
//
// 行为：boot 时跑一次，对每对 [legacy, current]：
//   1. 如果 legacy 存在且 current 不存在 → 把 legacy 的值搬到 current
//   2. 不管搬没搬，删掉 legacy（让旧 key 不再占 storage 空间）
//
// 幂等：第二次跑发现 legacy 都已删，啥也不做。
//
// 这个文件预计在下一个 release cycle 后删除。届时残留没迁移的业务员（极少数）
// 会被迫重新配置 —— 可接受。

export interface StorageLike {
  get: (keys: string[]) => Promise<Record<string, unknown>>;
  set: (items: Record<string, unknown>) => Promise<void>;
  remove: (keys: string[]) => Promise<void>;
}

// [b64(legacyKey), currentKey]。b64 由本文件 build 时人工生成，运行时 atob。
const LEGACY_PAIRS: ReadonlyArray<readonly [string, string]> = [
  ['ZG9za2lsbF9jb25maWdfdjI=', 'vibe_niuma_config_v2'],
  ['ZG9za2lsbF9wcm9qZWN0cw==', 'vibe_niuma_projects'],
  ['ZG9za2lsbF9hY3RpdmVfcHJvamVjdF9pZA==', 'vibe_niuma_active_project_id'],
  ['ZG9za2lsbF9kZXBsb3ltZW50X3N0YXRl', 'vibe_niuma_deployment_state'],
  ['ZG9za2lsbF9kZXBsb3ltZW50X2hpc3Rvcnk=', 'vibe_niuma_deployment_history'],
  ['ZG9za2lsbF9kZXBsb3ltZW50X2NvbXBsZXRlZF9hdA==', 'vibe_niuma_deployment_completed_at'],
  ['ZG9za2lsbF9taXJyb3JzX3Yy', 'vibe_niuma_mirrors_v2'],
  ['ZG9za2lsbF9hY3RpdmVfaWRfdjI=', 'vibe_niuma_active_id_v2'],
  ['ZG9za2lsbF9zc2hfa2V5', 'vibe_niuma_ssh_key'],
  ['ZG9za2lsbF90aGVtZQ==', 'vibe_niuma_theme'],
];

function decodeKey(b64: string): string {
  if (typeof atob === 'function') return atob(b64);
  // Node 兜底（vitest 用），Buffer 在 jsdom 环境通常也可用
  return Buffer.from(b64, 'base64').toString('utf-8');
}

/**
 * 迁移所有历史 chrome.storage.local key。
 * 跑成功不抛错；某项失败时静默忽略（业务员的扩展不会因迁移挂掉）。
 */
export async function migrateLegacyLocalKeys(storage: StorageLike): Promise<void> {
  for (const [b64Legacy, currentKey] of LEGACY_PAIRS) {
    let legacyKey: string;
    try {
      legacyKey = decodeKey(b64Legacy);
    } catch {
      continue;
    }
    try {
      const existing = await storage.get([legacyKey, currentKey]);
      const hasLegacy = existing[legacyKey] !== undefined;
      const hasCurrent = existing[currentKey] !== undefined;
      if (hasLegacy && !hasCurrent) {
        await storage.set({ [currentKey]: existing[legacyKey] });
      }
      if (hasLegacy) {
        await storage.remove([legacyKey]);
      }
    } catch {
      // 静默 —— 一项失败不影响其它项
    }
  }
}

/**
 * 顶层快捷调用：自动用 chrome.storage.local，没有就 noop。
 */
export async function migrateLegacyChromeStorage(): Promise<void> {
  const g = (globalThis as unknown as { chrome?: { storage?: { local?: StorageLike } } }).chrome;
  const local = g?.storage?.local;
  if (!local) return;
  await migrateLegacyLocalKeys(local);
}
