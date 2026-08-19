import {
  CadDomainError,
  SCHEMA_VERSION,
  parameterSetSchema,
  type ParameterCoupling,
  type ParameterDefinition,
  type ParameterSet,
  type ParameterValue,
} from '@amagine3d/cad-protocol';

type LiteralMatch = {
  name: string;
  value: ParameterValue;
  start: number;
  end: number;
  metadata: Record<string, string>;
};

const ASSIGNMENT =
  /^(?<name>[A-Z][A-Z0-9_]*)(?:\s*:\s*[^=]+)?\s*=\s*(?<literal>true|false|True|False|[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?|"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')(?<suffix>\s*(?:#.*)?)$/u;

function sourceHashConflict(message: string): CadDomainError {
  return new CadDomainError('SourceHashConflict', message, {
    category: 'integrity',
    retryable: false,
    operation: 'parameters',
  });
}

function parseStringLiteral(literal: string): string {
  const quote = literal[0];
  const body = literal.slice(1, -1);
  let result = '';
  for (let index = 0; index < body.length; index += 1) {
    const character = body[index];
    if (character !== '\\') {
      result += character;
      continue;
    }
    index += 1;
    const escaped = body[index];
    if (escaped === undefined) throw new Error('Incomplete string escape.');
    const replacements: Record<string, string> = {
      '\\': '\\',
      n: '\n',
      r: '\r',
      t: '\t',
      '"': '"',
      "'": "'",
    };
    result += replacements[escaped] ?? escaped;
  }
  if (quote !== '"' && quote !== "'") {
    throw new Error('Unsupported string literal.');
  }
  return result;
}

function parseLiteral(literal: string): ParameterValue {
  if (literal === 'true' || literal === 'True') return true;
  if (literal === 'false' || literal === 'False') return false;
  if (literal.startsWith('"') || literal.startsWith("'")) {
    return parseStringLiteral(literal);
  }
  const value = Number(literal);
  if (!Number.isFinite(value)) throw new Error('Parameter must be finite.');
  return value;
}

function parseMetadata(comment: string | undefined): Record<string, string> {
  if (comment === undefined) return {};
  const marker = comment.indexOf('@param');
  if (marker < 0) return {};
  const metadata: Record<string, string> = {};
  const body = comment.slice(marker + '@param'.length);
  const entry =
    /(?<key>[a-z][a-zA-Z]*)=(?<value>"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|[^\s]+)/gu;
  for (const match of body.matchAll(entry)) {
    const key = match.groups?.key;
    const rawValue = match.groups?.value;
    if (key === undefined || rawValue === undefined) continue;
    metadata[key] =
      rawValue.startsWith('"') || rawValue.startsWith("'")
        ? parseStringLiteral(rawValue)
        : rawValue;
  }
  return metadata;
}

function findLiterals(source: string): LiteralMatch[] {
  const matches: LiteralMatch[] = [];
  const lines = source.split(/(?<=\n)/u);
  let offset = 0;
  let pendingMetadata: Record<string, string> = {};
  for (const lineWithEnding of lines) {
    const line = lineWithEnding.replace(/\r?\n$/u, '');
    if (/^#\s*@param\b/u.test(line)) {
      pendingMetadata = parseMetadata(line);
      offset += lineWithEnding.length;
      continue;
    }
    const match = ASSIGNMENT.exec(line);
    if (
      match?.groups?.name !== undefined &&
      match.groups.literal !== undefined
    ) {
      const literal = match.groups.literal;
      const literalOffset = line.indexOf(literal);
      matches.push({
        name: match.groups.name,
        value: parseLiteral(literal),
        start: offset + literalOffset,
        end: offset + literalOffset + literal.length,
        metadata: {
          ...pendingMetadata,
          ...parseMetadata(match.groups.suffix),
        },
      });
    }
    pendingMetadata = {};
    offset += lineWithEnding.length;
  }
  return matches;
}

function titleCase(name: string): string {
  return name
    .toLowerCase()
    .split('_')
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ');
}

function optionalNumber(
  metadata: Record<string, string>,
  key: string,
): number | undefined {
  const raw = metadata[key];
  if (raw === undefined) return undefined;
  const value = Number(raw);
  if (!Number.isFinite(value)) {
    throw new Error(`Parameter metadata ${key} must be a finite number.`);
  }
  return value;
}

function definition(match: LiteralMatch): ParameterDefinition {
  const minimum = optionalNumber(match.metadata, 'min');
  const maximum = optionalNumber(match.metadata, 'max');
  const step = optionalNumber(match.metadata, 'step');
  if (minimum !== undefined && maximum !== undefined && minimum > maximum) {
    throw new Error(`${match.name} minimum cannot exceed its maximum.`);
  }
  if (step !== undefined && step <= 0) {
    throw new Error(`${match.name} step must be positive.`);
  }
  const type: ParameterDefinition['type'] =
    typeof match.value === 'boolean'
      ? 'boolean'
      : typeof match.value === 'number'
        ? 'number'
        : 'string';
  return {
    name: match.name,
    label: match.metadata.label ?? titleCase(match.name),
    type,
    defaultValue: match.value,
    value: match.value,
    ...(match.metadata.group === undefined
      ? {}
      : { group: match.metadata.group }),
    ...(match.metadata.description === undefined
      ? {}
      : { description: match.metadata.description }),
    ...(match.metadata.unit === undefined ? {} : { unit: match.metadata.unit }),
    ...(minimum === undefined ? {} : { minimum }),
    ...(maximum === undefined ? {} : { maximum }),
    ...(step === undefined ? {} : { step }),
  };
}

export function discoverParameterSet(
  source: string,
  sourceHash: string,
): ParameterSet {
  return parameterSetSchema.parse({
    schemaVersion: SCHEMA_VERSION,
    sourceHash,
    parameters: findLiterals(source).map(definition),
    history: [],
    historyCursor: 0,
    couplings: discoverParameterCouplings(source),
  });
}

// Build123d calls that carry cross-parameter hard invariants. Each entry maps
// the call to the ordered argument names so a coupling (width, height, radius)
// can be recognized, e.g. RectangleRounded(width, height, radius) requires
//   width > 2*radius AND height > 2*radius.
const INVARIANT_CALLS: Record<string, string[]> = {
  RectangleRounded: ['width', 'height', 'radius'],
};

export function discoverParameterCouplings(
  source: string,
): ParameterCoupling[] {
  const known = new Set(findLiterals(source).map((match) => match.name));
  const couplings: ParameterCoupling[] = [];
  for (const [callName, argNames] of Object.entries(INVARIANT_CALLS)) {
    const pattern = new RegExp(
      `\\b${callName}\\s*\\(\\s*([A-Z][A-Z0-9_]*)\\s*,\\s*([A-Z][A-Z0-9_]*)\\s*,\\s*([A-Z][A-Z0-9_]*)\\s*\\)`,
      'gu',
    );
    for (const match of source.matchAll(pattern)) {
      const members = match.slice(1, 1 + argNames.length);
      if (members.some((member) => !known.has(member))) continue;
      couplings.push({
        id: `${callName}(${members.join(',')})`,
        members,
        source: `${callName} requires ${argNames
          .slice(0, members.length - 1)
          .map((name) => `${name} > 2*${argNames.at(-1)}`)
          .join(' AND ')}`,
      });
    }
  }
  return couplings;
}

function valueFor(
  parameterSet: ParameterSet,
  parameterName: string,
): ParameterDefinition {
  const parameter = parameterSet.parameters.find(
    (candidate) => candidate.name === parameterName,
  );
  if (parameter === undefined) {
    throw new Error(`Unknown parameter ${parameterName}.`);
  }
  return parameter;
}

function validateValue(
  parameter: ParameterDefinition,
  value: ParameterValue,
): void {
  if (typeof value !== parameter.type) {
    throw new Error(`${parameter.name} must be ${parameter.type}.`);
  }
  if (typeof value === 'number') {
    if (parameter.minimum !== undefined && value < parameter.minimum) {
      throw new Error(
        `${parameter.name} must be at least ${parameter.minimum}.`,
      );
    }
    if (parameter.maximum !== undefined && value > parameter.maximum) {
      throw new Error(
        `${parameter.name} must be at most ${parameter.maximum}.`,
      );
    }
  }
}

function withParameterValue(
  parameterSet: ParameterSet,
  parameterName: string,
  value: ParameterValue,
): ParameterSet {
  return {
    ...parameterSet,
    parameters: parameterSet.parameters.map((parameter) =>
      parameter.name === parameterName ? { ...parameter, value } : parameter,
    ),
  };
}

export function changeParameter(
  parameterSet: ParameterSet,
  parameterName: string,
  nextValue: ParameterValue,
  changedAt = new Date().toISOString(),
): ParameterSet {
  const parameter = valueFor(parameterSet, parameterName);
  validateValue(parameter, nextValue);
  if (Object.is(parameter.value, nextValue)) return parameterSet;
  const history = parameterSet.history.slice(0, parameterSet.historyCursor);
  history.push({
    parameterName,
    previousValue: parameter.value,
    nextValue,
    changedAt,
  });
  return parameterSetSchema.parse({
    ...withParameterValue(parameterSet, parameterName, nextValue),
    history,
    historyCursor: history.length,
  });
}

export type ProportionalAdjustment = {
  parameterSet: ParameterSet;
  coupling: ParameterCoupling;
  changedName: string;
  scaledNames: string[];
};

function numericValue(value: ParameterValue): number | undefined {
  return typeof value === 'number' ? value : undefined;
}

export function applyProportionalAdjustment(
  parameterSet: ParameterSet,
  coupling: ParameterCoupling,
  changedName: string,
  nextValue: ParameterValue,
): ProportionalAdjustment {
  const changed = valueFor(parameterSet, changedName);
  const before = numericValue(changed.value);
  const after = numericValue(nextValue);
  if (before === undefined || after === undefined || before === 0) {
    return {
      parameterSet: changeParameter(parameterSet, changedName, nextValue),
      coupling,
      changedName,
      scaledNames: [],
    };
  }
  const scale = after / before;
  const scaledNames: string[] = [];
  let result = parameterSet;
  for (const member of coupling.members) {
    if (member === changedName) continue;
    const parameter = valueFor(parameterSet, member);
    const current = numericValue(parameter.value);
    if (current === undefined) continue;
    const scaled = current * scale;
    const clamped = clampToBounds(parameter, scaled);
    if (clamped === undefined) continue;
    result = changeParameter(result, member, clamped);
    scaledNames.push(member);
  }
  result = changeParameter(result, changedName, nextValue);
  return { parameterSet: result, coupling, changedName, scaledNames };
}

function clampToBounds(
  parameter: ParameterDefinition,
  value: number,
): number | undefined {
  let result = value;
  if (parameter.minimum !== undefined && result < parameter.minimum) {
    result = parameter.minimum;
  }
  if (parameter.maximum !== undefined && result > parameter.maximum) {
    result = parameter.maximum;
  }
  if (!Number.isFinite(result)) return undefined;
  return result;
}

export function couplingForParameter(
  parameterSet: ParameterSet,
  parameterName: string,
): ParameterCoupling | undefined {
  return (parameterSet.couplings ?? []).find((coupling) =>
    coupling.members.includes(parameterName),
  );
}

export function ensureParameterCouplings(
  parameterSet: ParameterSet,
  source: string,
): ParameterSet {
  if (parameterSet.couplings !== undefined) return parameterSet;
  const couplings = discoverParameterCouplings(source);
  if (couplings.length === 0) return parameterSet;
  return parameterSetSchema.parse({ ...parameterSet, couplings });
}

export function undoParameterChange(parameterSet: ParameterSet): ParameterSet {
  if (parameterSet.historyCursor === 0) return parameterSet;
  const change = parameterSet.history[parameterSet.historyCursor - 1];
  if (change === undefined) return parameterSet;
  return parameterSetSchema.parse({
    ...withParameterValue(
      parameterSet,
      change.parameterName,
      change.previousValue,
    ),
    historyCursor: parameterSet.historyCursor - 1,
  });
}

export function redoParameterChange(parameterSet: ParameterSet): ParameterSet {
  const change = parameterSet.history[parameterSet.historyCursor];
  if (change === undefined) return parameterSet;
  return parameterSetSchema.parse({
    ...withParameterValue(parameterSet, change.parameterName, change.nextValue),
    historyCursor: parameterSet.historyCursor + 1,
  });
}

export function resetParameters(
  parameterSet: ParameterSet,
  changedAt = new Date().toISOString(),
): ParameterSet {
  return parameterSet.parameters.reduce(
    (current, parameter) =>
      Object.is(parameter.value, parameter.defaultValue)
        ? current
        : changeParameter(
            current,
            parameter.name,
            parameter.defaultValue,
            changedAt,
          ),
    parameterSet,
  );
}

export function parameterOverrides(
  parameterSet: ParameterSet,
): Record<string, ParameterValue> {
  return Object.fromEntries(
    parameterSet.parameters
      .filter(
        (parameter) => !Object.is(parameter.value, parameter.defaultValue),
      )
      .map((parameter) => [parameter.name, parameter.value]),
  );
}

function pythonLiteral(value: ParameterValue): string {
  if (typeof value === 'boolean') return value ? 'True' : 'False';
  if (typeof value === 'number') return String(value);
  return JSON.stringify(value);
}

export function writeParametersToSource(
  source: string,
  currentSourceHash: string,
  parameterSet: ParameterSet,
): string {
  if (parameterSet.sourceHash !== currentSourceHash) {
    throw sourceHashConflict(
      'Parameter overrides belong to a different source revision.',
    );
  }
  const literals = new Map(
    findLiterals(source).map((match) => [match.name, match]),
  );
  const replacements = parameterSet.parameters
    .filter((parameter) => !Object.is(parameter.value, parameter.defaultValue))
    .map((parameter) => {
      const literal = literals.get(parameter.name);
      if (literal === undefined || typeof literal.value !== parameter.type) {
        throw sourceHashConflict(
          `Parameter ${parameter.name} is missing or changed type in the source.`,
        );
      }
      return {
        start: literal.start,
        end: literal.end,
        value: pythonLiteral(parameter.value),
      };
    })
    .sort((left, right) => right.start - left.start);
  return replacements.reduce(
    (result, replacement) =>
      `${result.slice(0, replacement.start)}${replacement.value}${result.slice(replacement.end)}`,
    source,
  );
}

export type SourceDiffLine = {
  kind: 'context' | 'added' | 'removed';
  text: string;
};

export function createSourceDiff(
  before: string,
  after: string,
): SourceDiffLine[] {
  const beforeLines = before.split('\n');
  const afterLines = after.split('\n');
  let prefix = 0;
  while (
    prefix < beforeLines.length &&
    prefix < afterLines.length &&
    beforeLines[prefix] === afterLines[prefix]
  ) {
    prefix += 1;
  }
  let suffix = 0;
  while (
    suffix < beforeLines.length - prefix &&
    suffix < afterLines.length - prefix &&
    beforeLines[beforeLines.length - suffix - 1] ===
      afterLines[afterLines.length - suffix - 1]
  ) {
    suffix += 1;
  }
  return [
    ...beforeLines
      .slice(0, prefix)
      .map((text) => ({ kind: 'context' as const, text })),
    ...beforeLines
      .slice(prefix, beforeLines.length - suffix)
      .map((text) => ({ kind: 'removed' as const, text })),
    ...afterLines
      .slice(prefix, afterLines.length - suffix)
      .map((text) => ({ kind: 'added' as const, text })),
    ...beforeLines
      .slice(beforeLines.length - suffix)
      .map((text) => ({ kind: 'context' as const, text })),
  ];
}
