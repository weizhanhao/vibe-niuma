// Plan 10 Task 13: SW 新增 SUBMIT_MESSAGE → 走 /conversations/{id}/messages.
//
// 业务员视角：MainShell 的输入框 ENTER → SW 拿当前 active conversation_id +
// 当前 attachments tray → POST /messages → 按 server 返的 mode 处理：
//   - chat_only：append ai message 到当前 conv（UI 自己 refresh），不新增 mirror
//   - new_cr / refine_cr：拉 CR snapshot 起 mirror、订阅 SSE
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const postMessageMock = vi.fn();
const getChangeRequestMock = vi.fn();
const subscribeEventsMock = vi.fn(() => () => {});

vi.mock('../src/background/orchestrator-client', () => ({
  createOrchestratorClient: vi.fn(() => ({
    baseUrl: 'http://test',
    postMessage: postMessageMock,
    getChangeRequest: getChangeRequestMock,
    subscribeEvents: subscribeEventsMock,
    createChangeRequest: vi.fn(),
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
    demoRepoPath: '/opt/vibe-niuma/demo',
    previewBackendUrl: 'http://vibe-niuma-demo-backend:8000',
  },
};

async function seedConfig() {
  await chrome.storage.local.set({ vibe_niuma_config_v2: VALID_CONFIG });
}

interface FakeBridge {
  fireMessage: (msg: unknown, sender: unknown, sendResponse: (reply?: unknown) => void) => void;
  resetMessageListeners: () => void;
}
function bridge(): FakeBridge {
  return (globalThis as unknown as { __fakeChrome: FakeBridge }).__fakeChrome;
}

async function fireMsg(m: unknown): Promise<unknown> {
  return new Promise((resolve) => {
    bridge().fireMessage(m, {}, (reply: unknown) => resolve(reply));
  });
}

async function importSW() {
  bridge().resetMessageListeners();
  vi.resetModules();
  await import('../src/background/service-worker');
  for (let i = 0; i < 5; i++) {
    await new Promise((r) => setTimeout(r, 0));
  }
}

beforeEach(async () => {
  postMessageMock.mockReset();
  getChangeRequestMock.mockReset();
  subscribeEventsMock.mockClear();
  await chrome.storage.local.clear();
  await seedConfig();
});

describe('SUBMIT_MESSAGE → /messages routing', () => {
  it('SET_CONVERSATION + SUBMIT_MESSAGE calls client.postMessage with convId + body', async () => {
    postMessageMock.mockResolvedValue({
      message_id: 'm1', mode: 'new_cr', cr_id: 'cr1', ai_message_id: null,
      confidence: 0.9, is_unsure: false, reason: 'ok',
    });
    getChangeRequestMock.mockResolvedValue({
      id: 'cr1', state: 'clarifying', url: 'http://x', request_text: '加搜索',
      branch: null, preview_url: null, fail_phase: null, fail_reason: null,
      retry_of: null,
    });
    await importSW();

    await fireMsg({ type: 'SET_CONVERSATION', id: 'conv-abc' });
    const resp = await fireMsg({
      type: 'SUBMIT_MESSAGE',
      text: '加个搜索',
      attachments: [
        { kind: 'pasted_image', mime: 'image/png', b64: 'AAA' },
      ],
    }) as { ok: boolean; mode?: string; cr_id?: string };

    expect(postMessageMock).toHaveBeenCalledTimes(1);
    const [convId, body] = postMessageMock.mock.calls[0];
    expect(convId).toBe('conv-abc');
    expect(body.text).toBe('加个搜索');
    expect(body.attachments).toHaveLength(1);
    expect(body.attachments[0].kind).toBe('pasted_image');
    expect(resp.ok).toBe(true);
    expect(resp.mode).toBe('new_cr');
    expect(resp.cr_id).toBe('cr1');
  });

  it('chat_only response → no new mirror added', async () => {
    postMessageMock.mockResolvedValue({
      message_id: 'm', mode: 'chat_only', cr_id: null, ai_message_id: 'ai1',
      confidence: 0.85, is_unsure: false, reason: '疑问',
    });
    await importSW();

    await fireMsg({ type: 'SET_CONVERSATION', id: 'conv-x' });
    const resp = await fireMsg({
      type: 'SUBMIT_MESSAGE', text: '你觉得怎么样？',
    }) as { ok: boolean; mode: string; cr_id?: string };

    expect(resp.mode).toBe('chat_only');
    expect(resp.cr_id ?? null).toBeNull();
    expect(getChangeRequestMock).not.toHaveBeenCalled();
    expect(subscribeEventsMock).not.toHaveBeenCalled();
  });

  it('new_cr response → bootstraps mirror + subscribes SSE', async () => {
    postMessageMock.mockResolvedValue({
      message_id: 'm', mode: 'new_cr', cr_id: 'cr-99', ai_message_id: null,
      confidence: 0.9, is_unsure: false, reason: 'new',
    });
    getChangeRequestMock.mockResolvedValue({
      id: 'cr-99', state: 'clarifying', url: 'http://demo', request_text: '加搜索',
      branch: null, preview_url: null, fail_phase: null, fail_reason: null,
      retry_of: null,
    });
    await importSW();

    await fireMsg({ type: 'SET_CONVERSATION', id: 'conv-y' });
    await fireMsg({ type: 'SUBMIT_MESSAGE', text: '加搜索' });

    expect(getChangeRequestMock).toHaveBeenCalledWith('cr-99');
    expect(subscribeEventsMock).toHaveBeenCalled();
  });

  it('SUBMIT_MESSAGE without active conversation returns error', async () => {
    await importSW();

    const resp = await fireMsg({
      type: 'SUBMIT_MESSAGE', text: '?',
    }) as { ok: boolean; error?: string };
    expect(resp.ok).toBe(false);
    expect(resp.error).toMatch(/对话|conversation/i);
    expect(postMessageMock).not.toHaveBeenCalled();
  });

  it('SUBMIT_MESSAGE with conversation_id in payload bypasses session state', async () => {
    postMessageMock.mockResolvedValue({
      message_id: 'm', mode: 'chat_only', cr_id: null, ai_message_id: 'ai1',
      confidence: 0.9, is_unsure: false, reason: 'ok',
    });
    await importSW();

    // 没 SET_CONVERSATION，直接发 SUBMIT_MESSAGE 携 convId
    const resp = await fireMsg({
      type: 'SUBMIT_MESSAGE', text: 'hi',
      conversation_id: 'inline-conv-123',
    }) as { ok: boolean };
    expect(resp.ok).toBe(true);
    expect(postMessageMock).toHaveBeenCalledWith('inline-conv-123', expect.any(Object));
  });

  it('override_mode forwarded to postMessage body', async () => {
    postMessageMock.mockResolvedValue({
      message_id: 'm', mode: 'new_cr', cr_id: 'cr1',
      confidence: 1, is_unsure: false, reason: 'forced',
    });
    getChangeRequestMock.mockResolvedValue({
      id: 'cr1', state: 'clarifying', url: '', request_text: '',
      branch: null, preview_url: null, fail_phase: null, fail_reason: null,
      retry_of: null,
    });
    await importSW();

    await fireMsg({ type: 'SET_CONVERSATION', id: 'conv-z' });
    await fireMsg({
      type: 'SUBMIT_MESSAGE', text: '字号大一点', override_mode: 'new_cr',
    });
    const [, body] = postMessageMock.mock.calls[0];
    expect(body.override_mode).toBe('new_cr');
  });
});
