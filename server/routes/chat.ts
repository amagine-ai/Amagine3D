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
import {
  FIRST_BUILD_REMINDER,
  FirstBuildReminder,
} from '../first-build-reminder.ts';
import { errorMessage } from '../http-utils.ts';
import { isChatRequest } from '../protocol.ts';
import {
  agentRunTimeoutsFromEnv,
  DEFAULT_FIRST_BUILD_REMINDER_MS,
  type AgentRunTimeouts,
} from '../run-config.ts';
import {
  type RunOutcome,
  RunStopped,
  RunSupervisor,
} from '../run-supervisor.ts';
import { acquireSessionActivity } from '../session-activity.ts';
import {
  sessionWorkspaceRoot,
  userSessionArtifacts,
} from '../sessions.ts';
import { appendSavedImageContext, saveImageAttachments } from '../uploads.ts';
import {
  auditCadVisualValidation,
  CadVisualAuditTrail,
  visualValidationInstruction,
  visualValidationRepairInstruction,
} from '../visual-audit.ts';
import {
  requiredWebSearchInstruction,
  webSearchRepairInstruction,
} from '../web-search.ts';

const MAX_VISUAL_REPAIR_ATTEMPTS = 3;
const MAX_WEB_SEARCH_REPAIR_ATTEMPTS = 2;
const DEFAULT_ABORT_GRACE_MS = 5_000;

interface CompletedRun {
  replyText: string;
  sourceStepId?: string;
}

export interface ChatRouteDependencies {
  abortGraceMs?: number;
  firstBuildReminderMs?: number;
  python: PythonHealth;
  runtime: PiRuntime | undefined;
  runtimeError: string | undefined;
  timeouts?: AgentRunTimeouts;
}

function writeEvent(response: Response, event: AgentEvent): void {
  if (!response.destroyed && !response.writableEnded) {
    response.write(`${JSON.stringify(event)}\n`);
  }
}

