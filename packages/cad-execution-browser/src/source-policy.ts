import { CadDomainError, type CadWorkflowKind } from '@amagine3d/cad-protocol';

const ALLOWED_IMPORT_ROOTS = new Set(['build123d', 'amagine_cad', 'math']);
const FORBIDDEN_CALLS = [
  'compile',
  'eval',
  'exec',
  'globals',
  'getattr',
  'input',
  'locals',
  'open',
  'setattr',
  'vars',
] as const;
const FORBIDDEN_TEXT = [
  '__builtins__',
  '__import__',
  'importlib',
  'micropip',
  'pyodide',
  'subprocess',
  'cad_helpers',
  'finalize_parts',
  'finalize(',
] as const;

function reject(message: string): never {
  throw new CadDomainError('ExecutionRejected', message, {
    category: 'execution',
    retryable: false,
    operation: 'validate-source',
  });
}

function importedRoots(source: string): string[] {
  const roots: string[] = [];
  for (const line of source.split(/\r?\n/u)) {
    const fromMatch = /^\s*from\s+([^\s]+)\s+import\s+/u.exec(line);
    if (fromMatch?.[1]) roots.push(fromMatch[1].split('.')[0] ?? '');

    const importMatch = /^\s*import\s+(.+)$/u.exec(line);
    if (!importMatch?.[1]) continue;
    for (const specifier of importMatch[1].split(',')) {
      roots.push(
        specifier
          .trim()
          .split(/\s+as\s+/u)[0]
          ?.split('.')[0] ?? '',
      );
    }
  }
  return roots;
}

