import {
  copyFile,
  mkdir,
  readFile,
  readdir,
  rename,
  writeFile,
} from 'node:fs/promises';
import { basename, extname, resolve } from 'node:path';

const appRoot = resolve(import.meta.dirname, '..');
const sourceRoot = resolve(appRoot, 'bundled-projects/amagine3d-pomodoro');
const outputRoot = resolve(appRoot, 'public/showcase/amagine3d-pomodoro');
const logicalManifestPath = resolve(sourceRoot, 'manifest.json');
const supportedExtensions = new Set(['.3mf', '.step', '.stl', '.stp']);

async function sourceFiles() {
  try {
    return (await readdir(sourceRoot, { withFileTypes: true }))
      .filter(
        (entry) =>
          entry.isFile() &&
          supportedExtensions.has(extname(entry.name).toLowerCase()),
      )
      .map((entry) => entry.name)
      .sort((left, right) => left.localeCompare(right, 'en'));
  } catch (error) {
    if (error?.code === 'ENOENT') return [];
    throw error;
  }
}

function safeStem(fileName, fallback) {
  const normalized = basename(fileName, extname(fileName))
    .normalize('NFKD')
    .replaceAll(/[^A-Za-z0-9]+/gu, '-')
    .replaceAll(/^-+|-+$/gu, '')
    .toLowerCase();
  return normalized || fallback;
}

function uniqueFileName(candidate, used) {
  const extension = extname(candidate);
  const stem = basename(candidate, extension);
  let next = candidate;
  let sequence = 2;
  while (used.has(next.toLowerCase())) {
    next = `${stem}-${String(sequence)}${extension}`;
    sequence += 1;
  }
  used.add(next.toLowerCase());
  return next;
}

async function loadLogicalManifest(stlFiles) {
  let manifest;
  try {
    manifest = JSON.parse(await readFile(logicalManifestPath, 'utf8'));
  } catch (error) {
    if (error?.code === 'ENOENT') {
      return { orderedStlFiles: stlFiles, manifest: undefined };
    }
    throw new Error(
      `Unable to read bundled Pomodoro logical manifest: ${error instanceof Error ? error.message : String(error)}`,
      { cause: error },
    );
  }
  if (
    manifest === null ||
    typeof manifest !== 'object' ||
    manifest.schemaVersion !== 1 ||
    manifest.units !== 'mm' ||
    manifest.parts === null ||
    typeof manifest.parts !== 'object' ||
    Array.isArray(manifest.parts)
  ) {
    throw new Error(
      'Bundled Pomodoro manifest.json must be version 1 with millimeter units and a parts object.',
    );
  }
  const available = new Set(stlFiles);
  const ordered = [];
  const parts = {};
  for (const [partName, entries] of Object.entries(manifest.parts)) {
    if (!Array.isArray(entries) || entries.length === 0) {
      throw new Error(`Pomodoro logical part ${partName} has no STL entries.`);
    }
    const normalizedEntries = [];
    for (const entry of entries) {
      const fileName =
        entry !== null &&
        typeof entry === 'object' &&
        typeof entry.file === 'string'
          ? basename(entry.file)
          : '';
      if (
        !available.has(fileName) ||
        extname(fileName).toLowerCase() !== '.stl' ||
        typeof entry.color !== 'string' ||
        !/^#[0-9A-Fa-f]{6}$/u.test(entry.color)
      ) {
        throw new Error(
          `Pomodoro logical part ${partName} references an invalid or missing STL.`,
        );
      }
      if (ordered.includes(fileName)) {
        throw new Error(
          `Pomodoro logical manifest references ${fileName} more than once.`,
        );
      }
      ordered.push(fileName);
      normalizedEntries.push({ file: fileName, color: entry.color });
    }
    parts[partName] = normalizedEntries;
  }
  const missing = stlFiles.filter((fileName) => !ordered.includes(fileName));
  if (missing.length > 0) {
    throw new Error(
      `Pomodoro logical manifest does not reference: ${missing.join(', ')}.`,
    );
  }
  const colors = manifest.colors ?? {};
  if (
    colors === null ||
    typeof colors !== 'object' ||
    Array.isArray(colors) ||
    Object.values(colors).some(
      (color) => typeof color !== 'string' || !/^#[0-9A-Fa-f]{6}$/u.test(color),
    )
  ) {
    throw new Error('Pomodoro logical manifest colors must be #RRGGBB values.');
  }
  const explodedOffsets = manifest.exploded_offsets_mm;
  if (
    explodedOffsets === null ||
    typeof explodedOffsets !== 'object' ||
    Array.isArray(explodedOffsets) ||
    Object.keys(parts).some((partName) => {
      const offset = explodedOffsets[partName];
      return (
        !Array.isArray(offset) ||
        offset.length !== 3 ||
        offset.some(
          (value) => typeof value !== 'number' || !Number.isFinite(value),
        )
      );
    }) ||
    Object.keys(explodedOffsets).some((partName) => !(partName in parts))
  ) {
    throw new Error(
      'Pomodoro logical manifest exploded_offsets_mm must define one finite XYZ offset for every logical part.',
    );
  }
  const animationOrder = manifest.animation_order;
  if (
    !Array.isArray(animationOrder) ||
    animationOrder.length === 0 ||
    animationOrder.some(
      (stage) => typeof stage !== 'string' || stage.trim().length === 0,
    )
  ) {
    throw new Error(
      'Pomodoro logical manifest animation_order must be a non-empty string array.',
    );
  }
  return {
    orderedStlFiles: ordered,
    manifest: {
      name:
        typeof manifest.name === 'string' && manifest.name.trim().length > 0
          ? manifest.name
          : 'Amagine3D Pomodoro Timer',
      units: 'mm',
      colors,
      parts,
      exploded_offsets_mm: explodedOffsets,
      animation_order: animationOrder,
    },
  };
}