function toolActivity(toolName: string): string {
  const labels: Record<string, string> = {
    bash: '正在执行 CAD 命令',
    cad_capabilities: '正在检查 CAD 能力',
    cad_compile: '正在编译并审计 CAD',
    edit: '正在修改参数化源码',
    find: '正在查找文件',
    grep: '正在检索工作区',
    ls: '正在检查输出目录',
    read: '正在读取文件或预览图',
    reference_analyze: '正在分析参考图',
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

function finishRunOutcome(
  outcome: RunOutcome<CompletedRun>,
  finish: (
    status: 'cancelled' | 'completed' | 'failed',
    replyText: string,
    sourceStepId?: string,
  ) => ChatTurn,
): ChatTurn {
  if (outcome.status === 'completed') {
    return finish(
      'completed',
      outcome.value.replyText,
      outcome.value.sourceStepId,
    );
  }
  if (outcome.status === 'cancelled') return finish('cancelled', '');
  return finish('failed', outcome.message);
}

export function registerChatRoute(
  app: Express,
  dependencies: ChatRouteDependencies,
): void {
  const timeouts = dependencies.timeouts ?? agentRunTimeoutsFromEnv();
  const abortGraceMs = dependencies.abortGraceMs ?? DEFAULT_ABORT_GRACE_MS;
  const firstBuildReminderMs =
    dependencies.firstBuildReminderMs ?? DEFAULT_FIRST_BUILD_REMINDER_MS;

  app.post('/api/chat', async (request, response) => {
    if (!isChatRequest(request.body)) {
      response.status(400).json({
        message:
          'The request needs taskType (cad or chat), a valid sessionId, and text or images.',
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
      taskType,
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

    const supervisor = new RunSupervisor<CompletedRun>({
      abortGraceMs,
      hardTimeoutMs: timeouts.hardTimeoutMs,
      idleTimeoutMs: timeouts.idleTimeoutMs,
      sessionId,
      timeoutMessages: {
        hard: '本轮执行超过最大时间限制，已停止。',
        idle: '本轮执行长时间没有模型或工具进展，已停止。',
      },
    });
    let session: AgentSession | undefined;
    let unsubscribe: (() => void) | undefined;
    let firstBuildReminder: FirstBuildReminder | undefined;
    let providerError: string | undefined;
    let runTurn = emptyChatTurn();
    let activeResponseStepId: string | undefined;
    let lastResponseStepId: string | undefined;
    let streamedMessageText = '';
    let webSearchSucceeded = false;
    let collectVisualAuditMessages = false;
    let visualAuditTrail: CadVisualAuditTrail | undefined;

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
        try {
          session.sessionManager.appendCustomEntry(
            CHAT_TURN_CUSTOM_TYPE,
            runTurn,
          );
        } catch (error) {
          console.error(`Could not persist chat turn: ${errorMessage(error)}`);
        }
      }
      return runTurn;
    };
    const sendFailure = (message: string, code: string) => {
      supervisor.fail(code, message);
    };

    const abortForDisconnect = () => {
      if (response.writableEnded) return;
      supervisor.disconnect();
    };
    request.once('aborted', abortForDisconnect);
    response.once('close', abortForDisconnect);

    try {
      startStep('正在启动 Amagine3D Agent', 'start');
      session = await supervisor.createSession(() =>
        runtime.createSession(sessionId, { webSearchEnabled }),
      );

      unsubscribe = session.subscribe((event: AgentSessionEvent) => {
        firstBuildReminder?.observe(event);
        supervisor.observe(event);
        if (!supervisor.running) return;
        if (collectVisualAuditMessages && event.type === 'message_end') {
          visualAuditTrail?.record(event.message);
        }
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
      const savedImages = await supervisor.run(() =>
        saveImageAttachments(runtime.stateRoot, sessionId, images),
      );
      const visualValidationRequired = taskType === 'cad';
      const referenceAnalysisRequired =
        visualValidationRequired && images.length > 0;
      const basePrompt = message.trim() || '请查看并分析我上传的图片。';
      const promptText = [
        appendSavedImageContext(basePrompt, savedImages),
        requiredWebSearchInstruction(webSearchEnabled),
        visualValidationInstruction(
          visualValidationRequired,
          referenceAnalysisRequired,
        ),
      ]
        .filter(Boolean)
        .join('\n\n');
      const imageContents = images.map(({ data, mimeType }) => ({
        data,
        mimeType,
        type: 'image' as const,
      }));
      const visualWorkspaceRoot = sessionWorkspaceRoot(
        runtime.workspaceRoot,
        sessionId,
      );
      if (!visualWorkspaceRoot) {
        throw new Error('Invalid session workspace for visual validation.');
      }
      const turnStartedAtMs = Date.now();
      visualAuditTrail = visualValidationRequired
        ? new CadVisualAuditTrail(visualWorkspaceRoot)
        : undefined;
      collectVisualAuditMessages = visualValidationRequired;
      if (visualValidationRequired) {
        firstBuildReminder = new FirstBuildReminder(
          firstBuildReminderMs,
          async () => {
            if (!supervisor.running || !session) return;
            startStep('尚未开始正式编译，已提醒 Agent 尽快构建', 'build-reminder');
            await session.steer(FIRST_BUILD_REMINDER);
          },
        );
      }
      supervisor.touch();
      await supervisor.run(() =>
        session!.prompt(promptText, {
          images: imageContents,
          source: 'rpc',
        }),
      );

      let webSearchRepairAttempts = 0;
      while (webSearchEnabled && !webSearchSucceeded) {
        if (!supervisor.running) return;
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
        supervisor.touch();
        await supervisor.run(() =>
          session!.prompt(
            webSearchRepairInstruction(
              webSearchRepairAttempts,
              MAX_WEB_SEARCH_REPAIR_ATTEMPTS,
            ),
            { source: 'rpc' },
          ),
        );
      }

      let visualRepairAttempts = 0;
      while (true) {
        if (!supervisor.running) return;
        if (providerError) {
          sendFailure(errorMessage(providerError), 'provider_error');
          return;
        }
        if (!visualValidationRequired) break;

        if (!visualAuditTrail) {
          throw new Error('Visual validation trail is unavailable.');
        }
        const audit = await supervisor.run(() =>
          auditCadVisualValidation(visualAuditTrail!.entries, {
            referenceImages: savedImages.map(({ path, sha256 }) => ({
              path,
              sha256,
            })),
            requireReferenceAnalysis: referenceAnalysisRequired,
            turnStartedAtMs,
            workspaceRoot: visualWorkspaceRoot,
          }),
        );
        if (audit.pass) break;
        if (visualRepairAttempts >= MAX_VISUAL_REPAIR_ATTEMPTS) {
          const missingEvidence = referenceAnalysisRequired
            ? '参考图分析、最新预览渲染与读图闭环'
            : '最新预览渲染与读图闭环';
          sendFailure(
            `本轮 CAD 任务未完成必需的${missingEvidence}。结果已拦截，不能仅凭尺寸或网格检查声称外观匹配。`,
            'visual_validation_required',
          );
          return;
        }

        visualRepairAttempts += 1;
        startStep(
          `视觉审计未通过，正在自动补救 ${visualRepairAttempts}/${MAX_VISUAL_REPAIR_ATTEMPTS}`,
          'visual-audit',
        );
        supervisor.touch();
        await supervisor.run(() =>
          session!.prompt(
            visualValidationRepairInstruction(audit, {
              attempt: visualRepairAttempts,
              maxAttempts: MAX_VISUAL_REPAIR_ATTEMPTS,
              requireReferenceAnalysis: referenceAnalysisRequired,
            }),
            { source: 'rpc' },
          ),
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
      const artifactCollection = await supervisor.run(() =>
        userSessionArtifacts(runtime.workspaceRoot, sessionId),
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
      supervisor.complete({
        replyText: answer,
        sourceStepId: lastResponseStepId,
      });
    } catch (error) {
      if (!(error instanceof RunStopped) && supervisor.running) {
        sendFailure(errorMessage(error), 'agent_error');
      }
    } finally {
      firstBuildReminder?.finish();
      request.off('aborted', abortForDisconnect);
      response.off('close', abortForDisconnect);
      unsubscribe?.();
      await supervisor.finalize(
        {
          code: 'agent_error',
          message: providerError ? errorMessage(providerError) : '',
          status: 'failed',
        },
        ({ deliver, outcome }) => {
          const turn = finishRunOutcome(outcome, finishRun);
          if (!deliver) return;
          if (outcome.status === 'completed') {
            writeEvent(response, {
              content: outcome.value.replyText,
              finishedAt: turn.finishedAt!,
              sessionId,
              sourceStepId: outcome.value.sourceStepId,
              type: 'complete',
            });
          } else if (
            outcome.status === 'failed' ||
            outcome.status === 'timed_out'
          ) {
            writeEvent(response, {
              code: outcome.code,
              finishedAt: turn.finishedAt!,
              message: outcome.message,
              type: 'error',
            });
          }
        },
      );
      releaseSession();
      if (!response.writableEnded && !response.destroyed) response.end();
    }
  });
}
