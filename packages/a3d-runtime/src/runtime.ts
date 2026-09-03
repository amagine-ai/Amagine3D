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
  CAD_COMPILE_TOOL_NAME,
  cadIntentStatePath,
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
        'Use text-a3d as the single Agent-visible CAD authoring surface. For every CAD generation, modification, or inspection, keep one immutable evidence-cad-intent/v5 target and one mutable evidence-semantic-scene/v1 implementation, then let the skill select internal compilers. The runtime hash-binds the first valid intent used in a user turn; preserve it throughout every repair attempt. When a later user request explicitly changes the target, author a new intent filename instead of rewriting an earlier contract. Do not expose separate single-material, color, or Hybrid modes.',
        `Manufactured color and material are semantic properties of physical parts or regions inside that same authoring surface. ${join(this.skillsRoot, 'text-a3d', 'color', 'BACKEND.md')} is internal backend documentation; read it only when implementing or debugging manufactured-color compilation, never as another Agent mode. LED/LCD content and other transient display appearance remain display-only unless the user explicitly requests printable geometry.`,
        'Let text-a3d route its supporting references from the complete task semantics and the current intent/scene, never from keyword matching or fixed component-name classes. Load only guidance relevant to the requested representation, multipart construction, enclosure, installed components, or manufactured color; do not inspect compiler implementation files during ordinary modeling.',
        'When an installed build123d symbol, representation family, or interface helper is uncertain, call cad_capabilities for a compact version-bound manifest before guessing or inspecting compiler implementation. Treat that manifest as advisory evidence, not as a mandatory phase or a shape template.',
        'cad_capabilities, reference_analyze, and cad_compile are peer tools inside the existing open Agent loop. Select and revisit them from current evidence and judgment; do not turn their availability into a fixed workflow state machine.',
        'Iterate autonomously by editing the semantic scene while preserving the immutable intent; do not ask for approval between a concept pass and physical compilation.',
        'Treat parts, color regions, and display decoration as separate concepts. Give every physical part exactly one representation master (BRep or mesh), derive mating male/female interface geometry from one clearance recipe, and make cuts, openings, walls, and connectors real manufacturing geometry. Build every cavity, pocket, recess, seat, or installed-component keepout by subtracting a real cutter from its owning part; observe() may preserve planning evidence but never replaces the cut. A functional port or connector opening for an internal item must form one continuous passage from the declared exterior face into its target interior cavity or keepout: extend the cutter across the full wall thickness and beyond both boundaries before applying checked_cut(). A shallow exterior recess is not a functional opening. The installed item itself may remain display-only, but its manufactured opening may not. Add support, stops, retention, and a feasible insertion path when the assembly needs them.',
        'Reason about installed items from assembly behavior, not their names. If an item must enter an enclosed volume or remain serviceable, default to a removable service cover with a locating seam and accessible direct fastening into printed plastic unless the user chose another closure; align each cover clearance hole and receiver pilot boss from one screw datum. If an item only passes through or follows a surface, model only the necessary opening, slot, channel, or local retention. Escalate to a serviceable enclosure only when the spatial and maintenance requirements call for it.',
        'A Three.js concept GLB is diagnostic, not the final deliverable. After booleans and interfaces compile, feed the resulting physical meshes back into one final display GLB and apply PBR materials there; that GLB may also contain explicitly excluded installed-component visuals such as the screen surface. Never preserve a prettier proxy when it disagrees with the printable surface.',
        'All geometry remains in millimetres at unit scale. When product dimensions are inferred, choose the initial semantic envelope so its spatial bounding-box diagonal does not exceed the smallest usable build extent; this preserves arbitrary rigid-rotation freedom from the first build. Print placement may rigidly rotate and translate a finished part, but must never resize it; repair driving dimensions and rebuild instead.',
        'For create, generate, build, or regenerate requests, pre-existing output files are references only. Rewrite the source and execute the build in the current run.',
        'For every CAD generation, modification, regeneration, or inspection, first create and validate the immutable intent in a separate contract-only authoring step, then call cad_compile with the current marker, intent, semantic scene path, and generated build source. Never put write_intent in the build source or run the full build source manually to bootstrap intent; the source may create or refresh the scene inside cad_compile. The tool selects internal compilers, runs applicable QA, and returns a fresh hash-bound display render. Read the exact returned preview before claiming success; a passing compile alone is not delivery-ready. This visual gate does not depend on prompt keywords, a reference image, subject recognizability, or color. When the user supplies a reference image, call reference_analyze on that exact saved file and SHA-256 before the build it informs, then compare the resulting evidence with the fresh display render. Do not invoke the analyzer script through bash.',
        'Python and all CAD dependencies are available through the python command in the repository-managed virtual environment. Do not use conda and do not install packages during a task.',
        'Place generated CAD source, models, reports, and previews directly in the current working directory so the user interface can discover them.',
      ],
      cwd: scopedWorkspaceRoot,
      extensionFactories: [
        createInvalidEncryptedContentRetryExtension(),
        createCadCompileResultExtension(),
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
