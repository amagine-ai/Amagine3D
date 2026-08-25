import type {
  AgentEvent,
  ArtifactCollection,
  HealthResponse,
  ImageAttachment,
  SessionCatalog,
  SessionDetail,
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

export async function fetchArtifacts(
  sessionId: string,
): Promise<ArtifactCollection> {
  const response = await fetch(
    `/api/sessions/${encodeURIComponent(sessionId)}/artifacts`,
  );
  if (!response.ok) throw new Error('无法读取工作区文件。');
  return (await response.json()) as ArtifactCollection;
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
  let assistantContent = '';
  let doneReceived = false;

  const dispatch = (line: string) => {
    const event = JSON.parse(line) as AgentEvent;
    if (event.type === 'token') assistantContent += event.content;
    if (event.type === 'assistant') assistantContent = event.content;
    if (event.type === 'done' && !assistantContent.trim()) {
      throw new Error('Amagine3D Agent 未返回最终回复，本轮不能标记为完成。');
    }
    if (event.type === 'done') doneReceived = true;
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
  if (!doneReceived) {
    throw new Error('Amagine3D Agent 响应意外中断，本轮未完成。');
  }
}
