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

import { CompileProgressExtractor } from './compile-progress.ts';
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
  readonly webSearchEnabled: boolean;
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

function configuredWebSearch(value: string | undefined): boolean {
  const normalized = value?.trim().toLowerCase();
  if (!normalized || normalized === 'true') return true;
  if (normalized === 'false') return false;
  throw new Error('CODEX_WEB_SEARCH_ENABLED must be true or false.');
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
          '这是一个 CAD 任务。直接在当前会话目录完成它；先运行一次 `a3d help`，再读取环境变量 `$AMAGINE3D_SKILL_DIR` 指向的确切 `SKILL.md` 一次。不要搜索或读取 cwd、用户目录或全局 skills 中的同名文件；任务分类、阶段顺序、reference 路由和重复动作条件以该项目 skill 为准。',
          '在重复 draft、compile、diagnose 字段读取或 guidance 加载前，应用 `SKILL.md` 的 progress invariant；不要用相同状态重放替代建模判断。',
          '用原生 view_image 读取最新五视图预览。根据实际看到的轮廓、比例、特征尺度、布局和功能关系给出反馈并修改对应参数；无法识别时说明视觉审查未完成，不要用颜色统计冒充看图。',
          '打印方向、支撑、桥接和免支撑结论必须引用当前 profile 绑定的实际 selected orientation 与 mesh audit；报告文字或预期姿态不能覆盖机器 warning。',
          '最终回复前读取本轮最新 `*_compile-result.json`。逐字遵守其中的 `status`、`visualReviewRequired` 和 `deliveryReady`；当 readiness 为 false 时，不得声称已完成、可交付或已完成视觉审查。',
          '在每个主要阶段或耗时工具调用前，用一句简短中文说明当前目标；只描述用户可理解的工作，不复述 shell 命令或内部推理。',
          '不要为了查询 API 主动阅读 `cad_helpers.py` 等内部实现；先使用公开 `a3d` 帮助、guide 或 capability。只有公开接口和具体报错仍不足以定位问题时，才检查最小范围的内部源码。',
          '每个工具调用都必须独立，不依赖上一次 shell 调用留下的变量或目录状态；一次只完成一个清晰操作。若工具包装出现 JavaScript 语法或引号错误，简化调用并立即重试。',
          '耗时命令要保留完整工具返回（包括执行句柄和退出状态），不要只输出 output 字段，也不要把 `a3d draft`、`a3d compile` 或 `a3d diagnose` 管道到 `head`、`tail` 等过滤器；直接进程退出状态和本轮落盘结果才是权威。续读只能使用工具实际返回的句柄；若被拒绝，检查本轮持久化结果，避免重复启动构建或把旧结果当成本轮成功。',
        ].join('\n')
      : '直接处理用户请求；只有确实需要时才修改当前会话目录中的文件。';
  const searchInstruction = webSearchEnabled
    ? [
        '本轮允许使用运行时提供的原生联网搜索；按任务需要使用可用工具补充可靠规格或参考资料。开启权限不代表搜索、原图获取和图像感知已经验证，不要为每轮任务预先做能力探测。',
        ...(taskType === 'cad' ? [
          '只有外观参考会实质改变造型判断时，才搜索少量资料并实际打开图片；搜索不得覆盖 `SKILL.md` 的阶段路由或延迟 assembly-critical functional draft。纯尺寸任务和已有参考无需为了流程搜索。',
          '网页标题或图片文字描述不等于看过图片，照片不能替代可靠的工程规格或尺寸。若当前工具不能搜索、获取或识别图片，准确说明是哪一步不可用，并基于已有资料继续，不要声称参考已查看。',
        ] : []),
      ].join('\n')
    : '本轮联网已关闭；使用用户提供的资料和本地文件，不要尝试通过其他工具联网。';
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
  readonly webSearchEnabled: boolean;
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
    this.webSearchEnabled = configuredWebSearch(
      options.environment.CODEX_WEB_SEARCH_ENABLED,
    );

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
          network: { enabled: this.webSearchEnabled },
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
      webSearchMode: this.webSearchEnabled ? 'live' : 'disabled',
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
          this.webSearchEnabled,
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
    const compileProgress = new CompileProgressExtractor();
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
      const normalized = normalizeThreadEvent(event);
      const progressEvents = compileProgress.extract(event).map(
        (progress): RuntimeEvent => ({
          ...progress,
          type: 'cad.compile.progress',
        }),
      );
      if (event.type === 'item.started') {
        await request.onEvent?.(normalized);
        for (const progress of progressEvents) {
          await request.onEvent?.(progress);
        }
      } else {
        for (const progress of progressEvents) {
          await request.onEvent?.(progress);
        }
        await request.onEvent?.(normalized);
      }
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
