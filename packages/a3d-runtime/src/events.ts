import type { ThreadEvent, ThreadItem } from '@openai/codex-sdk';

export type RuntimeItem =
  | { id: string; text: string; type: 'agent_message' }
  | {
      command: string;
      exitCode?: number;
      id: string;
      status: 'completed' | 'failed' | 'in_progress';
      type: 'command_execution';
    }
  | {
      changeCount: number;
      id: string;
      status: 'completed' | 'failed';
      type: 'file_change';
    }
  | {
      id: string;
      status: 'completed' | 'failed' | 'in_progress';
      type: 'mcp_tool_call';
    }
  | { id: string; text: string; type: 'reasoning' }
  | { id: string; query: string; type: 'web_search' }
  | {
      completedCount: number;
      id: string;
      totalCount: number;
      type: 'todo_list';
    }
  | { id: string; message: string; type: 'error' };

export type RuntimeEvent =
  | { threadId: string; type: 'thread.started' }
  | { type: 'turn.started' }
  | { type: 'turn.completed' }
  | { message: string; type: 'turn.failed' }
  | {
      item: RuntimeItem;
      type: 'item.completed' | 'item.started' | 'item.updated';
    }
  | { message: string; type: 'error' };

function normalizeThreadItem(item: ThreadItem): RuntimeItem {
  switch (item.type) {
    case 'agent_message':
      return { id: item.id, text: item.text, type: item.type };
    case 'command_execution':
      return {
        command: item.command,
        ...(item.exit_code === undefined ? {} : { exitCode: item.exit_code }),
        id: item.id,
        status: item.status,
        type: item.type,
      };
    case 'file_change':
      return {
        changeCount: item.changes.length,
        id: item.id,
        status: item.status,
        type: item.type,
      };
    case 'mcp_tool_call':
      return { id: item.id, status: item.status, type: item.type };
    case 'reasoning':
      return { id: item.id, text: item.text, type: item.type };
    case 'web_search':
      return { id: item.id, query: item.query, type: item.type };
    case 'todo_list':
      return {
        completedCount: item.items.filter(({ completed }) => completed).length,
        id: item.id,
        totalCount: item.items.length,
        type: item.type,
      };
    case 'error':
      return { id: item.id, message: item.message, type: item.type };
  }
}

export function normalizeThreadEvent(event: ThreadEvent): RuntimeEvent {
  switch (event.type) {
    case 'thread.started':
      return { threadId: event.thread_id, type: event.type };
    case 'turn.started':
    case 'turn.completed':
      return { type: event.type };
    case 'turn.failed':
      return { message: event.error.message, type: event.type };
    case 'item.started':
    case 'item.updated':
    case 'item.completed':
      return { item: normalizeThreadItem(event.item), type: event.type };
    case 'error':
      return { message: event.message, type: event.type };
  }
}

export function isRuntimeProgressEvent(event: RuntimeEvent): boolean {
  return event.type !== 'error' && event.type !== 'turn.failed';
}
