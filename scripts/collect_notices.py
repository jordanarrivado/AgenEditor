"""Collect actual dependency notices and a build inventory; does not grant redistribution approval."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

from license_report import ROOT, _licence, runtime_distributions

from ageneditor.media.hashing import sha256_file
from ageneditor.media.tools import find_tool

FALLBACKS = ROOT / "scripts" / "notice_fallbacks"


def collect(output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    rows = []
    # Upstream licence texts for packages that publish none; used only when the package has no file.
    fallbacks = json.loads((FALLBACKS / "index.json").read_text(encoding="utf-8"))

    def record(component: str, version: str, license_id: str, files: list[Path]) -> None:
        folder = output / re.sub(r"[^a-zA-Z0-9_.-]", "_", f"{component}-{version}")
        folder.mkdir(exist_ok=True)
        notices = []
        if not any(source.is_file() for source in files) and component in fallbacks:
            files = [FALLBACKS / name for name in fallbacks[component]]
        for index, source in enumerate(files):
            if source.is_file():
                target = folder / f"{index}-{source.name}"
                shutil.copyfile(source, target)
                notices.append(target.relative_to(output).as_posix())
        rows.append({"component": component, "version": version, "license": license_id, "notices": notices})

    for dist in runtime_distributions():
        files = [
            Path(dist.locate_file(file))
            for file in dist.files or []
            if re.search(r"(^|/)(licen[cs]e|copying|notice|copyright)", str(file), re.I)
        ]
        record(f"python-{dist.metadata['Name']}", dist.version, _licence(dist), files)
    packages = json.loads((ROOT / "package-lock.json").read_text(encoding="utf-8"))["packages"]
    for location, info in packages.items():
        if not location or info.get("dev"):
            continue
        folder = ROOT / location
        files = [
            path
            for path in folder.iterdir()
            if re.match(r"license|copying|notice|copyright", path.name, re.I)
        ]
        record(f"npm-{location}", info.get("version", "unknown"), info.get("license", "UNKNOWN"), files)
    manifest = ROOT / "apps/desktop/src-tauri/Cargo.toml"
    cargo = shutil.which("cargo")
    if cargo is None:
        raise RuntimeError("Rust Cargo is required to inventory the desktop dependencies")
    result = subprocess.run(
        [
            cargo,
            "metadata",
            "--locked",
            "--format-version",
            "1",
            "--filter-platform",
            "x86_64-pc-windows-msvc",
            "--manifest-path",
            str(manifest),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=300,
    )
    metadata = json.loads(result.stdout)
    included = {node["id"] for node in metadata["resolve"]["nodes"]}
    for package in metadata["packages"]:
        if not package["source"] or package["id"] not in included:
            continue
        folder = Path(package["manifest_path"]).parent
        files = [
            path
            for path in folder.iterdir()
            if re.match(r"license|copying|notice|copyright", path.name, re.I)
        ]
        record(f"rust-{package['name']}", package["version"], package.get("license") or "UNKNOWN", files)
    binaries = {}
    for name in ("ffmpeg", "ffprobe"):
        path = find_tool(name)
        result = subprocess.run(
            [str(path), "-version"], capture_output=True, text=True, check=True, timeout=15
        )
        binaries[name] = {"sha256": sha256_file(path), "version": result.stdout.splitlines()[0]}
    license_output = subprocess.run(
        [str(find_tool("ffmpeg")), "-L"], capture_output=True, text=True, check=True, timeout=15
    )
    (output / "FFmpeg-NOTICE.txt").write_text(license_output.stdout + license_output.stderr, encoding="utf-8")
    build_info = ROOT / ".tools" / "ffmpeg" / "build-info.json"
    pinned = (
        build_info.is_file()
        and json.loads(build_info.read_text(encoding="utf-8"))["outputs"].get("ffmpeg.exe")
        == binaries["ffmpeg"]["sha256"]
    )
    if pinned:
        shutil.copyfile(build_info, output / "FFmpeg-build-info.json")
        (output / "FFmpeg-SOURCE.txt").write_text(
            "The bundled ffmpeg.exe and ffprobe.exe are built by AgenEditor from pinned sources\n"
            "and are licensed GPL-2.0-or-later. The complete corresponding source (all source tarballs\n"
            "and the exact build scripts) is published with every release as\n"
            "ffmpeg-corresponding-source.tar.\n"
            "Inputs, toolchain and output hashes: FFmpeg-build-info.json.\n",
            encoding="utf-8",
        )
    inventory = {
        "approval": "Publication blocked; see docs/release-status.json",
        "binaries": binaries,
        "ffmpeg_pinned_build": pinned,
        "dependencies": rows,
        "missing_notice_files": [r["component"] for r in rows if not r["notices"]],
    }
    (output / "inventory.json").write_text(json.dumps(inventory, indent=2), encoding="utf-8")
    (output / "README.txt").write_text(
        "Dependency license texts and binary hashes for this build.\n"
        "This inventory is not redistribution approval. Corresponding source and native runtime obligations "
        "must be reviewed before publication. Missing license texts are listed in inventory.json.\n",
        encoding="utf-8",
    )
    return inventory


if __name__ == "__main__":
    collect(ROOT / "apps/desktop/src-tauri/resources/third-party")
