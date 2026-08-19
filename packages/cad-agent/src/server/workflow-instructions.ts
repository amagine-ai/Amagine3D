import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { resolve, sep } from 'node:path';

import {
  CadDomainError,
  containsPrintableClosureIntent,
  researchPacketSchema,
  type CadWorkflowKind,
  type ResearchPacket,
} from '@amagine3d/cad-protocol';
import { z } from 'zod';

import { getCadWorkflowProfile } from '../workflow-profile';

const manifestSchema = z.object({
  schemaVersion: z.literal(1),
  engine: z.literal('Amagine3D-CAD'),
  revision: z.string().regex(/^[A-Za-z0-9._-]+$/u),
  profiles: z.object({
    'single-color': z.string().min(1),
    'multi-color': z.string().min(1),
  }),
  authoringGuide: z.string().min(1),
  files: z.record(z.string(), z.string().regex(/^sha256:[a-f0-9]{64}$/u)),
  conditionalGuides: z.object({
    printableClosures: z.object({
      path: z.string().regex(/^mechanisms\/[A-Za-z0-9._/-]+\.md$/u),
      sha256: z.string().regex(/^sha256:[a-f0-9]{64}$/u),
    }),
  }),
});

export type VerifiedWorkflowInstructions = {
  revision: string;
  profileId: 'hardware-enclosure-single' | 'hardware-enclosure-multi';
  workflow: string;
  authoringGuide: string;
  printableClosuresGuide: string;
};

function insideRoot(root: string, relativePath: string): string {
  const absolute = resolve(root, relativePath);
  if (absolute !== root && !absolute.startsWith(`${root}${sep}`)) {
    throw new CadDomainError(
      'IntegrityMismatch',
      `Workflow resource escaped its root: ${relativePath}`,
      { category: 'integrity', retryable: false, operation: 'verify-workflow' },
    );
  }
  return absolute;
}

function sha256(bytes: Uint8Array): string {
  return createHash('sha256').update(bytes).digest('hex');
}

export async function verifyWorkflowResources(
  promptRoot: string,
): Promise<{ revision: string }> {
  const root = resolve(promptRoot);
  const manifest = manifestSchema.parse(
    JSON.parse(await readFile(resolve(root, 'manifest.json'), 'utf8')),
  );
  const required = new Set([
    manifest.profiles['single-color'],
    manifest.profiles['multi-color'],
    manifest.authoringGuide,
  ]);
  if ([...required].some((path) => manifest.files[path] === undefined)) {
    throw new CadDomainError(
      'IntegrityMismatch',
      'Workflow manifest does not cover every required resource.',
      { category: 'integrity', retryable: false, operation: 'verify-workflow' },
    );
  }
  for (const [relativePath, expected] of Object.entries(manifest.files)) {
    const actual = sha256(await readFile(insideRoot(root, relativePath)));
    if (`sha256:${actual}` !== expected) {
      throw new CadDomainError(
        'IntegrityMismatch',
        `Workflow resource checksum failed for ${relativePath}.`,
        {
          category: 'integrity',
          retryable: false,
          operation: 'verify-workflow',
        },
      );
    }
  }
  const printableClosures = manifest.conditionalGuides.printableClosures;
  const printableClosuresHash = sha256(
    await readFile(insideRoot(root, printableClosures.path)),
  );
  if (`sha256:${printableClosuresHash}` !== printableClosures.sha256) {
    throw new CadDomainError(
      'IntegrityMismatch',
      `Workflow resource checksum failed for ${printableClosures.path}.`,
      { category: 'integrity', retryable: false, operation: 'verify-workflow' },
    );
  }
  return { revision: manifest.revision };
}

export async function loadVerifiedWorkflowInstructions(
  promptRoot: string,
  workflowKind: CadWorkflowKind,
): Promise<VerifiedWorkflowInstructions> {
  const root = resolve(promptRoot);
  const manifest = manifestSchema.parse(
    JSON.parse(await readFile(resolve(root, 'manifest.json'), 'utf8')),
  );
  await verifyWorkflowResources(root);
  const profile = getCadWorkflowProfile(workflowKind);
  const [workflow, authoringGuide, printableClosuresGuide] = await Promise.all([
    readFile(insideRoot(root, manifest.profiles[workflowKind]), 'utf8'),
    readFile(insideRoot(root, manifest.authoringGuide), 'utf8'),
    readFile(
      insideRoot(root, manifest.conditionalGuides.printableClosures.path),
      'utf8',
    ),
  ]);
  return {
    revision: manifest.revision,
    profileId: profile.profileId,
    workflow,
    authoringGuide,
    printableClosuresGuide,
  };
}

function dataJson(value: unknown): string {
  return JSON.stringify(value).replaceAll('<', '\\u003c');
}

export type AssembleInstructionsInput = {
  runId: string;
  workflowKind: CadWorkflowKind;
  userRequest: string;
  instructions: VerifiedWorkflowInstructions;
  research?: ResearchPacket;
  modificationContext?: {
    source: string;
    designBrief: unknown;
    parameterSchema: unknown;
    latestQa?: unknown;
  };
};

function needsPrintableClosureGuide(input: AssembleInstructionsInput): boolean {
  const signals = [input.userRequest];
  if (input.modificationContext !== undefined) {
    signals.push(JSON.stringify(input.modificationContext.designBrief));
  }
  return containsPrintableClosureIntent(signals);
}

