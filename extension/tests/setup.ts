import '@testing-library/jest-dom/vitest';
import { afterEach, beforeEach, vi } from 'vitest';

// 全局 chrome.* mock —— 各测试可以 vi.mocked(chrome.xxx).mockReturnValue(...) 覆盖
type Listener = (...args: unknown[]) => unknown;
type StorageChangedListener = (
  changes: Record<string, { oldValue?: unknown; newValue?: unknown }>,
  areaName: string,
) => unknown;

interface FakeStorage {
  area: Record<string, unknown>;
}

const _storage: FakeStorage = { area: {} };
const _msgListeners: Listener[] = [];
const _runtimeListeners: Listener[] = [];
const _storageChangeListeners: StorageChangedListener[] = [];

function fireStorageChange(
  changes: Record<string, { oldValue?: unknown; newValue?: unknown }>,
) {
  for (const l of _storageChangeListeners) {
    l(changes, 'local');
  }
}

const fakeChrome = {
  runtime: {
    // 默认 echo 回 msg，方便单测断言；某些 type（GET_*）返回 null 更安全，
    // 避免 App.tsx 里 hooks 拿到 echo 的 msg 误判为有 pendingCapture / mirrors。
    sendMessage: vi.fn((msg: unknown) => {
      const t = (msg as { type?: string } | undefined)?.type;
      if (t === 'GET_PENDING_CAPTURE') return Promise.resolve(null);
      if (t === 'GET_MIRRORS') return Promise.resolve({ mirrors: {}, activeId: null });
      if (t === 'GET_REQUEST_STATE') return Promise.resolve(null);
      return Promise.resolve(msg);
    }),
    onMessage: {
      addListener: vi.fn((cb: Listener) => _msgListeners.push(cb)),
      removeListener: vi.fn(),
    },
    onInstalled: { addListener: vi.fn((cb: Listener) => _runtimeListeners.push(cb)) },
    lastError: undefined as undefined | { message: string },
  },
  tabs: {
    query: vi.fn(() =>
      Promise.resolve([{ id: 1, url: 'http://demo.local/orders', windowId: 1 }]),
    ),
    sendMessage: vi.fn(() => Promise.resolve()),
    captureVisibleTab: vi.fn(() =>
      Promise.resolve('data:image/png;base64,FAKE'),
    ),
    create: vi.fn(),
  },
  storage: {
    local: {
      get: vi.fn((key?: string | string[] | Record<string, unknown> | null) => {
        if (key === undefined || key === null) return Promise.resolve(_storage.area);
        if (typeof key === 'string') return Promise.resolve({ [key]: _storage.area[key] });
        if (Array.isArray(key)) {
          const out: Record<string, unknown> = {};
          for (const k of key) out[k] = _storage.area[k];
          return Promise.resolve(out);
        }
        const out: Record<string, unknown> = {};
        for (const k of Object.keys(key)) out[k] = _storage.area[k] ?? key[k];
        return Promise.resolve(out);
      }),
      set: vi.fn((vals: Record<string, unknown>) => {
        // 真实 chrome.storage.set 会触发 onChanged；Plan 6 Task 10 测试依赖这一点
        // 来验证 App.tsx 在 saveConfig 后立即重新路由。
        const changes: Record<string, { oldValue?: unknown; newValue?: unknown }> = {};
        for (const k of Object.keys(vals)) {
          changes[k] = { oldValue: _storage.area[k], newValue: vals[k] };
        }
        Object.assign(_storage.area, vals);
        fireStorageChange(changes);
        return Promise.resolve();
      }),
      remove: vi.fn((k: string) => {
        const oldValue = _storage.area[k];
        delete _storage.area[k];
        fireStorageChange({ [k]: { oldValue, newValue: undefined } });
        return Promise.resolve();
      }),
      clear: vi.fn(() => {
        _storage.area = {};
        return Promise.resolve();
      }),
    },
    // Plan 6 Task 10：chrome.storage.onChanged 监听器注册 + 触发桥
    onChanged: {
      addListener: vi.fn((cb: StorageChangedListener) => _storageChangeListeners.push(cb)),
      removeListener: vi.fn((cb: StorageChangedListener) => {
        const idx = _storageChangeListeners.indexOf(cb);
        if (idx >= 0) _storageChangeListeners.splice(idx, 1);
      }),
    },
  },
  sidePanel: { open: vi.fn(), setOptions: vi.fn() },
};

(globalThis as unknown as { chrome: typeof fakeChrome }).chrome = fakeChrome;

(globalThis as unknown as { __fakeChrome: unknown }).__fakeChrome = {
  fireMessage: (msg: unknown, sender: unknown = {}, sendResponse: Listener = () => {}) => {
    for (const l of _msgListeners) {
      l(msg, sender, sendResponse);
    }
  },
  // Plan 6 Task 10：测试可显式触发 storage.onChanged（mock 没经过 set 的路径时用）
  fireStorageChange,
  storage: _storage,
  resetMessageListeners: () => {
    _msgListeners.length = 0;
    _storageChangeListeners.length = 0;
  },
};

beforeEach(() => {
  _storage.area = {};
  _msgListeners.length = 0;
  _storageChangeListeners.length = 0;
});

afterEach(() => {
  vi.clearAllMocks();
});
