# Security audit — Phase 5 (beta hardening)

Scope: project path sandbox, symlink/junction escapes, local IPC authentication, secret storage, crash
hygiene. Date: 2026-11-10. Method: code review plus tests in `tests/engine/test_phase5.py` (marked ✔
below). Threat model: the engine runs as the user. Agents (via MCP) are semi-trusted and act only
through typed tools. Other local accounts on the same PC are **not** trusted. Network attackers must
never reach the engine.

## Fixed in this phase

| # | Finding | Fix | Test |
|---|---|---|---|
| F1 | `ProjectRelativePath` accepted Windows device names (`CON`, `nul.mp4`, `COM1`…), `:` (alternate data streams such as `x.mp4::$DATA`, drive-relative `C:x`), segments ending in `.`/space (Windows silently strips them, so two paths alias one file) and control characters. | Validator rejects all of them. | ✔ |
| F2 | Imported file names were used as-is in `originals/…`; unusual names produced a raw validation error instead of a structured one. | Names are sanitised (letters, digits, `._-()[]` and space). | ✔ |
| F3 | `cleanup --apply` (new in this phase) followed NTFS **junctions**, because `os.walk(followlinks=False)` and `is_symlink()` treat junctions as ordinary directories. It deleted a file outside the project in testing. | Every directory and file is checked for symlink, junction or resolution outside the project before it is listed or deleted. | ✔ (junction created without admin rights) |
| F4 | SQLite connections were never closed (`with sqlite3.connect()` only ends the transaction). Handles stayed open until garbage collection, locking project files on Windows. | `ProjectStore._connect` commits, rolls back on error, and always closes. | ✔ (benchmark deletes its projects) |
| F5 | Imports and renders could run the disk full mid-write. | Preflight raises `INSUFFICIENT_DISK` with required/free bytes before copying or rendering (256 MB reserve). | ✔ |

## Verified

| Boundary | Result |
|---|---|
| `safe_path` vs junction pointing outside the project | Refused (`PERMISSION_DENIED`) ✔ |
| `safe_path` vs symlink | Refused ✔ (test skips where symlinks need admin rights) |
| `..`, absolute, UNC, backslash paths | Refused ✔ |
| Service bind address | `127.0.0.1` only ✔ |
| Service auth | Constant-time bearer comparison; 403 without token, with a wrong token, or without the `Bearer` scheme ✔ |
| Oversized body | 413 (4 MiB limit) ✔ |
| Malformed `Content-Length` | Request dropped; service keeps serving ✔ (see O3) |
| Service logging | Request logging disabled; nothing written to stdout |
| Secrets | No API keys in the repository or UI. The only secret is the per-start service token (64 hex chars, regenerated on every start). |
| Render diagnostics | `render.log` shows project paths as `<project>` ✔ |

## Desktop-service follow-up (2026-11-11)

| Finding | Resolution and evidence |
|---|---|
| O1: discovery credentials inherited workspace permissions | Discovery and service lock moved to `%LOCALAPPDATA%/AgenEditor/services/<workspace-hash>/`. Protected Windows DACL permits only the current user and SYSTEM, applied before credentials are written. POSIX permissions are 0700/0600. An integration test checks the actual file and directory ACLs. |
| O2: desktop import accepts an absolute selected path | The native picker remains the intentional trusted import boundary; MCP tools cannot import arbitrary absolute paths. O1 is fixed. Same-user programs with filesystem access are outside this boundary and can read that user's credentials; no stronger capability isolation is claimed. |
| O3: malformed Content-Length traceback | Returns 400 for malformed, duplicate or transfer-encoded request bodies. Oversized bodies return 413. |
| O4: one stalled HTTP connection blocks the service | Bounded threaded server (16 connections, 10-second socket read timeout). A stalled-client test confirms health remains responsive. Worker registry initialization is locked to prevent concurrent clients from recovering an active worker's jobs. |
| O5: Host and browser Origin checks | Only the exact loopback Host is accepted; requests containing Origin are rejected. Tested with valid credentials and hostile headers. |
| Project-root junction escape | ProjectStore rejects linked/junction project roots and linked SQLite databases before opening them. A workspace discovery test refuses a junction to an external project. |
| Native export path aliases and overwrites | Rust resolves the destination parent before checking the workspace boundary and uses exclusive file creation, preventing overwrite through existing symlinks/hard links. Incomplete newly created exports are removed on copy failure. Native dialog behavior still needs the clean-Windows UI test. |
| UI availability | Blocking HTTP, model and dialog operations run in Tauri's blocking task pool. Drafts survive conflicts; import/project switching require save or explicit discard. Component tests bind QA approval to the exact final job. |

## Installed-app review (2026-11-12)

| Area | Result |
|---|---|
| Rust crates | `cargo deny check advisories licenses` passes for the Windows target (0 RUSTSEC advisories); policy in `apps/desktop/src-tauri/deny.toml`. |
| Installed contents | Silent current-user install contains only the shell, engine, uninstaller, pinned `ffmpeg.exe`/`ffprobe.exe`, caption fonts and licence notices. No PyAV, x264-in-process or NVIDIA DLLs inside the engine. |
| Installed engine | `smoke_packaged.py` against the installed engine (isolated PATH, private `LOCALAPPDATA`) passes; `render.log` shows the bundled FFmpeg and redacts project paths as `<project>`. |
| Uninstall | Silent uninstall removes every installed file and the per-user uninstall registry entry. |

## Not yet audited

* Tauri scope is limited to the app-local project directory; IPC commands were inspected and compiled.
  A full penetration test of the installed app and native file-dialog automation have not been performed.
* Clean-machine behaviour remains unverified (the review above ran on the development machine).
