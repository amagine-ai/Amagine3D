import { randomUUID } from 'node:crypto';

import {
  type AgentSession,
  type AgentSessionEvent,
  PiRuntime,
  TAVILY_SEARCH_TOOL_NAME,
} from '@amagine3d/a3d-runtime';
import type { Express, Response } from 'express';

import {
  finishChatStages,
  RUN_STAGES_CUSTOM_TYPE,
  startChatStage,
} from '../../src/lib/chat-stages.ts';
import type {
  AgentEvent,
  ChatStage,
  PythonHealth,
} from '../../src/types.ts';
import { assistantMessageOutcome } from '../agent-events.ts';
import { durationFromEnv, errorMessage } from '../http-utils.ts';
import { isChatRequest } from '../protocol.ts';
import { userSessionArtifacts } from '../sessions.ts';
import { appendSavedImageContext, saveImageAttachments } from '../uploads.ts';
import {
  auditCadVisualValidation,
  requiresCadVisualValidation,
  visualValidationInstruction,
  visualValidationRepairInstruction,
} from '../visual-audit.ts';
import {
  requiredWebSearchInstruction,
  webSearchRepairInstruction,
} from '../web-search.ts';

const activeSessionIds = new Set<string>();
const MAX_VISUAL_REPAIR_ATTEMPTS = 3;
const MAX_WEB_SEARCH_REPAIR_ATTEMPTS = 2;

export interface ChatRouteDependencies {
  python: PythonHealth;
  runtime: PiRuntime | undefined;
  runtimeError: string | undefined;
}

function writeEvent(response: Response, event: AgentEvent): void {
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
    web_search: '正在搜索网络资料',
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

export function registerChatRoute(
  app: Express,
  dependencies: ChatRouteDependencies,
): void {
  app.post('/api/chat', async (request, response) => {
    if (!isChatRequest(request.body)) {
      response.status(400).json({
        message: 'The request needs a valid sessionId plus text or images.',
      });
      return;
    }
    const { python, runtime, runtimeError } = dependencies;
    if (!runtime) {
      response.status(503).json({
        message: runtimeError || 'Amagine3D Agent is not ready.',
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

    const {
      images = [],
      message,
      sessionId,
      webSearchEnabled = false,
    } = request.body;
    if (webSearchEnabled && !process.env.TAVILY_API_KEY?.trim()) {
      response.status(503).json({
        message:
          'Web references are enabled, but TAVILY_API_KEY is not configured in .env.',
      });
      return;
    }
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
    let webSearchSucceeded = false;

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
      startStage('正在启动 Amagine3D Agent', 'start');
      session = await runtime.createSession(sessionId, { webSearchEnabled });
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
        const assistantOutcome = assistantMessageOutcome(event);
        if (assistantOutcome) {
          providerError =
            assistantOutcome.status === 'error'
              ? assistantOutcome.message
              : undefined;
          if (event.type === 'message_end') return;
        }
        if (event.type === 'message_update') {
          const update = event.assistantMessageEvent;
          if (update.type === 'text_delta') {
            if (!composingResponse) {
              composingResponse = true;
              startStage('正在组织回复', 'response');
            }
            streamedText += update.delta;
            writeEvent(response, { content: update.delta, type: 'token' });
          }
          return;
        }
        if (event.type === 'tool_execution_start') {
          composingResponse = false;
          startStage(toolActivity(event.toolName), event.toolName);
          return;
        }
        if (
          event.type === 'tool_execution_end' &&
          event.toolName === TAVILY_SEARCH_TOOL_NAME &&
          !event.isError
        ) {
          webSearchSucceeded = true;
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
      startStage(`Amagine3D Agent 已启动 ${runtime.modelName}`, 'agent');
      if (images.length > 0) startStage('正在保存参考图片', 'image');
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
        requiredWebSearchInstruction(webSearchEnabled),
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

      let webSearchRepairAttempts = 0;
      while (webSearchEnabled && !webSearchSucceeded) {
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
        if (
          webSearchRepairAttempts >= MAX_WEB_SEARCH_REPAIR_ATTEMPTS
        ) {
          terminalEventSent = true;
          persistRunTrace('failed');
          writeEvent(response, {
            code: 'web_search_required',
            message:
              '已开启联网参考，但 Amagine3D Agent 未能完成必需的 Tavily 搜索。本轮结果已拦截，请检查密钥、额度或网络连接。',
            type: 'error',
          });
          return;
        }
        webSearchRepairAttempts += 1;
        startStage(
          `未完成联网参考，正在强制搜索 ${webSearchRepairAttempts}/${MAX_WEB_SEARCH_REPAIR_ATTEMPTS}`,
          'web-search-audit',
        );
        await session.prompt(
          webSearchRepairInstruction(
            webSearchRepairAttempts,
            MAX_WEB_SEARCH_REPAIR_ATTEMPTS,
          ),
          { source: 'rpc' },
        );
      }

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
      if (!answer.trim()) {
        terminalEventSent = true;
        persistRunTrace('failed');
        writeEvent(response, {
          code: 'empty_agent_response',
          message: 'Amagine3D Agent 未返回最终回复，本轮不能标记为完成。',
          type: 'error',
        });
        return;
      }
      writeEvent(response, { content: answer, type: 'assistant' });
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
}
