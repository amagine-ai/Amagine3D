import { modelCapabilitiesSchema } from '@amagine3d/cad-protocol';
import { createCadModelFromEnvironment } from '@amagine3d/cad-agent/server';
import { generateText, tool } from 'ai';
import { z } from 'zod';

import { config } from '../../../config';
import { readJsonBody } from '../../../lib/server-request';

export const runtime = 'nodejs';

const profileRegistrationSchema = z.object({
  id: z.string().trim().min(1).max(160),
  modelId: z.string().trim().min(1).max(240),
  connectionId: z.string().trim().min(1).max(160),
  provider: z.string().trim().min(1).max(120),
  capabilities: modelCapabilitiesSchema,
  action: z.enum(['register', 'validate']).optional(),
});

export type ServerModelProfile = z.infer<typeof profileRegistrationSchema>;

const profiles = new Map<string, ServerModelProfile>();

export function resolveServerModelProfile(
  id: string,
): ServerModelProfile | undefined {
  return profiles.get(id);
}

export async function POST(request: Request): Promise<Response> {
  try {
    const profile = profileRegistrationSchema.parse(
      await readJsonBody(request, 65_536),
    );
    profiles.set(profile.id, profile);
    if (profile.action === 'validate') {
      const model = createCadModelFromEnvironment(
        config.cadModelEnvironment,
        profile.modelId,
      );
      await generateText({
        model,
        prompt: 'Reply with a short readiness confirmation.',
        maxOutputTokens: 24,
      });
      await generateText({
        model,
        prompt: 'Call the probe tool once, then stop.',
        tools: {
          probe: tool({
            description: 'Capability probe.',
            inputSchema: z.object({ ok: z.boolean() }),
            execute: async () => ({ ok: true }),
          }),
        },
        toolChoice: { type: 'tool', toolName: 'probe' },
        maxOutputTokens: 64,
      });
      if (profile.capabilities.imageInput) {
        await generateText({
          model,
          messages: [
            {
              role: 'user',
              content: [
                {
                  type: 'text',
                  text: 'Describe this tiny reference image in one short phrase.',
                },
                {
                  type: 'image',
                  image:
                    'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=',
                },
              ],
            },
          ],
          maxOutputTokens: 32,
        });
      }
    }
    return Response.json(
      { ok: true },
      { headers: { 'Cache-Control': 'no-store' } },
    );
  } catch (error) {
    return Response.json(
      {
        error:
          error instanceof Error
            ? error.message.slice(0, 2_000)
            : 'Invalid model profile registration.',
      },
      { status: 400 },
    );
  }
}

export async function DELETE(request: Request): Promise<Response> {
  const id = new URL(request.url).searchParams.get('id');
  if (id !== null) profiles.delete(id);
  return Response.json(
    { ok: true },
    { headers: { 'Cache-Control': 'no-store' } },
  );
}
