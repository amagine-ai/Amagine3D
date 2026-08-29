# Contributing to Amagine3D

> **简体中文**：[查看中文版](./CONTRIBUTING.zh-CN.md)

Use Node.js 20.19 or newer, Python 3.10 through 3.13, and npm.

```bash
npm ci
npm run python:setup
npm run typecheck
npm test
npm run test:python
npm run build
```

The PNG evidence renderer is a headless CPU Z-buffer implemented with trimesh,
NumPy, and Pillow. It renders five views sequentially in one process. Use
`npm run benchmark:renderer` to record small, medium, and larger
STL timings, peak memory, and triangle counts. The renderer defaults to 1x;
the five-view output is 640 pixels, and 2x supersampling is opt-in. Its
`pathlib`-based, single-process path is covered
on Ubuntu and Windows in CI and uses the same headless code on macOS.

Keep browser code behind the local API boundary. React must not access model
credentials, Agent session JSONL files, uploads, or session workspaces
directly. Server artifact routes must keep every path inside the selected Agent
session's `workspace/sessions/<sessionId>/` directory.

Submit one focused change with tests and documentation for new public behavior.
Automated tests must not call real model providers. Provider-backed CAD runs are
manual checks and must not commit credentials, private prompts, uploads,
generated workspaces, or unstable golden text. Keep `.env`, `.amagine-state/`,
`workspace/`, and `.venv/` out of commits.

For UI changes, record the browser version, exact route, screenshots or artifact
evidence, and known limitations. Changes to skills or CAD execution should
include focused tests for discovery, path isolation, and generated-artifact
validation. Security issues follow
[`SECURITY.md`](./SECURITY.md), not a public issue.
