import { mkdirSync } from 'node:fs';
import { join } from 'node:path';

import {
  createAgentSession,
  DefaultResourceLoader,
  ModelRuntime,
  SessionManager,
  SettingsManager,
  loadSkillsFromDir,
  type AgentSession,
  type Skill,
} from '@earendil-works/pi-coding-agent';

import {
  CAD_COMPILE_ISSUES_TOOL_NAME,
  CAD_COMPILE_TOOL_NAME,
  cadIntentStatePath,
  createCadCompileContextExtension,
  createCadCompileIssuesTool,
  createCadCompileTool,
  createCadCompileResultExtension,
} from './cad-compile-tool.ts';
import {
  CAD_CAPABILITIES_TOOL_NAME,
  createCadCapabilitiesTool,
} from './cad-capabilities-tool.ts';
import { createRestrictedToolDefinitions } from './restricted-tools.ts';
import { createInvalidEncryptedContentRetryExtension } from './provider-retry.ts';
import { sanitizeInternalPromptHistory } from './internal-prompts.ts';
import {
  createReferenceAnalyzeTool,
  REFERENCE_ANALYZE_TOOL_NAME,
} from './reference-analyze-tool.ts';
import {
  createRequiredWebSearchExtension,
  createTavilySearchTool,
  TAVILY_SEARCH_TOOL_NAME,
} from './tavily-search.ts';
import {
  booleanValue,
  inputModalities,
  optionalApiType,
  parseModelSpec,
  positiveInteger,
  thinkingLevel,
  type ThinkingLevel,
} from './runtime-config.ts';

export interface SkillSummary {
  description: string;
  name: string;
}

export interface PiSessionOptions {
  intentScopeId: string;
  webSearchEnabled?: boolean;
}

type PiModel = NonNullable<ReturnType<ModelRuntime['getModel']>>;

const USER_SESSION_ID =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/iu;

export class PiRuntime {
  readonly modelName: string;
  readonly runtimeReady = true;
  readonly skillDiagnostics: readonly string[];
  readonly skills: readonly SkillSummary[];
  readonly skillsRoot: string;
  readonly stateRoot: string;
  readonly workspaceRoot: string;

  private readonly agentDir: string;
  private readonly model: PiModel;
  private readonly modelRuntime: ModelRuntime;
  private readonly projectRoot: string;
  private readonly sessionRoot: string;
  private readonly skillDefinitions: readonly Skill[];
  private readonly thinkingLevel: ThinkingLevel;

  private constructor(options: {
    agentDir: string;
    model: PiModel;
    modelName: string;
    modelRuntime: ModelRuntime;
    projectRoot: string;
    sessionRoot: string;
    skillDefinitions: readonly Skill[];
    skillDiagnostics: readonly string[];
    skillsRoot: string;
    stateRoot: string;
    thinkingLevel: ThinkingLevel;
    workspaceRoot: string;
  }) {
    this.agentDir = options.agentDir;
    this.model = options.model;
    this.modelName = options.modelName;
    this.modelRuntime = options.modelRuntime;
    this.projectRoot = options.projectRoot;
    this.sessionRoot = options.sessionRoot;
    this.skillDefinitions = options.skillDefinitions;
    this.skillDiagnostics = options.skillDiagnostics;
    this.skillsRoot = options.skillsRoot;
    this.stateRoot = options.stateRoot;
    this.thinkingLevel = options.thinkingLevel;
    this.workspaceRoot = options.workspaceRoot;
    this.skills = options.skillDefinitions
      .map(({ description, name }) => ({ description, name }))
      .sort((left, right) => left.name.localeCompare(right.name));
  }

