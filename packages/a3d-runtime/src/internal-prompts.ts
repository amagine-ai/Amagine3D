import type { AgentSession } from '@earendil-works/pi-coding-agent';

type AgentMessage = AgentSession['messages'][number];

const INTERNAL_PROMPT_SUFFIX =
  /\n*<(?:first_build_reminder|uploaded_image_files|web_reference_(?:mode|repair)|visual_validation_(?:required|repair))\b[\s\S]*$/u;

export function stripInternalPromptSuffix(value: string): string {
  return value.replace(INTERNAL_PROMPT_SUFFIX, '').trim();
}

/**
 * Internal steering and audit instructions belong to the run that created
 * them. Keep the user's text and image blocks, but do not replay those
 * server-authored suffixes when a later API turn resumes the PI session.
 */
export function sanitizeInternalPromptHistory(
  messages: readonly AgentMessage[],
): AgentMessage[] {
  const sanitized: AgentMessage[] = [];
  for (const message of messages) {
    if (message.role !== 'user') {
      sanitized.push(message);
      continue;
    }
    if (typeof message.content === 'string') {
      const content = stripInternalPromptSuffix(message.content);
      if (content) sanitized.push({ ...message, content } as AgentMessage);
      continue;
    }
    const content: Exclude<typeof message.content, string> = [];
    for (const block of message.content) {
      if (block.type !== 'text') {
        content.push(block);
        continue;
      }
      const text = stripInternalPromptSuffix(block.text);
      if (text) content.push({ ...block, text });
    }
    if (content.length > 0) sanitized.push({ ...message, content });
  }
  return sanitized;
}
