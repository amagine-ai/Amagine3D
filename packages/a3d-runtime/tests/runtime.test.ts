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

import { CompileProgressExtractor } from '../src/compile-progress.ts';
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
  const cadPrompt = codexPrompt('cad', '建模', false);
  assert.match(cadPrompt, /a3d help/u);
  assert.match(cadPrompt, /`\$AMAGINE3D_SKILL_DIR` 指向的确切 `SKILL\.md` 一次/u);
  assert.match(cadPrompt, /不要搜索或读取 cwd、用户目录或全局 skills/u);
  assert.match(cadPrompt, /任务分类、阶段顺序、reference 路由/u);
  assert.match(cadPrompt, /progress invariant/u);
  assert.match(cadPrompt, /重复 draft、compile、diagnose/u);
  assert.match(cadPrompt, /a3d-admission-rejection\/v1/u);
  assert.match(cadPrompt, /读取 matchedResult/u);
  assert.match(cadPrompt, /缺失、不完整或失败证据仍可重试/u);
  assert.match(cadPrompt, /门控不决定阶段、几何、拓扑或修复策略/u);
  assert.match(cadPrompt, /view_image/u);
  assert.match(cadPrompt, /布局和功能关系/u);
  assert.match(cadPrompt, /`printOrientationEvidence` 与 mesh audit/u);
  assert.match(cadPrompt, /rotateDegreesXYZ 才是 STL\/3MF 的实际语义旋转/u);
  assert.match(cadPrompt, /`rotated_xy_90deg=false` 只表示排版时没有额外床面 XY/u);
  assert.match(cadPrompt, /最新 `\*_compile-result\.json`/u);
  assert.match(cadPrompt, /`status`、`visualReviewRequired` 和 `deliveryReady`/u);
  assert.match(cadPrompt, /不得声称已完成、可交付或已完成视觉审查/u);
  assert.match(cadPrompt, /不要为了查询 API/u);
  assert.match(cadPrompt, /公开 `a3d`/u);
  assert.match(cadPrompt, /不复述 shell 命令/u);
  assert.match(cadPrompt, /每个工具调用都必须独立/u);
  assert.match(cadPrompt, /简化调用并立即重试/u);
  assert.match(cadPrompt, /联网已关闭/u);
  assert.doesNotMatch(cadPrompt, /不绑定合同的可见草模/u);
  assert.doesNotMatch(cadPrompt, /视觉方向确认后再锁定/u);
  assert.doesNotMatch(cadPrompt, /一次初始完整 compile/u);
  assert.doesNotMatch(cadPrompt, /查询中二选一/u);
  const searchableCadPrompt = codexPrompt('cad', '建模', true);
  assert.match(searchableCadPrompt, /实质改变造型判断/u);
  assert.match(searchableCadPrompt, /不得覆盖 `SKILL\.md` 的阶段路由/u);
  assert.match(searchableCadPrompt, /延迟 assembly-critical functional draft/u);
  assert.match(searchableCadPrompt, /实际打开图片/u);
  assert.match(searchableCadPrompt, /照片不能替代可靠的工程规格/u);
  assert.match(searchableCadPrompt, /完整工具返回/u);
  assert.match(searchableCadPrompt, /不要把 `a3d draft`、`a3d compile` 或 `a3d diagnose` 管道/u);
  assert.match(searchableCadPrompt, /直接进程退出状态和本轮落盘结果才是权威/u);
  assert.doesNotMatch(searchableCadPrompt, /默认先寻找/u);
  assert.doesNotMatch(codexPrompt('chat', '解释', true), /functional draft/u);
  assert.doesNotMatch(codexPrompt('chat', '解释', false), /a3d help/u);
});

