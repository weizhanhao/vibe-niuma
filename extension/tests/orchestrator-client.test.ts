// Plan 6 Task 10：orchestrator-client 从 module-level singleton 改成 factory。
// 测试覆盖：
//   - createOrchestratorClient(baseUrl) 返回的 client.baseUrl 是传入值
//   - REST 方法走 baseUrl + path，body / method 正确
//   - 非 2xx 抛 HttpError
//   - 多个 createOrchestratorClient 调用返回独立实例（之前 setBaseUrl 是全局 mutate，现在是值绑定）
import { afterEach, describe, expect, it, vi } from 'vitest';
import { createOrchestratorClient, HttpError } from '../src/background/orchestrator-client';

function mockFetch(impl: (input: RequestInfo, init?: RequestInit) => Promise<Response>) {
  const fetchMock = vi.fn(impl) as unknown as typeof fetch;
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('createOrchestratorClient — factory', () => {
  it('exposes the baseUrl passed in', () => {
    const client = createOrchestratorClient('http://orch.test:9000');
    expect(client.baseUrl).toBe('http://orch.test:9000');
  });

  it('createChangeRequest POSTs payload to baseUrl + /change-requests', async () => {
    const captured: { url?: string; init?: RequestInit } = {};
    mockFetch(async (input, init) => {
      captured.url = String(input);
      captured.init = init;
      return new Response(JSON.stringify({
        id: 'r1', state: 'created', url: 'u', request_text: 't',
        branch: null, preview_url: null, fail_phase: null, fail_reason: null, retry_of: null,
      }), { status: 200 });
    });
    const client = createOrchestratorClient('http://orch.test:9000');
    const cr = await client.createChangeRequest({
      url: 'http://x/orders', screenshot_b64: 'b64',
      box_coords: { x: 1, y: 2, width: 3, height: 4 },
      viewport: { width: 1280, height: 800 },
      request_text: 'hi',
    });
    expect(cr.id).toBe('r1');
    expect(captured.url).toBe('http://orch.test:9000/change-requests');
    expect(captured.init?.method).toBe('POST');
    const body = JSON.parse(captured.init!.body as string);
    expect(body.request_text).toBe('hi');
  });

  it('throws HttpError on non-2xx', async () => {
    mockFetch(async () => new Response('boom', { status: 500 }));
    const client = createOrchestratorClient('http://orch.test:9000');
    await expect(client.getChangeRequest('x'))
      .rejects.toBeInstanceOf(HttpError);
  });

  it('submitAnswer / merge / discard / retry hit correct endpoints', async () => {
    const calls: string[] = [];
    mockFetch(async (input, init) => {
      calls.push(`${init?.method ?? 'GET'} ${String(input)}`);
      return new Response(JSON.stringify({
        id: 'r1', state: 'merged', url: '', request_text: '', branch: null,
        preview_url: null, fail_phase: null, fail_reason: null, retry_of: null,
      }), { status: 200 });
    });
    const client = createOrchestratorClient('http://orch.test:9000');
    await client.submitAnswer('r1', 'q1', 'a');
    await client.merge('r1');
    await client.discard('r1');
    await client.retry('r1');
    expect(calls).toEqual([
      'POST http://orch.test:9000/change-requests/r1/answer',
      'POST http://orch.test:9000/change-requests/r1/merge',
      'POST http://orch.test:9000/change-requests/r1/discard',
      'POST http://orch.test:9000/change-requests/r1/retry',
    ]);
  });

  it('two factory calls produce independent clients with different baseUrls', async () => {
    const urls: string[] = [];
    mockFetch(async (input) => {
      urls.push(String(input));
      return new Response(JSON.stringify({
        id: 'r1', state: 'created', url: '', request_text: '', branch: null,
        preview_url: null, fail_phase: null, fail_reason: null, retry_of: null,
      }), { status: 200 });
    });
    const a = createOrchestratorClient('http://a.test:9000');
    const b = createOrchestratorClient('http://b.test:9000');
    expect(a.baseUrl).toBe('http://a.test:9000');
    expect(b.baseUrl).toBe('http://b.test:9000');
    await a.getChangeRequest('r1');
    await b.getChangeRequest('r1');
    expect(urls).toEqual([
      'http://a.test:9000/change-requests/r1',
      'http://b.test:9000/change-requests/r1',
    ]);
  });
});
