# Amagine3D threat model

> **简体中文**：[查看中文版](./threat-model.zh-CN.md)

Status: Codex runtime and local Tavily search, 2026-09-15

## Security boundaries

Amagine3D treats model output, generated commands and Python, Web Search
results, imported ZIP files, restored project data, attachments, URLs, and
filenames as untrusted. A native Codex process runs each product session in its
own working and state directories. It uses `workspace-write` with
`approvalPolicy: never`, so commands can write inside the session directory
without UI prompts while writes outside that boundary remain sandboxed.

Model-provider credentials are passed only to the Codex process. Spawned
commands use a non-login, core shell environment with secret-name filtering
enabled. When Tavily search is configured, the account key remains in the
runtime process and each turn receives only an unguessable loopback capability
that accepts validated searches to Tavily's fixed endpoint; the listener and
capability are destroyed when the turn settles. No account credential is stored
in product sessions or export archives. Web research and workspace network are
disabled together when the server sets `CODEX_WEB_SEARCH_ENABLED=false`.

## Threats and controls

| Area | Principal threat | Enforced controls | Residual risk / operator action |
| --- | --- | --- | --- |
| Codex execution | Cross-session writes, destructive commands, denial of service | Per-session working directory and `CODEX_HOME`, `workspace-write`, no added writable directories, one active turn per session, cancellation, idle and hard timeouts | The sandbox is a write boundary, not a confidentiality boundary. Run the local app only for trusted users and do not place secrets in readable host files. |
| AI-generated Python | Unsafe imports, dynamic execution, invalid or misleading geometry | Managed Python, existing source validation and allowlists, compiler deadlines, bounded output, artifact readback, geometry and freshness checks | Validation cannot prove a part is safe to manufacture. Review dimensions, clearances, material, and print settings. |
| Web research | Prompt injection, false dimensions/specifications, or search-account key exposure | Server-controlled enablement; local `a3d search` allowlists request/result fields, fixes the Tavily endpoint, disables raw content and images, and delegates only a per-turn loopback capability; network is disabled when Web research is off | Search snippets and destination pages can still be wrong or hostile. The capability is available to model-controlled commands for its turn, and network permission is coarse. Review cited mechanical drawings before fabrication. |
| ZIP import | Traversal, duplicate paths, malformed directory, oversized archive | Stored ZIP32 only, unsafe path rejection, duplicate rejection, CRC/checksum verification, entry and 512 MiB archive limits, preflight before mutation | A valid large archive can use substantial memory. Import only expected project backups. |
| XSS / Markdown | Scriptable links, HTML injection, clickjacking | React rendering, no raw HTML rendering, HTTP(S)-only external links, `noopener noreferrer`, CSP, frame denial, MIME sniffing disabled | The current Vite application still permits required inline styling. A future nonce rollout can tighten this further. |
| Downloads | Path-like or control-character filename abuse | Artifact schemas, storage-segment validation, safe leaf-name normalization before browser download | Operating systems may still rewrite filenames. Verify extension and content before opening elsewhere. |
| Attachments | MIME spoofing, oversized images, secret leakage | MIME plus magic-byte validation, count/byte/pixel limits, attachment hashes, server-side revalidation | Images are sent to the configured provider. Do not attach confidential material unless its policy permits it. |
| Secrets | Exposure through UI, logs, generated commands, broker requests, or archives | Server-only configuration, model-provider keys removed from shell commands, Tavily key retained by the runtime, explicit `.env`/`.env.local` read denies, per-turn search capabilities, non-login shell, core inheritance, secret-name filtering, no secrets in project schemas | Local `.env` is operator-managed and must not be committed. Any model-controlled command can use its active search capability, though it cannot recover the Tavily account key. Rotate a key after suspected exposure. |

## Reporting

Follow [SECURITY.md](./SECURITY.md). Include the affected revision, browser and
operating-system version, minimal reproduction, impact, and whether a project
archive is safe to share. Remove API keys, prompts, attachments, and proprietary
CAD before attaching evidence.
