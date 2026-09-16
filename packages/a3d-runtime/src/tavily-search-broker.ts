import { randomBytes } from 'node:crypto';
import { createServer, type ServerResponse } from 'node:http';
import type { Socket } from 'node:net';

const TAVILY_SEARCH_URL = 'https://api.tavily.com/search';
const BROKER_REQUEST_BUDGET_BYTES = 64 * 1024;
const PROVIDER_RESPONSE_BUDGET_BYTES = 1024 * 1024;
const SEARCH_DEPTHS = new Set(['advanced', 'basic', 'fast', 'ultra-fast']);
const SEARCH_TOPICS = new Set(['finance', 'general', 'news']);
const TIME_RANGES = new Set(['d', 'day', 'm', 'month', 'w', 'week', 'y', 'year']);
const ANSWER_MODES = new Set(['advanced', 'basic']);
const REQUEST_KEYS = new Set([
  'include_answer',
  'include_images',
  'include_raw_content',
  'max_results',
  'query',
  'search_depth',
  'time_range',
  'topic',
]);

type JsonObject = Record<string, unknown>;
type Fetch = typeof fetch;

function isObject(value: unknown): value is JsonObject {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function writeJson(response: ServerResponse, status: number, value: unknown): void {
  if (response.destroyed || response.writableEnded) return;
  const body = Buffer.from(`${JSON.stringify(value)}\n`);
  response.writeHead(status, {
    connection: 'close',
    'content-length': body.length,
    'content-type': 'application/json; charset=utf-8',
  });
  response.end(body);
}

function writeError(response: ServerResponse, status: number, code: string): void {
  writeJson(response, status, { error: { code } });
}

async function readBody(
  body: AsyncIterable<Buffer | string>,
  limit: number,
): Promise<Buffer> {
  const chunks: Buffer[] = [];
  let size = 0;
  for await (const chunk of body) {
    const bytes = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
    size += bytes.length;
    if (size > limit) throw new Error('body-too-large');
    chunks.push(bytes);
  }
  return Buffer.concat(chunks);
}

async function readResponseBody(
  body: ReadableStream<Uint8Array>,
  limit: number,
): Promise<Buffer> {
  const chunks: Buffer[] = [];
  const reader = body.getReader();
  let size = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      const bytes = Buffer.from(value);
      size += bytes.length;
      if (size > limit) throw new Error('body-too-large');
      chunks.push(bytes);
    }
  } catch (error) {
    await reader.cancel().catch(() => {});
    throw error;
  } finally {
    reader.releaseLock();
  }
  return Buffer.concat(chunks);
}

function validSearchRequest(value: unknown): JsonObject | null {
  if (!isObject(value) || Object.keys(value).some((key) => !REQUEST_KEYS.has(key))) {
    return null;
  }
  if (typeof value.query !== 'string' || !value.query.trim()) return null;
  if (value.include_images !== false || value.include_raw_content !== false) return null;
  if (
    value.include_answer !== false
    && !(typeof value.include_answer === 'string' && ANSWER_MODES.has(value.include_answer))
  ) return null;
  if (
    value.max_results !== undefined
    && (!Number.isSafeInteger(value.max_results) || Number(value.max_results) < 0 || Number(value.max_results) > 20)
  ) return null;
  if (
    value.search_depth !== undefined
    && !(typeof value.search_depth === 'string' && SEARCH_DEPTHS.has(value.search_depth))
  ) return null;
  if (
    value.topic !== undefined
    && !(typeof value.topic === 'string' && SEARCH_TOPICS.has(value.topic))
  ) return null;
  if (
    value.time_range !== undefined
    && !(typeof value.time_range === 'string' && TIME_RANGES.has(value.time_range))
  ) return null;
  return {
    include_answer: value.include_answer,
    include_images: false,
    include_raw_content: false,
    query: value.query.trim(),
    ...(value.max_results === undefined ? {} : { max_results: value.max_results }),
    ...(value.search_depth === undefined ? {} : { search_depth: value.search_depth }),
    ...(value.time_range === undefined ? {} : { time_range: value.time_range }),
    ...(value.topic === undefined ? {} : { topic: value.topic }),
  };
}

function safeSearchResponse(value: unknown, apiKey: string): JsonObject | null {
  if (
    !isObject(value)
    || !Array.isArray(value.results)
    || JSON.stringify(value).includes(apiKey)
  ) return null;
  const safe: JsonObject = {
    results: value.results.map((item) => {
      if (!isObject(item)) return {};
      return {
        ...(typeof item.content === 'string' ? { content: item.content } : {}),
        ...(typeof item.published_date === 'string'
          ? { published_date: item.published_date }
          : {}),
        ...(typeof item.score === 'number' && Number.isFinite(item.score)
          ? { score: item.score }
          : {}),
        ...(typeof item.title === 'string' ? { title: item.title } : {}),
        ...(typeof item.url === 'string' ? { url: item.url } : {}),
      };
    }),
    ...(typeof value.answer === 'string' ? { answer: value.answer } : {}),
  };
  return safe;
}

