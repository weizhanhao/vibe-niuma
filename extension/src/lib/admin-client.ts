// Plan 6 · Task 5: 扩展端访问 orchestrator /admin/* 的 HTTP 客户端。
//
// 设计要点：
//   - 纯 fetch 封装，不依赖 chrome.*，service-worker 和 UI 都能直接用
//   - 所有 /admin/* 自动带 X-Admin-Token
//   - 错误用专属子类（AuthError / StaleVersionError / ValidationError / NetworkError），
//     调用方可以 `instanceof` 窄化，写 UI toast 不用 parse status code
//   - testConnection 走 /health 且不带 token —— 首次引导测连通性时还没 token

// ── Wire types ──────────────────────────────────────────────────────
// 这是 orchestrator 服务端响应里 `config` 字段的形状，跟 lib/config.ts 的
// ServerConfig（camelCase + 无 _set 字段）是两套表达。命名上：
//   - admin-client.ts → snake_case + _set bool，原样透传后端
//   - lib/config.ts → camelCase，UI / storage 用
// 两边类型互不引用，需要时由调用方在 wizard 里手动 map。

/** orchestrator /admin/config 响应里的 config 对象（snake_case，密钥永远脱敏成 null）。*/
export type ServerConfig = {
  dev_runner: 'opencode' | 'claude-code';
  dev_model: string;
  vision_model: string;
  deepseek_api_key: string | null;
  dashscope_api_key: string | null;
  anthropic_api_key: string | null;
  demo_repo_path: string;
  preview_backend_url: string;
  /** 后端是否已配置 deepseek key（bool；密钥本身永远不下行）。*/
  deepseek_api_key_set: boolean;
  dashscope_api_key_set: boolean;
  anthropic_api_key_set: boolean;
};

export type ConfigResponse = {
  config: ServerConfig;
  /** 乐观锁版本号；putConfig 必须带 expectedVersion，对不上就 409。*/
  version: number;
};

export type PutConfigResponse = ConfigResponse & {
  /** 本次 PUT 触发重启的 systemd 服务列表，可能为空数组（仅改了非 daemonized 字段时）。*/
  restartedServices: string[];
};

// ── Errors ──────────────────────────────────────────────────────────
// 都继承 AdminClientError，方便上层一把 `catch (e: unknown)` 然后用 instanceof 分流。

export class AdminClientError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'AdminClientError';
  }
}

/** 401：token 缺失或错。UI 应跳回引导页让用户重新粘 token。 */
export class AuthError extends AdminClientError {
  constructor(message: string) {
    super(message);
    this.name = 'AuthError';
  }
}

/**
 * 409：扩展持有的 version 落后于服务端。
 * `currentVersion` 来自服务端 detail 文案 `stale version: expected X, got Y` 里的 Y。
 */
export class StaleVersionError extends AdminClientError {
  constructor(
    public readonly currentVersion: number,
    public readonly expectedVersion: number,
    message: string,
  ) {
    super(message);
    this.name = 'StaleVersionError';
  }
}

/** 422：Pydantic 校验错误。message 里塞了原始 detail JSON，UI 可以渲染字段级提示。 */
export class ValidationError extends AdminClientError {
  constructor(message: string) {
    super(message);
    this.name = 'ValidationError';
  }
}

/** fetch reject / 5xx / 解析失败。可重试。 */
export class NetworkError extends AdminClientError {
  constructor(message: string) {
    super(message);
    this.name = 'NetworkError';
  }
}

// ── Internal helpers ────────────────────────────────────────────────

/** 把任意 thrown unknown 收敛成 string，避免在 catch 里再分支。*/
function errorMessage(err: unknown): string {
  if (err instanceof Error) return err.message;
  return String(err);
}

/** 安全读 response body 为字符串；失败返回空串而非抛错（用于错误路径再 parse JSON）。*/
async function readBodyText(resp: Response): Promise<string> {
  try {
    return await resp.text();
  } catch {
    return '';
  }
}

