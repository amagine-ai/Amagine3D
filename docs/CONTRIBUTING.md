# Contributing to Amagine3D

> **简体中文**：[查看中文版](./CONTRIBUTING.zh-CN.md)

Use Node.js 24 or newer and the pnpm version pinned in `package.json`.

```bash
pnpm install --frozen-lockfile
pnpm smoke:deterministic
pnpm check
```

Keep each package's public surface at its root entry point; cross-package deep
imports are rejected by `pnpm architecture:check`. React must not operate OPFS, Pyodide,
or provider SDKs directly.

Submit one focused change with tests and documentation for new public behavior.
Use fake models/search for automated tests. Real providers and desktop Chrome
paths are manual smoke tests and must not commit credentials or unstable golden
text. Normal changes run `pnpm licenses:check`, which rejects unknown,
prohibited, or not-yet-reviewed production license expressions. Release builds
run `pnpm licenses:generate` on their target platform and review the generated
inventory and notices.

For browser changes, record the Chrome version, exact route, screenshots or
artifact evidence, and known limitations. Security issues follow
[`SECURITY.md`](./SECURITY.md), not a public issue.