  static async create(projectRoot: string): Promise<PiRuntime> {
    const modelName = process.env.LLM_MODEL?.trim() || 'openai/gpt-5.5';
    const { id, provider } = parseModelSpec(modelName);
    const stateRoot = join(projectRoot, '.amagine-state');
    const workspaceRoot = join(projectRoot, 'workspace');
    const agentDir = join(stateRoot, 'agent');
    const sessionRoot = join(stateRoot, 'sessions');
    const skillsRoot = join(projectRoot, 'skills');
    mkdirSync(agentDir, { recursive: true });
    mkdirSync(sessionRoot, { recursive: true });
    mkdirSync(workspaceRoot, { recursive: true });

    const modelRuntime = await ModelRuntime.create({
      authPath: join(stateRoot, 'auth.json'),
      modelsPath: null,
      refreshOnCreate: false,
    });

    const baseUrl = process.env.LLM_BASE_URL?.trim();
    const api = optionalApiType(process.env.LLM_API_TYPE);
    if (baseUrl || api) {
      modelRuntime.registerProvider(provider, {
        ...(api ? { api } : {}),
        ...(baseUrl ? { baseUrl } : {}),
      });
    }

    let model = modelRuntime.getModel(provider, id);
    if (!model) {
      const providerDefault = modelRuntime.getModels(provider)[0];
      const customApi = api ?? providerDefault?.api;
      const customBaseUrl = baseUrl ?? providerDefault?.baseUrl;
      if (!customApi || !customBaseUrl) {
        throw new Error(
          `Cannot register custom model ${modelName}. Set LLM_BASE_URL and LLM_API_TYPE.`,
        );
      }
      modelRuntime.registerProvider(provider, {
        api: customApi,
        baseUrl: customBaseUrl,
        models: [
          {
            contextWindow: positiveInteger(
              'LLM_CONTEXT_WINDOW',
              process.env.LLM_CONTEXT_WINDOW,
              128_000,
            ),
            cost: { cacheRead: 0, cacheWrite: 0, input: 0, output: 0 },
            id,
            input: inputModalities(process.env.LLM_INPUT_MODALITIES),
            maxTokens: positiveInteger(
              'LLM_MAX_TOKENS',
              process.env.LLM_MAX_TOKENS,
              16_384,
            ),
            name: id,
            reasoning: booleanValue(
              'LLM_REASONING',
              process.env.LLM_REASONING,
              true,
            ),
          },
        ],
      });
      model = modelRuntime.getModel(provider, id);
      if (!model) {
        throw new Error(
          `Amagine3D Agent could not load ${modelName} after registration.`,
        );
      }
    }

    const apiKey = process.env.LLM_API_KEY?.trim();
    if (apiKey) await modelRuntime.setRuntimeApiKey(provider, apiKey);

    const loadedSkills = loadSkillsFromDir({
      dir: skillsRoot,
      source: 'project',
    });

    return new PiRuntime({
      agentDir,
      model,
      modelName,
      modelRuntime,
      projectRoot,
      sessionRoot,
      skillDefinitions: loadedSkills.skills,
      skillDiagnostics: loadedSkills.diagnostics.map(
        (diagnostic) => `${diagnostic.path}: ${diagnostic.message}`,
      ),
      skillsRoot,
      stateRoot,
      thinkingLevel: thinkingLevel(process.env.LLM_THINKING_LEVEL),
      workspaceRoot,
    });
  }

