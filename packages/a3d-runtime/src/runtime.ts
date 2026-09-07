import { mkdir } from 'node:fs/promises';
import { delimiter, join } from 'node:path';

import {
  Codex,
  type CodexOptions,
  type Input,
  type ModelReasoningEffort,
  type ThreadEvent,
  type ThreadOptions,
} from '@openai/codex-sdk';

import { normalizeThreadEvent, type RuntimeEvent } from './events.ts';

export type RuntimeTaskType = 'cad' | 'chat';

export interface RuntimeSkillSummary {
  description: string;
  name: string;
}

export interface CodexTurnRequest {
  imagePaths: readonly string[];
  message: string;
  onEvent?: (event: RuntimeEvent) => Promise<void> | void;
  onThreadStarted?: (threadId: string) => Promise<void> | void;
  sessionId: string;
  signal?: AbortSignal;
  taskType: RuntimeTaskType;
  threadId?: string;
  webSearchEnabled?: boolean;
}

export interface CodexTurnResult {
  finalResponse: string;
  threadId: string;
}

export interface CodexRuntimeLike {
  readonly configured: boolean;
  readonly modelName: string;
  readonly runtimeReady: boolean;
  readonly skillDiagnostics: readonly string[];
  readonly skills: readonly RuntimeSkillSummary[];
  readonly stateRoot: string;
  readonly workspaceRoot: string;
  runTurn(request: CodexTurnRequest): Promise<CodexTurnResult>;
}

interface CodexThreadLike {
  readonly id: string | null;
  runStreamed(
    input: Input,
    options?: { signal?: AbortSignal },
  ): Promise<{ events: AsyncGenerator<ThreadEvent> }>;
}

interface CodexClientLike {
  resumeThread(id: string, options?: ThreadOptions): CodexThreadLike;
  startThread(options?: ThreadOptions): CodexThreadLike;
}

type CodexClientFactory = (options: CodexOptions) => CodexClientLike;

const DEFAULT_MODEL = 'openai/gpt-5.5';
const SESSION_PERMISSION_PROFILE = 'amagine3d-session';
const USER_SESSION_ID =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/iu;
const RESPONSE_API_TYPES = new Set([
  'openai-codex-responses',
  'openai-responses',
]);
const REASONING_LEVELS = new Set<ModelReasoningEffort>([
  'minimal',
  'low',
  'medium',
  'high',
  'xhigh',
  'max',
  'ultra',
]);

function stringEnvironment(
  environment: NodeJS.ProcessEnv,
): Record<string, string> {
  return Object.fromEntries(
    Object.entries(environment).filter(
      (entry): entry is [string, string] => typeof entry[1] === 'string',
    ),
  );
}

export function codexModelId(modelName: string): string {
  const normalized = modelName.trim();
  const separator = normalized.indexOf('/');
  return separator > 0 && separator < normalized.length - 1
    ? normalized.slice(separator + 1)
    : normalized;
}

export function codexReasoningEffort(
  value: string | undefined,
): ModelReasoningEffort {
  const normalized = value?.trim().toLowerCase() || 'medium';
  if (normalized === 'off') return 'minimal';
  if (REASONING_LEVELS.has(normalized as ModelReasoningEffort)) {
    return normalized as ModelReasoningEffort;
  }
  throw new Error(`Unsupported LLM_THINKING_LEVEL: ${normalized}`);
}

export function codexPrompt(
  taskType: RuntimeTaskType,
  message: string,
  webSearchEnabled: boolean,
): string {
  const request = message.trim() || '请查看并分析上传的图片。';
  const taskInstruction =
    taskType === 'cad'
      ? [
          '这是一个 CAD 任务。直接在当前会话目录完成它；先运行 `a3d help`，使用项目提供的 CAD 工具，并在回复前检查生成的预览。',
          '用原生 view_image 读取最新五视图预览。工具返回图像内容但无法识别时，应说明视觉审查未完成；不要用颜色统计冒充看图，也不要改用其他技能启动查看器。',
          '在每个主要阶段或耗时工具调用前，用一句简短中文说明当前目标；只描述用户可理解的工作，不复述 shell 命令或内部推理。',
          '不要为了查询 API 主动阅读 `cad_helpers.py` 等内部实现；先使用 `a3d help`、一个与当前问题直接相关的 `a3d guide TOPIC`，以及 `a3d capabilities --symbol NAME`。只有公开接口和具体报错仍不足以定位问题时，才检查最小范围的内部源码。',
          '工具调用保持简短，一次只完成一个清晰操作，避免为了查看资料拼接多条 shell 命令。如果工具包装出现 JavaScript 语法或引号错误，简化调用并立即重试；单次包装错误不代表 CAD 工具不可用。',
        ].join('\n')
      : '直接处理用户请求；只有确实需要时才修改当前会话目录中的文件。';
  const searchInstruction = webSearchEnabled
    ? '本轮允许联网搜索；仅在搜索能补充可靠规格或参考资料时使用。'
    : '';
  return [request, taskInstruction, searchInstruction].filter(Boolean).join('\n\n');
}

