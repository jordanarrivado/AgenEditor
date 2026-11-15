# License inventory

> **Phase 5:** the authoritative, generated list of what a Windows build redistributes, including native
> DLLs inside Python wheels, is [`LICENSE_REPORT.md`](LICENSE_REPORT.md)
> (`uv run python scripts/license_report.py --out docs/LICENSE_REPORT.md`). Record decisions for flagged
> items in `docs/license-decisions.json` (`{"<component>": "<decision and reason>"}`); the release
> workflow fails while any are pending. The tables below are the hand-written overview.

Project license: **Apache-2.0** ([`LICENSE`](../LICENSE)). Bundled components keep their own licences; the
generated report and recorded decisions are authoritative (`docs/LICENSE_REPORT.md`,
`docs/license-decisions.json`). Speech-model weights are not bundled: the user downloads them with
`ageneditor cache-model`.

## Engine (Python) — runtime

| Package | License | Notes |
|---|---|---|
| pydantic / pydantic-core | MIT | Contracts |
| typing-extensions, annotated-types, typing-inspection | PSF-2.0 / MIT | Pydantic dependencies |

## Engine — development only (not distributed)

| Package | License |
|---|---|
| pytest, pluggy, iniconfig | MIT |
| ruff | MIT |
| hatchling | MIT |
| uv | MIT / Apache-2.0 |

## External tools

| Tool | License | Status |
|---|---|---|
| FFmpeg / FFprobe | LGPL-2.1+ or **GPL** depending on build | Called as a separate process. The Phase 4 sidecar build **bundles whichever FFmpeg is on PATH**; the dev machine's gyan.dev essentials build is GPL-3.0-or-later (libx264). Shipping it requires the licence text and an offer of the exact corresponding source. `ageneditor system` reports the build's licence. See https://ffmpeg.org/legal.html |
| faster-whisper | MIT | Local transcription (optional `transcribe` extra) |
| ctranslate2 | MIT | Its Windows wheel ships `cudnn64_9.dll` (NVIDIA SLA), **stripped from the CPU-only installer**, and `libiomp5md.dll` (Intel OpenMP; Intel EULA for Developer Tools, redistributable with Intel's notice, which the installer ships). Decision in `docs/license-decisions.json`. |
| PyAV (`av`) | BSD-3-Clause | Wheel bundles FFmpeg libraries reported as LGPL-3.0+, **plus `libx264`/`libx265` DLLs (GPL-2.0+)**. Development only: **excluded from the packaged engine** (`--exclude-module av`); the engine decodes audio with the separate `ffmpeg.exe`. |
| Whisper model weights (Systran faster-whisper conversions) | MIT (OpenAI Whisper) | Downloaded by the user with `cache-model`; not bundled |
| mcp | MIT | MCP SDK |
| tqdm (via faster-whisper/huggingface-hub) | MPL-2.0 AND MIT | Weak copyleft: unmodified redistribution is fine; flagged for an explicit decision |

## Desktop UI (Node) — runtime

| Package | License |
|---|---|
| react, react-dom, react-router-dom | MIT |
| @radix-ui/react-* (dialog, dropdown-menu, context-menu, popover, tooltip) | MIT |
| lucide-react | ISC |
| tailwindcss, @tailwindcss/vite | MIT |

Dev-only: vite, vitest, typescript (Apache-2.0), @testing-library/* (MIT), jsdom (MIT), @playwright/test (Apache-2.0).

## Fonts and media

| Asset | License | Notes |
|---|---|---|
| Inter, JetBrains Mono | SIL OFL 1.1 | Loaded from Google Fonts in the UI prototype; bundle locally for the desktop app. |
| Poppins Regular/Bold | SIL OFL 1.1 (`resources/fonts/OFL.txt`) | Bundled caption font; shipped with the installer, rendered into user exports. OFL permits this; the font may not be sold on its own. |
| Consolas (fixture slates) | Microsoft, OS font | Only rasterised into **generated, gitignored** test media on the developer's machine; never committed or shipped. |
| Kenney "Interface Sounds" sound pack | CC0 1.0 (`resources/sounds/README.txt`, `License.txt` in the zip) | Bundled starter sound pack, shipped unmodified in the installer and installed into the user's sound library. The other catalog packs (Kenney, CC0) are downloaded by the user from OpenGameArt, not shipped. |
| UI artwork, thumbnails | Original (CSS/SVG in repo) | No stock imagery. |
| Fixture media | Generated from FFmpeg `lavfi` sources | Reproducible from `packages/ageneditor/devtools/fixtures.py`; no third-party footage. Consented real-source fixtures stay private and out of the repo. |
