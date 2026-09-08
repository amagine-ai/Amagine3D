import { strict as assert } from 'node:assert';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { delimiter, join } from 'node:path';
import { test } from 'node:test';

import type {
  CodexOptions,
  Input,
  ThreadEvent,
  ThreadOptions,
} from '@openai/codex-sdk';

import {
  codexModelId,
  codexPrompt,
  codexReasoningEffort,
  CodexRuntime,
  type RuntimeEvent,
} from '../src/index.ts';

const SESSION_ID = '3b0d4f25-1707-4cc8-92cf-6f5c28edfc93';

test('maps the existing model and reasoning environment to Codex', () => {
  assert.equal(codexModelId('openai/org/gpt-5.5'), 'org/gpt-5.5');
  assert.equal(codexModelId('gpt-5.5'), 'gpt-5.5');
  assert.equal(codexReasoningEffort('off'), 'minimal');
  assert.equal(codexReasoningEffort('xhigh'), 'xhigh');
  assert.throws(() => codexReasoningEffort('turbo'), /LLM_THINKING_LEVEL/u);
  assert.match(codexPrompt('cad', '建模', false), /a3d help/u);
  assert.match(codexPrompt('cad', '建模', false), /不要为了查询 API/u);
  assert.match(codexPrompt('cad', '建模', false), /不复述 shell 命令/u);
  assert.match(codexPrompt('cad', '建模', false), /简化调用并立即重试/u);
  assert.match(codexPrompt('cad', '建模', false), /联网已关闭/u);
  assert.doesNotMatch(codexPrompt('cad', '建模', false), /默认先寻找少量相关参考/u);
  assert.match(codexPrompt('cad', '建模', true), /实际打开图片查看/u);
  assert.match(codexPrompt('cad', '建模', true), /照片不能替代可靠的工程规格/u);
  assert.match(codexPrompt('cad', '建模', true), /完整工具返回/u);
  assert.doesNotMatch(codexPrompt('chat', '解释', true), /造型方向/u);
  assert.doesNotMatch(codexPrompt('chat', '解释', false), /a3d help/u);
});

test('runs isolated threads and exposes only normalized runtime events', async () => {
  const root = await mkdtemp(join(tmpdir(), 'amagine-codex-runtime-'));
  let clientOptions: CodexOptions | undefined;
  let threadOptions: ThreadOptions | undefined;
  let receivedInput: Input | undefined;
  let startCount = 0;
  const resumedThreadIds: string[] = [];
  try {
    const runtime = await CodexRuntime.create(root, {
      environment: {
        LLM_API_KEY: 'test-key',
        LLM_API_TYPE: 'openai-responses',
        LLM_BASE_URL: 'https://gateway.example/v1',
        LLM_MODEL: 'openai/gpt-5.5',
        LLM_THINKING_LEVEL: 'high',
        PATH: '/usr/bin',
      },
      clientFactory: (options) => {
        clientOptions = options;
        const thread = {
          id: 'thread-1',
          runStreamed: async (input: Input) => {
            receivedInput = input;
            async function* events(): AsyncGenerator<ThreadEvent> {
              yield { thread_id: 'thread-1', type: 'thread.started' };
              yield { message: 'Reconnecting... 1/2', type: 'error' };
              yield {
                item: {
                  aggregated_output: 'private compiler output',
                  command: 'a3d compile part_scene.json',
                  id: 'command-1',
                  status: 'in_progress',
                  type: 'command_execution',
                },
                type: 'item.started',
              };
              yield {
                item: { id: 'message-1', text: '完成', type: 'agent_message' },
                type: 'item.completed',
              };
              yield {
                type: 'turn.completed',
                usage: {
                  cache_write_input_tokens: 0,
                  cached_input_tokens: 0,
                  input_tokens: 1,
                  output_tokens: 1,
                  reasoning_output_tokens: 0,
                },
              };
            }
            return { events: events() };
          },
        };
        return {
          resumeThread: (id, options) => {
            resumedThreadIds.push(id);
            threadOptions = options;
            return thread;
          },
          startThread: (options) => {
            startCount += 1;
            threadOptions = options;
            return thread;
          },
        };
      },
    });

    const startedThreadIds: string[] = [];
    const runtimeEvents: RuntimeEvent[] = [];
    const result = await runtime.runTurn({
      imagePaths: ['/tmp/reference.png'],
      message: '创建支架',
      onEvent: (event) => {
        runtimeEvents.push(event);
      },
      onThreadStarted: (threadId) => {
        startedThreadIds.push(threadId);
      },
      sessionId: SESSION_ID,
      taskType: 'cad',
    });

    assert.equal(result.finalResponse, '完成');
    assert.deepEqual(startedThreadIds, ['thread-1']);
    assert.deepEqual(runtimeEvents.slice(0, 3), [
      { threadId: 'thread-1', type: 'thread.started' },
      { message: 'Reconnecting... 1/2', type: 'error' },
      {
        item: {
          command: 'a3d compile part_scene.json',
          id: 'command-1',
          status: 'in_progress',
          type: 'command_execution',
        },
        type: 'item.started',
      },
    ]);
    assert.doesNotMatch(JSON.stringify(runtimeEvents), /private compiler output/u);
    assert.equal(clientOptions?.apiKey, 'test-key');
    assert.equal(clientOptions?.baseUrl, 'https://gateway.example/v1');
    assert.equal(clientOptions?.env?.LLM_API_KEY, undefined);
    assert.equal(clientOptions?.env?.CODEX_API_KEY, undefined);
    assert.equal(clientOptions?.config?.allow_login_shell, false);
    assert.deepEqual(clientOptions?.config?.agents, { enabled: false });
    assert.equal(clientOptions?.config?.default_permissions, 'amagine3d-session');
    assert.deepEqual(clientOptions?.config?.features, {
      code_mode: false,
      code_mode_host: true,
      code_mode_only: false,
      multi_agent: false,
      plugins: false,
      recommended_plugins: false,
      remote_plugin: false,
      skill_search: false,
      skip_host_skill_discovery: true,
      tool_suggest: false,
    });
    assert.deepEqual(clientOptions?.config?.permissions, {
      'amagine3d-session': {
        extends: ':workspace',
        network: { enabled: true },
      },
    });
    assert.deepEqual(clientOptions?.configOverrides, [
      `permissions.amagine3d-session.filesystem={${JSON.stringify(join(root, 'workspace', 'sessions'))}="deny"}`,
    ]);
    assert.equal(
      clientOptions?.config?.model_instructions_file,
      join(root, 'AGENTS.md'),
    );
    assert.deepEqual(clientOptions?.config?.project_root_markers, []);
    assert.equal(clientOptions?.config?.model_provider, 'amagine3d_gateway');
    assert.deepEqual(clientOptions?.config?.model_providers, {
      amagine3d_gateway: {
        base_url: 'https://gateway.example/v1',
        env_key: 'CODEX_API_KEY',
        name: 'Amagine3D Responses gateway',
        request_max_retries: 2,
        stream_max_retries: 2,
        supports_websockets: false,
        wire_api: 'responses',
      },
    });
    assert.deepEqual(clientOptions?.config?.shell_environment_policy, {
      ignore_default_excludes: false,
      inherit: 'core',
      set: {
        AMAGINE3D_ROOT: root,
        AMAGINE3D_SKILL_DIR: join(root, 'skills', 'text-a3d'),
        PYTHONDONTWRITEBYTECODE: '1',
        PYTHONNOUSERSITE: '1',
      },
    });
    assert.match(clientOptions?.env?.CODEX_HOME ?? '', new RegExp(SESSION_ID, 'u'));
    assert.equal(clientOptions?.env?.PATH?.split(delimiter)[0], join(root, 'bin'));
    assert.equal(threadOptions?.approvalPolicy, 'never');
    assert.equal(threadOptions?.sandboxMode, undefined);
    assert.equal(threadOptions?.networkAccessEnabled, undefined);
    assert.equal(threadOptions?.webSearchMode, 'live');
    assert.equal(threadOptions?.model, 'gpt-5.5');
    assert.equal(Array.isArray(receivedInput), true);

    await runtime.runTurn({
      imagePaths: [],
      message: '继续修改',
      sessionId: SESSION_ID,
      taskType: 'cad',
      threadId: result.threadId,
    });
    assert.equal(startCount, 1);
    assert.deepEqual(resumedThreadIds, ['thread-1']);
    assert.deepEqual(clientOptions?.config?.permissions, {
      'amagine3d-session': {
        extends: ':workspace',
        network: { enabled: true },
      },
    });
    assert.equal(threadOptions?.webSearchMode, 'live');
  } finally {
    await rm(root, { force: true, recursive: true });
  }
});

