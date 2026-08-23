import { readFile, readdir, writeFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';

import postcss from 'postcss';

const root = resolve(import.meta.dirname, '..');
const stylesheetRoot = join(
  root,
  'apps',
  'web',
  'src',
  'components',
);
const stylesheetNames = [
  'cad-workbench.shell.css',
  'cad-workbench.conversation.css',
  'cad-workbench.artifacts.css',
  'cad-workbench.composer.css',
  'cad-workbench.preview.css',
  'cad-workbench.parameters.css',
  'cad-workbench.interactions.css',
  'cad-workbench.controls.css',
  'cad-workbench.soft-theme.css',
  'cad-workbench.files.css',
  'cad-workbench.corrections.css',
  'cad-workbench.restraint.css',
];
const stylesheetPaths = stylesheetNames.map((name) =>
  join(stylesheetRoot, name),
);
const modulePath = join(stylesheetRoot, 'cad-workbench.module.css');
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

const expectedImports = [
  '/* Import order is the workbench cascade; Vite scopes the inlined bundle as one CSS Module. */',
  ...stylesheetNames.map((name) => `@import './${name}';`),
].join('\n');
const moduleSource = await readFile(modulePath, 'utf8');
if (moduleSource.trim() !== expectedImports) {
  throw new Error('Workbench stylesheet imports do not match prune order.');
}
const sources = await Promise.all(
  stylesheetPaths.map((path) => readFile(path, 'utf8')),
);
const stylesheets = sources.map((source, index) =>
  postcss.parse(source, { from: stylesheetPaths[index] }),
);
let removedRules = 0;
let removedDeclarations = 0;
for (const stylesheet of stylesheets) {
  stylesheet.walkRules((rule) => {
    if (rule.parent?.type === 'atrule' && /keyframes$/u.test(rule.parent.name)) {
      return;
    }
    const selectors = rule.selector.split(',').map((selector) => selector.trim());
    const retained = selectors.filter((selector) => {
      const classes = [...selector.matchAll(selectorClass)].map(
        (match) => match[1],
      );
      return (
        classes.length === 0 || classes.every((name) => usedClasses.has(name))
      );
    });
    if (retained.length === 0) {
      removedRules += 1;
      rule.remove();
    } else {
      rule.selector = retained.join(',\n');
    }
  });
}

function atRuleContext(rule) {
  const context = [];
  let parent = rule.parent;
  while (parent && parent.type !== 'root') {
    if (parent.type === 'atrule') {
      context.unshift(`@${parent.name} ${parent.params}`.trim());
    }
    parent = parent.parent;
  }
  return context.join('\u0000');
}

function isKeyframeRule(rule) {
  let parent = rule.parent;
  while (parent && parent.type !== 'root') {
    if (parent.type === 'atrule' && /keyframes$/u.test(parent.name)) return true;
    parent = parent.parent;
  }
  return false;
}

const laterDeclarations = new Map();
const rules = [];
for (const stylesheet of stylesheets) {
  stylesheet.walkRules((rule) => {
    if (!isKeyframeRule(rule)) rules.push(rule);
  });
}
for (const rule of rules.reverse()) {
  const selectorKeys = rule.selectors.map(
    (selector) => `${atRuleContext(rule)}\u0000${selector.trim()}`,
  );
  const declarations = (rule.nodes ?? []).filter(
    (node) => node.type === 'decl',
  );
  for (const declaration of declarations.reverse()) {
    const property = declaration.prop.startsWith('--')
      ? declaration.prop
      : declaration.prop.toLowerCase();
    const isShadowed = selectorKeys.every((key) => {
      const importance = laterDeclarations.get(`${key}\u0000${property}`) ?? 0;
      return declaration.important ? (importance & 2) !== 0 : importance !== 0;
    });
    if (isShadowed) {
      removedDeclarations += 1;
      declaration.remove();
    }
    for (const key of selectorKeys) {
      const declarationKey = `${key}\u0000${property}`;
      const importance = laterDeclarations.get(declarationKey) ?? 0;
      laterDeclarations.set(
        declarationKey,
        importance | (declaration.important ? 2 : 1),
      );
    }
  }
  if (!rule.nodes?.some((node) => node.type === 'decl')) {
    removedRules += 1;
    rule.remove();
  }
}
for (const stylesheet of stylesheets) {
  stylesheet.walkAtRules((rule) => {
    if (rule.nodes && rule.nodes.length === 0) rule.remove();
  });
}

const outputs = stylesheets.map(
  (stylesheet) => `${stylesheet.toString().trim()}\n`,
);
if (process.argv.includes('--write')) {
  await Promise.all(
    stylesheetPaths.map((path, index) => writeFile(path, outputs[index])),
  );
}
console.log(
  JSON.stringify({
    afterLines: outputs.reduce(
      (total, output) => total + output.split('\n').length - 1,
      0,
    ),
    beforeLines: sources.reduce(
      (total, source) => total + source.split('\n').length - 1,
      0,
    ),
    files: stylesheetNames.length,
    removedDeclarations,
    removedRules,
    usedClasses: usedClasses.size,
    wrote: process.argv.includes('--write'),
  }),
);
