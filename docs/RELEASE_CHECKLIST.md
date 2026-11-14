# Beta release checklist (Phase 5 exit gate)

The release workflow builds internal artifacts. `scripts/release_readiness.py` blocks draft-release
creation unless every field in `docs/release-status.json` is approved. The owner requested that
publication remain disabled on 2026-11-11. No release has been published.

## Blockers

| | Item | Status |
|---|---|---|
| ☐ | Phase 4 exit gate met: installer passes a clean-Windows test (no Python/Node/FFmpeg preinstalled); UI import → preview → revise → export works | 2026-11-12, on the development machine (not clean): silent install, installed engine passes `smoke_packaged.py` (isolated PATH; transcription, QA-gated export, captioned render) using its bundled FFmpeg, desktop app starts with its engine sidecar, silent uninstall leaves no files or registry entry. Clean-Windows UI test pending |
| ☑ | **Project licence chosen**; `LICENSE` file added | Apache-2.0, chosen by the owner 2026-11-11. `LICENSE` is the canonical apache.org text; licence set in `pyproject.toml`, `package.json` and `Cargo.toml` |
| ☑ | Licence decisions recorded in `docs/license-decisions.json` for every item flagged in `docs/LICENSE_REPORT.md`; `scripts/license_report.py --check` passes | `--check` passes (0 pending, 2026-11-12). FFmpeg and ctranslate2/Intel OpenMP decided 2026-11-12 under the owner's delegation; the FFmpeg decision applies only to the pinned build (hash-checked). tqdm accepted by the owner 2026-11-11. cuDNN removed: the installer is CPU-only (owner decision 2026-11-11). PyAV removed from the bundle on 2026-11-11 (engine decodes audio with `ffmpeg.exe`). |
| ☑ | FFmpeg obligations: bundled build pinned; its licence text shipped in the installer; exact corresponding source archived and offered | Own minimal build (FFmpeg 9.0.2 + x264, dav1d, libass, freetype, fribidi, harfbuzz, zlib; GPL-2.0-or-later) from hash-checked sources in a pinned image (`scripts/build_ffmpeg.py`, `docs/FFMPEG_BUILD.md`). Installer ships licence text, build info and a source notice; the release workflow attaches `ffmpeg-corresponding-source.tar`. All engine tests and the packaged smoke test pass on it. |
| ☑ | Third-party notices file generated from the licence report and included in the installer | Generated and bundled for 392 dependencies with no missing licence texts: 11 packages that publish none use upstream repository licence files vendored in `scripts/notice_fallbacks/` (default branch, fetched 2026-11-11). A person must still review them (`redistribution_review_complete` in `docs/release-status.json`). |
| ☑ | Security O1 (service token file readable by other local accounts) fixed and tested | Private discovery location and Windows ACL integration test pass |
| ☐ | Benchmark run on the consented set, on at least one low, one mid and one high-end PC; QA false positives and misses reviewed by a person | Harness ready; consented dataset does not exist yet |
| ☐ | Unaided tutorial: at least 3 new users complete `docs/TUTORIAL.md` (or the desktop equivalent) without help; issues fixed | Not run |
| ☑ | Captions use a bundled, licence-reviewed font | Poppins Regular/Bold (SIL OFL 1.1) in `apps/desktop/src-tauri/resources/fonts/`, passed to libass via `fontsdir` (verified: libass selects `Poppins-Bold`). `smoke_packaged.py` now renders a captioned revision from the packaged engine; passes locally (2026-11-11) and fails as expected with the font folder removed. Clean-machine check pending with the Phase 4 gate. |

## Should pass (verified in CI or by tests)

| | Item | Status |
|---|---|---|
| ☑ | Engine tests and lint (CI, Windows) | Passing locally |
| ☑ | Path sandbox: device names, ADS, junction and symlink escapes | Tests in `test_phase5.py` |
| ☑ | Crash hygiene: interrupted jobs recovered, `cleanup` removes only temporaries, `doctor` health check | Tests |
| ☑ | Disk preflight before import and render | Tests |
| ☑ | Render diagnostics (`render.log`) with redacted paths | Tests |
| ☑ | Transcription CPU fallback when the GPU path fails | Test simulates a missing cuBLAS DLL |
| ☐ | Code signing | Workflow signs only if `WINDOWS_CERTIFICATE_PFX_BASE64` / `WINDOWS_CERTIFICATE_PASSWORD` secrets exist; no certificate yet. An unsigned installer triggers SmartScreen warnings. |
| ☑ | Security O2–O4 (service hardening) | Import boundary documented; malformed and stalled request tests pass |
| ☑ | `cargo deny` / Rust licence review for the Tauri shell | `apps/desktop/src-tauri/deny.toml` allows only permissive licences plus MPL-2.0; `cargo deny check advisories licenses` passes (2026-11-11, Windows target, 0 RUSTSEC advisories). Re-run before each release. |

## Documentation

| | Item |
|---|---|
| ☑ | [INSTALL.md](INSTALL.md): requirements, models, device selection, maintenance commands |
| ☑ | [SUPPORTED_MEDIA.md](SUPPORTED_MEDIA.md): tested inputs, output format |
| ☑ | [PRIVACY.md](PRIVACY.md): what stays local, what the agent host receives |
| ☑ | [TUTORIAL.md](TUTORIAL.md): end-to-end workflow |
| ☑ | [BENCHMARK.md](BENCHMARK.md): evaluation harness and manifest |
| ☑ | [KNOWN_LIMITATIONS.md](../KNOWN_LIMITATIONS.md) |
| ☑ | [Desktop tutorial](TUTORIAL_DESKTOP.md); screenshots and unaided test remain pending |
