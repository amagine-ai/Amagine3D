import { randomUUID } from 'node:crypto';
import { join } from 'node:path';

import {
  agentRunTimeoutsFromEnv,
  isRuntimeProgressEvent,
  type AgentRunTimeouts,
  type CodexRuntimeLike,
  type RunOutcome,
  RunStopped,
  RunSupervisor,
  type RuntimeEvent,
  type RuntimeItem,
} from '@amagine3d/a3d-runtime';
import type { Express, Response } from 'express';

import {
  appendChatStepText,
  completeChatTurn,
  emptyChatTurn,
  startChatStep,
} from '../../src/lib/chat-turn.ts';
import type {
  AgentEvent,
  ChatStep,
  ChatTurn,
  LocalizedText,
  PythonHealth,
} from '../../src/types.ts';
import { errorMessage } from '../http-utils.ts';
import { isChatRequest } from '../protocol.ts';
import { acquireSessionActivity } from '../session-activity.ts';
import {
  appendSessionAssistantTurn,
  appendSessionUserMessage,
  readSessionThreadId,
  setSessionThreadId,
  userSessionArtifacts,
} from '../sessions.ts';
import { saveImageAttachments } from '../uploads.ts';

interface CompletedRun {
  replyText: string;
  sourceStepId?: string;
}

export interface ChatRouteDependencies {
  python: PythonHealth;
  runtime: CodexRuntimeLike | undefined;
  runtimeError: string | undefined;
  timeouts?: AgentRunTimeouts;
}

function writeEvent(response: Response, event: AgentEvent): void {
  if (!response.destroyed && !response.writableEnded) {
    response.write(`${JSON.stringify(event)}\n`);
  }
}

interface StepActivity {
  localizedLabel: LocalizedText;
  stage: string;
}

function localizedLabel(en: string, zh: string): LocalizedText {
  return { en, zh };
}

function commandActivity(command: string): LocalizedText {
  if (/\ba3d\s+compile\b/u.test(command)) {
    return localizedLabel('Compiling and validating CAD', '正在编译并检查 CAD');
  }
  if (/\ba3d\s+reference\b/u.test(command)) {
    return localizedLabel('Analyzing the reference image', '正在分析参考图');
  }
  if (/\ba3d\s+(intent|scene)\b/u.test(command)) {
    return localizedLabel('Checking the CAD structure', '正在检查 CAD 结构');
  }
  if (/\ba3d\s+(capabilities|guide|help)\b/u.test(command)) {
    return localizedLabel('Reading CAD capabilities', '正在读取 CAD 能力');
  }
  if (/\b(?:cat|find|grep|head|ls|rg|sed|tail)\b/u.test(command)) {
    return localizedLabel('Checking workspace files', '正在检查工作区资料');
  }
  return localizedLabel('Running modeling tools', '正在运行建模工具');
}

function itemActivity(item: RuntimeItem): StepActivity | undefined {
  if (item.type === 'command_execution') {
    return { localizedLabel: commandActivity(item.command), stage: 'command' };
  }
  if (item.type === 'file_change') {
    return {
      localizedLabel: localizedLabel(
        `Modifying ${String(item.changeCount)} files`,
        `正在修改 ${String(item.changeCount)} 个文件`,
      ),
      stage: 'files',
    };
  }
  if (item.type === 'web_search') {
    return {
      localizedLabel: localizedLabel(
        'Searching web references',
        '正在搜索网络资料',
      ),
      stage: 'web-search',
    };
  }
  if (item.type === 'reasoning') {
    return {
      localizedLabel: localizedLabel('A3D is analyzing', 'A3D 正在分析'),
      stage: 'reasoning',
    };
  }
  if (item.type === 'todo_list') {
    return {
      localizedLabel:
        item.totalCount > 0
          ? localizedLabel(
              `A3D is executing the plan · ${String(item.completedCount)}/${String(item.totalCount)}`,
              `A3D 正在执行计划 · ${String(item.completedCount)}/${String(item.totalCount)}`,
            )
          : localizedLabel('A3D is planning the task', 'A3D 正在规划任务'),
      stage: 'plan',
    };
  }
  return undefined;
}

