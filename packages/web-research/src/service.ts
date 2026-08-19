import type { ResearchPacket, ResearchRequest } from '@amagine3d/cad-protocol';

export interface WebResearchService {
  research(input: ResearchRequest): Promise<ResearchPacket>;
}
