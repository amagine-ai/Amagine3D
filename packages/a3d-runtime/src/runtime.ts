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
        'Use text-a3d as the single CAD skill. It internally selects either single-material mode or color mode; do not look for or invoke a separate color CAD skill.',
        `Permanent colors on manufactured parts or regions that distinguish a control, logo, material, inlay, functional region, printable bezel, or identity palette select text-a3d color mode. Read ${join(this.skillsRoot, 'text-a3d', 'color', 'MODE.md')} for that mode. A non-manufactured LED/LCD surface or transient screen content does not select color mode by itself; an explicit single-color request selects text-a3d single-material mode.`,
        'For appearance-sensitive or hybrid Three.js/build123d work, keep the immutable intent as the target and a separate mutable semantic scene graph as the implemented geometry. Iterate autonomously by editing that graph; do not ask for approval between a concept pass and physical compilation.',
        'Treat parts, color regions, and display decoration as separate concepts. Give every physical part exactly one representation master (BRep or mesh), derive mating male/female interface geometry from one clearance recipe, and make cuts, openings, walls, and connectors real manufacturing geometry.',
        'For a reference-sensitive rounded product shell, first encode identity-driving front/side/top silhouette landmarks and local contour changes as explicit cross-sections or a watertight freeform mesh. A four-section ellipse loft, sphere, capsule, or rounded box is only a blockout unless those sections are derived from the reference and the matched silhouettes pass. Prefer a mesh-master exterior when the reference has asymmetric cheeks, shoulders, feet, ears, or other local bulges; it may take precise unit-scale cutters and interface solids tessellated from build123d through the semantic scene compiler.',
        'Construct an enclosure as outer volume minus inner cavity. Every separate printable button, lid, pin, bezel, lens, or base part needs its paired receiving pocket, guide, bore, or socket in the mating part; do not deliver floating inserts or blind decorative port marks.',
        'For a removable enclosure cover or base when ordinary driver access and purchased hardware are acceptable and the user has not chosen another fastening method, start with a locating collar/socket or two well-spaced locating-pin pairs plus two symmetric M3 plastic thread-forming/self-tapping screw connections (two fastener axes). The locator positions the seam and the screws clamp it. Represent each collar/socket or pin/socket locator as an independent paired scene interface and reference one or more of those IDs from the screw joint through locatorInterfaceIds. Derive each removable-part clearance hole, receiver blind pilot, and receiver boss from one shared procedural screw-pair instance; apply one rigid transform to the full group and mirror or pattern repeated axes instead of copying hole coordinates. Never give those three hybrid-scene outputs independent source meshes or transforms. Treat the M3 pilot as a configurable, material-calibrated value and exclude purchased screws from printable outputs. Validate the compiled joint with full cylindrical and annular boolean witness volumes for the clearance, pilot, cover land, boss wall, and blind end; a clear centerline alone is not sufficient.',
        'Treat a real LED/LCD module as a non-manufactured installed component unless the user explicitly asks for a printable dummy. Derive a true front aperture, rear module keepout or seat, and any printable retainers from one screen datum and component envelope. Put the Three.js glass/content surface behind that opening as a display-only node linked to the physical cutter: include it in the final display GLB, but never in STEP, STL, 3MF, printable part counts, or manufacturing booleans.',
        'A Three.js concept GLB is diagnostic, not the final deliverable. After booleans and interfaces compile, feed the resulting physical meshes back into one final display GLB and apply PBR materials there; that GLB may also contain explicitly excluded installed-component visuals such as the screen surface. Never preserve a prettier proxy when it disagrees with the printable surface.',
        'All geometry remains in millimetres at unit scale. When product dimensions are inferred, choose the initial semantic envelope so its spatial bounding-box diagonal does not exceed the smallest usable build extent; this preserves arbitrary rigid-rotation freedom from the first build. Print placement may rigidly rotate and translate a finished part, but must never resize it; repair driving dimensions and rebuild instead.',
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
