# 参与 Amagine3D 开发

> **English**: [View the English version](./CONTRIBUTING.md)

使用 Node.js 20.19 或更新版本、Python 3.10 至 3.13 和 npm。

```bash
npm ci
npm run python:setup
npm run typecheck
npm test
npm run test:python
npm run build
```

PNG 证据图使用由 trimesh、NumPy 和 Pillow 实现的无界面 CPU Z-buffer。
四个视图默认在单进程内串行渲染。运行
`npm run benchmark:renderer` 可记录小型、中型和较大 STL 的
耗时、峰值内存与三角形数量。四视图默认输出 640 像素并使用 1×，2× 超采样需显式开启。
这条基于 `pathlib` 的单进程链路在 CI 中覆盖 Ubuntu 和 Windows，macOS
使用相同的无界面代码路径。

请保持浏览器代码与本地 API 之间的边界。React 不得直接访问模型凭据、
Agent session JSONL、上传目录或 session 工作区。服务端产物路由必须把所有路径
限制在所选 Agent session 的 `workspace/sessions/<sessionId>/` 目录内。

每次提交一个聚焦的改动，并为新的公开行为附带测试和文档。自动化测试
不得调用真实模型提供商。连接真实提供商的 CAD 运行属于手动检查，不得提交
凭据、私有提示词、上传文件、生成工作区或不稳定的黄金文本。不要提交 `.env`、
`.amagine-state/`、`workspace/` 和 `.venv/`。

对于 UI 改动，请记录浏览器版本、精确路由、截图或产物证据，以及已知局限。
修改 skill 或 CAD 执行流程时，应补充覆盖发现、路径隔离和产物验证的聚焦测试。
安全问题请遵循 [`SECURITY.zh-CN.md`](./SECURITY.zh-CN.md)，而不是通过公开 issue 上报。
