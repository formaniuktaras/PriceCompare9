"""Utilities for packaging the application with PyInstaller.

This script deletes intermediate build directories before invoking PyInstaller.
That helps Windows environments with limited free disk space avoid ``OSError:
[Errno 28] No space left on device`` errors that appear when old builds
accumulate in ``build/`` or ``dist/``.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Sequence


def _remove_directory(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)


def _run_pyinstaller(arguments: Sequence[str]) -> int:
    command = [sys.executable, "-m", "PyInstaller", *arguments]
    completed = subprocess.run(command, check=False)
    return completed.returncode


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a PyInstaller executable after cleaning old artifacts.")
    parser.add_argument(
        "--spec",
        help="Optional path to a .spec file. If omitted, main.py is used as the entry point.",
    )
    parser.add_argument(
        "pyinstaller_args",
        nargs=argparse.REMAINDER,
        help="Extra arguments forwarded to PyInstaller (e.g. --onefile --noconsole).",
    )
    args = parser.parse_args(argv)

    repo_root = Path(__file__).resolve().parents[1]
    for directory in (repo_root / "build", repo_root / "dist"):
        _remove_directory(directory)

    if args.spec:
        target = args.spec
        default_flags: list[str] = []
    else:
        target = str(repo_root / "main.py")
        default_flags = ["--onefile", "--noconsole"]

    extra_args = list(args.pyinstaller_args or [])
    if extra_args[:1] == ["--"]:
        extra_args = extra_args[1:]

    arguments = [target, *default_flags, *extra_args]
    return _run_pyinstaller(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
