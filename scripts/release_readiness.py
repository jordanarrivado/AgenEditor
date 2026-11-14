"""Fail closed before creating a release; local builds remain available for testing."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = (
    "publication_enabled",
    "project_license_approved",
    "redistribution_review_complete",
    "clean_windows_ui_test_passed",
    "consented_benchmark_review_complete",
    "low_mid_high_hardware_tested",
    "unaided_tutorial_test_passed",
)


def main() -> int:
    status = json.loads((ROOT / "docs/release-status.json").read_text(encoding="utf-8"))
    blockers = [key for key in REQUIRED if status.get(key) is not True]
    if not (ROOT / "LICENSE").is_file():
        blockers.append("LICENSE file missing")
    print(json.dumps({"ready": not blockers, "blockers": blockers, "reason": status.get("reason")}, indent=2))
    return int(bool(blockers))


if __name__ == "__main__":
    raise SystemExit(main())