test('extracts only validated compile progress from cumulative and delta output', () => {
  const extractor = new CompileProgressExtractor();
  const sourcePartial =
    'private compiler output\n{"schema":"cad-compile-progress/v1","stage":"source","status":"run';
  const sourceComplete = `${sourcePartial}ning"}\n{"schema":"cad-compile-progress/v1","stage":"source","status":"pass","elapsedMs":1200,"private":"hidden"}\nnot-json`;
  const events: ThreadEvent[] = [
    {
      item: {
        aggregated_output:
          '{"schema":"cad-compile-progress/v1","stage":"fake","status":"running"}\n',
        command: 'printf fake',
        id: 'not-compile',
        status: 'in_progress',
        type: 'command_execution',
      },
      type: 'item.started',
    },
    {
      item: {
        aggregated_output: sourcePartial,
        command: 'a3d compile model_scene.json',
        id: 'compile-cumulative',
        status: 'in_progress',
        type: 'command_execution',
      },
      type: 'item.started',
    },
    {
      item: {
        aggregated_output: sourceComplete,
        command: 'a3d compile model_scene.json',
        id: 'compile-cumulative',
        status: 'in_progress',
        type: 'command_execution',
      },
      type: 'item.updated',
    },
    {
      item: {
        aggregated_output: sourceComplete,
        command: 'a3d compile model_scene.json',
        id: 'compile-cumulative',
        status: 'in_progress',
        type: 'command_execution',
      },
      type: 'item.updated',
    },
    {
      item: {
        aggregated_output: `${sourceComplete}\n{"schema":"cad-compile-progress/v1","stage":"render","status":"running"}`,
        command: 'a3d compile model_scene.json',
        exit_code: 0,
        id: 'compile-cumulative',
        status: 'completed',
        type: 'command_execution',
      },
      type: 'item.completed',
    },
    {
      item: {
        aggregated_output:
          '{"schema":"cad-compile-progress/v1","stage":"mesh-qa:part","status":"',
        command: 'a3d compile model_scene.json',
        id: 'compile-delta',
        status: 'in_progress',
        type: 'command_execution',
      },
      type: 'item.started',
    },
    {
      item: {
        aggregated_output: 'running"}\n',
        command: 'a3d compile model_scene.json',
        id: 'compile-delta',
        status: 'in_progress',
        type: 'command_execution',
      },
      type: 'item.updated',
    },
    {
      item: {
        aggregated_output: 'running"}\n',
        command: 'a3d compile model_scene.json',
        id: 'compile-delta',
        status: 'in_progress',
        type: 'command_execution',
      },
      type: 'item.updated',
    },
    {
      item: {
        aggregated_output:
          '{"schema":"cad-compile-progress/v1","stage":"mesh-qa:part","status":"fail","elapsedMs":250}',
        command: 'a3d compile model_scene.json',
        exit_code: 1,
        id: 'compile-delta',
        status: 'failed',
        type: 'command_execution',
      },
      type: 'item.completed',
    },
    {
      item: {
        aggregated_output: [
          '{"schema":"cad-compile-progress/v1","stage":"bad/path","status":"running"}',
          '{"schema":"cad-compile-progress/v1","stage":"freshness","status":"timeout","elapsedMs":-1}',
          '{"schema":"cad-compile-progress/v1","stage":"freshness","status":"timeout","elapsedMs":500}',
        ].join('\n'),
        command: 'a3d compile model_scene.json',
        exit_code: 1,
        id: 'compile-validation',
        status: 'failed',
        type: 'command_execution',
      },
      type: 'item.completed',
    },
  ];

  const progress = events.flatMap((event) => extractor.extract(event));
  assert.deepEqual(progress, [
    {
      commandId: 'compile-cumulative',
      stage: 'source',
      status: 'running',
    },
    {
      commandId: 'compile-cumulative',
      elapsedMs: 1200,
      stage: 'source',
      status: 'pass',
    },
    {
      commandId: 'compile-cumulative',
      stage: 'render',
      status: 'running',
    },
    {
      commandId: 'compile-delta',
      stage: 'mesh-qa:part',
      status: 'running',
    },
    {
      commandId: 'compile-delta',
      elapsedMs: 250,
      stage: 'mesh-qa:part',
      status: 'fail',
    },
    {
      commandId: 'compile-validation',
      elapsedMs: 500,
      stage: 'freshness',
      status: 'timeout',
    },
  ]);
  assert.doesNotMatch(JSON.stringify(progress), /private compiler output|hidden/u);
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
                  aggregated_output:
                    'private compiler output\n{"schema":"cad-compile-progress/v1","stage":"source","status":"running"}\n',
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
    assert.deepEqual(runtimeEvents.slice(0, 4), [
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
      {
        commandId: 'command-1',
        stage: 'source',
        status: 'running',
        type: 'cad.compile.progress',
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
        AMAGINE3D_EVIDENCE_GATE: 'v1',
        AMAGINE3D_ROOT: root,
        AMAGINE3D_SKILL_DIR: join(root, 'skills', 'text-a3d'),
        PYTHONDONTWRITEBYTECODE: '1',
        PYTHONNOUSERSITE: '1',
      },
    });
    assert.equal(clientOptions?.env?.AMAGINE3D_EVIDENCE_GATE, 'v1');
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

    await runtime.runTurn({
      imagePaths: [],
      message: '解释模型',
      sessionId: SESSION_ID,
      taskType: 'chat',
      threadId: result.threadId,
    });
    assert.equal(clientOptions?.env?.AMAGINE3D_EVIDENCE_GATE, undefined);
    assert.equal(
      clientOptions?.config?.shell_environment_policy?.set?.AMAGINE3D_EVIDENCE_GATE,
      undefined,
    );
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
