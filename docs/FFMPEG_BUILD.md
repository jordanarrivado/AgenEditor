# AgenEditor's bundled FFmpeg

The Windows installer ships `ffmpeg.exe` and `ffprobe.exe` built by AgenEditor from pinned sources.
They run as **separate programs**: the engine starts them as subprocesses and never links them.

## What is in it

A static Windows x64 build of FFmpeg configured with `--enable-gpl` and only these external libraries:

| Library | Why | Licence |
|---|---|---|
| x264 | H.264 encoding of every render | GPL-2.0-or-later |
| dav1d | AV1 decoding of imported phone/web video | BSD-2-Clause |
| libass, freetype, fribidi, harfbuzz | Burned-in captions | ISC; FTL or GPL-2.0+; LGPL-2.1+; MIT |
| zlib | PNG and other compressed formats | Zlib |

Everything else is FFmpeg's own code. Because of x264 the build as a whole is licensed
**GPL-2.0-or-later**. Exact versions and SHA-256 hashes are in `scripts/ffmpeg/sources.json`.

## Corresponding source (GPL)

Each release attaches `ffmpeg-corresponding-source.tar`. It holds every source tarball the build used,
the build scripts (`build.sh`, `sources.json`, `build_ffmpeg.py`), the exact toolchain package versions
(`toolchain.txt`) and FFmpeg's configure output. Anyone may rebuild the bundled binaries from it.

## Rebuilding

Requirements: Docker with Linux containers, Python 3.12.

```bash
uv run python scripts/build_ffmpeg.py
```

The script downloads each source, **refuses any file whose SHA-256 differs** from `sources.json`,
cross-compiles inside the pinned Debian image with MinGW-w64, checks that the result has every encoder,
decoder and filter the engine uses, and writes `.tools/ffmpeg/` (binaries, `build-info.json`, source
archive). `scripts/build_desktop_sidecar.py` bundles `.tools/ffmpeg/bin/` when it exists.

To update a library, change its version, URL and SHA-256 together in `sources.json`, rebuild, and run
the engine tests and the packaged smoke test against the new binaries.
