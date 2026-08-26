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

interface StreamAgentOptions {
  images: ImageAttachment[];
  message: string;
  onEvent: (event: AgentEvent) => void;
  sessionId: string;
  signal: AbortSignal;
  webSearchEnabled: boolean;
}

export async function fetchHealth(): Promise<HealthResponse> {
  const response = await fetch('/api/health');
  if (!response.ok) throw new Error('无法连接到本地智能体服务。');
  return (await response.json()) as HealthResponse;
}

export async function fetchSessionCatalog(): Promise<SessionCatalog> {
  const response = await fetch('/api/sessions');
  if (!response.ok) throw new Error('无法读取会话列表。');
  return (await response.json()) as SessionCatalog;
}

export async function fetchSessionDetail(sessionId: string): Promise<SessionDetail> {
  const response = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}`);
  if (!response.ok) throw new Error('无法读取这个会话。');
  return (await response.json()) as SessionDetail;
}

export async function fetchWorkspaceStorage(): Promise<WorkspaceStorage> {
  const response = await fetch('/api/sessions/storage');
  if (!response.ok) throw new Error('无法读取工作区存储。');
  return (await response.json()) as WorkspaceStorage;
}

export async function fetchArtifacts(
  sessionId: string,
): Promise<ArtifactCollection> {
  const response = await fetch(
    `/api/sessions/${encodeURIComponent(sessionId)}/artifacts`,
  );
  if (!response.ok) throw new Error('无法读取工作区文件。');
  return (await response.json()) as ArtifactCollection;
}

export async function fetchModelParameters(
  sessionId: string,
): Promise<ParameterCollection> {
  const response = await fetch(
    `/api/sessions/${encodeURIComponent(sessionId)}/parameters`,
  );
  if (!response.ok) throw new Error('无法读取模型参数。');
  return (await response.json()) as ParameterCollection;
}

export async function rebuildModelParameters(
  sessionId: string,
  model: ParameterModel,
  values: Record<string, number>,
): Promise<ParameterBuildResult> {
  const response = await fetch(
    `/api/sessions/${encodeURIComponent(sessionId)}/parameters/rebuild`,
    {
      body: JSON.stringify({
        primaryPreviewPath: model.primaryPreviewPath,
        sourceHash: model.sourceHash,
        sourcePath: model.sourcePath,
        values,
      }),
      headers: { 'Content-Type': 'application/json' },
      method: 'POST',
    },
  );
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as {
      message?: string;
    };
    throw new Error(body.message || '参数化重建失败。');
  }
  return (await response.json()) as ParameterBuildResult;
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
  const response = await fetch(
    `/api/sessions/${encodeURIComponent(sessionId)}/artifacts/trash`,
    {
      body: JSON.stringify({ paths }),
      headers: { 'Content-Type': 'application/json' },
      method: 'POST',
    },
  );
  if (!response.ok) throw new Error('无法将所选文件移动到回收站。');
}

export async function trashStorageSessions(sessionIds: string[]): Promise<void> {
  const response = await fetch('/api/sessions/storage/trash', {
    body: JSON.stringify({ sessionIds }),
    headers: { 'Content-Type': 'application/json' },
    method: 'POST',
  });
  if (!response.ok) throw new Error('无法将所选会话移动到回收站。');
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
