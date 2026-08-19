import {
  CadDomainError,
  SCHEMA_VERSION,
  researchRequestSchema,
  researchStreamEventSchema,
  serializeCadError,
  type ResearchPacket,
  type ResearchRequest,
  type ResearchStreamEvent,
  type WorkflowEventRecord,
} from '@amagine3d/cad-protocol';

import { createFailedResearchPacket } from './normalize';
import type { WebResearchService } from './service';

export type ResearchStageResult = {
  packet?: ResearchPacket;
  streamEvents: ResearchStreamEvent[];
  workflowEvents: WorkflowEventRecord[];
};

export type ResearchStageOptions = {
  now?: () => Date;
  createId?: () => string;
  onStreamEvent?: (event: ResearchStreamEvent) => void;
};

export async function runResearchStage(
  service: WebResearchService,
  input: ResearchRequest,
  options: ResearchStageOptions = {},
): Promise<ResearchStageResult> {
  const request = researchRequestSchema.parse(input);
  const now = options.now ?? (() => new Date());
  const createId = options.createId ?? (() => crypto.randomUUID());
  const streamEvents: ResearchStreamEvent[] = [];
  const workflowEvents: WorkflowEventRecord[] = [];
  const emit = (event: ResearchStreamEvent) => {
    const parsed = researchStreamEventSchema.parse(event);
    streamEvents.push(parsed);
    options.onStreamEvent?.(parsed);
  };
  const transition = (eventType: string, from: string, to: string) => {
    workflowEvents.push({
      schemaVersion: SCHEMA_VERSION,
      id: createId(),
      runId: request.runId,
      sequence: workflowEvents.length,
      occurredAt: now().toISOString(),
      type: 'workflow-transition',
      payload: { eventType, from, to },
    });
  };

  if (!request.enabled) {
    transition('skip_research', 'received', 'research_skipped');
    emit({
      schemaVersion: SCHEMA_VERSION,
      runId: request.runId,
      type: 'research-status',
      status: 'skipped',
      message: 'Web Search was off for this request.',
    });
    transition(
      'begin_workflow_selection',
      'research_skipped',
      'selecting_workflow',
    );
    emit({
      schemaVersion: SCHEMA_VERSION,
      runId: request.runId,
      type: 'research-result',
    });
    emit({
      schemaVersion: SCHEMA_VERSION,
      runId: request.runId,
      type: 'workflow-ready',
      next: 'briefing',
    });
    return { streamEvents, workflowEvents };
  }

  transition('begin_research', 'received', 'researching');
  emit({
    schemaVersion: SCHEMA_VERSION,
    runId: request.runId,
    type: 'research-status',
    status: 'researching',
    message: 'Researching public hardware references…',
  });

  let packet: ResearchPacket;
  let transitionStatus: 'research_ready' | 'research_failed';
  try {
    packet = await service.research(request);
    transitionStatus =
      packet.status === 'failed' ? 'research_failed' : 'research_ready';
  } catch (error) {
    const serialized = serializeCadError(
      error instanceof CadDomainError
        ? error
        : new CadDomainError(
            'ResearchUnavailable',
            error instanceof Error ? error.message : 'Web Research failed.',
            {
              category: 'research',
              retryable: true,
              operation: 'web-research',
              cause: error,
            },
          ),
    );
    packet = createFailedResearchPacket(request.query, serialized.message);
    transitionStatus = 'research_failed';
    workflowEvents.push({
      schemaVersion: SCHEMA_VERSION,
      id: createId(),
      runId: request.runId,
      sequence: workflowEvents.length,
      occurredAt: now().toISOString(),
      type: 'warning',
      payload: { code: serialized.code, message: serialized.message },
    });
  }

  for (const source of packet.sources) {
    emit({
      schemaVersion: SCHEMA_VERSION,
      runId: request.runId,
      type: 'research-reference',
      source,
    });
  }
  transition(
    transitionStatus === 'research_ready'
      ? 'research_succeeded'
      : 'research_failed',
    'researching',
    transitionStatus,
  );
  emit({
    schemaVersion: SCHEMA_VERSION,
    runId: request.runId,
    type: 'research-status',
    status: packet.status,
    message:
      packet.status === 'complete'
        ? 'Research complete. Sources remain advisory.'
        : packet.status === 'partial'
          ? 'Research is partial. Review caveats before using dimensions.'
          : 'Research was unavailable. The workflow can continue without it.',
  });
  emit({
    schemaVersion: SCHEMA_VERSION,
    runId: request.runId,
    type: 'research-result',
    packet,
  });
  transition(
    'begin_workflow_selection',
    transitionStatus,
    'selecting_workflow',
  );
  emit({
    schemaVersion: SCHEMA_VERSION,
    runId: request.runId,
    type: 'workflow-ready',
    next: 'briefing',
  });
  return { packet, streamEvents, workflowEvents };
}