function completedItemActivity(
  item: RuntimeItem,
): StepActivity | undefined {
  if (item.type === 'command_execution') {
    const succeeded =
      item.status === 'completed' &&
      (item.exitCode === undefined || item.exitCode === 0);
    return {
      localizedLabel: succeeded
        ? localizedLabel(
            'A3D is analyzing the tool result',
            'A3D 正在分析执行结果',
          )
        : localizedLabel(
            'The tool failed; A3D is trying to repair the issue',
            '工具执行未成功，A3D 正在尝试修复',
          ),
      stage: 'reasoning',
    };
  }
  if (item.type === 'file_change') {
    return {
      localizedLabel: localizedLabel(
        'A3D is checking the changes',
        'A3D 正在检查修改结果',
      ),
      stage: 'reasoning',
    };
  }
  if (item.type === 'web_search') {
    return {
      localizedLabel: localizedLabel(
        'A3D is analyzing the search results',
        'A3D 正在分析搜索结果',
      ),
      stage: 'reasoning',
    };
  }
  return undefined;
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
        message: runtimeError || 'A3D runtime is not ready.',
      });
      return;
    }
    if (!runtime.configured) {
      response.status(503).json({
        message:
          'Configure LLM_API_KEY, CODEX_API_KEY, or OPENAI_API_KEY in .env.',
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
    if (taskType === 'cad' && !python.ready) {
      response.status(503).json({
        message: 'Python CAD runtime is not ready. Run npm run python:setup.',
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
      hardTimeoutMs: timeouts.hardTimeoutMs,
      idleTimeoutMs: timeouts.idleTimeoutMs,
      timeoutMessages: {
        hard: '本轮执行超过最大时间限制，已停止。',
        idle: '本轮执行长时间没有 A3D 进展，已停止。',
      },
    });
    let runTurn = emptyChatTurn();
    let activeResponseStepId: string | undefined;
    let lastResponseStepId: string | undefined;
    const assistantItems = new Map<string, string>();

    const startStep = (
      localizedStepLabel: LocalizedText,
      stage = 'agent',
    ): ChatStep => {
      const label = localizedStepLabel.zh;
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
        localizedLabel: localizedStepLabel,
        occurredAt: Date.now(),
        stage,
        status: 'running',
      };
      runTurn = startChatStep(runTurn, next);
      writeEvent(response, { step: next, type: 'step' });
      return next;
    };

    const appendAssistantText = (
      item: Extract<RuntimeItem, { type: 'agent_message' }>,
    ) => {
      const previous = assistantItems.get(item.id) ?? '';
      const delta = item.text.startsWith(previous)
        ? item.text.slice(previous.length)
        : previous
          ? ''
          : item.text;
      assistantItems.set(item.id, item.text);
      if (!delta) return;
      if (!activeResponseStepId) {
        activeResponseStepId = startStep(
          localizedLabel('Organizing the response', '正在组织回复'),
          'response',
        ).id;
      }
      runTurn = appendChatStepText(runTurn, activeResponseStepId, delta);
      writeEvent(response, {
        content: delta,
        stepId: activeResponseStepId,
        type: 'step_delta',
      });
    };

    const observeCodexEvent = (event: RuntimeEvent) => {
      if (isRuntimeProgressEvent(event)) supervisor.observeProgress();
      if (!supervisor.running) return;
      if (event.type === 'thread.started') {
        activeResponseStepId = undefined;
        startStep(localizedLabel('A3D started', 'A3D 已启动'), 'start');
        return;
      }
      if (event.type === 'turn.started') {
        activeResponseStepId = undefined;
        startStep(
          localizedLabel('A3D is analyzing the request', 'A3D 正在分析请求'),
          'reasoning',
        );
        return;
      }
      if (event.type === 'item.started' || event.type === 'item.updated') {
        if (event.item.type === 'agent_message') {
          appendAssistantText(event.item);
          return;
        }
        const activity = itemActivity(event.item);
        if (
          activity &&
          (event.type === 'item.started' ||
            runTurn.steps.at(-1)?.stage !== activity.stage)
        ) {
          activeResponseStepId = undefined;
          startStep(activity.localizedLabel, activity.stage);
        }
        return;
      }
      if (event.type === 'item.completed') {
        if (event.item.type === 'agent_message') {
          appendAssistantText(event.item);
          lastResponseStepId = activeResponseStepId;
          return;
        }
        const followUp = completedItemActivity(event.item);
        if (followUp) {
          activeResponseStepId = undefined;
          startStep(followUp.localizedLabel, followUp.stage);
          return;
        }
        const activity = itemActivity(event.item);
        if (activity && runTurn.steps.at(-1)?.stage !== activity.stage) {
          activeResponseStepId = undefined;
          startStep(activity.localizedLabel, activity.stage);
        }
      }
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
      return runTurn;
    };

    const abortForDisconnect = () => {
      if (!response.writableEnded) supervisor.disconnect();
    };
    request.once('aborted', abortForDisconnect);
    response.once('close', abortForDisconnect);

    try {
      startStep(
        localizedLabel(
          `Starting A3D · ${runtime.modelName}`,
          `正在启动 A3D · ${runtime.modelName}`,
        ),
        'start',
      );
      if (images.length > 0) {
        startStep(
          localizedLabel('Saving reference images', '正在保存参考图片'),
          'image',
        );
      }
      const savedImages = await supervisor.run(() =>
        saveImageAttachments(runtime.stateRoot, sessionId, images),
      );
      await supervisor.run(() =>
        appendSessionUserMessage(
          join(runtime.stateRoot, 'sessions'),
          sessionId,
          message,
        ),
      );
      const sessionRoot = join(runtime.stateRoot, 'sessions');
      const threadId = await supervisor.run(() =>
        readSessionThreadId(sessionRoot, sessionId),
      );
      const result = await supervisor.run((signal) =>
        runtime.runTurn({
          imagePaths: savedImages.map(({ path }) => path),
          message,
          onEvent: observeCodexEvent,
          onThreadStarted: (startedThreadId) =>
            setSessionThreadId(sessionRoot, sessionId, startedThreadId),
          sessionId,
          signal,
          taskType,
          threadId,
          webSearchEnabled,
        }),
      );

      startStep(
        localizedLabel('Collecting generated files', '正在整理生成文件'),
        'files',
      );
      const artifactCollection = await supervisor.run(() =>
        userSessionArtifacts(runtime.workspaceRoot, sessionId),
      );
      if (artifactCollection) {
        startStep(
          localizedLabel(
            `${String(artifactCollection.artifacts.length)} workspace files discovered`,
            `已发现 ${String(artifactCollection.artifacts.length)} 个工作区文件`,
          ),
          'files',
        );
        writeEvent(response, {
          ...artifactCollection,
          sessionId,
          type: 'artifacts',
        });
      }
      supervisor.complete({
        replyText: result.finalResponse,
        sourceStepId: lastResponseStepId,
      });
    } catch (error) {
      if (!(error instanceof RunStopped) && supervisor.running) {
        supervisor.fail('codex_error', errorMessage(error));
      }
    } finally {
      request.off('aborted', abortForDisconnect);
      response.off('close', abortForDisconnect);
      await supervisor.finalize(
        {
          code: 'codex_error',
          message: 'A3D run stopped unexpectedly.',
          status: 'failed',
        },
        async ({ deliver, outcome }) => {
          const turn = finishRunOutcome(outcome, finishRun);
          try {
            await appendSessionAssistantTurn(
              join(runtime.stateRoot, 'sessions'),
              sessionId,
              turn,
            );
          } catch (error) {
            console.error(`Could not persist chat turn: ${errorMessage(error)}`);
          }
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
