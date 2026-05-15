// Phase E：service-worker 多对话编排——SET_ACTIVE、DELETE_CONVERSATION、NEW_CONVERSATION。
// 通过 fireMessage 桥触发 onMessage 回调，断言 session 内部状态 + orchestrator client 订阅切换。
// Plan 6 Task 10：service-worker 现在通过 getOrchestratorClient() 异步取 client（读 chrome.storage）。
// 本测试在 beforeEach 里 seed config 到 storage，并 mock createOrchestratorClient 返回 spy。
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Message } from '../src/lib/messages';
import type { RequestStateMirror } from '../src/lib/types';
import { saveMirrors } from '../src/background/request-store';

const subscribeCalls: string[] = [];
let unsubCalls = 0;

// Mock 工厂：service-worker 内部 createOrchestratorClient 走这里。
vi.mock('../src/background/orchestrator-client', () => ({
  createOrchestratorClient: vi.fn(() => ({
    baseUrl: 'http://test',
    subscribeEvents: vi.fn((id: string) => {
      subscribeCalls.push(id);
      return () => { unsubCalls += 1; };
    }),
    createChangeRequest: vi.fn(),
    getChangeRequest: vi.fn(),
    submitAnswer: vi.fn(),
    merge: vi.fn(),
    discard: vi.fn(),
    retry: vi.fn(),
  })),
  HttpError: class HttpError extends Error {},
}));

// 一份能过 zod schema 的最小 config（orchestratorUrl URL + adminToken >= 20）
const VALID_CONFIG = {
  orchestratorUrl: 'http://test',
  adminToken: 'admin-token-1234567890abcdef',
  configVersion: 0,
  server: {
    devRunner: 'opencode',
    devModel: 'deepseek/deepseek-v4-flash',
    visionModel: 'qwen-vl-plus',
    demoRepoPath: '/opt/doskill/demo',
    previewBackendUrl: 'http://doskill-demo-backend:8000',
  },
};

async function seedConfig() {
  await chrome.storage.local.set({ doskill_config_v2: VALID_CONFIG });
}

// ── 工具 ────────────────────────────────────────────────────────────

function mkMirror(id: string, opts: Partial<RequestStateMirror> = {}): RequestStateMirror {
  return {
    id,
    state: 'coding',
    url: 'http://demo/orders',
    requestText: `需求 ${id}`,
    branch: null,
    previewUrl: null,
    failPhase: null,
    failReason: null,
    pendingQuestion: null,
    pendingVariants: null,
    logs: [],
    lastActivity: '2026-05-15T10:00:00.000Z',
    ...opts,
  };
}

interface FakeBridge {
  fireMessage: (msg: unknown, sender: unknown, sendResponse: (reply?: unknown) => void) => void;
  resetMessageListeners: () => void;
}

function getBridge(): FakeBridge {
  return (globalThis as unknown as { __fakeChrome: FakeBridge }).__fakeChrome;
}

async function send(msg: Message): Promise<unknown> {
  return await new Promise<unknown>((resolve) => {
    getBridge().fireMessage(msg, {}, (reply: unknown) => resolve(reply));
  });
}

// service-worker 模块每次 dynamic import 触发 top-level loadAll() —— 需要等 microtask 推进。
// Plan 6 Task 10：attachSubscription 现在 async（await getOrchestratorClient → loadConfig），
// 额外多 2 个 await，需要多 flush 几轮。
async function importServiceWorker() {
  getBridge().resetMessageListeners();
  vi.resetModules();
  await import('../src/background/service-worker');
  for (let i = 0; i < 5; i++) {
    await new Promise((r) => setTimeout(r, 0));
  }
}

// ── 测试 ────────────────────────────────────────────────────────────

beforeEach(async () => {
  subscribeCalls.length = 0;
  unsubCalls = 0;
  await seedConfig();
});

afterEach(() => {
  // 不动 vi.restoreAllMocks() —— 会撤销顶层 vi.mock；保留 mock 模块。
});

