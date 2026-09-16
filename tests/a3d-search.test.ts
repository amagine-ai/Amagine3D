import { strict as assert } from 'node:assert';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { test } from 'node:test';

import {
  normalizeSearchResponse,
  parseSearchArguments,
  runSearchCommand,
  search,
} from '../bin/a3d-search.mjs';

const execFileAsync = promisify(execFile);
const A3D = new URL('../bin/a3d.mjs', import.meta.url);
const API_KEY = 'tvly-cli-secret';
const RESULT = {
  answer: 'Current answer',
  ignored: 'private metadata',
  results: [
    {
      content: 'Useful snippet',
      ignored: 'private result metadata',
      published_date: '2026-09-15',
      score: 0.8,
      title: 'Useful title',
      url: 'https://example.com/source',
    },
  ],
};

function writer() {
  let value = '';
  return {
    stream: { write(chunk: string) { value += chunk; } },
    value() { return value; },
  };
}

test('search parses documented Tavily options without inventing defaults', () => {
  assert.deepEqual(
    parseSearchArguments([
      'latest CAD specification',
      '--max-results=7',
      '--search-depth', 'advanced',
      '--topic', 'news',
      '--time-range', 'week',
      '--answer', 'basic',
    ]),
    {
      help: false,
      query: 'latest CAD specification',
      request: {
        include_answer: 'basic',
        include_images: false,
        include_raw_content: false,
        max_results: 7,
        query: 'latest CAD specification',
        search_depth: 'advanced',
        time_range: 'week',
        topic: 'news',
      },
    },
  );
  const minimal = parseSearchArguments(['query']);
  assert.equal(minimal.help, false);
  if (minimal.help) throw new Error('Expected a search request.');
  assert.equal('max_results' in minimal.request, false);
  assert.equal(minimal.request.include_answer, false);
});

test('search rejects unsafe and malformed command options', async () => {
  for (const args of [
    [],
    ['two', 'queries'],
    ['query', '--api-key', API_KEY],
    ['query', '--endpoint', 'https://attacker.invalid'],
    ['query', '--max-results', '21'],
    ['query', '--max-results', '-1'],
    ['query', '--search-depth', 'unbounded'],
    ['query', '--topic', 'sports'],
    ['query', '--answer', 'true'],
    ['query', '--topic', 'news', '--topic', 'finance'],
  ]) {
    const stderr = writer();
    const code = await runSearchCommand(args, {
      environment: { TAVILY_API_KEY: API_KEY },
      stderr: stderr.stream,
      stdout: writer().stream,
    });
    assert.equal(code, 2);
    const failure = JSON.parse(stderr.value());
    assert.equal(failure.schema, 'a3d-search/v1');
    assert.equal(failure.ok, false);
    assert.equal(failure.error.code, 'invalid-arguments');
    assert.doesNotMatch(stderr.value(), new RegExp(API_KEY, 'u'));
  }
});

test('standalone search uses fixed Tavily REST with a Bearer key', async () => {
  let received: { input: string; init?: RequestInit } | undefined;
  const result = await search(['query', '--answer', 'advanced'], {
    environment: { TAVILY_API_KEY: API_KEY },
    async fetchImpl(input: URL | RequestInfo, init?: RequestInit) {
      received = { input: String(input), init };
      return new Response(JSON.stringify(RESULT));
    },
  });
  assert.equal(result.help, false);
  assert.equal(received?.input, 'https://api.tavily.com/search');
  assert.equal(new Headers(received?.init?.headers).get('authorization'), `Bearer ${API_KEY}`);
  assert.deepEqual(JSON.parse(String(received?.init?.body)), {
    include_answer: 'advanced',
    include_images: false,
    include_raw_content: false,
    query: 'query',
  });
  assert.equal(received?.init?.redirect, 'error');
  const output = JSON.parse(result.output);
  assert.equal(output.schema, 'a3d-search/v1');
  assert.equal(output.ok, true);
  assert.equal(output.answer, 'Current answer');
  assert.deepEqual(output.results, [{
    content: 'Useful snippet',
    publishedDate: '2026-09-15',
    score: 0.8,
    title: 'Useful title',
    url: 'https://example.com/source',
  }]);
  assert.doesNotMatch(result.output, /private metadata|private result/u);
});

test('managed broker takes precedence and never falls back to the raw key', async () => {
  const brokerUrl = 'http://127.0.0.1:45678/search/capability';
  const calls: Array<{ input: string; init?: RequestInit }> = [];
  const result = await search(['query'], {
    environment: {
      AMAGINE3D_SEARCH_BROKER_URL: brokerUrl,
      TAVILY_API_KEY: API_KEY,
    },
    async fetchImpl(input: URL | RequestInfo, init?: RequestInit) {
      calls.push({ input: String(input), init });
      return new Response(JSON.stringify(RESULT));
    },
  });
  assert.equal(result.help, false);
  assert.equal(calls.length, 1);
  assert.equal(calls[0]?.input, brokerUrl);
  assert.equal(new Headers(calls[0]?.init?.headers).has('authorization'), false);

  const stderr = writer();
  const code = await runSearchCommand(['query'], {
    environment: {
      AMAGINE3D_SEARCH_BROKER_URL: brokerUrl,
      TAVILY_API_KEY: API_KEY,
    },
    async fetchImpl() {
      return new Response('{"private":"provider body"}', { status: 503 });
    },
    stderr: stderr.stream,
    stdout: writer().stream,
  });
  assert.equal(code, 1);
  assert.equal(JSON.parse(stderr.value()).error.code, 'provider-unavailable');
  assert.doesNotMatch(stderr.value(), /private|tvly/u);
});

