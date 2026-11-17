"""Build the stand-alone Windows executable with PyInstaller.

    pip install -e ".[build]"
    python build_exe.py

Produces dist/Walka.exe: the companion, its dashboard page and a copy of
the addon (so the dashboard's "Install addon" button works) in one file.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ADDON = HERE.parent / "Walka"


def main() -> int:
    try:
        import PyInstaller.__main__
    except ImportError:
        print('PyInstaller is not installed. Run: pip install -e ".[build]"', file=sys.stderr)
        return 1
    if not (ADDON / "Walka.toc").exists():
        print(f"addon folder not found at {ADDON}", file=sys.stderr)
        return 1

    sep = os.pathsep  # ';' on Windows, ':' elsewhere
    args = [
        str(HERE / "walka_launcher.py"),
        "--name", "Walka",
        "--onefile",
        "--console",
        "--noconfirm",
        "--clean",
        "--paths", str(HERE / "src"),
        "--add-data", f"{HERE / 'src' / 'walka' / 'static'}{sep}walka/static",
        "--add-data", f"{ADDON}{sep}Walka",
        # bleak picks its Bluetooth backend (and the WinRT projections) at
        # run time, which PyInstaller's static analysis cannot see.
        "--collect-submodules", "bleak",
        "--distpath", str(HERE / "dist"),
        "--workpath", str(HERE / "build"),
        "--specpath", str(HERE / "build"),
    ]  # fmt: skip
    if sys.platform == "win32":
        args += ["--collect-submodules", "winrt"]
    PyInstaller.__main__.run(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