  async createSession(
    sessionId: string,
    options: PiSessionOptions,
  ): Promise<AgentSession> {
    const scopedWorkspaceRoot = this.workspaceRootForSession(sessionId);
    const uploadRoot = join(this.stateRoot, 'uploads', sessionId);
    const webSearchEnabled = options.webSearchEnabled ?? false;
    const tavilyApiKey = process.env.TAVILY_API_KEY?.trim();
    if (webSearchEnabled && !tavilyApiKey) {
      throw new Error(
        'TAVILY_API_KEY is required when web references are enabled.',
      );
    }
    mkdirSync(scopedWorkspaceRoot, { recursive: true });
    const resourceLoader = new DefaultResourceLoader({
      agentDir: this.agentDir,
      appendSystemPrompt: [
        `The available project skills are located at ${this.skillsRoot}.`,
        `Your only writable directory is ${scopedWorkspaceRoot}. Repository code and skills are read-only. Keep every task output inside this directory.`,
        'Use a matching skill whenever the user request falls within its description.',
        'For CAD work, text-a3d is the authoritative modeling and QA procedure. Keep one immutable intent target per user-turn scope and one mutable semantic scene; preserve the intent through repairs, and use a new intent filename only when a later user request changes the target.',
        'Use the structured CAD tools described by text-a3d. cad_compile is the only compilation, QA, packaging, and rendering entry point. Its first compact result indexes stable issue IDs; later results emphasize repairDelta. Query only the required full diagnostics with cad_compile_issues. Context compaction removes superseded compile payloads, never QA execution or persisted evidence.',
        'Do not trade correctness for speed: preserve millimetre unit scale and the requested geometry, resolve every error plus relevant warning or not_evaluated result, and read the fresh returned preview before claiming delivery. A passing compile without visual review is not delivery-ready.',
        'Use the repository-managed Python environment without installing packages. Place generated CAD sources, models, reports, and previews in the current working directory.',
      ],
      cwd: scopedWorkspaceRoot,
      extensionFactories: [
        createInvalidEncryptedContentRetryExtension(),
        createCadCompileResultExtension(),
        createCadCompileContextExtension(),
        ...(webSearchEnabled ? [createRequiredWebSearchExtension()] : []),
      ],
      noExtensions: true,
      noPromptTemplates: true,
      noThemes: true,
      skillsOverride: () => ({
        diagnostics: [],
        skills: [...this.skillDefinitions],
      }),
    });
    await resourceLoader.reload();

    const sessions = await SessionManager.listAll(this.sessionRoot);
    const previous = sessions.find((session) => session.id === sessionId);
    const sessionManager = previous
      ? SessionManager.open(
          previous.path,
          this.sessionRoot,
          scopedWorkspaceRoot,
        )
      : SessionManager.create(scopedWorkspaceRoot, this.sessionRoot, {
          id: sessionId,
        });

    const tavilySearchTool = webSearchEnabled
      ? createTavilySearchTool({
          apiKey: tavilyApiKey,
          includeImagesByDefault: true,
          searchDepthByDefault: 'advanced',
        })
      : undefined;
    const customTools = [
      ...createRestrictedToolDefinitions(scopedWorkspaceRoot),
      createCadCapabilitiesTool(this.projectRoot),
      createReferenceAnalyzeTool(
        this.projectRoot,
        scopedWorkspaceRoot,
        uploadRoot,
      ),
      createCadCompileTool({
        intentScopeId: options.intentScopeId,
        intentStatePath: cadIntentStatePath(this.sessionRoot, sessionId),
        projectRoot: this.projectRoot,
        workspaceRoot: scopedWorkspaceRoot,
      }),
      createCadCompileIssuesTool(scopedWorkspaceRoot),
      ...(tavilySearchTool ? [tavilySearchTool] : []),
    ];
    const { session } = await createAgentSession({
      agentDir: this.agentDir,
      cwd: scopedWorkspaceRoot,
      model: this.model,
      modelRuntime: this.modelRuntime,
      resourceLoader,
      sessionManager,
      settingsManager: SettingsManager.inMemory({
        compaction: { enabled: true },
        images: { autoResize: false },
      }),
      thinkingLevel: this.thinkingLevel,
      tools: [
        'read',
        'bash',
        'edit',
        'write',
        'grep',
        'find',
        'ls',
        CAD_CAPABILITIES_TOOL_NAME,
        REFERENCE_ANALYZE_TOOL_NAME,
        CAD_COMPILE_TOOL_NAME,
        CAD_COMPILE_ISSUES_TOOL_NAME,
        ...(tavilySearchTool ? [TAVILY_SEARCH_TOOL_NAME] : []),
      ],
      customTools,
    });
    session.state.messages = sanitizeInternalPromptHistory(
      session.state.messages,
    );
    return session;
  }

  workspaceRootForSession(sessionId: string): string {
    if (!USER_SESSION_ID.test(sessionId)) {
      throw new Error('Invalid user session id.');
    }
    return join(this.workspaceRoot, 'sessions', sessionId);
  }
}
