import { CadDomainError } from '@amagine3d/cad-protocol';
import { describe, expect, it } from 'vitest';

import {
  applyProportionalAdjustment,
  changeParameter,
  couplingForParameter,
  createSourceDiff,
  discoverParameterCouplings,
  discoverParameterSet,
  ensureParameterCouplings,
  parameterOverrides,
  redoParameterChange,
  resetParameters,
  undoParameterChange,
  writeParametersToSource,
} from './parameters';

const SOURCE_HASH = 'a'.repeat(64);
const SOURCE = `# @param label="Body width" group=Dimensions unit=mm min=40 max=120 step=0.5
BODY_W = 80.0
VENT_COUNT: int = 6  # @param group=Features min=0 max=12 step=1
HAS_LID = True # @param group=Features
FINISH = "matte" # @param group=Appearance
derived = BODY_W / 2
  INDENTED = 4
`;

describe('CAD parameter overrides', () => {
  it('discovers only top-level uppercase literals with constraint metadata', () => {
    const parameters = discoverParameterSet(SOURCE, SOURCE_HASH);
    expect(parameters.parameters).toEqual([
      expect.objectContaining({
        name: 'BODY_W',
        label: 'Body width',
        group: 'Dimensions',
        defaultValue: 80,
        value: 80,
        unit: 'mm',
        minimum: 40,
        maximum: 120,
        step: 0.5,
      }),
      expect.objectContaining({
        name: 'VENT_COUNT',
        group: 'Features',
        defaultValue: 6,
      }),
      expect.objectContaining({
        name: 'HAS_LID',
        type: 'boolean',
        defaultValue: true,
      }),
      expect.objectContaining({
        name: 'FINISH',
        type: 'string',
        defaultValue: 'matte',
      }),
    ]);
  });

  it('tracks undo, redo, reset, and truncates a stale redo branch', () => {
    const initial = discoverParameterSet(SOURCE, SOURCE_HASH);
    const changed = changeParameter(
      initial,
      'BODY_W',
      92,
      '2026-08-14T00:00:00.000Z',
    );
    expect(parameterOverrides(changed)).toEqual({ BODY_W: 92 });
    const undone = undoParameterChange(changed);
    expect(parameterOverrides(undone)).toEqual({});
    expect(parameterOverrides(redoParameterChange(undone))).toEqual({
      BODY_W: 92,
    });
    const branched = changeParameter(undone, 'BODY_W', 90);
    expect(branched.history).toHaveLength(1);
    expect(redoParameterChange(branched)).toBe(branched);
    expect(parameterOverrides(resetParameters(branched))).toEqual({});
  });

  it('validates type and numeric constraints', () => {
    const initial = discoverParameterSet(SOURCE, SOURCE_HASH);
    expect(() => changeParameter(initial, 'BODY_W', 20)).toThrow(
      'BODY_W must be at least 40.',
    );
    expect(() => changeParameter(initial, 'HAS_LID', 'yes')).toThrow(
      'HAS_LID must be boolean.',
    );
  });

  it('writes confirmed values into a new source without changing other lines', () => {
    const changed = changeParameter(
      changeParameter(
        discoverParameterSet(SOURCE, SOURCE_HASH),
        'BODY_W',
        92.5,
      ),
      'FINISH',
      'satin',
    );
    const written = writeParametersToSource(SOURCE, SOURCE_HASH, changed);
    expect(written).toContain('BODY_W = 92.5');
    expect(written).toContain('FINISH = "satin"');
    expect(written).toContain('derived = BODY_W / 2');
    expect(createSourceDiff(SOURCE, written)).toEqual(
      expect.arrayContaining([
        { kind: 'removed', text: 'BODY_W = 80.0' },
        { kind: 'added', text: 'BODY_W = 92.5' },
      ]),
    );
  });

  it('rejects writing overrides against another source revision', () => {
    const parameters = discoverParameterSet(SOURCE, SOURCE_HASH);
    expect(() =>
      writeParametersToSource(SOURCE, 'b'.repeat(64), parameters),
    ).toThrow(CadDomainError);
  });
});

