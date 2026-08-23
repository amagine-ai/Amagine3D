import 'dotenv/config';

import { randomUUID } from 'node:crypto';
import { existsSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import express from 'express';
import type {
  AgentSession,
  AgentSessionEvent,
} from '@earendil-works/pi-coding-agent';

import {
  API_VERSION,
  BUNDLED_POMODORO_SESSION_ID,
  type AgentEvent,
  type ChatStage,
  type HealthResponse,
  type PythonHealth,
} from '../src/types.ts';
import {
  artifactContentType,
  createReadStream,
  resolveArtifactPath,
} from './artifacts.ts';
import { bundledPomodoroArtifacts } from './bundled-workspace.ts';
import { PiRuntime } from './pi-runtime.ts';
import { isChatRequest } from './protocol.ts';
import { activateProjectPython } from './python-runtime.ts';
import { appendSavedImageContext, saveImageAttachments } from './uploads.ts';
import {
  artifactsForSession,
  BUILTIN_POMODORO_SESSION,
  findUserSession,
  listSessionCatalog,
  readSessionMessages,
  sessionWorkspaceRoot,
  userSessionArtifacts,
} from './sessions.ts';
import {
  auditCadVisualValidation,
  requiresCadVisualValidation,
  visualValidationInstruction,
  visualValidationRepairInstruction,
} from './visual-audit.ts';
import {
  finishChatStages,
  RUN_STAGES_CUSTOM_TYPE,
  startChatStage,
} from '../src/lib/chat-stages.ts';

const serverDirectory = dirname(fileURLToPath(import.meta.url));
const projectRoot = resolve(serverDirectory, '..');
const workspaceRoot = join(projectRoot, 'workspace');
const sessionRoot = join(projectRoot, '.amagine-state', 'sessions');
const bundledPomodoroRoot = join(
  projectRoot,
  'bundled-projects',
  'amagine3d-pomodoro',
);
const distPath = join(projectRoot, 'dist');
const port = Number(process.env.PORT ?? 6161);
const activeSessionIds = new Set<string>();
const MAX_VISUAL_REPAIR_ATTEMPTS = 3;

function errorMessage(error: unknown): string {
  const message = error instanceof Error ? error.message : String(error);
  return message
    .replace(/(authorization:\s*bearer\s+)[^\s,]+/gi, '$1[redacted]')
    .replace(/\b(?:sk|key)-[a-z0-9_-]{12,}\b/gi, '[redacted]');
}

function durationFromEnv(value: string | undefined, fallback: number): number {
  const parsed = Number(value ?? fallback);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

function writeEvent(response: express.Response, event: AgentEvent): void {
  if (!response.destroyed && !response.writableEnded) {
    response.write(`${JSON.stringify(event)}\n`);
  }
}

function toolActivity(toolName: string): string {
  const labels: Record<string, string> = {
    bash: '正在执行 CAD 命令',
    edit: '正在修改参数化源码',
    find: '正在查找文件',
    grep: '正在检索工作区',
    ls: '正在检查输出目录',
    read: '正在读取文件或预览图',
    write: '正在写入生成文件',
  };
  return labels[toolName] ?? `正在运行 ${toolName}`;
}

function finalAssistantText(session: AgentSession): string {
  for (const rawMessage of [...session.messages].reverse()) {
    const message = rawMessage as { content?: unknown; role?: unknown };
    if (message.role !== 'assistant') continue;
    if (typeof message.content === 'string') return message.content;
    if (!Array.isArray(message.content)) return '';
    return message.content
      .filter(
        (block): block is { text: string; type: 'text' } =>
          Boolean(
            block &&
              typeof block === 'object' &&
              (block as { type?: unknown }).type === 'text' &&
              typeof (block as { text?: unknown }).text === 'string',
          ),
      )
      .map((block) => block.text)
      .join('');
  }
  return '';
}

async function healthResponse(
  runtime: PiRuntime | undefined,
  runtimeError: string | undefined,
  python: PythonHealth,
): Promise<HealthResponse> {
  return {
    apiVersion: API_VERSION,
    configured: Boolean(process.env.LLM_API_KEY?.trim()),
    model: process.env.LLM_MODEL?.trim() || 'openai/gpt-5.5',
    python,
    ...(runtimeError ? { runtimeError } : {}),
    runtimeReady: Boolean(runtime),
    skills: runtime ? [...runtime.skills] : [],
    workspace: 'workspace/',
  };
}

async function main(): Promise<void> {
  const python = activateProjectPython(projectRoot);
  let runtime: PiRuntime | undefined;
  let runtimeError: string | undefined;
  try {
    runtime = await PiRuntime.create(projectRoot);
  } catch (error) {
    runtimeError = errorMessage(error);
    console.error(`PI runtime initialization failed: ${runtimeError}`);
  }

  const app = express();
  app.disable('x-powered-by');
  app.use((_request, response, next) => {
    response.setHeader('Referrer-Policy', 'same-origin');
    response.setHeader('X-Content-Type-Options', 'nosniff');
    response.setHeader('X-Frame-Options', 'DENY');
    next();
  });
  app.use(express.json({ limit: '18mb' }));

  app.get('/api/health', async (_request, response) => {
    response.json(await healthResponse(runtime, runtimeError, python));
  });

  app.get('/api/sessions', async (_request, response) => {
    response.json(await listSessionCatalog(sessionRoot));
  });

  app.get('/api/sessions/:sessionId', async (request, response) => {
    const { sessionId } = request.params;
    if (sessionId === BUNDLED_POMODORO_SESSION_ID) {
      response.json({
        ...(await bundledPomodoroArtifacts(bundledPomodoroRoot)),
        messages: [],
        session: BUILTIN_POMODORO_SESSION,
      });
      return;
    }
    const session = await findUserSession(sessionRoot, sessionId);
    if (!session) {
      response.status(404).json({ message: 'Session not found.' });
      return;
    }
    const artifacts = await userSessionArtifacts(workspaceRoot, sessionId);
    if (!artifacts) {
      response.status(400).json({ message: 'Invalid session id.' });
      return;
    }
    const catalog = await listSessionCatalog(sessionRoot);
    const summary = catalog.sessions.find(({ id }) => id === sessionId);
    if (!summary) {
      response.status(404).json({ message: 'Session not found.' });
      return;
    }
    response.json({
      ...artifacts,
      messages: await readSessionMessages(session.path),
      session: summary,
    });
  });

  app.get('/api/sessions/:sessionId/artifacts', async (request, response) => {
    const collection = await artifactsForSession(
      workspaceRoot,
      bundledPomodoroRoot,
      request.params.sessionId,
    );
    if (!collection) {
      response.status(400).json({ message: 'Invalid session id.' });
      return;
    }
    response.json(collection);
  });

  app.get('/api/sessions/:sessionId/artifacts/file', async (request, response) => {
    const scopedWorkspaceRoot = sessionWorkspaceRoot(
      workspaceRoot,
      request.params.sessionId,
    );
    const requestedPath = request.query.path;
    if (
      !scopedWorkspaceRoot ||
      typeof requestedPath !== 'string' ||
      requestedPath.length > 1_024
    ) {
      response.status(400).json({ message: 'A valid artifact path is required.' });
      return;
    }
    const artifactPath = await resolveArtifactPath(
      scopedWorkspaceRoot,
      requestedPath,
    );
    if (!artifactPath) {
      response.status(404).json({ message: 'Artifact not found.' });
      return;
    }
    response.setHeader('Cache-Control', 'no-store');
    response.setHeader('Content-Type', artifactContentType(artifactPath));
    createReadStream(artifactPath).pipe(response);
  });

  app.get('/api/bundled-artifacts/file', async (request, response) => {
    const requestedPath = request.query.path;
    if (typeof requestedPath !== 'string' || requestedPath.length > 1_024) {
      response.status(400).json({ message: 'A valid artifact path is required.' });
      return;
    }
    const artifactPath = await resolveArtifactPath(
      bundledPomodoroRoot,
      requestedPath,
    );
    if (!artifactPath) {
      response.status(404).json({ message: 'Bundled artifact not found.' });
      return;
    }
    response.setHeader('Cache-Control', 'public, max-age=31536000, immutable');
    response.setHeader('Content-Type', artifactContentType(artifactPath));
    createReadStream(artifactPath).pipe(response);
  });

  app.post('/api/chat', async (request, response) => {
    if (!isChatRequest(request.body)) {
      response.status(400).json({
        message: 'The request needs a valid sessionId plus text or images.',
      });
      return;
    }
    if (!runtime) {
      response.status(503).json({
        message: runtimeError || 'PI runtime is not ready.',
      });
      return;
    }
    if (!python.ready) {
      response.status(503).json({
        message: 'Python CAD runtime is not ready. Run npm run python:setup.',
      });
      return;
    }
    if (!process.env.LLM_API_KEY?.trim()) {
      response.status(503).json({
        message: 'LLM_API_KEY is not configured in .env.',
      });
      return;
    }

    const { images = [], message, sessionId } = request.body;
    if (activeSessionIds.has(sessionId)) {
      response.status(409).json({
        message: 'This session already has an active turn.',
      });
      return;
    }
    activeSessionIds.add(sessionId);

    response.status(200);
    response.setHeader('Content-Type', 'application/x-ndjson; charset=utf-8');
    response.setHeader('Cache-Control', 'no-cache, no-transform');
    response.setHeader('X-Content-Type-Options', 'nosniff');
    response.flushHeaders();

    let session: AgentSession | undefined;
    let unsubscribe: (() => void) | undefined;
    let clientDisconnected = false;
    let terminalEventSent = false;
    let streamedText = '';
    let providerError: string | undefined;
    let runStages: ChatStage[] = [];
    let runTracePersisted = false;
    let composingResponse = false;

    const startStage = (label: string, stage = 'agent') => {
      runStages = startChatStage(runStages, {
        id: randomUUID(),
        label,
        occurredAt: Date.now(),
        stage,
        status: 'running',
      });
      writeEvent(response, { label, tool: stage, type: 'activity' });
    };
    const persistRunTrace = (
      status: 'cancelled' | 'completed' | 'failed',
    ) => {
      if (!session || runTracePersisted || runStages.length === 0) return;
      runStages = finishChatStages(runStages, status);
      session.sessionManager.appendCustomEntry(RUN_STAGES_CUSTOM_TYPE, {
        stages: runStages,
      });
      runTracePersisted = true;
    };

    const abortForDisconnect = () => {
      if (response.writableEnded) return;
      clientDisconnected = true;
      void session?.abort().catch(() => undefined);
    };
    request.once('aborted', abortForDisconnect);
    response.once('close', abortForDisconnect);

    const timeout = setTimeout(() => {
      if (terminalEventSent || clientDisconnected) return;
      terminalEventSent = true;
      writeEvent(response, {
        code: 'run_timeout',
        message: '本轮执行超过时间限制，已停止。',
        type: 'error',
      });
      void session?.abort().catch(() => undefined);
    }, durationFromEnv(process.env.AGENT_RUN_TIMEOUT_MS, 1_800_000));

    try {
      startStage('正在启动 PI Agent', 'start');
      session = await runtime.createSession(sessionId);
      if (clientDisconnected || terminalEventSent) {
        await session.abort();
        return;
      }

      unsubscribe = session.subscribe((event: AgentSessionEvent) => {
        if (terminalEventSent || clientDisconnected) return;
        if (event.type === 'agent_start') {
          providerError = undefined;
          return;
        }
        if (event.type === 'message_update') {
          const update = event.assistantMessageEvent;
          if (update.type === 'start') providerError = undefined;
          if (update.type === 'text_delta') {
            if (!composingResponse) {
              composingResponse = true;
              startStage('正在组织回复', 'response');
            }
            streamedText += update.delta;
            writeEvent(response, { content: update.delta, type: 'token' });
          } else if (update.type === 'error' && update.reason === 'error') {
            providerError = update.error.errorMessage || 'Model request failed.';
          }
          return;
        }
        if (event.type === 'tool_execution_start') {
          composingResponse = false;
          startStage(toolActivity(event.toolName), event.toolName);
          return;
        }
        if (event.type === 'compaction_start') {
          startStage('正在压缩会话上下文', 'compaction');
          return;
        }
        if (event.type === 'auto_retry_start') {
          startStage(
            `模型请求重试 ${event.attempt}/${event.maxAttempts}`,
            'retry',
          );
        }
      });

      writeEvent(response, {
        model: runtime.modelName,
        skills: runtime.skills.map((skill) => skill.name),
        type: 'start',
      });
      startStage(`PI 已启动 ${runtime.modelName}`, 'agent');
      if (images.length > 0) {
        startStage('正在保存参考图片', 'image');
      }
      const savedImages = await saveImageAttachments(
        runtime.stateRoot,
        sessionId,
        images,
      );
      const visualValidationRequired = requiresCadVisualValidation(
        message,
        images.length,
      );
      const basePrompt = message.trim() || '请查看并分析我上传的图片。';
      const promptText = [
        appendSavedImageContext(basePrompt, savedImages),
        visualValidationInstruction(visualValidationRequired, images.length > 0),
      ]
        .filter(Boolean)
        .join('\n\n');
      const imageContents = images.map(({ data, mimeType }) => ({
        data,
        mimeType,
        type: 'image' as const,
      }));
      const currentTurnStart = session.messages.length;
      await session.prompt(promptText, {
        images: imageContents,
        source: 'rpc',
      });

      let visualRepairAttempts = 0;
      while (true) {
        if (terminalEventSent || clientDisconnected) return;
        if (providerError) {
          terminalEventSent = true;
          persistRunTrace('failed');
          writeEvent(response, {
            code: 'provider_error',
            message: errorMessage(providerError),
            type: 'error',
          });
          return;
        }
        if (!visualValidationRequired) break;

        const audit = auditCadVisualValidation(
          session.messages.slice(currentTurnStart),
          { requireReferenceAnalysis: images.length > 0 },
        );
        if (audit.pass) break;
        if (visualRepairAttempts >= MAX_VISUAL_REPAIR_ATTEMPTS) {
          terminalEventSent = true;
          writeEvent(response, {
            code: 'visual_validation_required',
            message:
              '本轮 CAD 任务未完成必需的参考分析、最新预览渲染与读图闭环。结果已拦截，不能仅凭尺寸或网格检查声称外观匹配。',
            type: 'error',
          });
          return;
        }

        visualRepairAttempts += 1;
        startStage(
          `视觉审计未通过，正在自动补救 ${visualRepairAttempts}/${MAX_VISUAL_REPAIR_ATTEMPTS}`,
          'visual-audit',
        );
        await session.prompt(
          visualValidationRepairInstruction(audit, {
            attempt: visualRepairAttempts,
            maxAttempts: MAX_VISUAL_REPAIR_ATTEMPTS,
            requireReferenceAnalysis: images.length > 0,
          }),
          { source: 'rpc' },
        );
      }

      const answer = finalAssistantText(session) || streamedText;
      if (answer) writeEvent(response, { content: answer, type: 'assistant' });
      startStage('正在整理生成文件', 'files');
      const artifactCollection = await userSessionArtifacts(
        runtime.workspaceRoot,
        sessionId,
      );
      if (artifactCollection) {
        startStage(
          `已发现 ${String(artifactCollection.artifacts.length)} 个工作区文件`,
          'files',
        );
        writeEvent(response, {
          ...artifactCollection,
          sessionId,
          type: 'artifacts',
        });
      }
      persistRunTrace('completed');
      writeEvent(response, { sessionId, type: 'done' });
      terminalEventSent = true;
    } catch (error) {
      if (!terminalEventSent && !clientDisconnected) {
        terminalEventSent = true;
        persistRunTrace('failed');
        writeEvent(response, {
          code: 'agent_error',
          message: errorMessage(error),
          type: 'error',
        });
      }
    } finally {
      if (!runTracePersisted) {
        persistRunTrace(clientDisconnected ? 'cancelled' : 'failed');
      }
      clearTimeout(timeout);
      request.off('aborted', abortForDisconnect);
      response.off('close', abortForDisconnect);
      unsubscribe?.();
      session?.dispose();
      activeSessionIds.delete(sessionId);
      if (!response.writableEnded && !response.destroyed) response.end();
    }
  });

  if (existsSync(distPath)) {
    app.use(express.static(distPath));
    app.get('*splat', (_request, response) => {
      response.sendFile(join(distPath, 'index.html'));
    });
  }

  const server = app.listen(port, '127.0.0.1', () => {
    console.log(`Amagine3D API: http://127.0.0.1:${port}`);
  });
  server.on('error', (error) => {
    console.error(`Could not start Amagine3D API: ${error.message}`);
    process.exitCode = 1;
  });
}

await main();
