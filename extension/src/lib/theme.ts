// 主题切换（light 默认 / dark 可选）。
//
// 存储策略：
//   - 主存：`chrome.storage.local['vibe_niuma_theme']`（跨 panel session 同步）
//   - 镜像：`localStorage['vibe_niuma_theme']`（同步可读，避免 React 挂载前一闪）
//
// 调用方：
//   - `ui-entry.tsx` 启动时同步读 localStorage、立刻给 body 加 class
//   - `App.tsx` mount 后用 `loadTheme()` 校正一次（万一别处 panel 改过）

export type Theme = 'light' | 'dark';

const STORAGE_KEY = 'vibe_niuma_theme';
const DEFAULT_THEME: Theme = 'light';

function isTheme(v: unknown): v is Theme {
  return v === 'light' || v === 'dark';
}

// 同步读，给 ui-entry 用 —— 没初始化过就返回默认 light
export function readThemeSync(): Theme {
  try {
    const v = typeof localStorage !== 'undefined' ? localStorage.getItem(STORAGE_KEY) : null;
    return isTheme(v) ? v : DEFAULT_THEME;
  } catch {
    return DEFAULT_THEME;
  }
}

export function applyTheme(t: Theme): void {
  if (typeof document === 'undefined') return;
  const cls = document.body.classList;
  cls.remove('theme-light', 'theme-dark');
  cls.add(`theme-${t}`);
}

export async function loadTheme(): Promise<Theme> {
  // chrome.storage 在 jsdom 测试里可能不存在 —— optional chain 兜底
  const getter = chrome?.storage?.local?.get?.bind(chrome.storage.local);
  if (!getter) return readThemeSync();
  return new Promise<Theme>((resolve) => {
    getter([STORAGE_KEY], (out: Record<string, unknown>) => {
      const v = out?.[STORAGE_KEY];
      resolve(isTheme(v) ? v : readThemeSync());
    });
  });
}

export async function saveTheme(t: Theme): Promise<void> {
  applyTheme(t);
  try {
    if (typeof localStorage !== 'undefined') localStorage.setItem(STORAGE_KEY, t);
  } catch { /* private mode 等 quota 错；不致命 */ }
  const setter = chrome?.storage?.local?.set?.bind(chrome.storage.local);
  if (!setter) return;
  return new Promise<void>((resolve) => {
    setter({ [STORAGE_KEY]: t }, () => resolve());
  });
}
