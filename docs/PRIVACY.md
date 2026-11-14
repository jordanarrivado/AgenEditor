# Privacy and data choices

AgenEditor's engine runs on your computer. This page lists everything that can leave it.

## What stays local

* **Media.** Originals are copied into your project folder and never uploaded by AgenEditor.
* **Processing.** Probing, transcription, rendering and QA run locally (FFmpeg and faster-whisper).
* **Telemetry.** None. The engine sends no analytics, crash reports or usage data.
* **Logs.** `render.log`, transcription logs and the `agent_events` audit table stay in the project.
  Render logs show project paths as `<project>`.

## What can leave your computer

| Data | When | Who controls it |
|---|---|---|
| Speech model download | Only when you run `ageneditor cache-model` | You; downloads from Hugging Face |
| Transcript text, timestamps, asset IDs, timeline JSON, QA results | When an AI agent (Codex, Claude Code, …) calls the MCP tools | **The agent host.** It sends tool results to its model provider under that provider's terms. Raw media bytes are never returned by any tool. |
| Shot labels you type | Same as above, via `suggest_broll` / asset listings | The agent host |

If transcripts are sensitive, use an agent host and provider whose data terms you accept, or edit
without an agent. AgenEditor has no setting that sends data anywhere itself.

## Desktop service

The desktop app and MCP bridge talk to one local engine service on `127.0.0.1` with a random port and
a 64-character token. It is never reachable from the network. See
[SECURITY_AUDIT.md](SECURITY_AUDIT.md) for the token file's location and permissions.

## Optional model indexing

Automatic B-roll indexing with a vision model is **not implemented**. If it is added, it will be off by
default and require explicit consent per project, as the product spec requires.
