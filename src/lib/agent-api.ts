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

export async function streamAgent({
  images,
  message,
  onEvent,
  sessionId,
  signal,
}: StreamAgentOptions): Promise<void> {
  const response = await fetch('/api/chat', {
    body: JSON.stringify({ images, message, sessionId }),
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

  while (true) {
    const { done, value } = await reader.read();
    buffered += decoder.decode(value, { stream: !done });
    const lines = buffered.split('\n');
    buffered = lines.pop() ?? '';

    for (const line of lines) {
      if (!line.trim()) continue;
      onEvent(JSON.parse(line) as AgentEvent);
    }
    if (done) break;
  }

  if (buffered.trim()) onEvent(JSON.parse(buffered) as AgentEvent);
}
