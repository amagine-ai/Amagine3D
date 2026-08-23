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

import type { SkillSummary } from '../src/types.ts';
import { parseModelSpec } from './protocol.ts';
import { createRestrictedToolDefinitions } from './restricted-tools.ts';
import { sessionWorkspaceRoot } from './sessions.ts';

const API_TYPES = [
  'anthropic-messages',
  'openai-completions',
  'openai-responses',
  'azure-openai-responses',
  'openai-codex-responses',
  'mistral-conversations',
  'google-generative-ai',
  'google-vertex',
  'bedrock-converse-stream',
] as const;

const THINKING_LEVELS = [
  'off',
  'minimal',
  'low',
  'medium',
  'high',
  'xhigh',
  'max',
] as const;

type ApiType = (typeof API_TYPES)[number];
type ThinkingLevel = (typeof THINKING_LEVELS)[number];
type PiModel = NonNullable<ReturnType<ModelRuntime['getModel']>>;
type InputModality = PiModel['input'][number];

function optionalApiType(value: string | undefined): ApiType | undefined {
  if (!value?.trim()) return undefined;
  const normalized = value.trim();
  if ((API_TYPES as readonly string[]).includes(normalized)) {
    return normalized as ApiType;
  }
  throw new Error(`Unsupported LLM_API_TYPE: ${normalized}`);
}

function thinkingLevel(value: string | undefined): ThinkingLevel {
  const normalized = value?.trim() || 'medium';
  if ((THINKING_LEVELS as readonly string[]).includes(normalized)) {
    return normalized as ThinkingLevel;
  }
  throw new Error(`Unsupported LLM_THINKING_LEVEL: ${normalized}`);
}

function positiveInteger(
  name: string,
  value: string | undefined,
  fallback: number,
): number {
  if (!value?.trim()) return fallback;
  const parsed = Number(value);
  if (Number.isSafeInteger(parsed) && parsed > 0) return parsed;
  throw new Error(`${name} must be a positive integer. Received: ${value}`);
}

function booleanValue(
  name: string,
  value: string | undefined,
  fallback: boolean,
): boolean {
  if (!value?.trim()) return fallback;
  const normalized = value.trim().toLowerCase();
  if (normalized === 'true') return true;
  if (normalized === 'false') return false;
  throw new Error(`${name} must be true or false. Received: ${value}`);
}

function inputModalities(value: string | undefined): InputModality[] {
  const values = (value?.trim() || 'text,image')
    .split(',')
    .map((item) => item.trim().toLowerCase())
    .filter(Boolean);
  if (
    values.length === 0 ||
    values.some((item) => item !== 'text' && item !== 'image')
  ) {
    throw new Error(
      `LLM_INPUT_MODALITIES accepts comma-separated text and image values. Received: ${value}`,
    );
  }
  return [...new Set(values)] as InputModality[];
}

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
        throw new Error(`PI could not load ${modelName} after registration.`);
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

  async createSession(sessionId: string): Promise<AgentSession> {
    const scopedWorkspaceRoot = this.workspaceRootForSession(sessionId);
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
      tools: ['read', 'bash', 'edit', 'write', 'grep', 'find', 'ls'],
      customTools: createRestrictedToolDefinitions(scopedWorkspaceRoot),
    });
    return session;
  }

  workspaceRootForSession(sessionId: string): string {
    const root = sessionWorkspaceRoot(this.workspaceRoot, sessionId);
    if (!root) throw new Error('Invalid user session id.');
    return root;
  }
}
