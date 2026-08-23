# Security policy

> **简体中文**：[查看中文版](./SECURITY.zh-CN.md)

Amagine3D is pre-1.0 and currently supports the latest desktop Google Chrome.
Security fixes are made on the current development line; no older-version
support window is promised yet.

Please report suspected generated-Python escape, secret disclosure, ZIP/storage
boundary bypass, XSS, unsafe URL/download behavior, attachment validation
bypass, or dependency integrity failure privately to the repository maintainer.
If the repository has no private advisory channel, ask the maintainer for a
secure contact without publishing exploit details.

Include the commit, Chrome/OS versions, minimal reproduction, impact, and safe
diagnostics. Remove credentials, private prompts, attachments, CAD, project
exports, and provider responses. Do not access other users' systems or data.

The current trust boundaries and known residual risks are documented in
[`threat-model.md`](./threat-model.md).
