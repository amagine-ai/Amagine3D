import { strict as assert } from 'node:assert';
import { test } from 'node:test';

import {
  requiredWebSearchInstruction,
  webSearchRepairInstruction,
} from '../server/web-search.ts';

test('web reference instruction is isolated to enabled turns', () => {
  assert.equal(requiredWebSearchInstruction(false), '');
  const instruction = requiredWebSearchInstruction(true);
  assert.match(instruction, /must successfully call web_search/u);
  assert.match(instruction, /Official sources.*not required/u);
  assert.match(instruction, /Images are best-effort/u);
  assert.match(instruction, /does not replace, shorten, reorder, or waive/u);
});

test('web reference repair preserves the original Skill workflow', () => {
  const instruction = webSearchRepairInstruction(1, 2);
  assert.match(instruction, /attempt="1"/u);
  assert.match(instruction, /matching project Skill in full/u);
});
