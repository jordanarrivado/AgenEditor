# Installing AgenEditor (beta)

AgenEditor edits footage you already have. It never generates video, images, voices or music, and it
never modifies your original files.

> **Status.** An unsigned local Windows installer builds successfully. Public release is disabled by
> `docs/release-status.json`. The clean-Windows acceptance test remains pending.

For the native app, use the local installer or `npm run desktop:dev` from a configured development
checkout. See the [desktop tutorial](TUTORIAL_DESKTOP.md). `npm run desktop:build` bundles Python,
FFmpeg/FFprobe and a generated dependency inventory. The installer targets the current Windows user.

## System requirements

| | Requirement | Notes |
|---|---|---|
| OS | Windows 10/11 x64 | The engine also runs on macOS/Linux from source; only Windows is packaged. |
| CPU | x64 | Rendering uses libx264 on the CPU. Transcription uses CPU int8 by default. |
| RAM | Not yet measured | Peak memory per model preset will be recorded by the benchmark before release. |
| Disk | Source size × 1.2 + 1 GB | Originals are **copied** into the project. Renders and a 256 MB reserve are checked before work starts (`INSUFFICIENT_DISK`). |
| GPU | Optional | See [Transcription device](#transcription-device). No speed figures are published until the benchmark has run on low/mid/high machines. |

Run `ageneditor system --workspace <folder>` to see what the engine detects on your machine (CPU, RAM,
free disk, FFmpeg build and licence, usable hardware encoders, CUDA devices).

## From source

Requires Python 3.12 with [uv](https://docs.astral.sh/uv/), and FFmpeg + FFprobe with libx264 and
libass (point `AGENEDITOR_FFMPEG` / `AGENEDITOR_FFPROBE` at them if they are not on `PATH`).

```powershell
uv sync --locked --extra transcribe
uv run ageneditor system --workspace .local-projects
uv run ageneditor cache-model base      # one explicit download; see below
```

Then connect an MCP host: [MCP_PHASE1.md](MCP_PHASE1.md#start-or-configure-a-host), and follow the
[tutorial](TUTORIAL.md).

## Speech models

Transcription is local ([faster-whisper](https://github.com/SYSTRAN/faster-whisper)). The engine never
downloads a model by itself; `ageneditor cache-model <preset>` is the only download step, and it stores
the model in the Hugging Face cache (`%USERPROFILE%\.cache\huggingface\hub` by default).

| Preset | Approx. download | Use |
|---|---|---|
| `tiny` | ~75 MB | Quick checks; noticeably more word errors |
| `base` | ~145 MB | Default |
| `small` | ~480 MB | Better accuracy, slower on CPU |

Accuracy on real speech has not been measured yet; the [benchmark](BENCHMARK.md) reports word error
rate once the consented dataset exists.

### Transcription device

**The Windows installer is CPU-only:** it does not ship NVIDIA's cuDNN, so the installed app always
transcribes on CPU (int8) and ignores the setting below. GPU transcription is available only when
running the engine from source with the `transcribe` extra.

From source, `AGENEDITOR_TRANSCRIBE_DEVICE` selects the device:

* `cpu` (default): CPU int8. Works everywhere.
* `auto`: CUDA float16 when `ctranslate2` reports a CUDA device, otherwise CPU.
* `cuda`: try CUDA first even if detection reported none.

Any failure on the accelerated device (missing cuDNN/cuBLAS DLLs are the usual cause on Windows)
falls back to CPU int8 for that job. Each transcript records the device that actually produced it
(`compute`, e.g. `cpu/int8`).

## Maintenance commands

| Command | What it does |
|---|---|
| `ageneditor doctor <project> [--hash]` | Read-only health report: database integrity, timeline history, originals present/read-only/unchanged, missing render outputs, crash leftovers. Exit code 1 if anything is wrong. |
| `ageneditor cleanup <project> [--apply]` | Lists temporary files left by crashes; `--apply` deletes only those. Never touches originals, finished renders, timelines or transcripts, and never follows links or junctions. |
| `ageneditor system [--workspace dir]` | Hardware and toolchain report (above). |

Every render writes `render.log` next to its output: the exact FFmpeg command (project paths shown as
`<project>`), exit code, duration and FFmpeg's error output. Attach it to bug reports.

## Uninstalling

Delete the application. Projects are ordinary folders in the workspace you chose; delete them yourself
if you no longer need them. Cached speech models live in the Hugging Face cache folder above.