export class CodexRuntime implements CodexRuntimeLike {
  readonly configured: boolean;
  readonly modelName: string;
  readonly runtimeReady = true;
  readonly skillDiagnostics: readonly string[] = [];
  readonly skills: readonly RuntimeSkillSummary[] = [
    {
      description: 'Create and validate printable 3D models with the project a3d CLI.',
      name: 'text-a3d',
    },
  ];
  readonly stateRoot: string;
  readonly workspaceRoot: string;

  private readonly apiKey: string | undefined;
  private readonly baseUrl: string | undefined;
  private readonly clientFactory: CodexClientFactory;
  private readonly environment: NodeJS.ProcessEnv;
  private readonly modelId: string;
  private readonly projectRoot: string;
  private readonly reasoningEffort: ModelReasoningEffort;

  private constructor(options: {
    clientFactory: CodexClientFactory;
    environment: NodeJS.ProcessEnv;
    projectRoot: string;
  }) {
    this.clientFactory = options.clientFactory;
    this.environment = options.environment;
    this.projectRoot = options.projectRoot;
    this.stateRoot = join(options.projectRoot, '.amagine-state');
    this.workspaceRoot = join(options.projectRoot, 'workspace');

    this.apiKey =
      options.environment.LLM_API_KEY?.trim() ||
      options.environment.CODEX_API_KEY?.trim() ||
      options.environment.OPENAI_API_KEY?.trim();
    this.baseUrl =
      options.environment.LLM_BASE_URL?.trim() ||
      options.environment.OPENAI_BASE_URL?.trim();
    this.configured = Boolean(this.apiKey);
    this.modelName = options.environment.LLM_MODEL?.trim() || DEFAULT_MODEL;
    this.modelId = codexModelId(this.modelName);
    this.reasoningEffort = codexReasoningEffort(
      options.environment.LLM_THINKING_LEVEL,
    );

    const apiType = options.environment.LLM_API_TYPE?.trim();
    if (apiType && !RESPONSE_API_TYPES.has(apiType)) {
      throw new Error(
        `A3D runtime requires a Responses API endpoint; unsupported LLM_API_TYPE: ${apiType}`,
      );
    }
  }

  static async create(
    projectRoot: string,
    options: {
      clientFactory?: CodexClientFactory;
      environment?: NodeJS.ProcessEnv;
    } = {},
  ): Promise<CodexRuntime> {
    const runtime = new CodexRuntime({
      clientFactory: options.clientFactory ?? ((config) => new Codex(config)),
      environment: options.environment ?? process.env,
      projectRoot,
    });
    await Promise.all([
      mkdir(runtime.workspaceRoot, { recursive: true }),
      mkdir(join(runtime.stateRoot, 'codex'), { recursive: true }),
    ]);
    return runtime;
  }

