// Phase G：service-worker 的 review 流。
// 关键不变量：
// - CAPTURE_RESULT 不再立刻 POST orchestrator；只暂存 pendingCapture。
// - CONFIRM_CAPTURE 才 POST，POST 完 pendingCapture 清空。
// - RETAKE_CAPTURE 直接清 pendingCapture，POST 没被调用。
// Plan 6 Task 10：service-worker 通过 getOrchestratorClient() → loadConfig 取 client；
// 测试 seed config 到 chrome.storage，mock createOrchestratorClient 返回 spy。
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Message } from '../src/lib/messages';
import type { ChangeRequestOut, PendingCapture } from '../src/lib/types';

const subscribeCalls: string[] = [];
const createCalls: unknown[] = [];

vi.mock('../src/background/orchestrator-client', () => ({
  createOrchestratorClient: vi.fn(() => ({
    baseUrl: 'http://test',
    subscribeEvents: vi.fn((id: string) => {
      subscribeCalls.push(id);
      return () => {};
    }),
    createChangeRequest: vi.fn(async (payload: unknown): Promise<ChangeRequestOut> => {
      createCalls.push(payload);
      return {
        id: 'cr-new',
        state: 'created',
        url: 'http://demo.local/orders',
        request_text: (payload as { request_text: string }).request_text,
        branch: null,
        preview_url: null,
        fail_phase: null,
        fail_reason: null,
        retry_of: null,
      };
    }),
    getChangeRequest: vi.fn(),
    submitAnswer: vi.fn(),
    merge: vi.fn(),
    discard: vi.fn(),
    retry: vi.fn(),
  })),
  HttpError: class HttpError extends Error {},
}));

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

async function importServiceWorker() {
  getBridge().resetMessageListeners();
  vi.resetModules();
  await import('../src/background/service-worker');
  // Plan 6：getOrchestratorClient → loadConfig 多了 await，flush 多轮 microtask
  for (let i = 0; i < 5; i++) {
    await new Promise((r) => setTimeout(r, 0));
  }
}

const captureResultMsg: Message = {
  type: 'CAPTURE_RESULT',
  url: 'http://demo.local/orders',
  boxCoords: { x: 10, y: 20, width: 100, height: 50 },
  viewport: { width: 1280, height: 800 },
};

beforeEach(async () => {
  subscribeCalls.length = 0;
  createCalls.length = 0;
  await seedConfig();
});

afterEach(() => {
  // 不动 vi.restoreAllMocks() —— 会撤销顶层 vi.mock；保留 mock 模块。
});

// [Plan 10 obsolete] Phase G 的 review-then-confirm 流被 cursor-like 的
// 「框选 → 直接进输入栏 chip → SUBMIT_MESSAGE」替代。pendingCapture 路径在 SW
// 里还存在（向后兼容 v0.5），但 UI 不再触发它。新流程覆盖见 service-worker-messages.test.ts.
describe.skip('service-worker Phase G review flow', () => {
  it('CAPTURE_RESULT does NOT POST orchestrator; stashes pendingCapture', async () => {
    await importServiceWorker();
    await send({ type: 'UI_START_CAPTURE', requestText: '改红色' });
    await send(captureResultMsg);

    expect(createCalls).toEqual([]);

    const pc = (await send({ type: 'GET_PENDING_CAPTURE' } as Message)) as PendingCapture | null;
    expect(pc).not.toBeNull();
    expect(pc!.url).toBe('http://demo.local/orders');
    expect(pc!.boxCoords).toEqual({ x: 10, y: 20, width: 100, height: 50 });
    expect(pc!.viewport).toEqual({ width: 1280, height: 800 });
    expect(pc!.requestText).toBe('改红色');
    // chrome.tabs.captureVisibleTab mock returns 'data:image/png;base64,FAKE' → 截掉 prefix
    expect(pc!.screenshotB64).toBe('FAKE');
  });

  it('CONFIRM_CAPTURE POSTs orchestrator and clears pendingCapture', async () => {
    await importServiceWorker();
    await send({ type: 'UI_START_CAPTURE', requestText: '改红色' });
    await send(captureResultMsg);
    expect(createCalls).toEqual([]);

    const reply = await send({ type: 'CONFIRM_CAPTURE', requestText: '改红色' } as Message) as
      { ok: boolean; id?: string };
    expect(reply.ok).toBe(true);
    expect(reply.id).toBe('cr-new');
    expect(createCalls.length).toBe(1);
    expect(createCalls[0]).toMatchObject({
      url: 'http://demo.local/orders',
      screenshot_b64: 'FAKE',
      box_coords: { x: 10, y: 20, width: 100, height: 50 },
      viewport: { width: 1280, height: 800 },
      request_text: '改红色',
    });

    const pc = await send({ type: 'GET_PENDING_CAPTURE' } as Message);
    expect(pc).toBeNull();
    // SSE 订阅启动
    expect(subscribeCalls).toContain('cr-new');
  });

  it('CONFIRM_CAPTURE uses requestText from UI (edited in review)', async () => {
    await importServiceWorker();
    await send({ type: 'UI_START_CAPTURE', requestText: '改红色' });
    await send(captureResultMsg);

    await send({ type: 'CONFIRM_CAPTURE', requestText: '改绿色' } as Message);
    expect(createCalls[0]).toMatchObject({ request_text: '改绿色' });
  });

  it('CONFIRM_CAPTURE without pendingCapture returns error, no POST', async () => {
    await importServiceWorker();
    const reply = await send({ type: 'CONFIRM_CAPTURE', requestText: 'x' } as Message) as
      { ok: boolean; error?: string };
    expect(reply.ok).toBe(false);
    expect(createCalls).toEqual([]);
  });

  it('RETAKE_CAPTURE clears pendingCapture, no POST', async () => {
    await importServiceWorker();
    await send({ type: 'UI_START_CAPTURE', requestText: '改红色' });
    await send(captureResultMsg);

    let pc = await send({ type: 'GET_PENDING_CAPTURE' } as Message);
    expect(pc).not.toBeNull();

    await send({ type: 'RETAKE_CAPTURE' } as Message);
    pc = await send({ type: 'GET_PENDING_CAPTURE' } as Message);
    expect(pc).toBeNull();
    expect(createCalls).toEqual([]);
  });
});
