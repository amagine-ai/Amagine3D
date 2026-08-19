import {
  researchPacketDraftSchema,
  researchPacketSchema,
  type ResearchFinding,
  type ResearchPacket,
  type ResearchPacketDraft,
  type ResearchSource,
} from '@amagine3d/cad-protocol';

const SOURCE_PRIORITY: Record<ResearchSource['sourceType'], number> = {
  manufacturer: 0,
  datasheet: 1,
  distributor: 2,
  community: 3,
  other: 4,
};

const TRACKING_PARAMETER = /^(?:utm_.+|fbclid|gclid|mc_cid|mc_eid)$/iu;

const LENGTH_UNITS: Record<string, { unit: 'mm'; multiplier: number }> = {
  mm: { unit: 'mm', multiplier: 1 },
  millimeter: { unit: 'mm', multiplier: 1 },
  millimeters: { unit: 'mm', multiplier: 1 },
  cm: { unit: 'mm', multiplier: 10 },
  centimeter: { unit: 'mm', multiplier: 10 },
  centimeters: { unit: 'mm', multiplier: 10 },
  m: { unit: 'mm', multiplier: 1_000 },
  meter: { unit: 'mm', multiplier: 1_000 },
  meters: { unit: 'mm', multiplier: 1_000 },
  in: { unit: 'mm', multiplier: 25.4 },
  inch: { unit: 'mm', multiplier: 25.4 },
  inches: { unit: 'mm', multiplier: 25.4 },
};

function canonicalUrl(input: string): string {
  const url = new URL(input);
  url.hash = '';
  for (const key of [...url.searchParams.keys()]) {
    if (TRACKING_PARAMETER.test(key)) {
      url.searchParams.delete(key);
    }
  }
  url.searchParams.sort();
  return url.toString();
}

function normalizeFindingUnit(finding: ResearchFinding): ResearchFinding {
  if (finding.unit === undefined || typeof finding.value !== 'number') {
    return finding;
  }
  const normalized = LENGTH_UNITS[finding.unit.trim().toLowerCase()];
  if (normalized === undefined) {
    return finding;
  }
  return {
    ...finding,
    value: Number((finding.value * normalized.multiplier).toPrecision(12)),
    unit: normalized.unit,
    originalExpression:
      finding.originalExpression ?? `${String(finding.value)} ${finding.unit}`,
  };
}

function findingIdentity(finding: ResearchFinding): string {
  return JSON.stringify([
    finding.topic.trim().toLocaleLowerCase(),
    finding.value,
    finding.unit?.toLocaleLowerCase(),
    [...finding.sourceIds].sort(),
  ]);
}

function appendCaveat(finding: ResearchFinding, caveat: string) {
  if (finding.caveat?.includes(caveat) === true) return finding;
  return {
    ...finding,
    caveat:
      finding.caveat === undefined ? caveat : `${finding.caveat} ${caveat}`,
  };
}

function preserveConflicts(findings: ResearchFinding[]) {
  const topicValues = new Map<string, Set<string>>();
  for (const finding of findings) {
    if (finding.value === undefined) continue;
    const topic = finding.topic.trim().toLocaleLowerCase();
    const values = topicValues.get(topic) ?? new Set<string>();
    values.add(JSON.stringify([finding.value, finding.unit ?? null]));
    topicValues.set(topic, values);
  }
  const conflictTopics = new Set(
    [...topicValues.entries()]
      .filter(([, values]) => values.size > 1)
      .map(([topic]) => topic),
  );
  return {
    findings: findings.map((finding) =>
      conflictTopics.has(finding.topic.trim().toLocaleLowerCase())
        ? appendCaveat(
            finding,
            'Conflicting sourced values are preserved for user review.',
          )
        : finding,
    ),
    conflictTopics,
  };
}

export type NormalizeResearchOptions = {
  maxResponseBytes?: number;
};

export function normalizeResearchPacket(
  input: ResearchPacketDraft,
  options: NormalizeResearchOptions = {},
): ResearchPacket {
  const draft = researchPacketDraftSchema.parse(input);
  const sortedSources = [...draft.sources].sort(
    (left, right) =>
      SOURCE_PRIORITY[left.sourceType] - SOURCE_PRIORITY[right.sourceType] ||
      left.url.localeCompare(right.url),
  );
  const sources: ResearchSource[] = [];
  const sourceIdMap = new Map<string, string>();
  const sourceByUrl = new Map<string, ResearchSource>();
  for (const source of sortedSources) {
    const url = canonicalUrl(source.url);
    const existing = sourceByUrl.get(url);
    if (existing !== undefined) {
      sourceIdMap.set(source.id, existing.id);
      continue;
    }
    const normalized = { ...source, url };
    sourceByUrl.set(url, normalized);
    sourceIdMap.set(source.id, normalized.id);
    sources.push(normalized);
  }

  const seenFindings = new Set<string>();
  const normalizedFindings: ResearchFinding[] = [];
  for (const rawFinding of draft.findings) {
    const sourceIds = [
      ...new Set(
        rawFinding.sourceIds
          .map((sourceId) => sourceIdMap.get(sourceId))
          .filter((sourceId): sourceId is string => sourceId !== undefined),
      ),
    ];
    if (sourceIds.length === 0) continue;
    const finding = normalizeFindingUnit({ ...rawFinding, sourceIds });
    const identity = findingIdentity(finding);
    if (seenFindings.has(identity)) continue;
    seenFindings.add(identity);
    normalizedFindings.push(finding);
  }

  const { findings, conflictTopics } = preserveConflicts(normalizedFindings);
  const warnings = [...draft.warnings];
  if (conflictTopics.size > 0) {
    warnings.push(
      `Conflicting values were retained for: ${[...conflictTopics].sort().join(', ')}.`,
    );
  }
  const uniqueWarnings = [...new Set(warnings)];
  const status =
    findings.length === 0
      ? 'failed'
      : draft.status === 'failed' || sources.length < draft.sources.length
        ? 'partial'
        : draft.status;
  const packet = researchPacketSchema.parse({
    ...draft,
    status,
    findings,
    sources,
    warnings: uniqueWarnings,
  });
  const maxResponseBytes = options.maxResponseBytes ?? 64_000;
  if (
    new TextEncoder().encode(JSON.stringify(packet)).byteLength >
    maxResponseBytes
  ) {
    throw new RangeError(
      `Research packet exceeds the ${String(maxResponseBytes)} byte response limit.`,
    );
  }
  return packet;
}

export function createFailedResearchPacket(
  query: string,
  warning: string,
): ResearchPacket {
  return researchPacketSchema.parse({
    schemaVersion: 1,
    status: 'failed',
    advisoryOnly: true,
    queries: [query.slice(0, 1_000)],
    findings: [],
    sources: [],
    warnings: [warning.slice(0, 1_000)],
  });
}
