# Contributing to Amagine3D

> **简体中文**：[查看中文版](./CONTRIBUTING.zh-CN.md)

Use Node.js 20.19 or newer, Python 3.10 through 3.13, and npm.

```bash
npm ci
npm run python:setup
npm run typecheck
npm test
npm run build
```

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
