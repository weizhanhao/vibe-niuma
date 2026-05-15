import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { getBaseUrl, HttpError, orchestratorClient, setBaseUrl } from '../src/background/orchestrator-client';

function mockFetch(impl: (input: RequestInfo, init?: RequestInit) => Promise<Response>) {
  const fetchMock = vi.fn(impl) as unknown as typeof fetch;
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

beforeEach(async () => {
  await setBaseUrl('http://orch.test:9000');
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('orchestratorClient — REST', () => {
  it('createChangeRequest POSTs payload to /change-requests', async () => {
    const captured: { url?: string; init?: RequestInit } = {};
    mockFetch(async (input, init) => {
      captured.url = String(input);
      captured.init = init;
      return new Response(JSON.stringify({
        id: 'r1', state: 'created', url: 'u', request_text: 't',
        branch: null, preview_url: null, fail_phase: null, fail_reason: null, retry_of: null,
      }), { status: 200 });
    });
    const cr = await orchestratorClient.createChangeRequest({
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
    await expect(orchestratorClient.getChangeRequest('x'))
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
    await orchestratorClient.submitAnswer('r1', 'q1', 'a');
    await orchestratorClient.merge('r1');
    await orchestratorClient.discard('r1');
    await orchestratorClient.retry('r1');
    expect(calls).toEqual([
      'POST http://orch.test:9000/change-requests/r1/answer',
      'POST http://orch.test:9000/change-requests/r1/merge',
      'POST http://orch.test:9000/change-requests/r1/discard',
      'POST http://orch.test:9000/change-requests/r1/retry',
    ]);
  });

  it('base url defaults to localhost when storage empty', async () => {
    await chrome.storage.local.clear();
    expect(await getBaseUrl()).toBe('http://localhost:9000');
  });
});
