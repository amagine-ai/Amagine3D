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

import { createRestrictedToolDefinitions } from './restricted-tools.ts';
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
  readonly stateRoot: string;
  readonly workspaceRoot: string;

  private readonly agentDir: string;
  private readonly model: PiModel;
  private readonly modelRuntime: ModelRuntime;
  private readonly sessionRoot: string;
  private readonly skillDefinitions: readonly Skill[];
  private readonly skillsRoot: string;
  private readonly thinkingLevel: ThinkingLevel;

  private constructor(options: {
    agentDir: string;
    model: PiModel;
    modelName: string;
    modelRuntime: ModelRuntime;
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
    options: PiSessionOptions = {},
  ): Promise<AgentSession> {
    const scopedWorkspaceRoot = this.workspaceRootForSession(sessionId);
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
        'CAD skill routing is mutually exclusive. Object-owned colors that distinguish a display, control, logo, material, inlay, functional region, or identity palette route to text-a3d-color. An explicit single-color request routes to text-a3d.',
        'For create, generate, build, or regenerate requests, pre-existing output files are references only. Rewrite the source and execute the build in the current run.',
        'For CAD tasks with an uploaded reference, recognizable subject, appearance requirement, or multi-color appearance, render the latest artifact and read the generated preview image before claiming success.',
        'Python and all CAD dependencies are available through the python command in the repository-managed virtual environment. Do not use conda and do not install packages during a task.',
        'Place generated CAD source, models, reports, and previews directly in the current working directory so the user interface can discover them.',
      ],
      cwd: scopedWorkspaceRoot,
      extensionFactories: webSearchEnabled
        ? [createRequiredWebSearchExtension()]
        : [],
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
        ...(tavilySearchTool ? [TAVILY_SEARCH_TOOL_NAME] : []),
      ],
      customTools,
    });
    return session;
  }

  workspaceRootForSession(sessionId: string): string {
    if (!USER_SESSION_ID.test(sessionId)) {
      throw new Error('Invalid user session id.');
    }
    return join(this.workspaceRoot, 'sessions', sessionId);
  }
}
