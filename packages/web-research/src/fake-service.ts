import {
  CadDomainError,
  researchPacketSchema,
  researchRequestSchema,
  type ResearchPacket,
  type ResearchRequest,
} from '@amagine3d/cad-protocol';

import type { WebResearchService } from './service';

export type FakeWebResearchServiceOptions = {
  packet?: ResearchPacket;
  error?: unknown;
};

export class FakeWebResearchService implements WebResearchService {
  readonly calls: ResearchRequest[] = [];

  constructor(private readonly options: FakeWebResearchServiceOptions = {}) {}

  async research(input: ResearchRequest): Promise<ResearchPacket> {
    const request = researchRequestSchema.parse(input);
    this.calls.push(request);
    if (!request.enabled) {
      throw new CadDomainError(
        'ResearchUnavailable',
        'The research service must not be called when Web Search is disabled.',
        {
          category: 'research',
          retryable: false,
          operation: 'fake-web-research',
        },
      );
    }
    if (this.options.error !== undefined) {
      throw this.options.error;
    }
    if (this.options.packet === undefined) {
      throw new CadDomainError(
        'ResearchUnavailable',
        'The fake research service has no configured packet.',
        {
          category: 'research',
          retryable: false,
          operation: 'fake-web-research',
        },
      );
    }
    return researchPacketSchema.parse(this.options.packet);
  }
}