/** 从 "stale version: expected 3, got 5" 抠出 currentVersion=5。抠不到回 NaN。*/
function parseStaleVersion(detail: string): number {
  const match = detail.match(/got\s+(\d+)/i);
  if (!match) return Number.NaN;
  return Number.parseInt(match[1], 10);
}

/** Pydantic detail 既可能是字符串也可能是 list，归一化成可读 string。*/
function formatDetail(raw: string): string {
  if (!raw) return '';
  try {
    const parsed: unknown = JSON.parse(raw);
    if (parsed && typeof parsed === 'object' && 'detail' in parsed) {
      const detail = (parsed as { detail: unknown }).detail;
      if (typeof detail === 'string') return detail;
      // Pydantic v2 list: [{loc, msg, type}, ...]
      return JSON.stringify(detail);
    }
    return raw;
  } catch {
    return raw;
  }
}

// ── Client ──────────────────────────────────────────────────────────

export class AdminClient {
  constructor(
    private readonly baseUrl: string,
    private readonly token: string,
  ) {}

  /**
   * GET /admin/config —— 拉服务端当前配置 + 版本号。
   * @throws AuthError 401
   * @throws NetworkError 其他 4xx/5xx 或 fetch reject
   */
  async getConfig(): Promise<ConfigResponse> {
    let resp: Response;
    try {
      resp = await fetch(`${this.baseUrl}/admin/config`, {
        method: 'GET',
        headers: { 'X-Admin-Token': this.token },
      });
    } catch (err: unknown) {
      throw new NetworkError(`network failure: ${errorMessage(err)}`);
    }

    const body = await readBodyText(resp);

    if (resp.status === 401) {
      throw new AuthError(formatDetail(body) || 'unauthorized');
    }
    if (!resp.ok) {
      throw new NetworkError(`HTTP ${resp.status}: ${body.slice(0, 200)}`);
    }

    try {
      return JSON.parse(body) as ConfigResponse;
    } catch (err: unknown) {
      throw new NetworkError(`bad JSON: ${errorMessage(err)}`);
    }
  }

  /**
   * PUT /admin/config —— 提交部分字段 + 当前 version，服务端做乐观锁校验。
   * @throws AuthError 401
   * @throws StaleVersionError 409
   * @throws ValidationError 422
   * @throws NetworkError 其他 5xx / fetch reject
   */
  async putConfig(patch: Partial<ServerConfig>, expectedVersion: number): Promise<PutConfigResponse> {
    let resp: Response;
    try {
      resp = await fetch(`${this.baseUrl}/admin/config`, {
        method: 'PUT',
        headers: {
          'X-Admin-Token': this.token,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ config: patch, expectedVersion }),
      });
    } catch (err: unknown) {
      throw new NetworkError(`network failure: ${errorMessage(err)}`);
    }

    const body = await readBodyText(resp);

    if (resp.status === 401) {
      throw new AuthError(formatDetail(body) || 'unauthorized');
    }
    if (resp.status === 409) {
      const detail = formatDetail(body);
      const current = parseStaleVersion(detail);
      throw new StaleVersionError(current, expectedVersion, detail || 'stale version');
    }
    if (resp.status === 422) {
      throw new ValidationError(formatDetail(body) || 'validation error');
    }
    if (!resp.ok) {
      throw new NetworkError(`HTTP ${resp.status}: ${body.slice(0, 200)}`);
    }

    try {
      return JSON.parse(body) as PutConfigResponse;
    } catch (err: unknown) {
      throw new NetworkError(`bad JSON: ${errorMessage(err)}`);
    }
  }

  /**
   * GET /health —— 不带鉴权的健康检查，wizard 第 1 步「测试连接」用。
   * 永远不抛错：200 → true，任何其他情况 → false。
   */
  async testConnection(): Promise<boolean> {
    try {
      const resp = await fetch(`${this.baseUrl}/health`, { method: 'GET' });
      return resp.status === 200;
    } catch {
      return false;
    }
  }
}
