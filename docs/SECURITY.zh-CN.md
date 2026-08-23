# 安全策略

> **English**: [View the English version](./SECURITY.md)

Amagine3D 处于 pre-1.0 阶段，目前支持最新版桌面 Google Chrome。安全修复在当前开发线上进行；目前不承诺对旧版本的支持窗口。

请通过私有渠道向仓库维护者上报疑似问题，包括：生成的 Python 逃逸、密钥泄露、ZIP/存储边界绕过、XSS、不安全的 URL/下载行为、附件校验绕过，或依赖完整性失败。如果仓库没有私有安全通告渠道，请在不上报利用细节的前提下向维护者索取安全联系方式。

请附上 commit、Chrome/操作系统版本、最小复现步骤、影响和安全诊断信息。移除凭据、私有提示词、附件、CAD、项目导出和提供商响应。不要访问其他用户的系统或数据。

当前信任边界和已知的残余风险记录在 [`threat-model.zh-CN.md`](./threat-model.zh-CN.md)。
