/**
 * DeepSeekClient — pure fetch + SSE, no external SDK.
 * DeepSeek's API is OpenAI-compatible; we use the streaming endpoint directly.
 */

export type ChatMessage = {
  role: 'system' | 'user' | 'assistant';
  content: string;
};

export class DeepSeekAuthError extends Error {
  constructor(message = 'invalid api key') {
    super(message);
    this.name = 'DeepSeekAuthError';
    Object.setPrototypeOf(this, DeepSeekAuthError.prototype);
  }
}

export class DeepSeekClientError extends Error {
  status?: number;
  constructor(message: string, status?: number) {
    super(message);
    this.name = 'DeepSeekClientError';
    this.status = status;
    Object.setPrototypeOf(this, DeepSeekClientError.prototype);
  }
}

// ---------------------------------------------------------------------------
// Private types & helpers
// ---------------------------------------------------------------------------

interface DeepSeekClientOpts {
  apiKey: string;
  model?: string;
  baseUrl?: string;
}

interface SseChunk {
  choices: Array<{ delta: { content?: string } }>;
}

const DEFAULT_MODEL = 'deepseek-chat';
const DEFAULT_BASE_URL = 'https://api.deepseek.com/v1';
const RETRY_DELAYS_MS = [250, 500];

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** Races a reader.read() against an AbortSignal so slow streams don't hang. */
function readWithAbort(
  reader: ReadableStreamDefaultReader<Uint8Array>,
  signal: AbortSignal,
): Promise<ReadableStreamReadResult<Uint8Array>> {
  return Promise.race([
    reader.read(),
    new Promise<never>((_, reject) => {
      if (signal.aborted) { reject(new DOMException('Aborted', 'AbortError')); return; }
      signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')));
    }),
  ]);
}

// ---------------------------------------------------------------------------
// DeepSeekClient
// ---------------------------------------------------------------------------

export class DeepSeekClient {
  private readonly apiKey: string;
  private readonly model: string;
  private readonly baseUrl: string;

  constructor({ apiKey, model = DEFAULT_MODEL, baseUrl = DEFAULT_BASE_URL }: DeepSeekClientOpts) {
    this.apiKey = apiKey;
    this.model = model;
    this.baseUrl = baseUrl;
  }

  async *chat(
    messages: ChatMessage[],
    opts?: { signal?: AbortSignal },
  ): AsyncGenerator<string> {
    const url = `${this.baseUrl}/chat/completions`;
    const response = await this.fetchWithRetry(url, messages, opts?.signal);
    yield* this.parseSseStream(response, opts?.signal);
  }

  private async fetchWithRetry(
    url: string,
    messages: ChatMessage[],
    signal?: AbortSignal,
  ): Promise<Response> {
    for (let attempt = 0; attempt <= RETRY_DELAYS_MS.length; attempt++) {
      let response: Response;
      try {
        response = await fetch(url, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${this.apiKey}`,
          },
          body: JSON.stringify({ model: this.model, messages, stream: true }),
          signal,
        });
      } catch (err) {
        throw err; // AbortError and network errors propagate immediately
      }

      if (response.status === 401) throw new DeepSeekAuthError('invalid api key');

      if (response.status === 429) {
        if (attempt < RETRY_DELAYS_MS.length) {
          await sleep(RETRY_DELAYS_MS[attempt]);
          continue;
        }
        throw new DeepSeekClientError('rate limit exceeded', 429);
      }

      if (!response.ok) {
        throw new DeepSeekClientError(`request failed with status ${response.status}`, response.status);
      }

      return response;
    }

    // Unreachable — loop always throws or returns before exhausting
    throw new DeepSeekClientError('unknown error');
  }

  private async *parseSseStream(response: Response, signal?: AbortSignal): AsyncGenerator<string> {
    const reader = response.body!.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    try {
      while (!signal?.aborted) {
        const { done, value } = signal
          ? await readWithAbort(reader, signal)
          : await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() ?? '';

        for (const line of lines) {
          const trimmed = line.trim();
          if (!trimmed.startsWith('data:')) continue;
          const data = trimmed.slice('data:'.length).trim();
          if (data === '[DONE]') return;
          try {
            const chunk = JSON.parse(data) as SseChunk;
            const content = chunk.choices?.[0]?.delta?.content;
            if (content) yield content;
          } catch {
            /* skip malformed lines */
          }
        }
      }
    } finally {
      reader.releaseLock();
    }
  }
}
