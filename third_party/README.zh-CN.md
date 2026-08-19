# 第三方源码清单

> **English**: [View the English version](./README.md)

项目自有实现位于 `packages/`。经审阅的依赖许可证存放在
`third_party/licenses/`。发布目标的 npm 清单和声明文件分别位于
`third_party/npm-production-licenses.json` 与
`third_party/npm-production-notices.txt`。

`pnpm licenses:check` 会检查仓库许可证文件与应用公开副本一致，并拒绝当前
生产依赖图中的未知、禁止或尚未审阅的许可证表达式。它不会要求生成的精确
包清单在所有开发平台上完全一致。

每个发布目标应运行 `pnpm licenses:generate`，重新生成精确的 npm 清单和汇总
声明文本，并在发布前审阅差异。
