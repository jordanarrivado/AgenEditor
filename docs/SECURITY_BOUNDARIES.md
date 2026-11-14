# Security boundaries

Phase 5 audit and open findings: [`SECURITY_AUDIT.md`](SECURITY_AUDIT.md).

Derived from product spec §4.1, §6 and §11. Status column says what is enforced **today** versus planned.

| Boundary | Rule | Status |
|---|---|---|
| Agent → engine | Agents act only through typed MCP tools. No tool accepts shell commands, URLs, absolute paths, directories or FFmpeg filter strings. | Contracts reject unknown fields (`extra="forbid"`) and enumerate codecs/presets. MCP v2 stdio tools use typed inputs and project-scoped imports. |
| Paths | Every stored path is project-relative, POSIX, no `..`/absolute/drive/UNC/`:`/device names. Assets live under `originals/`. | Enforced by `ProjectRelativePath` and `Asset` validators (tested). The storage layer rechecks resolved paths and rejects symlinks and junctions at I/O time; cleanup never follows links (tested). |
| Source integrity | Originals are copied into `originals/`, hashed (SHA-256) and never written. Renders go to temp files, are verified, then published atomically. | Hashing + read-only probe tested. Imports copy to read-only originals; renders verify temp output before atomic publish. |
| Subprocesses | `subprocess.run`/`Popen` with argument lists, `shell=False` and a timeout; tool paths from PATH or `AGENEDITOR_FFMPEG`/`AGENEDITOR_FFPROBE`. | Enforced in probe, fixture generation, rendering and QA. Ruff `S` rules run in CI. |
| Concurrent edits | UI and MCP commit with `expected_revision`; conflicts return `REVISION_CONFLICT`. | SQLite `BEGIN IMMEDIATE` serializes commits; expected revision conflicts return `REVISION_CONFLICT`. |
| Local IPC | One per-user engine service; named pipe or 127.0.0.1 with an ephemeral token; never bound to LAN. | 127.0.0.1 HTTP with a 64-hex bearer token, constant-time check, 4 MiB body cap (tested). Discovery is stored outside projects with a current-user/SYSTEM-only DACL; bounded threaded requests reject hostile Host/Origin headers (tested). |
| Data leaving the machine | Raw media never uploaded. Transcripts/metadata sent to an external agent only with disclosure; UI shows what is shared. | MCP transcription runs with a local-only model and no download. The explicit `cache-model` CLI command downloads model files; external MCP hosts control their own model traffic. The native handoff panel discloses transcript, filename and metadata sharing. |
| MCP stdio hygiene | stdout carries protocol only; logs to stderr/files; tool args logged redacted in `agent_events`. | The stdio adapter keeps stdout for MCP; write actions store redacted IDs/status in `agent_events`. |
| Secrets | No API keys in repo or frontend. Future provider keys in OS credential storage. | No secrets present. |

Report security issues privately to the maintainer; do not open public issues for them.
