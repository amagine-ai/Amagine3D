# Amagine3D threat model

> **简体中文**：[查看中文版](./threat-model.zh-CN.md)

Status: Codex runtime baseline, 2026-09-06

## Security boundaries

Amagine3D treats model output, generated commands and Python, Web Search
results, imported ZIP files, restored project data, attachments, URLs, and
filenames as untrusted. A native Codex process runs each product session in its
own working and state directories. It uses `workspace-write` with
`approvalPolicy: never`, so commands can write inside the session directory
without UI prompts while writes outside that boundary remain sandboxed.

Provider credentials are passed only to the Codex process. Spawned commands use
a non-login, core shell environment with secret-name filtering enabled, and the
credentials are never stored in product sessions or export archives. Network
access for workspace commands and native Web Search are disabled unless the
user enables **Web refs** for that turn.

## Threats and controls

| Area | Principal threat | Enforced controls | Residual risk / operator action |
| --- | --- | --- | --- |
| Codex execution | Cross-session writes, destructive commands, denial of service | Per-session working directory and `CODEX_HOME`, `workspace-write`, no added writable directories, one active turn per session, cancellation, idle and hard timeouts | The sandbox is a write boundary, not a confidentiality boundary. Run the local app only for trusted users and do not place secrets in readable host files. |
| AI-generated Python | Unsafe imports, dynamic execution, invalid or misleading geometry | Managed Python, existing source validation and allowlists, compiler deadlines, bounded output, artifact readback, geometry and freshness checks | Validation cannot prove a part is safe to manufacture. Review dimensions, clearances, material, and print settings. |
| Web research | Prompt injection or false dimensions/specifications | Explicit per-turn opt-in, native Codex search, network disabled otherwise, user constraints remain authoritative | Sources can still be wrong. Review cited mechanical drawings before fabrication. |
| ZIP import | Traversal, duplicate paths, malformed directory, oversized archive | Stored ZIP32 only, unsafe path rejection, duplicate rejection, CRC/checksum verification, entry and 512 MiB archive limits, preflight before mutation | A valid large archive can use substantial memory. Import only expected project backups. |
| XSS / Markdown | Scriptable links, HTML injection, clickjacking | React rendering, no raw HTML rendering, HTTP(S)-only external links, `noopener noreferrer`, CSP, frame denial, MIME sniffing disabled | The current Vite application still permits required inline styling. A future nonce rollout can tighten this further. |
| Downloads | Path-like or control-character filename abuse | Artifact schemas, storage-segment validation, safe leaf-name normalization before browser download | Operating systems may still rewrite filenames. Verify extension and content before opening elsewhere. |
| Attachments | MIME spoofing, oversized images, secret leakage | MIME plus magic-byte validation, count/byte/pixel limits, attachment hashes, server-side revalidation | Images are sent to the configured provider. Do not attach confidential material unless its policy permits it. |
| Secrets | Exposure through UI, logs, generated commands, or archives | Server-only configuration, controlled Codex process environment, non-login shell, core inheritance, secret-name filtering, no secrets in project schemas | Local `.env` is operator-managed and must not be committed. Rotate a key after suspected exposure. |

## Reporting

Follow [SECURITY.md](./SECURITY.md). Include the affected revision, browser and
operating-system version, minimal reproduction, impact, and whether a project
archive is safe to share. Remove API keys, prompts, attachments, and proprietary
CAD before attaching evidence.
