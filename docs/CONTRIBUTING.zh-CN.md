# 参与 Amagine3D 开发

> **English**: [View the English version](./CONTRIBUTING.md)

使用 Node.js 24 或更新版本，以及 `package.json` 中固定的 pnpm 版本。

```bash
pnpm install --frozen-lockfile
pnpm smoke:deterministic
pnpm check
```

请保持每个包的公开入口只在根导出中；跨包深层导入会被 `pnpm architecture:check` 拒绝。React 不得直接操作 OPFS、Pyodide 或提供商 SDK。

每次提交一个聚焦的改动，并为新的公开行为附带测试和文档。自动化测试使用 fake 模型/搜索。真实的提供商和桌面 Chrome 路径属于手动冒烟测试，不得提交凭据或不稳定的黄金文本。普通改动运行 `pnpm licenses:check`，拒绝未知、禁止或尚未审阅的生产依赖许可证表达式。发布构建在目标平台运行 `pnpm licenses:generate`，并审阅生成的清单和声明文件。

对于浏览器相关改动，请记录 Chrome 版本、精确路由、截图或产物证据，以及已知局限。安全问题请遵循 [`SECURITY.md`](./SECURITY.zh-CN.md)，而不是通过公开 issue 上报。
