/**
 * Tests for DeepSeekClient — SSE streaming, error handling, retry, abort.
 * Uses vi.stubGlobal('fetch', ...) since msw is not in devDeps.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  ChatMessage,
  DeepSeekAuthError,
  DeepSeekClient,
  DeepSeekClientError,
} from '../src/ai/DeepSeekClient';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Builds a ReadableStream that emits SSE lines and ends with [DONE]. */
function makeSseStream(chunks: Array<{ content: string } | '[DONE]'>): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  const lines: string[] = [];
  for (const chunk of chunks) {
    if (chunk === '[DONE]') {
      lines.push('data: [DONE]\n\n');
    } else {
      const payload = JSON.stringify({
        choices: [{ delta: { content: chunk.content } }],
      });
      lines.push(`data: ${payload}\n\n`);
    }
  }
  const text = lines.join('');
  return new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(encoder.encode(text));
      controller.close();
    },
  });
}

/** Creates a mock fetch that returns a streaming SSE response. */
function stubSseResponse(
  chunks: Array<{ content: string } | '[DONE]'>,
  status = 200,
) {
  const fetchMock = vi.fn(async () =>
    new Response(makeSseStream(chunks), {
      status,
      headers: { 'content-type': 'text/event-stream' },
    }),
  ) as unknown as typeof fetch;
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

/** Creates a mock fetch that returns a plain error response. */
function stubErrorResponse(status: number, body = 'error') {
  const fetchMock = vi.fn(async () => new Response(body, { status })) as unknown as typeof fetch;
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const messages: ChatMessage[] = [{ role: 'user', content: 'hi' }];

// ---------------------------------------------------------------------------
// Suite 1 — Happy path: SSE stream yields chunks then [DONE]
// ---------------------------------------------------------------------------

describe('DeepSeekClient — happy path', () => {
  it('yields delta.content from each SSE chunk and stops at [DONE]', async () => {
    stubSseResponse([
      { content: 'Hello' },
      { content: ', world' },
      { content: '!' },
      '[DONE]',
    ]);

    const client = new DeepSeekClient({ apiKey: 'sk-test' });
    const collected: string[] = [];

    for await (const token of client.chat(messages)) {
      collected.push(token);
    }

    expect(collected).toEqual(['Hello', ', world', '!']);
  });

  it('sends POST to correct endpoint with Authorization header and stream:true', async () => {
    const fetchMock = stubSseResponse([{ content: 'ok' }, '[DONE]']);

    const client = new DeepSeekClient({ apiKey: 'sk-abc', model: 'deepseek-coder' });
    // consume generator
    for await (const _ of client.chat(messages)) { /* drain */ }

    expect(fetchMock).toHaveBeenCalledOnce();
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('https://api.deepseek.com/v1/chat/completions');
    expect((init.headers as Record<string, string>)['Authorization']).toBe('Bearer sk-abc');
    expect(JSON.parse(init.body as string)).toMatchObject({
      model: 'deepseek-coder',
      stream: true,
      messages,
    });
  });

  it('uses custom baseUrl when provided', async () => {
    const fetchMock = stubSseResponse([{ content: 'x' }, '[DONE]']);

    const client = new DeepSeekClient({ apiKey: 'sk-x', baseUrl: 'http://localhost:8080/v1' });
    for await (const _ of client.chat(messages)) { /* drain */ }

    const [url] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('http://localhost:8080/v1/chat/completions');
  });

  it('skips SSE chunks where delta.content is empty or absent', async () => {
    const encoder = new TextEncoder();
    // Some chunks have empty content (role-only deltas at stream start)
    const raw = [
      `data: ${JSON.stringify({ choices: [{ delta: { role: 'assistant' } }] })}\n\n`,
      `data: ${JSON.stringify({ choices: [{ delta: { content: '' } }] })}\n\n`,
      `data: ${JSON.stringify({ choices: [{ delta: { content: 'hi' } }] })}\n\n`,
      `data: [DONE]\n\n`,
    ].join('');
    vi.stubGlobal('fetch', vi.fn(async () =>
      new Response(
        new ReadableStream<Uint8Array>({ start(c) { c.enqueue(encoder.encode(raw)); c.close(); } }),
        { status: 200, headers: { 'content-type': 'text/event-stream' } },
      ),
    ));

    const client = new DeepSeekClient({ apiKey: 'sk-test' });
    const tokens: string[] = [];
    for await (const t of client.chat(messages)) tokens.push(t);
    expect(tokens).toEqual(['hi']);
  });

  it('uses default model deepseek-chat when not specified', async () => {
    const fetchMock = stubSseResponse([{ content: 'ok' }, '[DONE]']);
    const client = new DeepSeekClient({ apiKey: 'sk-test' });
    for await (const _ of client.chat(messages)) { /* drain */ }
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(init.body as string).model).toBe('deepseek-chat');
  });
});

// ---------------------------------------------------------------------------
// Suite 2 — Error handling: 401, 429 retry, other 4xx/5xx
// ---------------------------------------------------------------------------

describe('DeepSeekClient — error handling', () => {
  it('throws DeepSeekAuthError on 401', async () => {
    stubErrorResponse(401);

    const client = new DeepSeekClient({ apiKey: 'bad-key' });
    await expect(async () => {
      for await (const _ of client.chat(messages)) { /* drain */ }
    }).rejects.toBeInstanceOf(DeepSeekAuthError);
  });

  it('DeepSeekAuthError message mentions invalid api key', async () => {
    stubErrorResponse(401);

    const client = new DeepSeekClient({ apiKey: 'bad-key' });
    let caught: unknown;
    try {
      for await (const _ of client.chat(messages)) { /* drain */ }
    } catch (e) {
      caught = e;
    }
    expect((caught as DeepSeekAuthError).message).toMatch(/invalid api key/i);
  });

  it('retries on 429 up to 2 times then throws DeepSeekClientError', async () => {
    // Use real timers but with very short delays by patching sleep via the retry path.
    // We override RETRY_DELAYS_MS indirectly by patching global setTimeout to be immediate.
    let callCount = 0;
    vi.stubGlobal('fetch', vi.fn(async () => {
      callCount++;
      return new Response('rate limited', { status: 429 });
    }));

    // Speed up sleeps: replace setTimeout with immediate resolution
    const realSetTimeout = globalThis.setTimeout;
    vi.stubGlobal('setTimeout', (fn: () => void) => realSetTimeout(fn, 0));

    const client = new DeepSeekClient({ apiKey: 'sk-test' });

    let caught: unknown;
    try {
      for await (const _ of client.chat(messages)) { /* drain */ }
    } catch (e) {
      caught = e;
    }

    expect(caught).toBeInstanceOf(DeepSeekClientError);
    // 1 initial + 2 retries = 3 total calls
    expect(callCount).toBe(3);

    vi.unstubAllGlobals();
  });

  it('throws DeepSeekClientError with status for other 4xx errors', async () => {
    stubErrorResponse(400);

    const client = new DeepSeekClient({ apiKey: 'sk-test' });
    let caught: unknown;
    try {
      for await (const _ of client.chat(messages)) { /* drain */ }
    } catch (e) {
      caught = e;
    }
    expect(caught).toBeInstanceOf(DeepSeekClientError);
    expect((caught as DeepSeekClientError).status).toBe(400);
  });

  it('throws DeepSeekClientError with status for 5xx errors', async () => {
    stubErrorResponse(503);

    const client = new DeepSeekClient({ apiKey: 'sk-test' });
    let caught: unknown;
    try {
      for await (const _ of client.chat(messages)) { /* drain */ }
    } catch (e) {
      caught = e;
    }
    expect(caught).toBeInstanceOf(DeepSeekClientError);
    expect((caught as DeepSeekClientError).status).toBe(503);
  });

  it('DeepSeekClientError is not instance of DeepSeekAuthError', async () => {
    stubErrorResponse(500);
    const client = new DeepSeekClient({ apiKey: 'sk-test' });
    let caught: unknown;
    try {
      for await (const _ of client.chat(messages)) { /* drain */ }
    } catch (e) {
      caught = e;
    }
    expect(caught).not.toBeInstanceOf(DeepSeekAuthError);
    expect(caught).toBeInstanceOf(DeepSeekClientError);
  });
});

// ---------------------------------------------------------------------------
// Suite 3 — AbortSignal cancels the fetch
// ---------------------------------------------------------------------------

describe('DeepSeekClient — AbortSignal', () => {
  it('passes signal to fetch and stops iteration when aborted', async () => {
    const ac = new AbortController();

    // Build a slow stream that emits one chunk then waits (simulated by never closing)
    const encoder = new TextEncoder();
    let streamController!: ReadableStreamDefaultController<Uint8Array>;
    const slowStream = new ReadableStream<Uint8Array>({
      start(ctrl) { streamController = ctrl; },
    });

    vi.stubGlobal('fetch', vi.fn(async (_url: unknown, init: RequestInit) => {
      // When the signal aborts, throw DOMException (like real fetch does)
      return new Promise<Response>((resolve, reject) => {
        if (init.signal?.aborted) {
          reject(new DOMException('Aborted', 'AbortError'));
          return;
        }
        init.signal?.addEventListener('abort', () => {
          reject(new DOMException('Aborted', 'AbortError'));
        });
        // Emit one chunk then pause
        const resp = new Response(slowStream, {
          status: 200,
          headers: { 'content-type': 'text/event-stream' },
        });
        // Enqueue first chunk
        streamController.enqueue(
          encoder.encode(
            `data: ${JSON.stringify({ choices: [{ delta: { content: 'tok' } }] })}\n\n`,
          ),
        );
        resolve(resp);
      });
    }));

    const client = new DeepSeekClient({ apiKey: 'sk-test' });
    const tokens: string[] = [];

    const consume = async () => {
      for await (const t of client.chat(messages, { signal: ac.signal })) {
        tokens.push(t);
        // Abort after receiving the first token
        ac.abort();
      }
    };

    // AbortError should propagate out (or the loop ends) — not throw DeepSeekClientError
    try {
      await consume();
    } catch (e) {
      // Could be a DOMException AbortError — that is acceptable
      expect((e as DOMException).name).toBe('AbortError');
    }

    // We should have received the first token before abort
    expect(tokens).toHaveLength(1);
    expect(tokens[0]).toBe('tok');
  });

  it('propagates signal to underlying fetch call', async () => {
    const ac = new AbortController();
    let capturedSignal: AbortSignal | undefined;

    vi.stubGlobal('fetch', vi.fn(async (_url: unknown, init: RequestInit) => {
      capturedSignal = init.signal ?? undefined;
      return new Response('', { status: 401 });
    }));

    const client = new DeepSeekClient({ apiKey: 'sk-test' });
    try {
      for await (const _ of client.chat(messages, { signal: ac.signal })) { /* drain */ }
    } catch {
      // expected — 401 throws
    }

    expect(capturedSignal).toBe(ac.signal);
  });
});