describe('service-worker multi-mirror', () => {
  it('GET_MIRRORS returns mirrors + activeId from storage on boot', async () => {
    await saveMirrors({ r1: mkMirror('r1'), r2: mkMirror('r2') }, 'r2');
    await importServiceWorker();
    const reply = (await send({ type: 'GET_MIRRORS' } as Message)) as
      { mirrors: Record<string, RequestStateMirror>; activeId: string | null };
    expect(reply.activeId).toBe('r2');
    expect(Object.keys(reply.mirrors).sort()).toEqual(['r1', 'r2']);
  });

  it('subscribes to active mirror on boot if non-terminal', async () => {
    await saveMirrors({ r1: mkMirror('r1', { state: 'coding' }) }, 'r1');
    await importServiceWorker();
    expect(subscribeCalls).toContain('r1');
  });

  it('does NOT subscribe to active mirror on boot if terminal', async () => {
    await saveMirrors({ r1: mkMirror('r1', { state: 'merged' }) }, 'r1');
    await importServiceWorker();
    expect(subscribeCalls).toEqual([]);
  });

  it('SET_ACTIVE switches subscription: unsubs old, subs new', async () => {
    await saveMirrors({
      r1: mkMirror('r1', { state: 'coding' }),
      r2: mkMirror('r2', { state: 'coding' }),
    }, 'r1');
    await importServiceWorker();
    expect(subscribeCalls).toEqual(['r1']);

    await send({ type: 'SET_ACTIVE', id: 'r2' } as Message);
    expect(unsubCalls).toBe(1);
    expect(subscribeCalls).toEqual(['r1', 'r2']);

    const state = await send({ type: 'GET_REQUEST_STATE' } as Message) as RequestStateMirror;
    expect(state.id).toBe('r2');
  });

  it('SET_ACTIVE to null detaches subscription, GET_REQUEST_STATE returns null', async () => {
    await saveMirrors({ r1: mkMirror('r1', { state: 'coding' }) }, 'r1');
    await importServiceWorker();
    expect(subscribeCalls).toEqual(['r1']);

    await send({ type: 'SET_ACTIVE', id: null } as Message);
    expect(unsubCalls).toBe(1);
    expect(await send({ type: 'GET_REQUEST_STATE' } as Message)).toBeNull();
  });

  it('NEW_CONVERSATION sets active to null without touching mirrors map', async () => {
    await saveMirrors({ r1: mkMirror('r1', { state: 'coding' }) }, 'r1');
    await importServiceWorker();

    await send({ type: 'NEW_CONVERSATION' } as Message);
    const reply = await send({ type: 'GET_MIRRORS' } as Message) as
      { mirrors: Record<string, RequestStateMirror>; activeId: string | null };
    expect(reply.activeId).toBeNull();
    expect(reply.mirrors.r1).toBeDefined();
  });

  it('DELETE_CONVERSATION on active mirror auto-switches to next in-flight', async () => {
    await saveMirrors({
      r1: mkMirror('r1', { state: 'coding', lastActivity: '2026-05-15T10:00:00.000Z' }),
      r2: mkMirror('r2', { state: 'merged', lastActivity: '2026-05-15T11:00:00.000Z' }),
      r3: mkMirror('r3', { state: 'clarifying', lastActivity: '2026-05-15T09:00:00.000Z' }),
    }, 'r1');
    await importServiceWorker();
    expect(subscribeCalls).toEqual(['r1']);

    await send({ type: 'DELETE_CONVERSATION', id: 'r1' } as Message);
    // r1 deleted, expect active → r3（剩下的非终态里最近的）
    const reply = await send({ type: 'GET_MIRRORS' } as Message) as
      { mirrors: Record<string, RequestStateMirror>; activeId: string | null };
    expect(reply.mirrors.r1).toBeUndefined();
    expect(reply.activeId).toBe('r3');
    expect(subscribeCalls).toContain('r3');
  });

  it('DELETE_CONVERSATION on active mirror with only terminal left falls back to latest', async () => {
    await saveMirrors({
      r1: mkMirror('r1', { state: 'coding', lastActivity: '2026-05-15T10:00:00.000Z' }),
      r2: mkMirror('r2', { state: 'merged', lastActivity: '2026-05-15T11:00:00.000Z' }),
    }, 'r1');
    await importServiceWorker();
    await send({ type: 'DELETE_CONVERSATION', id: 'r1' } as Message);
    const reply = await send({ type: 'GET_MIRRORS' } as Message) as
      { activeId: string | null; mirrors: Record<string, RequestStateMirror> };
    expect(reply.activeId).toBe('r2');
  });

  it('DELETE_CONVERSATION on last remaining mirror sets active to null', async () => {
    await saveMirrors({ r1: mkMirror('r1', { state: 'coding' }) }, 'r1');
    await importServiceWorker();
    await send({ type: 'DELETE_CONVERSATION', id: 'r1' } as Message);
    const reply = await send({ type: 'GET_MIRRORS' } as Message) as
      { activeId: string | null; mirrors: Record<string, RequestStateMirror> };
    expect(reply.activeId).toBeNull();
    expect(Object.keys(reply.mirrors)).toEqual([]);
  });

  it('DELETE_CONVERSATION on non-active mirror keeps active unchanged', async () => {
    await saveMirrors({
      r1: mkMirror('r1', { state: 'coding' }),
      r2: mkMirror('r2', { state: 'merged' }),
    }, 'r1');
    await importServiceWorker();
    const before = subscribeCalls.length;

    await send({ type: 'DELETE_CONVERSATION', id: 'r2' } as Message);
    const reply = await send({ type: 'GET_MIRRORS' } as Message) as
      { activeId: string | null; mirrors: Record<string, RequestStateMirror> };
    expect(reply.activeId).toBe('r1');
    expect(reply.mirrors.r2).toBeUndefined();
    // 没有新订阅
    expect(subscribeCalls.length).toBe(before);
  });
});
