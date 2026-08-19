import { describe, expect, it } from 'vitest';

import { distanceBetween, measureSelections } from './measurement';

describe('viewer measurement', () => {
  it('uses exact selected world points', () => {
    expect(distanceBetween([1, 2, 3], [4, 6, 15])).toBe(13);
  });

  it('requires two selections', () => {
    expect(
      measureSelections([
        {
          entityId: 'one',
          kind: 'face',
          partId: 'part',
          point: [0, 0, 0],
        },
      ]),
    ).toBeUndefined();
  });

  it('rejects non-finite coordinates before they enter viewer state', () => {
    expect(() => distanceBetween([0, 0, 0], [Number.NaN, 1, 2])).toThrow(
      RangeError,
    );
  });

  it('uses point-aware IDs and copies the selected coordinates', () => {
    const from: [number, number, number] = [0, 0, 0];
    const first = measureSelections([
      { entityId: 'part::face', kind: 'face', partId: 'part', point: from },
      { entityId: 'edge', kind: 'edge', partId: 'part', point: [1, 0, 0] },
    ]);
    const second = measureSelections([
      {
        entityId: 'part::face',
        kind: 'face',
        partId: 'part',
        point: [0, 1, 0],
      },
      { entityId: 'edge', kind: 'edge', partId: 'part', point: [1, 0, 0] },
    ]);
    from[0] = 9;

    expect(first?.id).not.toBe(second?.id);
    expect(first?.from).toEqual([0, 0, 0]);
  });
});
