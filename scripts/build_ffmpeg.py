"""Build AgenEditor's pinned, minimal FFmpeg for Windows and its GPL corresponding-source archive.

Downloads every input listed in scripts/ffmpeg/sources.json, refuses any whose SHA-256 differs, runs
scripts/ffmpeg/build.sh inside the pinned Debian image (Docker, Linux containers), checks the result
has everything the engine uses, and writes to .tools/ffmpeg/:

    bin/ffmpeg.exe, bin/ffprobe.exe     what the installer bundles
    build-info.json                     inputs, toolchain, configure line and output hashes
    ffmpeg-corresponding-source.tar     every source tarball plus the exact build scripts

    uv run python scripts/build_ffmpeg.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RECIPE = ROOT / "scripts" / "ffmpeg"
SOURCES = ROOT / ".tools" / "ffmpeg-src"
OUT = ROOT / ".tools" / "ffmpeg"
#: What the engine needs from the build (render, captions, QA, probing); checked after every build.
REQUIRED = {
    "-encoders": ["libx264", "aac"],
    "-decoders": ["h264", "hevc", "libdav1d", "aac", "mp3", "opus", "pcm_s16le"],
    "-filters": (
        "ass loudnorm silencedetect blackdetect overlay scale crop pad fade afade amix adelay aresample "
        "aformat atrim trim setpts asetpts format colorbalance eq concat tpad apad volume "
        "sidechaincompress setsar select fps color anullsrc xfade"
    ).split(),
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch_sources(recipe: dict) -> None:
    SOURCES.mkdir(parents=True, exist_ok=True)
    for source in recipe["sources"]:
        target = SOURCES / source["file"]
        if not target.is_file():
            if not source["url"].startswith("https://"):
                raise SystemExit(f"{source['name']}: only https source URLs are allowed")
            print(f"downloading {source['url']}")
            request = urllib.request.Request(source["url"], headers={"User-Agent": "ageneditor-build"})  # noqa: S310 (https checked)
            partial = target.with_suffix(target.suffix + ".part")
            with urllib.request.urlopen(request, timeout=300) as response, partial.open("wb") as handle:  # noqa: S310
                shutil.copyfileobj(response, handle)
            partial.replace(target)
        actual = sha256(target)
        if actual != source["sha256"]:
            raise SystemExit(f"{source['file']}: sha256 {actual} does not match pinned {source['sha256']}")


def run_build(image: str) -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    docker = shutil.which("docker")
    if docker is None:
        raise SystemExit("Docker (Linux containers) is required to build FFmpeg")
    subprocess.run(
        [
            docker, "run", "--rm",
            "-v", f"{SOURCES}:/src:ro", "-v", f"{RECIPE}:/build:ro", "-v", f"{OUT}:/out",
            image, "bash", "/build/build.sh",
        ],
        check=True,
        timeout=3600,
    )  # fmt: skip


def verify(ffmpeg: Path) -> dict:
    def query(*args: str) -> str:
        result = subprocess.run([str(ffmpeg), "-hide_banner", *args], capture_output=True, text=True,
                                check=True, timeout=60)  # fmt: skip
        return result.stdout + result.stderr

    missing = []
    for flag, names in REQUIRED.items():
        listed = set(re.findall(r"^\s*\S+\s+(\S+)", query(flag), re.M))
        missing += [f"{flag[1:-1]} {name}" for name in names if name not in listed]
    if missing:
        raise SystemExit(f"built FFmpeg lacks: {', '.join(missing)}")
    licence = query("-L")
    if "nonfree" in licence.lower():
        raise SystemExit("built FFmpeg is nonfree and could not be redistributed")
    return {"version": query("-version").splitlines()[0], "license_text": licence.strip()[:400]}


def write_source_archive(recipe: dict) -> Path:
    archive = OUT / "ffmpeg-corresponding-source.tar"
    with tarfile.open(archive, "w") as tar:
        for source in recipe["sources"]:
            tar.add(SOURCES / source["file"], arcname=f"sources/{source['file']}")
        for name in ("build.sh", "sources.json"):
            tar.add(RECIPE / name, arcname=f"recipe/{name}")
        tar.add(Path(__file__), arcname="recipe/build_ffmpeg.py")
        for name in ("toolchain.txt", "configure.txt"):
            tar.add(OUT / name, arcname=f"recipe/{name}")
        tar.add(ROOT / "docs" / "FFMPEG_BUILD.md", arcname="README.md")
    return archive


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="check an existing .tools/ffmpeg build (Windows); CI builds on Linux and verifies on Windows",
    )
    args = parser.parse_args(argv)
    recipe = json.loads((RECIPE / "sources.json").read_text(encoding="utf-8"))
    ffmpeg, ffprobe = OUT / "bin" / "ffmpeg.exe", OUT / "bin" / "ffprobe.exe"
    info_path = OUT / "build-info.json"
    if args.verify_only:
        info = json.loads(info_path.read_text(encoding="utf-8"))
        for path in (ffmpeg, ffprobe):
            if sha256(path) != info["outputs"][path.name]:
                raise SystemExit(f"{path.name} does not match build-info.json")
    else:
        fetch_sources(recipe)
        run_build(recipe["image"])
        archive = write_source_archive(recipe)
        info = {
            "image": recipe["image"],
            "sources": [
                {key: source[key] for key in ("name", "version", "license", "sha256")}
                for source in recipe["sources"]
            ],
            "toolchain": (OUT / "toolchain.txt").read_text(encoding="utf-8").splitlines(),
            "outputs": {p.name: sha256(p) for p in (ffmpeg, ffprobe, archive)},
        }
    if sys.platform == "win32":
        info.update(verify(ffmpeg), verified=True)
    else:
        info["verified"] = False  # Windows binaries; run --verify-only on Windows before bundling
    info_path.write_text(json.dumps(info, indent=2), encoding="utf-8")
    print(json.dumps({"verified": info["verified"], **info["outputs"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
