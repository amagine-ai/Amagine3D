# @amagine3d/cad-execution-browser

> **English**: [View the English version](./README.md)

Amagine3D 的框架无关浏览器 CAD 执行模块。公开入口提供一个可恢复的 `BrowserCadExecutor`；实现仅在专用 Web Worker 中运行，并遵循以下路径：

```text
BrowserCadExecutor -> versioned Worker protocol -> Pyodide -> OCP.wasm
                   -> build123d -> Amagine3D host operations -> mesh audit
```

执行器惰性引导。超时、中止、无效响应或 Worker 崩溃都会终止该 Worker；下一次请求会创建并引导一个新的 Worker。进度和受限日志以事件形式提供。二进制产物以带 SHA-256 元数据的可转移 `ArrayBuffer` 值返回。

## 运行时契约

- 运行时版本和 SHA-256 值存放在 `runtime-assets.json` 中，并通过 `runtime-manifest.ts` 暴露。
- 准备阶段将精确的 Pyodide 核心/包闭包、固定的项目 wheel 和 Amagine3D 工作流运行时资产拼接成一个确定性的同源 bundle。Worker 在从内存加载 Pyodide 之前会校验其固定的 SHA-256 和严格索引；意外的碎片化运行时请求会被拒绝。
- 精确的 build123d、OCP.wasm、lib3mf 和 trimesh wheel 文件名及 SHA-256 值都被固定。准备阶段校验每个下载的资产，引导阶段在受信安装程序运行前校验完整 bundle。
- Worker 将专用的 `amagine3d-cad-worker` OPFS 目录挂载到 `/opfs`，与主线程写入的项目事务隔离。构建使用 `/<project-id>/.cad-worker/<run-id>`，并在可转移产物组装完成后移除该临时目录。
- 每个 Worker 只运行一次构建。主线程取消和超时使用 Worker 终止，因为繁忙的 Python 解释器无法可靠地消费第二条取消消息。

## 源码边界

源码在主机端检查一次，在 Worker 中用 Python `ast` 再检查一次。运行时允许公开的 build123d 导入、`math`，以及冻结工作流 profile 的五个公开 helper 操作。它拒绝直接文件访问、动态求值、dunder 穿越、直接导出器、网络/进程模块、跨 profile helper，以及 `cad_out` 之外的输出目录。

被执行的命名空间接收受限的内建对象和受限的 `__import__`。受信的 build123d 和 helper 模块在该命名空间之外引导。Worker 不包含模型密钥、搜索服务、cookie 或提供商 SDK。

## QA 与持久化

单色运行执行形状有效性、漏切、水密性、绕线方向、正体积、组件数量、尺寸以及可选的体积检查。多色运行额外执行精确的区域计划匹配、逐区域网格 QA、两两重叠，以及实际调用 `amagine_three_mf.inspect_3mf` 回读。尽力而为的彩色 STEP 是警告；缺失或不可读的 3MF 和区域重叠是失败。

当冻结的设计简报声明刚性机构时，两种配置都会重新读取每个已发布实体或颜色区域的 STEP，要求运动件与静止件 ID 精确覆盖全部发布内容，并对每段旋转、平移或螺旋运动采样。几何结果未知、扫掠碰撞超限或声明的运行间隙不足都会使 QA 失败；失败报告同时包含最严重的碰撞零件对、运动段、采样位置，以及最小/最大间隙出现的姿态，供 Agent 定位并修正具体接口。

冻结的 feature check 会读取匹配 `observe_feature` 记录的边界、中心和体积。标记为 `keep-out` 的观测体会与每个最终发布实体或颜色区域求交；缺失测量、未知相交结果或超过 0.01 mm³ 的 keep-out 重叠都会使 QA 失败。

`publish_color_model` 字典必须使用每个冻结颜色区域的 `id` 作为其键。人类可读的区域 `name` 值只是供 UI 显示的标签，可能包含空格；它们不属于 Python 导出契约。

storage 包的 `persistSuccessfulExecution` 消费共享的 `CadExecutionResult`，拒绝失败的 QA，通过 P2 `ProjectRepository` 保存需要持久化的产物，将 run 标记为不可变，并推进项目的当前 run 指针。每个通过 QA 的 STEP 都作为项目主产物持久化，因此项目恢复与 ZIP 导出会保留当时通过验证的精确 B-rep。将该适配器保留在 storage 中可以维持包的依赖边界。

本包不导入 React；Web 工作台通过 `BrowserCadExecutor` 边界访问它。
