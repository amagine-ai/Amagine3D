import { strict as assert } from 'node:assert';
import { test } from 'node:test';

import {
  createTavilySearchTool,
  loadPublicReferenceImage,
  shouldBlockCadToolBeforeWebSearch,
  TAVILY_SEARCH_TOOL_NAME,
} from '@amagine3d/a3d-runtime';

test('Tavily search tool is disabled without an API key', () => {
  assert.equal(createTavilySearchTool(), undefined);
});

test('Tavily search tool maps PI arguments and returns source snippets', async () => {
  const calls: unknown[] = [];
  const tool = createTavilySearchTool({
    search: async (query, options) => {
      calls.push({ options, query });
      return {
        images: [],
        query,
        requestId: 'request-1',
        responseTime: 0.2,
        results: [
          {
            content: 'A source snippet',
            id: 'result-1',
            publishedDate: '2026-08-24',
            score: 0.98,
            title: 'Source title',
            url: 'https://example.com/source',
          },
        ],
      };
    },
  });

  assert.ok(tool);
  assert.equal(tool.name, TAVILY_SEARCH_TOOL_NAME);
  const result = await tool.execute(
    'tool-call-1',
    {
      max_results: 3,
      query: ' latest CAD standard ',
      search_depth: 'advanced',
      time_range: 'month',
      topic: 'news',
    },
    undefined,
    undefined,
    {} as never,
  );

  assert.deepEqual(calls, [
    {
      options: {
        chunksPerSource: 3,
        includeAnswer: false,
        includeImageDescriptions: false,
        includeImages: false,
        includeRawContent: false,
        maxResults: 3,
        searchDepth: 'advanced',
        timeRange: 'month',
        timeout: 30_000,
        topic: 'news',
      },
      query: 'latest CAD standard',
    },
  ]);
  const content = result.content[0];
  assert.equal(content?.type, 'text');
  assert.match(
    content?.type === 'text' ? content.text : '',
    /https:\/\/example\.com\/source/u,
  );
});

test('Tavily search tool returns best-effort reference images to PI', async () => {
  const tool = createTavilySearchTool({
    imageLoader: async (url) =>
      url.endsWith('front.jpg')
        ? {
            data: Buffer.from('reference image').toString('base64'),
            mimeType: 'image/jpeg',
          }
        : undefined,
    includeImagesByDefault: true,
    search: async (query, options) => {
      assert.equal(options?.includeImages, true);
      assert.equal(options?.includeImageDescriptions, true);
      return {
        images: [
          {
            description: 'Front product view',
            url: 'https://example.com/front.jpg',
          },
          {
            description: 'Unavailable side view',
            url: 'https://example.com/side.jpg',
          },
        ],
        query,
        requestId: 'request-images',
        responseTime: 0.3,
        results: [],
      };
    },
  });

  assert.ok(tool);
  const result = await tool.execute(
    'tool-call-images',
    { query: 'product dimensions and reference photos' },
    undefined,
    undefined,
    {} as never,
  );
  assert.equal(result.content.length, 2);
  assert.deepEqual(result.content[1], {
    data: Buffer.from('reference image').toString('base64'),
    mimeType: 'image/jpeg',
    type: 'image',
  });
  const text = result.content[0];
  assert.match(text?.type === 'text' ? text.text : '', /Front product view/u);
  assert.equal(
    (result.details as { imageCount: number }).imageCount,
    1,
  );
});

test('web reference gate blocks CAD mutations until search succeeds', () => {
  assert.equal(shouldBlockCadToolBeforeWebSearch('read', false), false);
  assert.equal(shouldBlockCadToolBeforeWebSearch('web_search', false), false);
  assert.equal(shouldBlockCadToolBeforeWebSearch('bash', false), true);
  assert.equal(shouldBlockCadToolBeforeWebSearch('edit', false), true);
  assert.equal(shouldBlockCadToolBeforeWebSearch('write', true), false);
});

test('reference image loader rejects local and non-HTTPS hosts', async () => {
  await assert.rejects(
    loadPublicReferenceImage('http://example.com/reference.jpg'),
    /public HTTPS URLs/u,
  );
  await assert.rejects(
    loadPublicReferenceImage('https://127.0.0.1/reference.jpg'),
    /Private reference image hosts/u,
  );
});

test('Tavily search tool does not expose provider error details', async () => {
  const tool = createTavilySearchTool({
    search: async () => {
      throw new Error('request contained tvly-secret-value');
    },
  });

  assert.ok(tool);
  await assert.rejects(
    tool.execute(
      'tool-call-2',
      { query: 'test' },
      undefined,
      undefined,
      {} as never,
    ),
    (error: unknown) =>
      error instanceof Error &&
      error.message === 'Tavily search request failed.' &&
      !error.message.includes('tvly-secret-value'),
  );
});