const ROUNDED_SOURCE = `# @param label="Width" unit=mm min=10 max=200
CASE_W = 100.0
# @param label="Depth" unit=mm min=10 max=200
CASE_D = 50.0
# @param label="Radius" unit=mm min=1 max=30
OUTER_R = 12.0
body = RectangleRounded(CASE_W, CASE_D, OUTER_R)
`;

describe('parameter coupling (hard-invariant triples)', () => {
  it('discovers the RectangleRounded coupling when all args are literals', () => {
    const couplings = discoverParameterCouplings(ROUNDED_SOURCE);
    expect(couplings).toEqual([
      {
        id: 'RectangleRounded(CASE_W,CASE_D,OUTER_R)',
        members: ['CASE_W', 'CASE_D', 'OUTER_R'],
        source:
          'RectangleRounded requires width > 2*radius AND height > 2*radius',
      },
    ]);
    const set = discoverParameterSet(ROUNDED_SOURCE, SOURCE_HASH);
    expect(set.couplings).toHaveLength(1);
    expect(couplingForParameter(set, 'CASE_W')?.members).toEqual([
      'CASE_W',
      'CASE_D',
      'OUTER_R',
    ]);
  });

  it('skips calls whose arguments are not top-level literals', () => {
    const source = `W = 100.0
body = RectangleRounded(W, W - 4, 12.0)
`;
    expect(discoverParameterCouplings(source)).toEqual([]);
  });

  it('proportionally scales the other members when one changes', () => {
    const set = discoverParameterSet(ROUNDED_SOURCE, SOURCE_HASH);
    const coupling = couplingForParameter(set, 'OUTER_R');
    if (coupling === undefined) throw new Error('Expected OUTER_R coupling.');
    const result = applyProportionalAdjustment(set, coupling, 'OUTER_R', 24.0);
    // scale = 24 / 12 = 2 => both width and height double
    const values = Object.fromEntries(
      result.parameterSet.parameters.map((p) => [p.name, p.value]),
    );
    expect(values).toMatchObject({
      CASE_W: 200,
      CASE_D: 100,
      OUTER_R: 24,
    });
    expect(result.scaledNames.sort()).toEqual(['CASE_D', 'CASE_W']);
    expect(parameterOverrides(result.parameterSet)).toEqual({
      CASE_D: 100,
      CASE_W: 200,
      OUTER_R: 24,
    });
  });

  it('clamps scaled members to their declared bounds', () => {
    const set = discoverParameterSet(ROUNDED_SOURCE, SOURCE_HASH);
    const coupling = couplingForParameter(set, 'CASE_W');
    if (coupling === undefined) throw new Error('Expected CASE_W coupling.');
    // scale = 200 / 100 = 2 => OUTER_R -> 24 (max 30), CASE_D -> 100 (max 200)
    const result = applyProportionalAdjustment(set, coupling, 'CASE_W', 200.0);
    const values = Object.fromEntries(
      result.parameterSet.parameters.map((p) => [p.name, p.value]),
    );
    expect(values).toMatchObject({
      CASE_W: 200,
      CASE_D: 100,
      OUTER_R: 24,
    });
  });

  it('backfills couplings onto a legacy parameter set from the source', () => {
    // Simulate a parameter set persisted before couplings existed: strip them.
    const discovered = discoverParameterSet(ROUNDED_SOURCE, SOURCE_HASH);
    const legacy = {
      ...discovered,
      couplings: undefined,
    };
    const backfilled = ensureParameterCouplings(legacy, ROUNDED_SOURCE);
    expect(backfilled.couplings).toHaveLength(1);
    expect(backfilled.couplings?.[0]?.members).toEqual([
      'CASE_W',
      'CASE_D',
      'OUTER_R',
    ]);
    // A parameter set that already has couplings is returned untouched.
    expect(ensureParameterCouplings(discovered, ROUNDED_SOURCE)).toBe(
      discovered,
    );
  });
});