const files = await sourceFiles();
if (files.length === 0) {
  console.log(
    'Bundled Pomodoro project skipped: add .3mf, .step, and split .stl files under apps/web/bundled-projects/amagine3d-pomodoro/.',
  );
} else {
  const threeMfFiles = files.filter(
    (fileName) => extname(fileName).toLowerCase() === '.3mf',
  );
  const stepFiles = files.filter((fileName) =>
    ['.step', '.stp'].includes(extname(fileName).toLowerCase()),
  );
  const logical = await loadLogicalManifest(
    files.filter((fileName) => extname(fileName).toLowerCase() === '.stl'),
  );
  const stlFiles = logical.orderedStlFiles;
  if (
    threeMfFiles.length !== 1 ||
    stepFiles.length === 0 ||
    stlFiles.length === 0
  ) {
    throw new Error(
      'Bundled Pomodoro assets require exactly one 3MF, at least one STEP/STP, and at least one split STL file.',
    );
  }

  await mkdir(outputRoot, { recursive: true });
  const used = new Set();
  const entries = [];
  const outputNames = new Map();
  const groups = [
    {
      files: threeMfFiles,
      kind: 'model-3mf',
      fallback: 'model',
      extension: '.3mf',
    },
    { files: stepFiles, kind: 'step', fallback: 'model', extension: '.step' },
    { files: stlFiles, kind: 'stl', fallback: 'part', extension: '.stl' },
  ];
  for (const group of groups) {
    for (const [index, sourceName] of group.files.entries()) {
      const preferredStem =
        group.kind === 'model-3mf'
          ? 'Amagine3D-Pomodoro-Timer'
          : safeStem(
              sourceName,
              `${group.fallback}-${String(index + 1).padStart(2, '0')}`,
            );
      const outputName = uniqueFileName(
        `${preferredStem}${group.extension}`,
        used,
      );
      await copyFile(
        resolve(sourceRoot, sourceName),
        resolve(outputRoot, outputName),
      );
      outputNames.set(sourceName, outputName);
      entries.push({ kind: group.kind, fileName: outputName });
    }
  }

  const outputNameFor = (sourceName) => {
    const outputName = outputNames.get(sourceName);
    if (outputName === undefined) {
      throw new Error(`Bundled Pomodoro output is missing ${sourceName}.`);
    }
    return outputName;
  };

  const manifest = {
    schemaVersion: 1,
    ...(logical.manifest === undefined
      ? {}
      : {
          ...logical.manifest,
          parts: Object.fromEntries(
            Object.entries(logical.manifest.parts).map(
              ([partName, partEntries]) => [
                partName,
                partEntries.map((entry) => ({
                  ...entry,
                  file: outputNameFor(entry.file),
                })),
              ],
            ),
          ),
        }),
    assets: entries,
  };
  const manifestPath = resolve(outputRoot, 'manifest.json');
  const temporaryPath = `${manifestPath}.${process.pid}.tmp`;
  await writeFile(
    temporaryPath,
    `${JSON.stringify(manifest, null, 2)}\n`,
    'utf8',
  );
  await rename(temporaryPath, manifestPath);

  // Read once after the atomic rename so build failures surface filesystem issues.
  await readFile(manifestPath, 'utf8');
  console.log(
    `Prepared bundled Pomodoro project with ${String(entries.length)} model files.`,
  );
}
