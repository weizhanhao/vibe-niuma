import '@testing-library/jest-dom/vitest';
import { afterEach, beforeEach, vi } from 'vitest';

// 全局 chrome.* mock —— 各测试可以 vi.mocked(chrome.xxx).mockReturnValue(...) 覆盖
type Listener = (...args: unknown[]) => unknown;

interface FakeStorage {
  area: Record<string, unknown>;
}

const _storage: FakeStorage = { area: {} };
const _msgListeners: Listener[] = [];
const _runtimeListeners: Listener[] = [];

const fakeChrome = {
  runtime: {
    sendMessage: vi.fn((msg: unknown) => Promise.resolve(msg)),
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
        Object.assign(_storage.area, vals);
        return Promise.resolve();
      }),
      remove: vi.fn((k: string) => {
        delete _storage.area[k];
        return Promise.resolve();
      }),
      clear: vi.fn(() => {
        _storage.area = {};
        return Promise.resolve();
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
  storage: _storage,
  resetMessageListeners: () => {
    _msgListeners.length = 0;
  },
};

beforeEach(() => {
  _storage.area = {};
  _msgListeners.length = 0;
});

afterEach(() => {
  vi.clearAllMocks();
});
