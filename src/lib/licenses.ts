export type Language = 'en' | 'zh';

type LocalizedText = Record<Language, string>;

export interface CuratedLicense {
  files: Array<{ href: string; label: string }>;
  license: string;
  name: string;
  source: string;
  use: LocalizedText;
  version: string;
}

export const curatedLicenses: CuratedLicense[] = [
  {
    files: [{ href: '/licenses/three.txt', label: 'MIT' }],
    license: 'MIT',
    name: 'three.js',
    source: 'https://github.com/mrdoob/three.js/tree/r182',
    use: { en: 'WebGL preview and model loading', zh: 'WebGL 预览与模型加载' },
    version: '0.182.0',
  },
  {
    files: [{ href: '/licenses/pi.txt', label: 'MIT' }],
    license: 'MIT',
    name: 'PI coding agent',
    source: 'https://github.com/earendil-works/pi',
    use: {
      en: 'Agent sessions, streaming, and tool calls',
      zh: 'Agent session、流式响应与工具调用',
    },
    version: '0.84.2',
  },
  {
    files: [{ href: '/licenses/ibm-plex-sans.txt', label: 'OFL-1.1' }],
    license: 'OFL-1.1',
    name: 'IBM Plex Sans Variable',
    source: 'https://github.com/IBM/plex',
    use: { en: 'Application interface font', zh: '应用界面字体' },
    version: 'Fontsource 5.3.0 / font v23',
  },
  {
    files: [{ href: '/licenses/jetbrains-mono.txt', label: 'OFL-1.1' }],
    license: 'OFL-1.1',
    name: 'JetBrains Mono Variable',
    source: 'https://github.com/JetBrains/JetBrainsMono',
    use: { en: 'Code and technical interface font', zh: '代码与技术界面字体' },
    version: 'Fontsource 5.3.0 / font v24',
  },
  {
    files: [{ href: '/licenses/vite.txt', label: 'MIT' }],
    license: 'MIT',
    name: 'Vite',
    source: 'https://github.com/vitejs/vite/tree/v7.3.6',
    use: { en: 'Web application build and development server', zh: 'Web 应用构建与开发服务器' },
    version: '7.3.6',
  },
  {
    files: [{ href: '/licenses/react.txt', label: 'MIT' }],
    license: 'MIT',
    name: 'React',
    source: 'https://github.com/facebook/react',
    use: { en: 'Application renderer', zh: '应用界面渲染' },
    version: '19.1.1',
  },
  {
    files: [{ href: '/licenses/express.txt', label: 'MIT' }],
    license: 'MIT',
    name: 'Express',
    source: 'https://github.com/expressjs/express/tree/5.1.0',
    use: { en: 'Local API and artifact routes', zh: '本地 API 与产物路由' },
    version: '5.1.0',
  },
  {
    files: [
      { href: '/licenses/build123d.txt', label: 'Apache-2.0' },
      { href: '/licenses/build123d-notice.txt', label: 'NOTICE' },
    ],
    license: 'Apache-2.0',
    name: 'build123d',
    source: 'https://github.com/gumyr/build123d/tree/v0.11.1',
    use: { en: 'Parametric CAD API', zh: '参数化 CAD API' },
    version: '0.11.1',
  },
  {
    files: [
      { href: '/licenses/opencascade-lgpl-2.1.txt', label: 'LGPL-2.1' },
      { href: '/licenses/opencascade-exception.txt', label: 'Exception' },
    ],
    license: 'LGPL-2.1 + exception',
    name: 'Open CASCADE Technology / CadQuery OCP',
    source: 'https://github.com/CadQuery/OCP',
    use: { en: 'CAD kernel and Python bindings', zh: 'CAD 几何内核与 Python 绑定' },
    version: 'OCCT 7.9.3 / OCP 7.9.3.1.1',
  },
  {
    files: [{ href: '/licenses/trimesh.txt', label: 'MIT' }],
    license: 'MIT',
    name: 'trimesh',
    source: 'https://github.com/mikedh/trimesh/tree/5.0.0',
    use: { en: 'STL quality checks and previews', zh: 'STL 质量检查与预览' },
    version: '5.0.0',
  },
  {
    files: [{ href: '/licenses/rtree.txt', label: 'MIT' }],
    license: 'MIT',
    name: 'Rtree',
    source: 'https://github.com/Toblerity/rtree/tree/1.4.1',
    use: { en: 'Spatial index for mesh thickness queries', zh: '网格厚度查询空间索引' },
    version: '1.4.1',
  },
  {
    files: [{ href: '/licenses/lib3mf.txt', label: 'BSD-2-Clause' }],
    license: 'BSD-2-Clause',
    name: 'lib3mf',
    source: 'https://github.com/3MFConsortium/lib3mf',
    use: { en: 'Colored 3MF export and readback', zh: '彩色 3MF 导出与回读' },
    version: '2.5.0',
  },
];

export const licensePageCopy = {
  en: {
    back: 'Back to workbench',
    component: 'Component',
    dependencies: 'Production npm inventory',
    dependencyBody:
      'Generated from the current package lock and grouped by SPDX license expression.',
    downloadInventory: 'Download JSON inventory',
    eyebrow: 'LEGAL / APACHE-2.0',
    fullText: 'Read full license',
    generatedNote: 'The inventory reflects the current production dependency graph.',
    intro:
      'Amagine3D is distributed under Apache-2.0. This page keeps the project license, current runtime attributions, and production dependency inventory available inside the application.',
    license: 'License',
    notice: 'Read project notice',
    organization: 'amagine-ai on GitHub',
    owner: 'Copyright owner',
    packages: 'packages',
    project: 'Project license',
    projectBody:
      'Copyright 2026 amagine-ai. You may use, modify, and distribute Amagine3D under the terms of Apache License 2.0.',
    route: 'Licenses and notices',
    runtime: 'Runtime components',
    runtimeBody:
      'These are the principal libraries and runtime components used by this repository.',
    source: 'Source',
    text: 'License text',
    title: 'Open source info',
    use: 'Current use',
  },
  zh: {
    back: '返回设计工坊',
    component: '组件',
    dependencies: '生产 npm 依赖清单',
    dependencyBody: '清单从当前 package lock 生成，并按 SPDX 许可证表达式分组。',
    downloadInventory: '下载 JSON 清单',
    eyebrow: '法律信息 / APACHE-2.0',
    fullText: '阅读完整许可证',
    generatedNote: '该清单对应当前生产依赖图。',
    intro:
      'Amagine3D 以 Apache-2.0 协议发布。本页面集中提供项目许可证、当前运行时组件署名和生产依赖清单。',
    license: '许可证',
    notice: '阅读项目声明',
    organization: 'GitHub 上的 amagine-ai',
    owner: '版权所有者',
    packages: '个包',
    project: '项目许可证',
    projectBody:
      '版权所有 2026 amagine-ai。你可以依据 Apache License 2.0 的条款使用、修改和分发 Amagine3D。',
    route: '许可证与声明',
    runtime: '主要运行时组件',
    runtimeBody: '这里列出当前仓库实际使用的主要库与运行时组件。',
    source: '源码',
    text: '许可证文本',
    title: '开源信息',
    use: '当前用途',
  },
} satisfies Record<Language, Record<string, string>>;
