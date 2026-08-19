import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

import {
  assembleCadInstructions,
  loadVerifiedWorkflowInstructions,
  verifyWorkflowResources,
} from './workflow-instructions';

const promptRoot = fileURLToPath(new URL('../../prompt/', import.meta.url));

describe('Amagine3D workflow instruction assembly', () => {
  it('verifies every versioned workflow resource', async () => {
    await expect(verifyWorkflowResources(promptRoot)).resolves.toEqual({
      revision: 'workflow-2026.08.19.3',
    });
  });

  it.each([
    [
      'single-color',
      'hardware-enclosure-single',
      'publish_model',
      'publish_color_model',
    ],
    [
      'multi-color',
      'hardware-enclosure-multi',
      'publish_color_model',
      'publish_model(',
    ],
  ] as const)(
    'assembles only the selected %s profile',
    async (workflowKind, profileId, included, excluded) => {
      const resources = await loadVerifiedWorkflowInstructions(
        promptRoot,
        workflowKind,
      );
      const instructions = assembleCadInstructions({
        runId: `run-${workflowKind}`,
        workflowKind,
        userRequest: 'Design a hardware enclosure.',
        instructions: resources,
      });

      expect(resources.profileId).toBe(profileId);
      expect(instructions).toContain(included);
      expect(instructions).toContain(
        '# @param label="Wall thickness" group=Shell unit=mm',
      );
      expect(instructions).not.toContain(
        '<amagine3d_printable_closure_design>',
      );
      expect(instructions).not.toContain(
        `<amagine3d_workflow profile="${workflowKind === 'single-color' ? 'hardware-enclosure-multi' : 'hardware-enclosure-single'}"`,
      );
      if (workflowKind === 'single-color')
        expect(instructions).not.toContain(excluded);
      if (workflowKind === 'multi-color') {
        expect(instructions).toContain(
          'dictionary keys must exactly equal the frozen region IDs',
        );
      }
    },
  );

  it('keeps advisory research in its own data partition', async () => {
    const resources = await loadVerifiedWorkflowInstructions(
      promptRoot,
      'single-color',
    );
    const instructions = assembleCadInstructions({
      runId: 'run-research',
      workflowKind: 'single-color',
      userRequest: 'Use my explicit width of 50 mm.',
      instructions: resources,
      research: {
        schemaVersion: 1,
        status: 'partial',
        advisoryOnly: true,
        queries: ['board width'],
        findings: [],
        sources: [],
        warnings: ['No authoritative dimension found.'],
      },
    });

    expect(instructions).toContain(
      '<advisory_web_research advisory_only="true">',
    );
    expect(instructions).toContain(
      'unit values must be short measurement labels',
    );
    expect(instructions).toContain('must not become a verification target');
  });

  it('carries printability and multi-body rules', async () => {
    const resources = await loadVerifiedWorkflowInstructions(
      promptRoot,
      'single-color',
    );
    const instructions = assembleCadInstructions({
      runId: 'run-printability',
      workflowKind: 'single-color',
      userRequest: 'Design a box with a lid and a hinge pin.',
      instructions: resources,
    });

    expect(resources.workflow).toContain('## Printability rules');
    expect(resources.workflow).toContain(
      'pins, gears, captive parts and moving joints',
    );
    expect(resources.workflow).toContain(
      'one or more separately printable bodies',
    );
    expect(resources.workflow).toContain(
      'exactly one connected, printable, watertight',
    );
    expect(resources.workflow).toContain(
      '`qaTargets.componentCount` is the intended number of named',
    );
    expect(resources.workflow).toContain(
      'Use a success-first complexity budget',
    );
    expect(resources.authoringGuide).toContain(
      'build123d 0.11.1 has no `Scale` class',
    );
    expect(resources.authoringGuide).toContain(
      'round_edges_checked(shape, edges, radius, label="round")',
    );
    expect(resources.authoringGuide).toContain(
      '## Pinned build123d 0.11.1 API quick reference',
    );
    expect(resources.authoringGuide).toContain(
      'align=(Align.CENTER, Align.CENTER, Align.MIN)',
    );
    expect(resources.authoringGuide).toContain(
      'GridLocations(x_spacing, y_spacing, x_count, y_count).locations',
    );
    expect(resources.authoringGuide).toContain('Do not use CadQuery syntax');
    expect(resources.authoringGuide).toContain(
      'The exact keyword is `dir`, not `direction`',
    );
    expect(resources.authoringGuide).toContain(
      'place the section plane perpendicular to the path tangent',
    );
    expect(resources.authoringGuide).toContain(
      '`BuildLine` is the sole exception',
    );
    expect(resources.authoringGuide).toContain(
      '`RegularPolygon(..., side_count=...)`',
    );
    expect(resources.authoringGuide).toContain(
      '## Deterministic repair discipline',
    );
    expect(resources.authoringGuide).toContain(
      '`repairContext.newlyFailedCheckIds`',
    );
    expect(resources.authoringGuide).toContain(
      '`len(body.solids()) == 1` before finishing',
    );
    expect(resources.authoringGuide).toContain(
      'never select every edge parallel to an',
    );
    expect(resources.authoringGuide).not.toContain(
      '## Printable closure and moving-mechanism design',
    );
    expect(resources.printableClosuresGuide).toContain(
      'intersection volume in that pose must remain at or below 0.01 mm³',
    );
    expect(resources.printableClosuresGuide).toContain(
      '## Removable pin hinge',
    );
    expect(resources.printableClosuresGuide).toContain(
      'Every knuckle must be an annular sleeve with a real through-bore',
    );
    expect(resources.printableClosuresGuide).toContain(
      '`PIN_DIAMETER + 2 * HINGE_RADIAL_CLEARANCE`',
    );
    expect(resources.printableClosuresGuide).toContain(
      '### Required hollow-knuckle construction order',
    );
    expect(resources.printableClosuresGuide).toContain(
      'base = subtract_checked(base, bore_cutter',
    );
    expect(resources.printableClosuresGuide).toContain(
      '`BODY_OVERLAP` for a pair containing the pin',
    );
    expect(resources.printableClosuresGuide).toContain(
      'complete assembled publication, including every separate body',
    );
    expect(resources.printableClosuresGuide).toContain(
      "design brief's `mechanisms` array",
    );
    expect(resources.printableClosuresGuide).toContain(
      'two mechanism definitions',
    );
    expect(resources.printableClosuresGuide).toContain(
      'sampled motion collision',
    );
    expect(resources.printableClosuresGuide).toContain(
      'hinge axis, knuckle spans, bore, pin and attachment tabs',
    );
    expect(resources.printableClosuresGuide).toContain(
      'three named printable bodies: `base`, `lid`',
    );
    expect(resources.printableClosuresGuide).toContain(
      'keep the intended assembly count at three',
    );
    expect(resources.printableClosuresGuide).toContain(
      '## Success-first closure workflow',
    );
    expect(resources.printableClosuresGuide).toContain(
      '## Design from the mechanism outward',
    );
    expect(resources.printableClosuresGuide).toContain(
      'use `features` and `derivationNotes` to record',
    );
    expect(resources.printableClosuresGuide).toContain(
      '### Default parameter recipe',
    );
    expect(resources.printableClosuresGuide).toContain(
      '`BORE_RADIUS = PIN_DIAMETER / 2 + HINGE_RADIAL_CLEARANCE`',
    );
    expect(resources.printableClosuresGuide).toContain(
      '## Use QA to converge the generated design',
    );
    expect(resources.printableClosuresGuide).toContain('| Slide-on lid');
    expect(resources.printableClosuresGuide).toContain(
      '| Replaceable flex strap',
    );
    expect(resources.printableClosuresGuide).toContain('## Slide-on lid');
    expect(resources.printableClosuresGuide).toContain(
      '## Cantilever snap lid',
    );
    expect(resources.printableClosuresGuide).toContain('## Bayonet lid');
    expect(resources.printableClosuresGuide).toContain('## Threaded lid');
    expect(resources.printableClosuresGuide).toContain('## Magnetic lid');
    expect(resources.printableClosuresGuide).toContain(
      '## Living hinge or flexible strap',
    );
    expect(resources.workflow).toContain(
      'host-supplied printable-closure guide',
    );
    expect(instructions).toContain('<amagine3d_printable_closure_design>');
    expect(instructions).toContain('## Removable pin hinge');
    expect(instructions).toContain('exactly one connected watertight solid');
    expect(instructions).toContain(
      'buildAndCheck returns deterministic diagnostics and a repairContext',
    );
    expect(instructions).toContain(
      'Every publisher dictionary value is exactly one physical print',
    );
    expect(instructions).toContain(
      'first source revision must use the simplest topology',
    );
    expect(instructions).toContain(
      'Mechanism definitions are taken from the frozen design brief',
    );
    expect(instructions).toContain(
      'Generate a closure from its functional interface outward',
    );
    expect(instructions).toContain(
      'Treat the reported worst body pair and motion pose as a local source-repair target',
    );
  });

  it('loads the closure guide from Chinese intent and modification context', async () => {
    const resources = await loadVerifiedWorkflowInstructions(
      promptRoot,
      'single-color',
    );
    const chineseRequest = assembleCadInstructions({
      runId: 'run-chinese-closure',
      workflowKind: 'single-color',
      userRequest: '设计一个带磁吸盖的收纳盒。',
      instructions: resources,
    });
    expect(chineseRequest).toContain('<amagine3d_printable_closure_design>');

    const modification = assembleCadInstructions({
      runId: 'run-existing-closure',
      workflowKind: 'single-color',
      userRequest: 'Increase the width by 5 mm.',
      instructions: resources,
      modificationContext: {
        source: 'body = Box(1, 2, 3)',
        designBrief: { features: ['removable hinge pin'] },
        parameterSchema: {},
      },
    });
    expect(modification).toContain('<amagine3d_printable_closure_design>');
  });
});
