import { readFile, readdir, writeFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';

import postcss from 'postcss';

const root = resolve(import.meta.dirname, '..');
const stylesheetPath = join(
  root,
  'apps',
  'web',
  'src',
  'components',
  'cad-workbench.module.css',
);
const componentRoot = join(root, 'src', 'components', 'cad-workbench');
const componentPaths = (await readdir(componentRoot))
  .filter((name) => name.endsWith('.tsx'))
  .map((name) => join(componentRoot, name));
componentPaths.push(join(root, 'src', 'components', 'CadWorkbench.tsx'));

const classReference = /styles\.([A-Za-z_][A-Za-z0-9_]*)/gu;
const selectorClass = /\.([A-Za-z_][A-Za-z0-9_-]*)/gu;
const usedClasses = new Set();
for (const path of componentPaths) {
  const source = await readFile(path, 'utf8');
  for (const match of source.matchAll(classReference)) usedClasses.add(match[1]);
}

const source = await readFile(stylesheetPath, 'utf8');
const stylesheet = postcss.parse(source, { from: stylesheetPath });
let removedRules = 0;
stylesheet.walkRules((rule) => {
  if (rule.parent?.type === 'atrule' && /keyframes$/u.test(rule.parent.name)) {
    return;
  }
  const selectors = rule.selector.split(',').map((selector) => selector.trim());
  const retained = selectors.filter((selector) => {
    const classes = [...selector.matchAll(selectorClass)].map((match) => match[1]);
    return classes.length === 0 || classes.every((name) => usedClasses.has(name));
  });
  if (retained.length === 0) {
    removedRules += 1;
    rule.remove();
  } else {
    rule.selector = retained.join(',\n');
  }
});
stylesheet.walkAtRules((rule) => {
  if (rule.nodes && rule.nodes.length === 0) rule.remove();
});

const output = `${stylesheet.toString().trim()}\n`;
if (process.argv.includes('--write')) {
  await writeFile(stylesheetPath, output);
}
console.log(
  JSON.stringify({
    afterLines: output.split('\n').length,
    beforeLines: source.split('\n').length,
    removedRules,
    usedClasses: usedClasses.size,
    wrote: process.argv.includes('--write'),
  }),
);
