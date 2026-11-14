"""PyInstaller entry point for the packaged local engine service."""

from __future__ import annotations

from multiprocessing import freeze_support

from ageneditor.cli import main

if __name__ == "__main__":
    freeze_support()
    raise SystemExit(main())
