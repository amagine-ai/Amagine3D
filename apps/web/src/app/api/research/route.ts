import {
  researchRequestSchema,
  serializeCadError,
  type ResearchRequest,
} from '@amagine3d/cad-protocol';
import {
  createOpenAiCompatibleTavilyResearchServiceFromEnvironment,
  runResearchStage,
  type WebResearchService,
} from '@amagine3d/web-research';

import { config } from '../../../config';
import { readJsonBody } from '../../../lib/server-request';

export const runtime = 'nodejs';

const encoder = new TextEncoder();

function disabledService(): WebResearchService {
  return {
    research: async () => {
      throw new Error('Disabled research must not call its service.');
    },
  };
}

function configuredService(request: ResearchRequest): WebResearchService {
  if (!request.enabled) return disabledService();
  try {
    return createOpenAiCompatibleTavilyResearchServiceFromEnvironment(
      config.researchEnvironment,
    );
  } catch (error) {
    return {
      research: async () => {
        throw error;
      },
    };
  }
}

export async function POST(request: Request): Promise<Response> {
  let parsed: ResearchRequest;
  try {
    parsed = researchRequestSchema.parse(
      await readJsonBody(request, 8_000 * 4 + 1_024),
    );
  } catch (error) {
    return Response.json({ error: serializeCadError(error) }, { status: 400 });
  }

  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      void runResearchStage(configuredService(parsed), parsed, {
        onStreamEvent: (event) => {
          controller.enqueue(encoder.encode(`${JSON.stringify(event)}\n`));
        },
      })
        .then(() => controller.close())
        .catch((error: unknown) => controller.error(error));
    },
  });
  return new Response(stream, {
    headers: {
      'Cache-Control': 'no-store',
      'Content-Type': 'application/x-ndjson; charset=utf-8',
      'X-Content-Type-Options': 'nosniff',
    },
  });
}