export interface TavilySearchBroker {
  readonly url: string;
  close(): Promise<void>;
}

export async function createTavilySearchBroker(options: {
  apiKey: string;
  fetchImpl?: Fetch;
  signal?: AbortSignal;
}): Promise<TavilySearchBroker> {
  options.signal?.throwIfAborted();
  const fetchImpl = options.fetchImpl ?? fetch;
  const capabilityPath = `/search/${randomBytes(32).toString('base64url')}`;
  const controllers = new Set<AbortController>();
  const sockets = new Set<Socket>();
  let closed = false;
  let closing: Promise<void> | undefined;

  const server = createServer((request, response) => {
    void (async () => {
      if (request.method !== 'POST' || request.url !== capabilityPath) {
        writeError(response, 404, 'search-capability-not-found');
        return;
      }
      if (!/^application\/json(?:\s*;|$)/iu.test(request.headers['content-type'] || '')) {
        writeError(response, 415, 'invalid-content-type');
        return;
      }
      const declared = Number(request.headers['content-length']);
      if (Number.isFinite(declared) && declared > BROKER_REQUEST_BUDGET_BYTES) {
        writeError(response, 413, 'request-too-large');
        return;
      }
      let parsed: unknown;
      try {
        parsed = JSON.parse((await readBody(request, BROKER_REQUEST_BUDGET_BYTES)).toString('utf8'));
      } catch {
        writeError(response, 400, 'invalid-search-request');
        return;
      }
      const searchRequest = validSearchRequest(parsed);
      if (!searchRequest) {
        writeError(response, 400, 'invalid-search-request');
        return;
      }

      const controller = new AbortController();
      controllers.add(controller);
      const abort = () => controller.abort();
      request.once('aborted', abort);
      response.once('close', () => {
        if (!response.writableFinished) abort();
      });
      try {
        const upstream = await fetchImpl(TAVILY_SEARCH_URL, {
          body: JSON.stringify(searchRequest),
          headers: {
            authorization: `Bearer ${options.apiKey}`,
            'content-type': 'application/json',
          },
          method: 'POST',
          redirect: 'error',
          signal: controller.signal,
        });
        if (!upstream.ok) {
          await upstream.body?.cancel();
          writeError(response, upstream.status, 'tavily-request-failed');
          return;
        }
        const declaredUpstream = Number(upstream.headers.get('content-length'));
        if (
          Number.isFinite(declaredUpstream)
          && declaredUpstream > PROVIDER_RESPONSE_BUDGET_BYTES
        ) {
          await upstream.body?.cancel();
          writeError(response, 502, 'tavily-response-too-large');
          return;
        }
        if (!upstream.body) {
          writeError(response, 502, 'invalid-tavily-response');
          return;
        }
        let value: unknown;
        try {
          value = JSON.parse(
            (await readResponseBody(upstream.body, PROVIDER_RESPONSE_BUDGET_BYTES)).toString('utf8'),
          );
        } catch {
          writeError(response, 502, 'invalid-tavily-response');
          return;
        }
        const safe = safeSearchResponse(value, options.apiKey);
        if (!safe) {
          writeError(response, 502, 'invalid-tavily-response');
          return;
        }
        writeJson(response, 200, safe);
      } catch {
        if (!closed && !controller.signal.aborted) {
          writeError(response, 503, 'tavily-unavailable');
        }
      } finally {
        request.off('aborted', abort);
        controllers.delete(controller);
      }
    })();
  });
  server.on('connection', (socket) => {
    sockets.add(socket);
    socket.once('close', () => sockets.delete(socket));
  });

  function onAbort(): void {
    void close();
  }

  function close(): Promise<void> {
    if (closing) return closing;
    closed = true;
    options.signal?.removeEventListener('abort', onAbort);
    for (const controller of controllers) controller.abort();
    for (const socket of sockets) socket.destroy();
    closing = new Promise<void>((resolve) => {
      if (!server.listening) resolve();
      else server.close(() => resolve());
    });
    return closing;
  }

  try {
    await new Promise<void>((resolve, reject) => {
      server.once('error', reject);
      server.listen(0, '127.0.0.1', () => {
        server.removeListener('error', reject);
        resolve();
      });
    });
    const address = server.address();
    if (!address || typeof address === 'string') {
      throw new Error('Unable to start the local Tavily search broker.');
    }
    options.signal?.addEventListener('abort', onAbort, { once: true });
    options.signal?.throwIfAborted();
    return {
      close,
      url: `http://127.0.0.1:${String(address.port)}${capabilityPath}`,
    };
  } catch (error) {
    await close();
    throw error;
  }
}
