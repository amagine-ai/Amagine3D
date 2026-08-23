import type {
  AcceptedImageType,
  ArtifactWorkspace,
  SessionSummary,
} from '../../types';
import type { Language, PendingImage } from './types';

export function draftSession(sessionId: string): SessionSummary {
  const timestamp = new Date().toISOString();
  return {
    createdAt: timestamp,
    id: sessionId,
    kind: 'user',
    persisted: false,
    readOnly: false,
    title: 'New printable object',
    updatedAt: timestamp,
  };
}

export function draftWorkspace(sessionId: string): ArtifactWorkspace {
  return {
    id: sessionId,
    name: 'Workspace',
    path: `workspace/sessions/${sessionId}/`,
    readOnly: false,
    sessionId,
  };
}

export function clamp(
  value: number,
  minimum: number,
  maximum: number,
): number {
  return Math.min(maximum, Math.max(minimum, value));
}

export function createSessionId(): string {
  return crypto.randomUUID();
}

export function errorText(error: unknown, language: Language): string {
  if (error instanceof DOMException && error.name === 'AbortError') {
    return language === 'zh' ? '本轮执行已停止。' : 'This run was stopped.';
  }
  return error instanceof Error
    ? error.message
    : language === 'zh'
      ? '智能体执行失败，请检查服务端日志。'
      : 'The agent run failed. Check the server log.';
}

export function readImage(file: File): Promise<PendingImage> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error(`Unable to read ${file.name}.`));
    reader.onload = () => {
      if (typeof reader.result !== 'string') {
        reject(new Error(`Unable to read ${file.name}.`));
        return;
      }
      const separator = reader.result.indexOf(',');
      if (separator < 0) {
        reject(new Error(`Invalid image data: ${file.name}.`));
        return;
      }
      resolve({
        data: reader.result.slice(separator + 1),
        id: crypto.randomUUID(),
        mimeType: file.type as AcceptedImageType,
        name: file.name.slice(0, 255),
        size: file.size,
        url: reader.result,
      });
    };
    reader.readAsDataURL(file);
  });
}
