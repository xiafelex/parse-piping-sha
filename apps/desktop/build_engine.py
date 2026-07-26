#!/usr/bin/env python3
"""Build the self-contained macOS engine consumed by the Electron shell."""

from __future__ import annotations

import subprocess
import sys
import os
from pathlib import Path


DESKTOP_ROOT = Path(__file__).resolve().parent
REPOSITORY_ROOT = DESKTOP_ROOT.parent.parent
ENGINE_DIR = DESKTOP_ROOT / "build" / "engine"


def main() -> None:
    ENGINE_DIR.mkdir(parents=True, exist_ok=True)
    data_files = [
        ("web", "web"),
        ("analyze_iso_split.py", "."),
        ("analyze_sha_pages.py", "."),
        ("run_sha_iso_render.py", "."),
        ("sha_to_svg_prototype.py", "."),
    ]
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--name",
        "piping-iso-engine",
        "--distpath",
        str(ENGINE_DIR),
        "--workpath",
        str(DESKTOP_ROOT / "build" / "pyinstaller-work"),
        "--specpath",
        str(DESKTOP_ROOT / "build"),
        "--hidden-import",
        "olefile",
    ]
    for relative, target in data_files:
        command.extend(["--add-data", f"{REPOSITORY_ROOT / relative}{os.pathsep}{target}"])
    command.append(str(REPOSITORY_ROOT / "app_server.py"))
    subprocess.run(command, check=True, cwd=REPOSITORY_ROOT)


if __name__ == "__main__":
    main()
