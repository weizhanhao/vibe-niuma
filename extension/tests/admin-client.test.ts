// Plan 6 · Task 5: admin-client 单元测试。
//
// 设计：用 vi.stubGlobal('fetch', ...) mock 全局 fetch，逐个验证：
//   - X-Admin-Token header 在所有 /admin/* 请求里都带上
//   - PUT body 形如 { config: patch, expectedVersion }
//   - 4xx/5xx/网络错误 → 各自专属错误类（type narrowing 友好）
//   - testConnection 永远不抛错（false / true）

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  AdminClient,
  AdminClientError,
  AuthError,
  NetworkError,
  StaleVersionError,
  ValidationError,
  type ConfigResponse,
  type PutConfigResponse,
  type ServerConfig,
} from '../src/lib/admin-client';

const BASE_URL = 'http://orch.test:9000';
const TOKEN = 'test-admin-token-1234567890';

function mockFetch(impl: (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>) {
  const fetchMock = vi.fn(impl) as unknown as typeof fetch;
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

function makeServerConfig(overrides: Partial<ServerConfig> = {}): ServerConfig {
  return {
    dev_runner: 'opencode',
    dev_model: 'deepseek/deepseek-v4-flash',
    vision_model: 'qwen-vl-plus',
    deepseek_api_key: null,
    dashscope_api_key: null,
    anthropic_api_key: null,
    demo_repo_path: '/opt/doskill/demo',
    preview_backend_url: 'http://doskill-demo-backend:8000',
    deepseek_api_key_set: true,
    dashscope_api_key_set: true,
    anthropic_api_key_set: false,
    ...overrides,
  };
}

let client: AdminClient;

beforeEach(() => {
  client = new AdminClient(BASE_URL, TOKEN);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('AdminClient.getConfig', () => {
  it('includes X-Admin-Token header', async () => {
    const captured: { url?: string; init?: RequestInit } = {};
    mockFetch(async (input, init) => {
      captured.url = String(input);
      captured.init = init;
      return new Response(
        JSON.stringify({ config: makeServerConfig(), version: 3 }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      );
    });

    await client.getConfig();

    expect(captured.url).toBe(`${BASE_URL}/admin/config`);
    const headers = new Headers(captured.init?.headers);
    expect(headers.get('X-Admin-Token')).toBe(TOKEN);
  });

  it('parses response correctly', async () => {
    mockFetch(async () =>
      new Response(
        JSON.stringify({
          config: makeServerConfig({ dev_runner: 'claude-code' }),
          version: 7,
        }),
        { status: 200 },
      ),
    );

    const resp: ConfigResponse = await client.getConfig();

    expect(resp.version).toBe(7);
    expect(resp.config.dev_runner).toBe('claude-code');
    expect(resp.config.deepseek_api_key).toBeNull();
    expect(resp.config.deepseek_api_key_set).toBe(true);
  });

  it('throws AuthError on 401', async () => {
    mockFetch(async () =>
      new Response(JSON.stringify({ detail: 'unauthorized' }), { status: 401 }),
    );

    await expect(client.getConfig()).rejects.toBeInstanceOf(AuthError);
  });
});

describe('AdminClient.putConfig', () => {
  it('sends correct body with method=PUT and X-Admin-Token', async () => {
    const captured: { url?: string; init?: RequestInit } = {};
    mockFetch(async (input, init) => {
      captured.url = String(input);
      captured.init = init;
      return new Response(
        JSON.stringify({
          config: makeServerConfig(),
          version: 4,
          restartedServices: [],
        }),
        { status: 200 },
      );
    });

    await client.putConfig({ dev_runner: 'claude-code' }, 3);

    expect(captured.url).toBe(`${BASE_URL}/admin/config`);
    expect(captured.init?.method).toBe('PUT');
    const headers = new Headers(captured.init?.headers);
    expect(headers.get('X-Admin-Token')).toBe(TOKEN);
    expect(headers.get('Content-Type')).toBe('application/json');

    const body = JSON.parse(captured.init!.body as string);
    expect(body).toEqual({
      config: { dev_runner: 'claude-code' },
      expectedVersion: 3,
    });
  });

  it('returns restartedServices', async () => {
    mockFetch(async () =>
      new Response(
        JSON.stringify({
          config: makeServerConfig(),
          version: 4,
          restartedServices: ['doskill-llm-proxy', 'doskill-orchestrator'],
        }),
        { status: 200 },
      ),
    );

    const resp: PutConfigResponse = await client.putConfig({ dev_model: 'x' }, 3);
    expect(resp.version).toBe(4);
    expect(resp.restartedServices).toEqual(['doskill-llm-proxy', 'doskill-orchestrator']);
  });

  it('throws StaleVersionError with currentVersion on 409', async () => {
    mockFetch(async () =>
      new Response(
        JSON.stringify({ detail: 'stale version: expected 3, got 5' }),
        { status: 409 },
      ),
    );

    let thrown: unknown;
    try {
      await client.putConfig({ dev_model: 'x' }, 3);
    } catch (e: unknown) {
      thrown = e;
    }

    expect(thrown).toBeInstanceOf(StaleVersionError);
    const err = thrown as StaleVersionError;
    expect(err.expectedVersion).toBe(3);
    expect(err.currentVersion).toBe(5);
  });

  it('throws AuthError on 401', async () => {
    mockFetch(async () =>
      new Response(JSON.stringify({ detail: 'unauthorized' }), { status: 401 }),
    );

    await expect(client.putConfig({ dev_model: 'x' }, 1)).rejects.toBeInstanceOf(AuthError);
  });

  it('throws ValidationError with detail on 422', async () => {
    mockFetch(async () =>
      new Response(
        JSON.stringify({
          detail: [
            { loc: ['body', 'config', 'dev_runner'], msg: 'Input should be opencode or claude-code', type: 'enum' },
          ],
        }),
        { status: 422 },
      ),
    );

    let thrown: unknown;
    try {
      await client.putConfig({ dev_runner: 'bogus' as unknown as ServerConfig['dev_runner'] }, 1);
    } catch (e: unknown) {
      thrown = e;
    }

    expect(thrown).toBeInstanceOf(ValidationError);
    const err = thrown as ValidationError;
    expect(err.message).toContain('dev_runner');
  });

  it('throws NetworkError on fetch reject', async () => {
    mockFetch(async () => {
      throw new TypeError('Failed to fetch');
    });

    await expect(client.putConfig({ dev_model: 'x' }, 1)).rejects.toBeInstanceOf(NetworkError);
  });

  it('throws NetworkError on 5xx', async () => {
    mockFetch(async () =>
      new Response('upstream broke', { status: 503 }),
    );

    await expect(client.putConfig({ dev_model: 'x' }, 1)).rejects.toBeInstanceOf(NetworkError);
  });

  it('all subclasses extend AdminClientError', () => {
    expect(new AuthError('x')).toBeInstanceOf(AdminClientError);
    expect(new ValidationError('x')).toBeInstanceOf(AdminClientError);
    expect(new NetworkError('x')).toBeInstanceOf(AdminClientError);
    expect(new StaleVersionError(5, 3, 'x')).toBeInstanceOf(AdminClientError);
  });
});

describe('AdminClient.testConnection', () => {
  it('returns true on 200', async () => {
    const captured: { url?: string; init?: RequestInit } = {};
    mockFetch(async (input, init) => {
      captured.url = String(input);
      captured.init = init;
      return new Response(JSON.stringify({ ok: true }), { status: 200 });
    });

    const ok = await client.testConnection();
    expect(ok).toBe(true);
    expect(captured.url).toBe(`${BASE_URL}/health`);

    // 不带鉴权
    const headers = new Headers(captured.init?.headers);
    expect(headers.get('X-Admin-Token')).toBeNull();
  });

  it('returns false on 500', async () => {
    mockFetch(async () => new Response('boom', { status: 500 }));
    const ok = await client.testConnection();
    expect(ok).toBe(false);
  });

  it('returns false on fetch reject', async () => {
    mockFetch(async () => {
      throw new TypeError('Failed to fetch');
    });
    const ok = await client.testConnection();
    expect(ok).toBe(false);
  });
});
