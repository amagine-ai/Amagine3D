import type { Language } from '../lib/i18n';

type LocalizedText = Record<Language, string>;

export type CuratedLicense = {
  name: string;
  version: string;
  license: string;
  source: string;
  use: LocalizedText;
  files: Array<{ label: string; href: string }>;
};

export const curatedLicenses: CuratedLicense[] = [
  {
    name: 'three.js',
    version: '0.182.0',
    license: 'MIT',
    source: 'https://github.com/mrdoob/three.js/tree/r182',
    use: { en: 'WebGL renderer and model loaders', zh: 'WebGL 渲染与模型加载' },
    files: [{ label: 'MIT', href: '/licenses/three.txt' }],
  },
  {
    name: 'Vercel AI SDK',
    version: '7.0.62',
    license: 'Apache-2.0',
    source: 'https://github.com/vercel/ai',
    use: {
      en: 'Agent loop, streaming and tool calling',
      zh: '智能体循环、流式响应与工具调用',
    },
    files: [{ label: 'Apache-2.0', href: '/licenses/vercel-ai-sdk.txt' }],
  },
  {
    name: 'Next.js',
    version: '16.3.0',
    license: 'MIT',
    source: 'https://github.com/vercel/next.js/tree/v16.3.0',
    use: {
      en: 'Web application and server routes',
      zh: 'Web 应用与服务端路由',
    },
    files: [{ label: 'MIT', href: '/licenses/nextjs.txt' }],
  },
  {
    name: 'React',
    version: '19.2.8',
    license: 'MIT',
    source: 'https://github.com/facebook/react',
    use: { en: 'Application renderer', zh: '应用界面渲染' },
    files: [{ label: 'MIT', href: '/licenses/react.txt' }],
  },
  {
    name: 'Zod',
    version: '4.4.3',
    license: 'MIT',
    source: 'https://github.com/colinhacks/zod',
    use: { en: 'Runtime protocol validation', zh: '运行时协议校验' },
    files: [{ label: 'MIT', href: '/licenses/zod.txt' }],
  },
  {
    name: 'OCP.wasm',
    version: '19c9c39',
    license: 'MIT',
    source:
      'https://github.com/yeicor/OCP.wasm/tree/19c9c39e1591e2e239ceaf9201407f1b6d8f760b',
    use: { en: 'Browser CAD kernel bootstrap', zh: '浏览器 CAD 内核引导层' },
    files: [{ label: 'MIT', href: '/licenses/ocp-wasm.txt' }],
  },
  {
    name: 'build123d',
    version: '0.11.1',
    license: 'Apache-2.0',
    source: 'https://github.com/gumyr/build123d/tree/v0.11.1',
    use: { en: 'Parametric CAD API', zh: '参数化 CAD API' },
    files: [{ label: 'Apache-2.0', href: '/licenses/build123d.txt' }],
  },
  {
    name: 'Open CASCADE Technology',
    version: '7.9.3',
    license: 'LGPL-2.1 + exception',
    source: 'https://github.com/Open-Cascade-SAS/OCCT/tree/V7_9_3',
    use: {
      en: 'Transitive geometric modeling kernel',
      zh: '传递引入的几何建模内核',
    },
    files: [
      { label: 'LGPL-2.1', href: '/licenses/opencascade-lgpl-2.1.txt' },
      { label: 'Exception', href: '/licenses/opencascade-exception.txt' },
    ],
  },
  {
    name: 'Pyodide',
    version: '314.0.3 package build',
    license: 'MPL-2.0',
    source: 'https://github.com/pyodide/pyodide',
    use: { en: 'Python/WASM worker runtime', zh: 'Python/WASM Worker 运行时' },
    files: [{ label: 'MPL-2.0', href: '/licenses/pyodide.txt' }],
  },
  {
    name: 'trimesh',
    version: '5.0.0',
    license: 'MIT',
    source: 'https://github.com/mikedh/trimesh/tree/5.0.0',
    use: { en: 'Deterministic STL quality checks', zh: '确定性 STL 质量检查' },
    files: [{ label: 'MIT', href: '/licenses/trimesh.txt' }],
  },
  {
    name: 'lib3mf',
    version: '2.5.0',
    license: 'BSD-2-Clause',
    source: 'https://github.com/3MFConsortium/lib3mf',
    use: { en: 'Colored 3MF export and readback', zh: '彩色 3MF 导出与回读' },
    files: [{ label: 'BSD-2-Clause', href: '/licenses/lib3mf.txt' }],
  },
  {
    name: 'sharp / libvips',
    version: 'release platform package',
    license: 'LGPL-3.0-or-later',
    source: 'https://github.com/libvips/libvips',
    use: {
      en: 'Transitive Next.js image processing',
      zh: 'Next.js 传递引入的图像处理组件',
    },
    files: [
      { label: 'Inventory', href: '/licenses/npm-production-notices.txt' },
    ],
  },
];

export const licensePageCopy = {
  en: {
    route: 'Licenses and notices',
    eyebrow: 'LEGAL / APACHE-2.0',
    title: 'Open source info',
    intro:
      'Amagine3D is distributed under Apache-2.0. This page keeps the project license, curated runtime attributions, and the release dependency inventory available inside the application.',
    back: 'Back to workbench',
    project: 'Project license',
    projectBody:
      'Copyright 2026 amagine-ai. You may use, modify, and distribute Amagine3D under the terms of Apache License 2.0.',
    owner: 'Copyright owner',
    license: 'License',
    organization: 'amagine-ai on GitHub',
    fullText: 'Read full license',
    notice: 'Read project notice',
    runtime: 'Runtime components',
    runtimeBody:
      'These are the principal libraries and browser-runtime components used by the application. The source and exact license text remain one click away.',
    component: 'Component',
    use: 'Current use',
    source: 'Source',
    text: 'License text',
    dependencies: 'Production npm inventory',
    dependencyBody:
      'Generated for the installed release target. The list is grouped by SPDX license expression so additions are easy to review.',
    packages: 'packages',
    downloadInventory: 'Download JSON inventory',
    downloadNotices: 'Download collected notices',
    generatedNote:
      'Release artifacts regenerate this snapshot on their actual target platform.',
  },
  zh: {
    route: '许可证与声明',
    eyebrow: '法律信息 / APACHE-2.0',
    title: '开源信息',
    intro:
      'Amagine3D 以 Apache-2.0 协议发布。本页面在应用内集中提供项目许可证、主要运行时组件署名，以及发布依赖清单。',
    back: '返回设计工坊',
    project: '项目许可证',
    projectBody:
      '版权所有 2026 amagine-ai。你可以依据 Apache License 2.0 的条款使用、修改和分发 Amagine3D。',
    owner: '版权所有者',
    license: '许可证',
    organization: 'GitHub 上的 amagine-ai',
    fullText: '阅读完整许可证',
    notice: '阅读项目声明',
    runtime: '主要运行时组件',
    runtimeBody:
      '这里列出应用使用的主要库与浏览器运行时组件，并直接提供源码与完整许可证文本。',
    component: '组件',
    use: '当前用途',
    source: '源码',
    text: '许可证文本',
    dependencies: '生产 npm 依赖清单',
    dependencyBody:
      '清单针对当前安装的发布目标生成，并按 SPDX 许可证表达式分组，便于审阅新增依赖。',
    packages: '个包',
    downloadInventory: '下载 JSON 清单',
    downloadNotices: '下载汇总声明',
    generatedNote: '发布产物会在实际目标平台重新生成这份快照。',
  },
} satisfies Record<Language, Record<string, string>>;
