import { strict as assert } from 'node:assert';
import { test } from 'node:test';

import { createTavilySearchBroker } from '../src/tavily-search-broker.ts';

const API_KEY = 'tvly-test-secret-never-forward';
const VALID_REQUEST = {
  include_answer: 'basic',
  include_images: false,
  include_raw_content: false,
  max_results: 3,
  query: 'current CAD reference',
  search_depth: 'advanced',
  time_range: 'week',
  topic: 'news',
};

async function post(url: string, body: unknown): Promise<Response> {
  return fetch(url, {
    body: JSON.stringify(body),
    headers: { 'content-type': 'application/json' },
    method: 'POST',
  });
}

test('turn broker forwards only validated searches and allowlists Tavily output', async () => {
  const requests: Array<{ input: string; init?: RequestInit }> = [];
  const broker = await createTavilySearchBroker({
    apiKey: API_KEY,
    async fetchImpl(input, init) {
      requests.push({ input: String(input), init });
      return new Response(JSON.stringify({
        answer: 'A safe answer',
        images: [{ url: 'https://private.invalid/image.png' }],
        raw_content: 'discard me',
        request_id: 'private-request',
        results: [
          {
            content: 'Useful snippet',
            extra: 'discard me',
            published_date: '2026-09-15',
            score: 0.9,
            title: 'Useful result',
            url: 'https://example.com/reference',
          },
        ],
      }), { headers: { 'content-type': 'application/json' }, status: 200 });
    },
  });
  try {
    assert.match(broker.url, /^http:\/\/127\.0\.0\.1:\d+\/search\/[A-Za-z0-9_-]+$/u);
    assert.doesNotMatch(broker.url, new RegExp(API_KEY, 'u'));
    const response = await post(broker.url, VALID_REQUEST);
    assert.equal(response.status, 200);
    const text = await response.text();
    assert.doesNotMatch(text, new RegExp(API_KEY, 'u'));
    assert.deepEqual(JSON.parse(text), {
      answer: 'A safe answer',
      results: [
        {
          content: 'Useful snippet',
          published_date: '2026-09-15',
          score: 0.9,
          title: 'Useful result',
          url: 'https://example.com/reference',
        },
      ],
    });
    assert.equal(requests.length, 1);
    assert.equal(requests[0]?.input, 'https://api.tavily.com/search');
    assert.equal(
      new Headers(requests[0]?.init?.headers).get('authorization'),
      `Bearer ${API_KEY}`,
    );
    assert.deepEqual(JSON.parse(String(requests[0]?.init?.body)), VALID_REQUEST);
    assert.equal(requests[0]?.init?.redirect, 'error');

    const unknown = new URL(broker.url);
    unknown.pathname = '/search/unknown';
    assert.equal((await post(unknown.href, VALID_REQUEST)).status, 404);
  } finally {
    await broker.close();
  }
  await assert.rejects(post(broker.url, VALID_REQUEST));
});

test('turn broker rejects unsafe request shapes before Tavily', async () => {
  let calls = 0;
  const broker = await createTavilySearchBroker({
    apiKey: API_KEY,
    async fetchImpl() {
      calls += 1;
      return new Response('{"results":[]}');
    },
  });
  try {
    for (const body of [
      { ...VALID_REQUEST, endpoint: 'https://attacker.invalid' },
      { ...VALID_REQUEST, include_images: true },
      { ...VALID_REQUEST, include_raw_content: 'markdown' },
      { ...VALID_REQUEST, max_results: 21 },
      { ...VALID_REQUEST, query: '   ' },
    ]) {
      assert.equal((await post(broker.url, body)).status, 400);
    }
    const wrongType = await fetch(broker.url, {
      body: JSON.stringify(VALID_REQUEST),
      headers: { 'content-type': 'text/plain' },
      method: 'POST',
    });
    assert.equal(wrongType.status, 415);
    assert.equal(calls, 0);
  } finally {
    await broker.close();
  }
});

test('turn broker contains upstream failures and key-shaped output', async () => {
  for (const fixture of [
    {
      expected: 429,
      fetchImpl: async () => new Response(`private ${API_KEY}`, { status: 429 }),
    },
    {
      expected: 502,
      fetchImpl: async () => new Response(JSON.stringify({
        results: [{ title: 'leak', url: 'https://example.com', content: API_KEY }],
      }), { status: 200 }),
    },
    {
      expected: 502,
      fetchImpl: async () => new Response(JSON.stringify({
        padding: 'x'.repeat(1024 * 1024),
        results: [],
      }), { status: 200 }),
    },
    {
      expected: 503,
      fetchImpl: async () => { throw new Error(`private ${API_KEY}`); },
    },
  ]) {
    const broker = await createTavilySearchBroker({
      apiKey: API_KEY,
      fetchImpl: fixture.fetchImpl,
    });
    try {
      const response = await post(broker.url, VALID_REQUEST);
      assert.equal(response.status, fixture.expected);
      assert.doesNotMatch(await response.text(), new RegExp(API_KEY, 'u'));
    } finally {
      await broker.close();
    }
  }
});

test('closing a turn broker aborts active upstream searches', async () => {
  let upstreamAborted = false;
  let started!: () => void;
  const upstreamStarted = new Promise<void>((resolve) => { started = resolve; });
  const broker = await createTavilySearchBroker({
    apiKey: API_KEY,
    fetchImpl: async (_input, init) => {
      started();
      return new Promise<Response>((_resolve, reject) => {
        init?.signal?.addEventListener('abort', () => {
          upstreamAborted = true;
          reject(new DOMException('Aborted', 'AbortError'));
        }, { once: true });
      });
    },
  });
  const pending = post(broker.url, VALID_REQUEST);
  await upstreamStarted;
  await broker.close();
  await assert.rejects(pending);
  assert.equal(upstreamAborted, true);
});

test('one broker safely serves concurrent searches', async () => {
  const queries: string[] = [];
  const broker = await createTavilySearchBroker({
    apiKey: API_KEY,
    async fetchImpl(_input, init) {
      const request = JSON.parse(String(init?.body));
      queries.push(request.query);
      return new Response(JSON.stringify({ results: [{
        content: request.query,
        title: request.query,
        url: `https://example.com/${encodeURIComponent(request.query)}`,
      }] }));
    },
  });
  try {
    const responses = await Promise.all([
      post(broker.url, { ...VALID_REQUEST, query: 'alpha' }),
      post(broker.url, { ...VALID_REQUEST, query: 'beta' }),
    ]);
    assert.deepEqual(responses.map(({ status }) => status), [200, 200]);
    assert.deepEqual(queries.sort(), ['alpha', 'beta']);
  } finally {
    await broker.close();
  }
});
