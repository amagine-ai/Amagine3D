# Third-party notices

> **简体中文**：[查看中文版](./NOTICES.zh-CN.md)

This inventory distinguishes dependencies, vendored files and prepared browser
runtime assets. It supplements the full license texts under
`third_party/licenses/`.

| Component                                                                                    | Version or commit                          | License                              | Current use                                                       | Modification status          |
| -------------------------------------------------------------------------------------------- | ------------------------------------------ | ------------------------------------ | ----------------------------------------------------------------- | ---------------------------- |
| [three.js](https://github.com/mrdoob/three.js/tree/r182)                                     | `0.182.0`                                  | MIT                                  | P4 WebGL renderer, STL/GLB loaders and orbit controls             | Unmodified dependency        |
| [Vercel AI SDK](https://github.com/vercel/ai)                                                | `7.0.62`                                   | Apache-2.0                           | Agent, streaming, tool calling and UI message contracts           | Unmodified dependency        |
| [Next.js](https://github.com/vercel/next.js/tree/v16.3.0)                                    | `16.3.0`                                   | MIT                                  | App Router web application and server routes                      | Unmodified dependency        |
| [React](https://github.com/facebook/react)                                                   | `19.2.8`                                   | MIT                                  | Application renderer                                              | Unmodified dependency        |
| [Zod](https://github.com/colinhacks/zod)                                                     | `4.4.3`                                    | MIT                                  | Runtime protocol validation                                       | Unmodified dependency        |
| [OCP.wasm](https://github.com/yeicor/OCP.wasm/tree/19c9c39e1591e2e239ceaf9201407f1b6d8f760b) | `19c9c39e1591e2e239ceaf9201407f1b6d8f760b` | MIT                                  | P3 Worker bootstrap and browser CAD kernel                        | Unmodified, pinned fetch     |
| [build123d](https://github.com/gumyr/build123d/tree/v0.11.1)                                 | `0.11.1`                                   | Apache-2.0                           | P3 browser-side parametric CAD                                    | Unmodified runtime package   |
| [Open CASCADE Technology](https://github.com/Open-Cascade-SAS/OCCT/tree/V7_9_3)              | OCP.wasm `7.9.3.1.post202607021200`        | LGPL-2.1 with Open CASCADE exception | Transitive P3 CAD kernel                                          | Unmodified runtime component |
| [Pyodide](https://github.com/pyodide/pyodide)                                                | `314.0.3` package build                    | MPL-2.0                              | P3 Worker Python/WASM runtime                                     | Unmodified npm dependency    |
| [trimesh](https://github.com/mikedh/trimesh/tree/5.0.0)                                      | `5.0.0`                                    | MIT                                  | P3 deterministic STL QA                                           | Unmodified runtime package   |
| [lib3mf](https://github.com/3MFConsortium/lib3mf/tree/v2.5.0)                                | `2.5.0.post202607021107`                   | BSD-2-Clause                         | P3 colored 3MF export and readback                                | Unmodified runtime package   |
| [sharp/libvips platform package](https://github.com/libvips/libvips)                         | `@img/sharp-libvips-darwin-arm64@1.3.2`    | LGPL-3.0-or-later                    | Platform package in the Next.js/sharp production dependency graph | Unmodified npm dependency    |

The release-target production npm dependency inventory and collected notices
are checked in as `third_party/npm-production-licenses.json` and
`third_party/npm-production-notices.txt`. Application-accessible copies are
mirrored under `apps/web/public/licenses`. `pnpm licenses:check` rejects unknown,
prohibited, or unreviewed license expressions and verifies the repository and
application copies remain synchronized. It does not compare the exact package
list across development platforms. Release builds run `pnpm licenses:generate`
on the actual distribution target and review the result.

The production inventory includes OCCT under LGPL-2.1 with the Open CASCADE
exception, Pyodide under MPL-2.0, and lib3mf under BSD-2-Clause. The source repository does not
vendor or publish the sharp/libvips native platform package; distributors that
include installed production dependencies must review the obligations for their
exact binary distribution.
