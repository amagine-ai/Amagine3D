# Amagine3D threat model

> **简体中文**：[查看中文版](./threat-model.zh-CN.md)

Status: release baseline, 2026-08-15

## Security boundaries

Amagine3D treats model output, generated Python, Web Search results, imported
ZIP files, restored project data, attachment metadata, URLs, and filenames as
untrusted. Browser CAD execution runs in a dedicated Worker without model or
search credentials. Provider credentials stay in server-only environment
variables and are never part of project storage or export archives.

## Threats and controls

| Area                | Principal threat                                                                    | Enforced controls                                                                                                                                                                                               | Residual risk / operator action                                                                                                              |
| ------------------- | ----------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| AI-generated Python | File/network access, dynamic code escape, cross-skill helper use, denial of service | Host validation plus Python AST validation, import and builtin allowlists, finalizer/output restrictions, isolated Worker, project/run directory, build timeout, Worker termination, bounded logs and artifacts | WebAssembly is not a complete hostile-code sandbox. Do not expose the local app to untrusted remote users; report policy bypasses privately. |
| Web Research        | Prompt injection, false dimensions, SSRF, or oversized/spoofed reference images     | Opt-in research gate, no raw HTML, bounded structured summaries, HTTPS-only public image hosts, redirect revalidation, MIME/signature and byte limits, user constraints win, fail-soft image handling            | Sources can still be wrong. Review cited mechanical drawings before fabrication.                                                             |
| ZIP import          | Traversal, duplicate paths, malformed directory, oversized archive                  | Stored ZIP32 only, absolute/backslash/drive/`.`/`..` rejection, duplicate rejection, CRC/checksum verification, entry and 512 MiB archive limits, preflight before repository mutation                          | A valid large archive can use substantial memory. Import only expected project backups.                                                      |
| XSS / Markdown      | Scriptable links, HTML injection, clickjacking                                      | React text rendering, no `dangerouslySetInnerHTML`, constrained reasoning renderer, HTTP(S)-only external links, `noopener noreferrer`, CSP, frame denial, MIME sniffing disabled                               | The static CSP permits inline styles/scripts required by the current Next.js build. A future nonce rollout can tighten this further.         |
| Runtime URLs        | Remote runtime substitution or fragmented dependency requests                       | Versioned same-origin runtime bundle, pinned SHA-256, strict bundle index, unexpected runtime fetch rejection, immutable cache headers                                                                          | The first bundle is large; users should wait for the explicit ready state.                                                                   |
| Downloads           | Path-like or control-character filename abuse                                       | Artifact schemas, storage-segment validation, safe leaf-name normalization before browser download                                                                                                              | Operating systems may still rewrite filenames. Verify extension/content before opening elsewhere.                                            |
| Attachments         | MIME spoofing, oversized/decompression-heavy images, secret leakage                 | MIME plus magic-byte validation, count/byte/pixel limits, attachment hashes, capability gate, server-side revalidation                                                                                          | Images are sent to the selected provider. Do not attach confidential material unless the provider policy permits it.                         |
| Secrets             | Key exposure through UI, logs, Worker, or ZIP                                       | Server-only env access, browser requests use profile IDs, no provider SDK in browser packages, no secrets in project schemas                                                                                    | Local `.env.local` remains operator-managed and must not be committed. Rotate a key after suspected exposure.                                |

## Reporting

Follow [SECURITY.md](./SECURITY.md). Include the affected revision, Chrome
version, minimal reproduction, impact, and whether a project archive is safe to
share. Remove API keys, prompts, attachments, and proprietary CAD before
attaching evidence.
