# 第三方声明

> **English**: [View the English version](./NOTICES.md)

这份清单区分了依赖、vendored 文件和预先准备的浏览器运行时资产。它是对 `third_party/licenses/` 下完整许可证文本的补充。

| 组件                                                                                         | 版本或 commit                              | 许可                                 | 当前用途                                  | 修改状态             |
| -------------------------------------------------------------------------------------------- | ------------------------------------------ | ------------------------------------ | ----------------------------------------- | -------------------- |
| [three.js](https://github.com/mrdoob/three.js/tree/r182)                                     | `0.182.0`                                  | MIT                                  | P4 WebGL 渲染器、STL/GLB 加载器和轨道控制 | 未修改的依赖         |
| [Vercel AI SDK](https://github.com/vercel/ai)                                                | `7.0.62`                                   | Apache-2.0                           | 智能体、流式、工具调用和 UI 消息契约      | 未修改的依赖         |
| [Next.js](https://github.com/vercel/next.js/tree/v16.3.0)                                    | `16.3.0`                                   | MIT                                  | App Router Web 应用和服务器路由           | 未修改的依赖         |
| [React](https://github.com/facebook/react)                                                   | `19.2.8`                                   | MIT                                  | 应用渲染器                                | 未修改的依赖         |
| [Zod](https://github.com/colinhacks/zod)                                                     | `4.4.3`                                    | MIT                                  | 运行时协议校验                            | 未修改的依赖         |
| [OCP.wasm](https://github.com/yeicor/OCP.wasm/tree/19c9c39e1591e2e239ceaf9201407f1b6d8f760b) | `19c9c39e1591e2e239ceaf9201407f1b6d8f760b` | MIT                                  | P3 Worker 引导和浏览器 CAD 内核           | 未修改，固定地址拉取 |
| [build123d](https://github.com/gumyr/build123d/tree/v0.11.1)                                 | `0.11.1`                                   | Apache-2.0                           | P3 浏览器端参数化 CAD                     | 未修改的运行时包     |
| [Open CASCADE Technology](https://github.com/Open-Cascade-SAS/OCCT/tree/V7_9_3)              | OCP.wasm `7.9.3.1.post202607021200`        | LGPL-2.1，附带 Open CASCADE 例外条款 | 传递性的 P3 CAD 内核                      | 未修改的运行时组件   |
| [Pyodide](https://github.com/pyodide/pyodide)                                                | `314.0.3` 包构建                           | MPL-2.0                              | P3 Worker Python/WASM 运行时              | 未修改的 npm 依赖    |
| [trimesh](https://github.com/mikedh/trimesh/tree/5.0.0)                                      | `5.0.0`                                    | MIT                                  | P3 确定性 STL QA                          | 未修改的运行时包     |
| [lib3mf](https://github.com/3MFConsortium/lib3mf/tree/v2.5.0)                                | `2.5.0.post202607021107`                   | BSD-2-Clause                         | P3 彩色 3MF 导出和回读                    | 未修改的运行时包     |
| [sharp/libvips 平台包](https://github.com/libvips/libvips)                                   | `@img/sharp-libvips-darwin-arm64@1.3.2`    | LGPL-3.0-or-later                    | Next.js/sharp 生产依赖图中的平台包        | 未修改的 npm 依赖    |

针对发布目标生成的生产 npm 依赖清单与汇总声明分别位于 `third_party/npm-production-licenses.json` 和 `third_party/npm-production-notices.txt`，应用内可访问的副本同步在 `apps/web/public/licenses`。`pnpm licenses:check` 会拒绝未知、禁止或尚未审阅的许可证表达式，并校验仓库副本与应用副本一致；它不比较不同开发平台上的精确包列表。发布构建应在实际分发目标运行 `pnpm licenses:generate` 并审阅结果。

生产清单包含以 LGPL-2.1 加 Open CASCADE 例外条款许可的 OCCT、以 MPL-2.0 许可的 Pyodide，以及以 BSD-2-Clause 许可的 lib3mf。源码仓库不会 vendored 或发布 sharp/libvips 原生平台包；包含已安装生产依赖的发行方必须审查其精确二进制分发的相关义务。
