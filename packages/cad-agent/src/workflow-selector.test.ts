import { describe, expect, it } from 'vitest';

import { selectCadWorkflow } from './workflow-selector';

describe('CadWorkflowSelector', () => {
  it.each([
    ['ordinary enclosure', 'Design a printable enclosure for a Pico W.'],
    ['explicit single color', 'Design a black enclosure.'],
  ])('defaults %s requests to single-color', (_label, userRequest) => {
    expect(selectCadWorkflow({ preference: 'auto', userRequest }).kind).toBe(
      'single-color',
    );
  });

  it.each([
    'Make a two-tone enclosure with a white logo.',
    '做一个支持 AMS 的双色传感器外壳',
    'Export a colored 3MF with separate color regions.',
  ])('routes explicit color semantics to multi-color', (userRequest) => {
    expect(selectCadWorkflow({ preference: 'auto', userRequest }).kind).toBe(
      'multi-color',
    );
  });

  it('gives a pre-briefing user override priority over automatic routing', () => {
    expect(
      selectCadWorkflow({
        preference: 'single-color',
        userRequest: 'Make a two-tone AMS enclosure.',
      }),
    ).toMatchObject({ kind: 'single-color', mode: 'user-override' });
  });
});
