# Third-party source inventory

> **简体中文**：[查看中文版](./README.zh-CN.md)

Project-owned implementations live under `packages/`. Curated dependency
licenses are stored under `third_party/licenses/`. Release-target npm inventory
and notices are stored in `third_party/npm-production-licenses.json` and
`third_party/npm-production-notices.txt`.

`pnpm licenses:check` verifies that every repository license file matches its
public application copy and rejects unknown, prohibited, or unreviewed license
expressions in the currently installed production graph. It intentionally does
not require the generated package list to match every development platform.

Run `pnpm licenses:generate` on each release target to regenerate the exact npm
inventory and collected notice text. Review its diff before publishing.
