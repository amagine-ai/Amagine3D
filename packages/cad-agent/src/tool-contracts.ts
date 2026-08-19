import {
  buildAndCheckInputSchema,
  cadToolOutputSchema,
  designBriefSchema,
  finishCadRunInputSchema,
  visualReviewInputSchema,
  writeCadSourceInputSchema,
  type BuildAndCheckInput,
  type CadToolOutput,
  type CadWorkflowKind,
  type DesignBrief,
  type FinishCadRunInput,
  type VisualReviewInput,
  type WriteCadSourceInput,
} from '@amagine3d/cad-protocol';
import { tool } from 'ai';

export type CadToolImplementations = {
  saveDesignBrief: (input: DesignBrief) => Promise<CadToolOutput>;
  writeCadSource: (input: WriteCadSourceInput) => Promise<CadToolOutput>;
  buildAndCheck: (input: BuildAndCheckInput) => Promise<CadToolOutput>;
  requestVisualReview: (input: VisualReviewInput) => Promise<CadToolOutput>;
  finishCadRun: (input: FinishCadRunInput) => Promise<CadToolOutput>;
};

function optionalExecute<Input>(
  implementation: ((input: Input) => Promise<CadToolOutput>) | undefined,
) {
  return implementation === undefined ? {} : { execute: implementation };
}

export function createCadTools(
  workflowKind: CadWorkflowKind,
  implementations?: Partial<CadToolImplementations>,
) {
  const profileLabel =
    workflowKind === 'single-color' ? 'single-color' : 'multi-color';
  return {
    saveDesignBrief: tool({
      description: `Save the structured ${profileLabel} design brief. Research values belong only in researchHints unless the user explicitly accepted them. For an opening/closing mechanism, select a printable construction pattern before coding and record body/feature ownership, functional datum and fit equations, print poses, assembly order, complete motion, and running clearances; these values must drive generation rather than being inferred afterward for QA.`,
      inputSchema: designBriefSchema,
      outputSchema: cadToolOutputSchema,
      ...optionalExecute(implementations?.saveDesignBrief),
    }),
    writeCadSource: tool({
      description: `Write the complete ${profileLabel} build123d model.py source. Generate mating interfaces from the frozen shared datums and clearance equations, fuse attachments to their owning body, cut mating voids after unions, and reserve the full moving/assembly envelope before optional details. Use only the selected Amagine3D workflow operations and publisher.${
        workflowKind === 'multi-color'
          ? ' publish_color_model dictionary keys must exactly match the frozen color-region IDs.'
          : ''
      }`,
      inputSchema: writeCadSourceInputSchema,
      outputSchema: cadToolOutputSchema,
      ...optionalExecute(implementations?.writeCadSource),
    }),
    buildAndCheck: tool({
      description:
        workflowKind === 'single-color'
          ? 'Run model.py in the browser CAD Worker, export STEP/STL, and perform deterministic overall QA plus every mechanism motion and clearance check frozen in the design brief. qaTargets sizeX/sizeY/sizeZ describe the full assembled envelope of every published body and protrusion, never a shell-only size. componentCount is the intended number of separately published physical prints; every named body must contain exactly one connected solid.'
          : 'Run model.py in the browser CAD Worker, export region STLs and colored 3MF, then perform per-region, overlap, overall, mechanism motion, clearance, and 3MF readback QA. qaTargets sizeX/sizeY/sizeZ describe the full assembled envelope of every published body and protrusion, never a shell-only size.',
      inputSchema: buildAndCheckInputSchema,
      outputSchema: cadToolOutputSchema,
      ...optionalExecute(implementations?.buildAndCheck),
    }),
    requestVisualReview: tool({
      description:
        'Request the explicitly user-approved visual review. This tool is unavailable unless consent was frozen for this run.',
      inputSchema: visualReviewInputSchema,
      outputSchema: cadToolOutputSchema,
      ...optionalExecute(implementations?.requestVisualReview),
    }),
    finishCadRun: tool({
      description:
        'Finish only after the selected profile has passed every deterministic completion requirement and any approved visual review.',
      inputSchema: finishCadRunInputSchema,
      outputSchema: cadToolOutputSchema,
      ...optionalExecute(implementations?.finishCadRun),
    }),
  };
}

export type CadTools = ReturnType<typeof createCadTools>;