test('only environment configuration controls native search and network access', async () => {
  const root = await mkdtemp(join(tmpdir(), 'amagine-codex-search-'));
  try {
    for (const value of [undefined, '', 'true', 'false', ' FALSE ']) {
      const enabled = value?.trim().toLowerCase() !== 'false';
      let clientOptions: CodexOptions | undefined;
      let threadOptions: ThreadOptions | undefined;
      let receivedInput: Input | undefined;
      const runtime = await CodexRuntime.create(root, {
        environment: { LLM_API_KEY: 'test-key', CODEX_WEB_SEARCH_ENABLED: value },
        clientFactory: (options) => {
          clientOptions = options;
          const createThread = (options?: ThreadOptions) => {
            threadOptions = options;
            return {
              id: 'thread-search',
              async runStreamed(input: Input) {
                receivedInput = input;
                async function* events(): AsyncGenerator<ThreadEvent> {
                  yield { type: 'item.completed', item: { id: 'answer', type: 'agent_message', text: 'ok' } };
                }
                return { events: events() };
              },
            };
          };
          return { startThread: createThread, resumeThread: (_, options) => createThread(options) };
        },
      });
      assert.equal(runtime.webSearchEnabled, enabled);
      for (const legacyValue of [undefined, true, false]) {
        await runtime.runTurn({
          imagePaths: [], message: '建模', sessionId: SESSION_ID, taskType: 'cad',
          ...(legacyValue === undefined ? {} : { threadId: 'thread-search', webSearchEnabled: legacyValue }),
        });
        assert.equal(threadOptions?.webSearchMode, enabled ? 'live' : 'disabled');
        assert.deepEqual(clientOptions?.config?.permissions, {
          'amagine3d-session': { extends: ':workspace', network: { enabled } },
        });
        const prompt = JSON.stringify(receivedInput);
        if (enabled) assert.match(prompt, /原生联网搜索/u);
        else assert.match(prompt, /不要尝试通过其他工具联网/u);
      }
    }
    await assert.rejects(
      CodexRuntime.create(root, { environment: { LLM_API_KEY: 'test-key', CODEX_WEB_SEARCH_ENABLED: 'fales' } }),
      /CODEX_WEB_SEARCH_ENABLED must be true or false/u,
    );
  } finally {
    await rm(root, { force: true, recursive: true });
  }
});

test('rejects non-Responses legacy API types', async () => {
  const root = await mkdtemp(join(tmpdir(), 'amagine-codex-api-'));
  try {
    await assert.rejects(
      CodexRuntime.create(root, {
        environment: {
          LLM_API_KEY: 'test-key',
          LLM_API_TYPE: 'openai-completions',
        },
      }),
      /Responses API/u,
    );
  } finally {
    await rm(root, { force: true, recursive: true });
  }
});