test('search rejects non-loopback managed capabilities without direct fallback', async () => {
  let calls = 0;
  const stderr = writer();
  const code = await runSearchCommand(['query'], {
    environment: {
      AMAGINE3D_SEARCH_BROKER_URL: 'https://attacker.invalid/search',
      TAVILY_API_KEY: API_KEY,
    },
    async fetchImpl() {
      calls += 1;
      return new Response(JSON.stringify(RESULT));
    },
    stderr: stderr.stream,
    stdout: writer().stream,
  });
  assert.equal(code, 2);
  assert.equal(calls, 0);
  assert.equal(JSON.parse(stderr.value()).error.code, 'invalid-broker-configuration');
});

test('search output preserves complete records and reports omissions', async () => {
  const result = await search(['query'], {
    environment: { TAVILY_API_KEY: API_KEY },
    async fetchImpl() {
      return new Response(JSON.stringify({
        results: [
          { title: 'kept', url: 'https://example.com/kept', content: 'small' },
          { title: 'large', url: 'https://example.com/large', content: 'x'.repeat(40_000) },
          { title: 'later', url: 'https://example.com/later', content: 'small' },
        ],
      }));
    },
  });
  const output = JSON.parse(result.output);
  assert.deepEqual(output.results.map(({ title }: { title: string }) => title), ['kept']);
  assert.equal(output.count, 1);
  assert.equal(output.receivedCount, 3);
  assert.equal(output.omittedResults, 2);
  assert.equal(output.discardedResults, 0);
  assert.equal(output.truncated, true);
  assert.equal(Buffer.byteLength(result.output) <= 32 * 1024, true);
});

test('search discards malformed result records and maps provider failures statically', async () => {
  assert.deepEqual(normalizeSearchResponse({ results: [
    { title: 'bad', url: 'file:///etc/passwd', content: 'unsafe' },
    { title: 'good', url: 'https://example.com', content: 'safe' },
  ] }), {
    discardedResults: 1,
    receivedCount: 2,
    results: [{ title: 'good', url: 'https://example.com', content: 'safe' }],
  });
  for (const [status, code] of [[400, 'provider-rejected-request'], [401, 'provider-authentication-failed'], [429, 'provider-rate-limited'], [503, 'provider-unavailable']] as const) {
    const stderr = writer();
    const exitCode = await runSearchCommand(['query'], {
      environment: { TAVILY_API_KEY: API_KEY },
      async fetchImpl() {
        return new Response(`private provider body ${API_KEY}`, { status });
      },
      stderr: stderr.stream,
      stdout: writer().stream,
    });
    assert.equal(exitCode, 1);
    assert.equal(JSON.parse(stderr.value()).error.code, code);
    assert.doesNotMatch(stderr.value(), new RegExp(API_KEY, 'u'));
    assert.doesNotMatch(stderr.value(), /private provider body/u);
  }
});

test('search bounds request bytes before provider access', async () => {
  let calls = 0;
  const stderr = writer();
  const code = await runSearchCommand(['x'.repeat(64 * 1024)], {
    environment: { TAVILY_API_KEY: API_KEY },
    async fetchImpl() {
      calls += 1;
      return new Response(JSON.stringify(RESULT));
    },
    stderr: stderr.stream,
    stdout: writer().stream,
  });
  assert.equal(code, 2);
  assert.equal(calls, 0);
  assert.equal(JSON.parse(stderr.value()).error.code, 'request-too-large');
});

test('search bounds provider response bytes before JSON projection', async () => {
  const stderr = writer();
  const code = await runSearchCommand(['query'], {
    environment: { TAVILY_API_KEY: API_KEY },
    async fetchImpl() {
      return new Response('x'.repeat(1024 * 1024 + 1));
    },
    stderr: stderr.stream,
    stdout: writer().stream,
  });
  assert.equal(code, 1);
  assert.equal(JSON.parse(stderr.value()).error.code, 'provider-response-too-large');
});

test('a3d search help does not require managed Python', async () => {
  const environment: NodeJS.ProcessEnv = {
    ...process.env,
    AMAGINE3D_PYTHON: '/missing/python',
  };
  delete environment.TAVILY_API_KEY;
  const { stdout, stderr } = await execFileAsync(
    process.execPath,
    [A3D.pathname, 'search', '--help'],
    { env: environment },
  );
  assert.match(stdout, /a3d search QUERY/u);
  assert.equal(stderr, '');
});
