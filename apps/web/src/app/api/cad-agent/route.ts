import { resolve } from 'node:path';

import {
  cadAgentCallOptionsSchema,
  createCadAgentAbortErrorTransform,
  createCadAgent,
  createCadModelFromEnvironment,
  formatCadAgentStreamError,
  loadVerifiedWorkflowInstructions,
} from '@amagine3d/cad-agent/server';
import { serializeCadError } from '@amagine3d/cad-protocol';
import { createAgentUIStreamResponse } from 'ai';
import { z } from 'zod';

import { config } from '../../../config';
import { registerCadAgentDevTools } from '../../../lib/ai-devtools';
import { readJsonBody } from '../../../lib/server-request';
import { resolveServerModelProfile } from '../model-profiles/route';

export const runtime = 'nodejs';
// Let the model finish large CAD source generations without the previous
// five-minute application timeout. Hosting providers may still enforce their
// own execution limit, so advertise a longer ceiling for platforms that honor
// Next.js route metadata.
export const maxDuration = 1_800;

const requestSchema = z.object({
  messages: z.array(z.unknown()).max(500),
  runContext: cadAgentCallOptionsSchema,
});

export async function POST(request: Request): Promise<Response> {
  try {
    await registerCadAgentDevTools();
    const body = requestSchema.parse(
      await readJsonBody(request, 32 * 1024 * 1024),
    );
    const promptRoot = resolve(
      process.cwd(),
      '..',
      '..',
      'packages',
      'cad-agent',
      'prompt',
    );
    const instructions = await loadVerifiedWorkflowInstructions(
      promptRoot,
      body.runContext.workflowKind,
    );
    const registeredProfile =
      body.runContext.modelProfileId === undefined
        ? undefined
        : resolveServerModelProfile(body.runContext.modelProfileId);
    if (
      body.runContext.modelProfileId !== undefined &&
      registeredProfile === undefined
    ) {
      throw new Error(
        'The selected model profile is not registered on the server.',
      );
    }
    const hasImageInput = body.messages.some(
      (message) =>
        typeof message === 'object' &&
        message !== null &&
        Array.isArray((message as { parts?: unknown }).parts) &&
        (message as { parts: unknown[] }).parts.some(
          (part) =>
            typeof part === 'object' &&
            part !== null &&
            (part as { type?: unknown }).type === 'file',
        ),
    );
    if (
      hasImageInput &&
      registeredProfile !== undefined &&
      !registeredProfile.capabilities.imageInput
    ) {
      throw new Error(
        'The selected model profile does not support image input.',
      );
    }
    const model = createCadModelFromEnvironment(
      config.cadModelEnvironment,
      registeredProfile?.modelId,
    );
    const agent = createCadAgent({
      model,
      instructions,
      workflowKind: body.runContext.workflowKind,
    });
    return createAgentUIStreamResponse({
      agent,
      uiMessages: body.messages,
      options: body.runContext,
      abortSignal: request.signal,
      experimental_transform: createCadAgentAbortErrorTransform(
        body.runContext.phase,
      ),
      onError: formatCadAgentStreamError,
      headers: {
        'Cache-Control': 'no-store',
        'X-Content-Type-Options': 'nosniff',
      },
    });
  } catch (error) {
    return Response.json(
      { error: serializeCadError(error) },
      { status: 400, headers: { 'Cache-Control': 'no-store' } },
    );
  }
}
