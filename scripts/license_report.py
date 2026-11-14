"""Generate the third-party licence report for what a Windows build would redistribute (roadmap Phase 5).

Walks the *installed* runtime dependency tree of ``ageneditor`` (with the ``transcribe`` extra), reads
each distribution's licence metadata and lists native libraries it ships, inspects the FFmpeg build that
the sidecar packaging copies, and reads npm licences from ``package-lock.json``.

Anything that needs a human decision (copyleft, proprietary runtime DLLs, unknown licence) is marked
REVIEW. Decisions are recorded in ``docs/license-decisions.json``; ``--check`` fails while any REVIEW item
has no decision. This script reports facts; it is not legal advice.

    uv run python scripts/license_report.py --out docs/LICENSE_REPORT.md [--check]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from importlib import metadata
from pathlib import Path

from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[1]
DECISIONS = ROOT / "docs" / "license-decisions.json"
COPYLEFT = re.compile(r"\b(A?GPL|LGPL|GNU (Lesser |Affero )?General Public|MPL|EPL|CDDL)", re.I)
#: Native libraries whose presence needs an explicit redistribution decision, with the reason.
NATIVE_FLAGS = {
    "cudnn": "NVIDIA cuDNN runtime (NVIDIA Software License Agreement; redistribution conditions apply)",
    "cublas": "NVIDIA cuBLAS runtime (NVIDIA EULA)",
    "libiomp5": "Intel OpenMP runtime (Intel EULA for Developer Tools; redistributable with notices)",
    "libx264": "x264 encoder (GPL-2.0-or-later)",
    "libx265": "x265 encoder (GPL-2.0-or-later)",
    "avcodec": "FFmpeg libraries (LGPL or GPL depending on build configuration)",
}
#: Installed dependencies the packaged engine deliberately leaves out (PyInstaller ``--exclude-module``),
#: so they are not redistributed. Tests prove the engine works without them.
EXCLUDED_FROM_BUNDLE = {
    "av": "PyAV ships GPL libx264/libx265 DLLs; the engine decodes audio with the separate ffmpeg.exe",
}

#: Native libraries of included packages that the packaged engine strips (lower-case file names).
EXCLUDED_BINARIES = {
    "cudnn64_9.dll": "NVIDIA cuDNN; the installer is CPU-only, so GPU transcription is not shipped",
}


def _licence(dist: metadata.Distribution) -> str:
    meta = dist.metadata
    expression = meta.get("License-Expression")
    if expression:
        return expression
    classifiers = [
        c.split("::")[-1].strip() for c in meta.get_all("Classifier") or [] if c.startswith("License")
    ]
    if classifiers:
        return " / ".join(classifiers)
    text = (meta.get("License") or "").strip().splitlines()
    return text[0][:120] if text else "UNKNOWN"


def runtime_distributions(root: str = "ageneditor", extras: tuple[str, ...] = ("transcribe",)) -> list:
    seen: dict[str, metadata.Distribution] = {}
    queue = [(root, extras)]
    while queue:
        name, active = queue.pop()
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            continue
        key = dist.metadata["Name"].lower().replace("_", "-")
        if key in EXCLUDED_FROM_BUNDLE:
            continue
        if key in seen and not active:
            continue
        seen[key] = dist
        for raw in dist.requires or []:
            req = Requirement(raw)
            environments = [{"extra": extra} for extra in active] or [{"extra": ""}]
            if req.marker and not any(req.marker.evaluate(env) for env in environments):
                continue
            queue.append((req.name, tuple(req.extras)))
    seen.pop(root, None)
    return sorted(seen.values(), key=lambda d: d.metadata["Name"].lower())


def native_libraries(dist: metadata.Distribution) -> list[str]:
    names = {
        Path(str(f)).name
        for f in dist.files or []
        if str(f).lower().endswith((".dll", ".so", ".dylib")) or ".so." in str(f).lower()
    }
    return sorted(names)


def python_rows(decisions: dict) -> list[dict]:
    rows = []
    for dist in runtime_distributions():
        name = dist.metadata["Name"]
        licence = _licence(dist)
        natives = native_libraries(dist)
        reasons = []
        if licence == "UNKNOWN":
            reasons.append("licence metadata missing")
        elif COPYLEFT.search(licence):
            reasons.append(f"copyleft licence: {licence}")
        for lib in natives:
            if lib.lower() in EXCLUDED_BINARIES:
                continue
            for marker, reason in NATIVE_FLAGS.items():
                if lib.lower().startswith(marker):
                    reasons.append(f"ships {lib}: {reason}")
        rows.append(
            {
                "component": name,
                "version": dist.version,
                "licence": licence,
                "native": len(natives),
                "review": reasons,
                "decision": decisions.get(name),
            }
        )
    return rows


PINNED_FFMPEG = ROOT / ".tools" / "ffmpeg"


def ffmpeg_row(decisions: dict) -> dict | None:
    """Inspect the FFmpeg that would be bundled: the pinned build when present (as the installer does)."""
    import hashlib
    import os

    build_info = PINNED_FFMPEG / "build-info.json"
    pinned_exe = PINNED_FFMPEG / "bin" / "ffmpeg.exe"
    if build_info.is_file() and pinned_exe.is_file():
        os.environ["AGENEDITOR_FFMPEG"] = str(pinned_exe)
        os.environ["AGENEDITOR_FFPROBE"] = str(PINNED_FFMPEG / "bin" / "ffprobe.exe")
    try:
        from ageneditor.system import ffmpeg_info

        info = ffmpeg_info(probe_hw=False)
    except Exception as exc:  # report generation must not need FFmpeg
        return {"component": "FFmpeg (bundled exe)", "version": "?", "licence": f"not found: {exc}",
                "native": 0, "review": ["FFmpeg not found; cannot inspect the build to be bundled"],
                "decision": decisions.get("FFmpeg")}  # fmt: skip
    reasons = [
        f"build licence {info.license}; ship licence text and corresponding source for this exact build"
    ]
    # The recorded decision covers only the pinned build (scripts/build_ffmpeg.py), identified by hash.
    pinned = False
    if build_info.is_file():
        expected = json.loads(build_info.read_text(encoding="utf-8"))["outputs"].get("ffmpeg.exe")
        pinned = expected == hashlib.sha256(Path(info.path).read_bytes()).hexdigest()
    if not pinned:
        reasons.insert(0, "not the pinned build (scripts/build_ffmpeg.py); decision does not apply")
    if info.nonfree:
        reasons.insert(0, "NONFREE build: must not be redistributed")
    return {
        "component": "FFmpeg (bundled exe)",
        "version": info.version,
        "licence": info.license,
        "native": 0,
        "review": reasons,
        "decision": decisions.get("FFmpeg") if pinned else None,
    }


def npm_rows(decisions: dict) -> list[dict]:
    lock = ROOT / "package-lock.json"
    if not lock.is_file():
        return []
    packages = json.loads(lock.read_text(encoding="utf-8")).get("packages", {})
    rows = []
    for path, info in packages.items():
        name = path.removeprefix("node_modules/")
        if not path or info.get("dev"):
            continue
        licence = info.get("license") or "UNKNOWN"
        reasons = ["licence metadata missing"] if licence == "UNKNOWN" else []
        if COPYLEFT.search(licence):
            reasons.append(f"copyleft licence: {licence}")
        rows.append({"component": f"npm:{name}", "version": info.get("version", "?"), "licence": licence,
                     "native": 0, "review": reasons, "decision": decisions.get(f"npm:{name}")})  # fmt: skip
    return sorted(rows, key=lambda r: r["component"])


def render(rows: list[dict]) -> str:
    pending = [r for r in rows if r["review"] and not r["decision"]]
    lines = [
        "# Third-party licence report (generated)",
        "",
        "Generated by `scripts/license_report.py` from the installed runtime dependency tree, the FFmpeg",
        "build found on PATH and `package-lock.json`. Facts only; not legal advice. Rust crates of the",
        "Tauri shell are checked separately with `cargo deny` (`apps/desktop/src-tauri/deny.toml`).",
        "",
        "Installed but excluded from the packaged engine (not redistributed): "
        + "; ".join(
            f"`{name}` ({why})" for name, why in sorted({**EXCLUDED_FROM_BUNDLE, **EXCLUDED_BINARIES}.items())
        ),
        "",
        f"**Items needing a recorded decision:** {len(pending)}",
        "",
        "| Component | Version | Licence | Native libs | Review | Decision |",
        "|---|---|---|---|---|---|",
    ]
    for r in rows:
        review = "<br>".join(r["review"]) or "—"
        lines.append(
            f"| {r['component']} | {r['version']} | {r['licence']} | {r['native']} | {review} | "
            f"{r['decision'] or ('**PENDING**' if r['review'] else '—')} |"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, help="write the Markdown report here")
    parser.add_argument("--check", action="store_true", help="exit 1 while REVIEW items lack a decision")
    args = parser.parse_args(argv)
    decisions = json.loads(DECISIONS.read_text(encoding="utf-8")) if DECISIONS.is_file() else {}
    rows = [*python_rows(decisions), *filter(None, [ffmpeg_row(decisions)]), *npm_rows(decisions)]
    report = render(rows)
    if args.out:
        args.out.write_text(report, encoding="utf-8")
    else:
        sys.stdout.write(report)
    pending = [r["component"] for r in rows if r["review"] and not r["decision"]]
    if args.check and pending:
        print(f"licence decisions pending: {', '.join(pending)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