function importedAmagineNames(source: string): string[] {
  const names: string[] = [];
  const pattern =
    /^[\t ]*from[\t ]+amagine_cad[\t ]+import[\t ]+(?:\(([\s\S]*?)\)|([^\r\n]+))/gmu;
  for (const match of source.matchAll(pattern)) {
    const imported = match[1] ?? match[2] ?? '';
    names.push(
      ...imported
        .replaceAll(/#[^\r\n]*/gu, '')
        .split(',')
        .map((name) => name.trim())
        .filter(Boolean),
    );
  }
  return names;
}

function namedCallArguments(source: string, name: string): string[][] {
  const calls: string[][] = [];
  const pattern = new RegExp(`\\b${name}\\s*\\(`, 'gu');
  for (const match of source.matchAll(pattern)) {
    const opening = (match.index ?? 0) + match[0].lastIndexOf('(');
    let depth = 1;
    let quote: "'" | '"' | undefined;
    let escaped = false;
    let inComment = false;
    let start = opening + 1;
    const args: string[] = [];
    for (let index = opening + 1; index < source.length; index += 1) {
      const character = source[index];
      if (inComment) {
        if (character === '\n') inComment = false;
        continue;
      }
      if (quote !== undefined) {
        if (escaped) {
          escaped = false;
        } else if (character === '\\') {
          escaped = true;
        } else if (character === quote) {
          quote = undefined;
        }
        continue;
      }
      if (character === '#') {
        inComment = true;
      } else if (character === "'" || character === '"') {
        quote = character;
      } else if (character === '(' || character === '[' || character === '{') {
        depth += 1;
      } else if (character === ')' || character === ']' || character === '}') {
        depth -= 1;
        if (depth === 0) {
          const argument = source.slice(start, index).trim();
          if (argument.length > 0) args.push(argument);
          calls.push(args);
          pattern.lastIndex = index + 1;
          break;
        }
      } else if (character === ',' && depth === 1) {
        args.push(source.slice(start, index).trim());
        start = index + 1;
      }
    }
  }
  return calls;
}

function requireExplicitHelperArgument(
  source: string,
  helper: string,
  parameter: string,
  positionalIndex: number,
): void {
  for (const args of namedCallArguments(source, helper)) {
    if (args.some((argument) => argument.startsWith('*'))) {
      reject(
        `${helper} requires explicit arguments; argument expansion is not allowed.`,
      );
    }
    const keywordPresent = args.some((argument) =>
      new RegExp(`^${parameter}\\s*=`, 'u').test(argument),
    );
    const positionalCount = args.filter(
      (argument) => !/^[A-Za-z_][A-Za-z0-9_]*\s*=/u.test(argument),
    ).length;
    if (!keywordPresent && positionalCount <= positionalIndex) {
      reject(
        `${helper} requires '${parameter}'. Use ${helper}(shape, edges, ${parameter}=VALUE, label="...").`,
      );
    }
  }
}

function rejectUnsupportedCallKeywords(
  source: string,
  call: string,
  keywords: string[],
  message: string,
): void {
  const patterns = keywords.map(
    (keyword) => new RegExp(`^${keyword}\\s*=`, 'u'),
  );
  for (const args of namedCallArguments(source, call)) {
    if (
      args.some((argument) =>
        patterns.some((pattern) => pattern.test(argument)),
      )
    ) {
      reject(message);
    }
  }
}

const HELPERS_BY_WORKFLOW = {
  'single-color': new Set([
    'bevel_edges_checked',
    'observe_feature',
    'publish_model',
    'round_edges_checked',
    'subtract_checked',
  ]),
  'multi-color': new Set([
    'bevel_edges_checked',
    'observe_feature',
    'publish_color_model',
    'round_edges_checked',
    'subtract_checked',
  ]),
} as const;

/**
 * Fast host-side rejection. The Worker repeats these rules with Python's AST
 * and a restricted builtins table before executing any source.
 */
export function validateCadSource(
  source: string,
  workflowKind: CadWorkflowKind,
): void {
  if (!source.trim()) reject('CAD source is empty.');

  for (const root of importedRoots(source)) {
    if (!ALLOWED_IMPORT_ROOTS.has(root)) {
      reject(`Import '${root}' is not allowed in CAD source.`);
    }
  }

  if (/^\s*import\s+(?:build123d|amagine_cad)(?:\s|$)/mu.test(source)) {
    reject('build123d and amagine_cad require explicit from-imports.');
  }
  for (const importedName of importedAmagineNames(source)) {
    if (/\s+as\s+/u.test(importedName)) {
      reject('amagine_cad imports cannot be aliased.');
    }
    const name = importedName.split(/\s+as\s+/u)[0] ?? '';
    if (!HELPERS_BY_WORKFLOW[workflowKind].has(name)) {
      reject(
        `amagine_cad import crosses the ${workflowKind} profile boundary.`,
      );
    }
  }

  for (const name of FORBIDDEN_CALLS) {
    if (new RegExp(`\\b${name}\\s*\\(`, 'u').test(source)) {
      reject(`Call '${name}' is not allowed in CAD source.`);
    }
  }
  for (const text of FORBIDDEN_TEXT) {
    if (source.includes(text))
      reject(`CAD source contains forbidden '${text}'.`);
  }

  if (/\.\s*__[A-Za-z0-9_]+/u.test(source)) {
    reject('Dunder attribute access is not allowed in CAD source.');
  }
  if (/\bexport_[A-Za-z0-9_]*\b/u.test(source)) {
    reject('Direct CAD exporters are not allowed; use the selected helper.');
  }
  if (/\b(?:import_[A-Za-z0-9_]*|FontManager|Mesher)\b/u.test(source)) {
    reject('Direct CAD file readers and writers are not allowed.');
  }
  rejectUnsupportedCallKeywords(
    source,
    'Cylinder',
    ['depth'],
    "build123d Cylinder uses 'height=', not 'depth='.",
  );
  rejectUnsupportedCallKeywords(
    source,
    'Cylinder',
    ['h'],
    "build123d Cylinder uses 'height=', not 'h='.",
  );
  rejectUnsupportedCallKeywords(
    source,
    'extrude',
    ['direction'],
    "build123d extrude uses 'dir=', not 'direction='.",
  );
  rejectUnsupportedCallKeywords(
    source,
    'revolve',
    ['angle'],
    "build123d revolve uses 'revolution_arc=', not 'angle='.",
  );
  rejectUnsupportedCallKeywords(
    source,
    'RegularPolygon',
    ['sides'],
    "build123d RegularPolygon uses 'side_count=', not 'sides='.",
  );
  rejectUnsupportedCallKeywords(
    source,
    'Ellipse',
    ['width', 'height'],
    "build123d Ellipse uses 'x_radius=' and 'y_radius=', not width/height.",
  );
  if (/\bSlotCenterLine\s*\(/u.test(source)) {
    reject(
      'build123d 0.11.1 has no SlotCenterLine; use SlotCenterToCenter(center_separation, height).',
    );
  }
  if (/\.center\s*\(\s*\)\s*\.[xyz]\b/u.test(source)) {
    reject('build123d Vector coordinates are uppercase: use .X, .Y, or .Z.');
  }
  if (/\bWorkplane\s*\(/u.test(source)) {
    reject(
      'CadQuery Workplane syntax is not part of the build123d algebra API.',
    );
  }
  if (/\bScale\s*\(/u.test(source)) {
    reject(
      'build123d 0.11.1 has no Scale transform; use scale(shape, by=(sx, sy, sz)) or construct the intended primitive directly.',
    );
  }
  requireExplicitHelperArgument(source, 'round_edges_checked', 'radius', 2);
  requireExplicitHelperArgument(source, 'bevel_edges_checked', 'length', 2);
  // Generated programs may use Python raw strings (`r"cad_out"`).
  // Match the literal value rather than the quote immediately following `=`;
  // the Worker repeats this boundary check with Python AST semantics.
  const unsafeOutput =
    /\b(?:publish_model|publish_color_model)\s*\([^\n]*out_dir\s*=\s*(?!(?:[rR])?["']cad_out["'])/u;
  if (unsafeOutput.test(source)) {
    reject("Helper output is restricted to the run's 'cad_out' directory.");
  }

  const usesSinglePublisher = /\bpublish_model\s*\(/u.test(source);
  const usesMultiPublisher = /\bpublish_color_model\s*\(/u.test(source);
  if (workflowKind === 'single-color' && usesMultiPublisher) {
    reject('Single-color runs cannot call publish_color_model().');
  }
  if (workflowKind === 'multi-color' && usesSinglePublisher) {
    reject('Multi-color runs cannot call publish_model().');
  }
}

export function safeWorkspaceSegment(value: string): string {
  if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,159}$/u.test(value)) {
    reject(`Unsafe workspace identifier '${value}'.`);
  }
  return value;
}
