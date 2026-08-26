import { randomUUID } from 'node:crypto';

import {
  type AgentSession,
  type AgentSessionEvent,
  PiRuntime,
  TAVILY_SEARCH_TOOL_NAME,
} from '@amagine3d/a3d-runtime';
import type { Express, Response } from 'express';

import {
  appendChatStepText,
  CHAT_TURN_CUSTOM_TYPE,
  completeChatTurn,
  emptyChatTurn,
  startChatStep,
} from '../../src/lib/chat-turn.ts';
import type {
  AgentEvent,
  ChatStep,
  ChatTurn,
  PythonHealth,
} from '../../src/types.ts';
import { assistantMessageOutcome } from '../agent-events.ts';
import { durationFromEnv, errorMessage } from '../http-utils.ts';
import { isChatRequest } from '../protocol.ts';
import { acquireSessionActivity } from '../session-activity.ts';
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

function assistantText(content: unknown): string {
  if (typeof content === 'string') return content;
  if (!Array.isArray(content)) return '';
  return content
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

function finalAssistantText(session: AgentSession): string {
  for (const rawMessage of [...session.messages].reverse()) {
    const message = rawMessage as { content?: unknown; role?: unknown };
    if (message.role === 'assistant') return assistantText(message.content);
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
    const releaseSession = acquireSessionActivity(sessionId);
    if (!releaseSession) {
      response.status(409).json({
        message: 'This session already has an active turn.',
      });
      return;
    }

    response.status(200);
    response.setHeader('Content-Type', 'application/x-ndjson; charset=utf-8');
    response.setHeader('Cache-Control', 'no-cache, no-transform');
    response.setHeader('X-Content-Type-Options', 'nosniff');
    response.flushHeaders();

    let session: AgentSession | undefined;
    let unsubscribe: (() => void) | undefined;
    let clientDisconnected = false;
    let terminalEventSent = false;
    let providerError: string | undefined;
    let runTurn = emptyChatTurn();
    let activeResponseStepId: string | undefined;
    let lastResponseStepId: string | undefined;
    let streamedMessageText = '';
    let webSearchSucceeded = false;

    const startStep = (label: string, stage = 'agent'): ChatStep => {
      const active = runTurn.steps.at(-1);
      if (
        active?.status === 'running' &&
        active.label === label &&
        active.stage === stage
      ) {
        return active;
      }
      const next: ChatStep = {
        id: randomUUID(),
        label,
        occurredAt: Date.now(),
        stage,
        status: 'running',
      };
      runTurn = startChatStep(runTurn, next);
      writeEvent(response, { step: next, type: 'step' });
      return next;
    };
    const finishRun = (
      status: 'cancelled' | 'completed' | 'failed',
      replyText: string,
      sourceStepId?: string,
    ): ChatTurn => {
      if (runTurn.finishedAt !== undefined) return runTurn;
      runTurn = completeChatTurn(runTurn, {
        finishedAt: Date.now(),
        replyText,
        sourceStepId,
        status,
      });
      if (session && runTurn.steps.length > 0) {
        session.sessionManager.appendCustomEntry(
          CHAT_TURN_CUSTOM_TYPE,
          runTurn,
        );
      }
      return runTurn;
    };
    const sendFailure = (message: string, code: string) => {
      if (terminalEventSent || clientDisconnected) return;
      const turn = finishRun('failed', message);
      terminalEventSent = true;
      writeEvent(response, {
        code,
        finishedAt: turn.finishedAt!,
        message,
        type: 'error',
      });
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
      sendFailure('本轮执行超过时间限制，已停止。', 'run_timeout');
      void session?.abort().catch(() => undefined);
    }, durationFromEnv(process.env.AGENT_RUN_TIMEOUT_MS, 1_800_000));

    try {
      startStep('正在启动 Amagine3D Agent', 'start');
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
        }
        if (
          event.type === 'message_start' &&
          event.message.role === 'assistant'
        ) {
          activeResponseStepId = undefined;
          streamedMessageText = '';
          return;
        }
        if (event.type === 'message_update') {
          const update = event.assistantMessageEvent;
          if (update.type === 'text_delta') {
            if (!activeResponseStepId) {
              activeResponseStepId = startStep(
                '正在组织回复',
                'response',
              ).id;
            }
            streamedMessageText += update.delta;
            runTurn = appendChatStepText(
              runTurn,
              activeResponseStepId,
              update.delta,
            );
            writeEvent(response, {
              content: update.delta,
              stepId: activeResponseStepId,
              type: 'step_delta',
            });
          }
          return;
        }
        if (
          event.type === 'message_end' &&
          event.message.role === 'assistant'
        ) {
          const content =
            assistantText(event.message.content) || streamedMessageText;
          if (
            assistantOutcome?.status === 'success' &&
            content.trim() &&
            !activeResponseStepId
          ) {
            activeResponseStepId = startStep(
              '正在组织回复',
              'response',
            ).id;
            runTurn = appendChatStepText(
              runTurn,
              activeResponseStepId,
              content,
            );
            writeEvent(response, {
              content,
              stepId: activeResponseStepId,
              type: 'step_delta',
            });
          }
          if (assistantOutcome?.status === 'success' && content.trim()) {
            lastResponseStepId = activeResponseStepId;
          }
          activeResponseStepId = undefined;
          streamedMessageText = '';
          return;
        }
        if (event.type === 'tool_execution_start') {
          startStep(toolActivity(event.toolName), event.toolName);
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
          startStep('正在压缩会话上下文', 'compaction');
          return;
        }
        if (event.type === 'auto_retry_start') {
          startStep(
            `模型请求重试 ${event.attempt}/${event.maxAttempts}`,
            'retry',
          );
        }
      });

      startStep(`Amagine3D Agent 已启动 ${runtime.modelName}`, 'agent');
      if (images.length > 0) startStep('正在保存参考图片', 'image');
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
          sendFailure(errorMessage(providerError), 'provider_error');
          return;
        }
        if (
          webSearchRepairAttempts >= MAX_WEB_SEARCH_REPAIR_ATTEMPTS
        ) {
          sendFailure(
            '已开启联网参考，但 Amagine3D Agent 未能完成必需的 Tavily 搜索。本轮结果已拦截，请检查密钥、额度或网络连接。',
            'web_search_required',
          );
          return;
        }
        webSearchRepairAttempts += 1;
        startStep(
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
          sendFailure(errorMessage(providerError), 'provider_error');
          return;
        }
        if (!visualValidationRequired) break;

        const audit = auditCadVisualValidation(
          session.messages.slice(currentTurnStart),
          { requireReferenceAnalysis: images.length > 0 },
        );
        if (audit.pass) break;
        if (visualRepairAttempts >= MAX_VISUAL_REPAIR_ATTEMPTS) {
          sendFailure(
            '本轮 CAD 任务未完成必需的参考分析、最新预览渲染与读图闭环。结果已拦截，不能仅凭尺寸或网格检查声称外观匹配。',
            'visual_validation_required',
          );
          return;
        }

        visualRepairAttempts += 1;
        startStep(
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

      const answer = finalAssistantText(session);
      if (!answer.trim()) {
        sendFailure(
          'Amagine3D Agent 未返回最终回复，本轮不能标记为完成。',
          'empty_agent_response',
        );
        return;
      }
      startStep('正在整理生成文件', 'files');
      const artifactCollection = await userSessionArtifacts(
        runtime.workspaceRoot,
        sessionId,
      );
      if (artifactCollection) {
        startStep(
          `已发现 ${String(artifactCollection.artifacts.length)} 个工作区文件`,
          'files',
        );
        writeEvent(response, {
          ...artifactCollection,
          sessionId,
          type: 'artifacts',
        });
      }
      const completedTurn = finishRun(
        'completed',
        answer,
        lastResponseStepId,
      );
      writeEvent(response, {
        content: answer,
        finishedAt: completedTurn.finishedAt!,
        sessionId,
        sourceStepId: lastResponseStepId,
        type: 'complete',
      });
      terminalEventSent = true;
    } catch (error) {
      if (!terminalEventSent && !clientDisconnected) {
        sendFailure(errorMessage(error), 'agent_error');
      }
    } finally {
      if (runTurn.finishedAt === undefined) {
        finishRun(
          clientDisconnected ? 'cancelled' : 'failed',
          providerError ? errorMessage(providerError) : '',
        );
      }
      clearTimeout(timeout);
      request.off('aborted', abortForDisconnect);
      response.off('close', abortForDisconnect);
      unsubscribe?.();
      session?.dispose();
      releaseSession();
      if (!response.writableEnded && !response.destroyed) response.end();
    }
  });
}
