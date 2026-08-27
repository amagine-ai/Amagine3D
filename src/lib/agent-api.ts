import type {
  AgentEvent,
  ArtifactCollection,
  HealthResponse,
  ImageAttachment,
  ParameterBuildResult,
  ParameterCollection,
  ParameterModel,
  SessionCatalog,
  SessionDetail,
  WorkspaceStorage,
} from '../types';
import { trpc } from './trpc-client';

interface StreamAgentOptions {
  images: ImageAttachment[];
  message: string;
  onEvent: (event: AgentEvent) => void;
  sessionId: string;
  signal: AbortSignal;
  webSearchEnabled: boolean;
}

export async function fetchHealth(): Promise<HealthResponse> {
  try {
    return await trpc.health.query();
  } catch {
    throw new Error('无法连接到本地智能体服务。');
  }
}

export async function fetchSessionCatalog(): Promise<SessionCatalog> {
  try {
    return await trpc.sessions.catalog.query();
  } catch {
    throw new Error('无法读取会话列表。');
  }
}

export async function fetchSessionDetail(sessionId: string): Promise<SessionDetail> {
  try {
    return await trpc.sessions.detail.query({ sessionId });
  } catch {
    throw new Error('无法读取这个会话。');
  }
}

export async function fetchWorkspaceStorage(): Promise<WorkspaceStorage> {
  try {
    return await trpc.sessions.storage.query();
  } catch {
    throw new Error('无法读取工作区存储。');
  }
}

export async function fetchArtifacts(
  sessionId: string,
): Promise<ArtifactCollection> {
  try {
    return await trpc.sessions.artifacts.query({ sessionId });
  } catch {
    throw new Error('无法读取工作区文件。');
  }
}

export async function fetchModelParameters(
  sessionId: string,
): Promise<ParameterCollection> {
  try {
    return await trpc.sessions.parameters.query({ sessionId });
  } catch {
    throw new Error('无法读取模型参数。');
  }
}

export async function rebuildModelParameters(
  sessionId: string,
  model: ParameterModel,
  values: Record<string, number>,
): Promise<ParameterBuildResult> {
  try {
    return await trpc.sessions.rebuildParameters.mutate({
      primaryPreviewPath: model.primaryPreviewPath,
      sessionId,
      sourceHash: model.sourceHash,
      sourcePath: model.sourcePath,
      values,
    });
  } catch (error) {
    throw new Error(
      error instanceof Error && error.message
        ? error.message
        : '参数化重建失败。',
    );
  }
}

export async function fetchArtifactArchive(
  sessionId: string,
  paths: string[],
): Promise<Blob> {
  const response = await fetch(
    `/api/sessions/${encodeURIComponent(sessionId)}/artifacts/archive`,
    {
      body: JSON.stringify({ paths }),
      headers: { 'Content-Type': 'application/json' },
      method: 'POST',
    },
  );
  if (!response.ok) throw new Error('无法打包所选文件。');
  return response.blob();
}

export async function trashArtifacts(
  sessionId: string,
  paths: string[],
): Promise<void> {
  try {
    await trpc.sessions.trashArtifacts.mutate({ paths, sessionId });
  } catch {
    throw new Error('无法将所选文件移动到回收站。');
  }
}

export async function trashStorageSessions(sessionIds: string[]): Promise<void> {
  try {
    await trpc.sessions.trashStorage.mutate({ sessionIds });
  } catch {
    throw new Error('无法将所选会话移动到回收站。');
  }
}

export async function streamAgent({
  images,
  message,
  onEvent,
  sessionId,
  signal,
  webSearchEnabled,
}: StreamAgentOptions): Promise<void> {
  const response = await fetch('/api/chat', {
    body: JSON.stringify({ images, message, sessionId, webSearchEnabled }),
    headers: { 'Content-Type': 'application/json' },
    method: 'POST',
    signal,
  });

  if (!response.body) {
    throw new Error('智能体服务没有返回可读取的数据流。');
  }
  if (!response.ok) {
    const body = (await response.text()).trim();
    try {
      const parsed = JSON.parse(body) as { message?: string };
      throw new Error(parsed.message || body || `请求失败 (${response.status})`);
    } catch (error) {
      if (error instanceof SyntaxError) {
        throw new Error(body || `请求失败 (${response.status})`);
      }
      throw error;
    }
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffered = '';
  let terminalReceived = false;

  const dispatch = (line: string) => {
    const event = JSON.parse(line) as AgentEvent;
    if (event.type === 'complete' && !event.content.trim()) {
      throw new Error('Amagine3D Agent 未返回最终回复，本轮不能标记为完成。');
    }
    if (event.type === 'complete') terminalReceived = true;
    if (event.type === 'error') terminalReceived = true;
    onEvent(event);
  };

  while (true) {
    const { done, value } = await reader.read();
    buffered += decoder.decode(value, { stream: !done });
    const lines = buffered.split('\n');
    buffered = lines.pop() ?? '';

    for (const line of lines) {
      if (!line.trim()) continue;
      dispatch(line);
    }
    if (done) break;
  }

  if (buffered.trim()) dispatch(buffered);
  if (!terminalReceived) {
    throw new Error('Amagine3D Agent 响应意外中断，本轮未完成。');
  }
}