  async runTurn(request: CodexTurnRequest): Promise<CodexTurnResult> {
    if (!USER_SESSION_ID.test(request.sessionId)) {
      throw new Error('Invalid user session id.');
    }
    if (!this.apiKey) {
      throw new Error('LLM_API_KEY is not configured in .env.');
    }
    const workingDirectory = join(
      this.workspaceRoot,
      'sessions',
      request.sessionId,
    );
    const codexHome = join(this.stateRoot, 'codex', request.sessionId);
    await Promise.all([
      mkdir(workingDirectory, { recursive: true }),
      mkdir(codexHome, { recursive: true }),
    ]);

    const environment = stringEnvironment(this.environment);
    delete environment.LLM_API_KEY;
    delete environment.CODEX_API_KEY;
    delete environment.OPENAI_API_KEY;
    environment.CODEX_HOME = codexHome;
    environment.AMAGINE3D_ROOT = this.projectRoot;
    environment.AMAGINE3D_SKILL_DIR = join(
      this.projectRoot,
      'skills',
      'text-a3d',
    );
    environment.PYTHONDONTWRITEBYTECODE = '1';
    environment.PYTHONNOUSERSITE = '1';
    environment.PATH = [
      join(this.projectRoot, 'bin'),
      join(
        this.projectRoot,
        '.venv',
        process.platform === 'win32' ? 'Scripts' : 'bin',
      ),
      environment.PATH ?? '',
    ]
      .filter(Boolean)
      .join(delimiter);

    const config: NonNullable<CodexOptions['config']> = {
      agents: { enabled: false },
      allow_login_shell: false,
      default_permissions: SESSION_PERMISSION_PROFILE,
      features: {
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
      },
      model_instructions_file: join(this.projectRoot, 'AGENTS.md'),
      permissions: {
        [SESSION_PERMISSION_PROFILE]: {
          extends: ':workspace',
          network: { enabled: Boolean(request.webSearchEnabled) },
        },
      },
      project_root_markers: [],
      shell_environment_policy: {
        ignore_default_excludes: false,
        inherit: 'core',
        set: {
          AMAGINE3D_ROOT: this.projectRoot,
          AMAGINE3D_SKILL_DIR: join(
            this.projectRoot,
            'skills',
            'text-a3d',
          ),
          PYTHONDONTWRITEBYTECODE: '1',
          PYTHONNOUSERSITE: '1',
        },
      },
    };
    if (this.baseUrl) {
      config.model_provider = 'amagine3d_gateway';
      config.model_providers = {
        amagine3d_gateway: {
          base_url: this.baseUrl,
          env_key: 'CODEX_API_KEY',
          name: 'Amagine3D Responses gateway',
          request_max_retries: 2,
          stream_max_retries: 2,
          supports_websockets: false,
          wire_api: 'responses',
        },
      };
    }
    const client = this.clientFactory({
      apiKey: this.apiKey,
      ...(this.baseUrl ? { baseUrl: this.baseUrl } : {}),
      config,
      configOverrides: [
        `permissions.${SESSION_PERMISSION_PROFILE}.filesystem={${JSON.stringify(join(this.workspaceRoot, 'sessions'))}="deny"}`,
      ],
      env: environment,
    });
    const threadOptions: ThreadOptions = {
      approvalPolicy: 'never',
      model: this.modelId,
      modelReasoningEffort: this.reasoningEffort,
      skipGitRepoCheck: true,
      webSearchMode: request.webSearchEnabled ? 'live' : 'disabled',
      workingDirectory,
    };
    const thread = request.threadId
      ? client.resumeThread(request.threadId, threadOptions)
      : client.startThread(threadOptions);
    const input: Input = [
      {
        text: codexPrompt(
          request.taskType,
          request.message,
          Boolean(request.webSearchEnabled),
        ),
        type: 'text',
      },
      ...request.imagePaths.map((path) => ({
        path,
        type: 'local_image' as const,
      })),
    ];
    const { events } = await thread.runStreamed(input, {
      signal: request.signal,
    });

    let finalResponse = '';
    let lastRuntimeError = '';
    let threadId = request.threadId;
    for await (const event of events) {
      if (event.type === 'thread.started') {
        threadId = event.thread_id;
        await request.onThreadStarted?.(event.thread_id);
      }
      if (
        event.type === 'item.completed' &&
        event.item.type === 'agent_message'
      ) {
        finalResponse = event.item.text;
      }
      await request.onEvent?.(normalizeThreadEvent(event));
      if (event.type === 'turn.failed') throw new Error(event.error.message);
      if (event.type === 'error') lastRuntimeError = event.message;
    }
    const resolvedThreadId = threadId || thread.id;
    if (!resolvedThreadId) throw new Error('A3D runtime did not return a thread id.');
    if (!finalResponse.trim()) {
      throw new Error(lastRuntimeError || 'A3D did not return a final response.');
    }
    return { finalResponse, threadId: resolvedThreadId };
  }
}