export function assembleCadInstructions(
  input: AssembleInstructionsInput,
): string {
  const request = input.userRequest.trim();
  if (request.length === 0 || request.length > 8_000) {
    throw new CadDomainError(
      'InvalidExternalData',
      'CAD user request must contain between 1 and 8000 characters.',
      {
        category: 'protocol',
        retryable: false,
        operation: 'assemble-instructions',
      },
    );
  }
  const profile = getCadWorkflowProfile(input.workflowKind);
  if (input.instructions.profileId !== profile.profileId) {
    throw new CadDomainError(
      'IntegrityMismatch',
      'Loaded workflow instructions do not match the selected profile.',
      {
        category: 'integrity',
        retryable: false,
        operation: 'assemble-instructions',
      },
    );
  }
  const research =
    input.research === undefined
      ? undefined
      : researchPacketSchema.parse(input.research);
  const advisory =
    research === undefined
      ? '<advisory_web_research status="absent" />'
      : `<advisory_web_research advisory_only="true">${dataJson(research)}</advisory_web_research>`;
  const modification =
    input.modificationContext === undefined
      ? '<modification_context status="absent" />'
      : `<modification_context>${dataJson(input.modificationContext)}</modification_context>`;
  const printableClosures = needsPrintableClosureGuide(input)
    ? `<amagine3d_printable_closure_design>
${input.instructions.printableClosuresGuide}
</amagine3d_printable_closure_design>

`
    : '';

  return `<amagine3d_workflow profile="${profile.profileId}" revision="${input.instructions.revision}">
${input.instructions.workflow}
</amagine3d_workflow>

<amagine3d_build123d_authoring>
${input.instructions.authoringGuide}
</amagine3d_build123d_authoring>

${printableClosures}<host_contract>
You are operating Amagine3D run ${input.runId}. The workflow is frozen to ${input.workflowKind}.
Do not emit shell commands or access files directly. Use exactly the active typed host tool for the current phase.
saveDesignBrief must separate userConstraints, agentAssumptions, researchHints, features, verificationTargets, and derivationNotes. Constraint unit values must be short measurement labels such as mm, degrees, count, or posts; put explanations in rationale. Research is advisory-only and must not become a verification target unless the user request explicitly accepts that value. Freeze every exact hole, interface, keep-out and datum requirement as a featureChecks entry with a machine-safe feature ID, metric, expected value and justified tolerance. Any lid, hinge, latch or other opening/closing interface requires frozen mechanisms entries whose movingBodyIds and stationaryBodyIds exactly partition every published body and whose motions describe the complete rigid opening and assembly paths from the canonical published pose. Before freezing a closure brief, choose one suitable construction pattern and put its body/feature ownership, functional datum equations, fit-budget equations, per-body print poses, assembly sequence and reserved moving envelope in features and derivationNotes. The frozen axis, travel and clearance must be evaluated from the same parameters and equations that will generate the mating geometry; never invent a QA path after modeling.
writeCadSource must return one complete model.py using only the selected profile's amagine_cad operations and ${profile.publisher}. A request may create multiple separately printable bodies. Every publisher dictionary value is exactly one physical print and must contain exactly one connected watertight solid; publish every intentionally separate component under its own key. Generate a closure from its functional interface outward: primary bodies in the canonical pose, closure attachments fused to their named owners, shared mating cuts after all unions, and separate pins or retainers. The first source revision must use the simplest topology that satisfies the requested shell, selected closure and explicit fit features; omit invented internals and cosmetic finishing until structural and motion QA pass. Derive mating dimensions from common source parameters, such as bore radius from pin radius plus radial clearance or groove size from rail size plus sliding clearance; do not use independent magic numbers on opposite sides of one fit. Call observe_feature with every frozen featureChecks featureId before a boolean erases that shape. Represent reserved connector insertion, cable bend, antenna and control-travel spaces as construction solids observed with feature_type="keep-out"; never publish a keep-out as a physical body. All bodies must be published in one call. Do not add sys.path manipulation. Represent every independent geometry-driving dimension as a separate top-level uppercase constant assigned a plain literal. Directly above each driving constant place a # @param annotation with justified label, group, unit, bounds, step, and description metadata. Derived mating sizes and coordinates must be explicit equations using those literals, not independently editable duplicates or unexplained inline values.
buildAndCheck returns deterministic diagnostics and a repairContext on QA failure. Mechanism definitions are taken from the frozen design brief, not invented or weakened during the build call. Feature measurements and keep-outs are likewise frozen and evaluated from observed source geometry. The Worker applies every motion to the exported body STEP geometry and rejects unknown body IDs, incomplete body partitions, sampled collisions and failed declared running-clearance checks. Treat the reported worst body pair and motion pose as a local source-repair target, together with any reported feature owner: preserve the selected mechanism, shared datum and every passing check, then adjust the responsible interface clearance, swept-envelope obstruction, owner attachment or insertion lead-in. Use repairContext.baselineRevisionId and baselineSourceHash, and edit only repairContext.allowedMutationScope. When newlyFailedCheckIds is nonempty, the host has already restored the baseline source and persisted an automatic-rollback revision; continue from that restored source instead of the rejected candidate. Structural booleans and valid connected solids come first, assembly overlap/clearance/insertion second, dimensions third, and finishing last. The host stops a deterministic failure signature after it recurs five times, including nonconsecutive attempts.
finishCadRun is legal only after all ${profile.requiredQaSections.join(', ')} requirements pass. Do not claim completion before finishCadRun succeeds.
Visual review is available only after explicit user consent, for at most three rounds.
</host_contract>

<user_request_data_json>${dataJson(request)}</user_request_data_json>

${advisory}

${modification}`;
}
