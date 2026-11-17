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

# Where the addon goes inside the bundle (walka.addon_sync looks for it
# there). Not "Walka" at the top level: Windows file names ignore case, so
# that would be the same folder as the package's own "walka", and "Install
# addon" would copy the dashboard's files into the game along with the addon.
ADDON_DESTINATION = "_addon/Walka"

# The Windows Runtime packages bleak needs. winrt 3.x is a namespace package
# (no __init__.py in winrt/ or winrt/windows/), which --collect-submodules
# does not descend into; and its compiled modules import some of these by
# name at run time - winrt.windows.foundation.collections above all, without
# which no advertisement can be read - so nothing imports them where
# PyInstaller's analysis could see it. "Walka.exe selfcheck" (walka.cli)
# imports the same list, and the CI smoke test runs it.
WINRT_HIDDEN_IMPORTS = (
    "winrt.windows.foundation",
    "winrt.windows.foundation.collections",
    "winrt.windows.devices.bluetooth",
    "winrt.windows.devices.bluetooth.advertisement",
    "winrt.windows.devices.bluetooth.genericattributeprofile",
    "winrt.windows.devices.enumeration",
    "winrt.windows.devices.radios",
    "winrt.windows.storage.streams",
)


def pyinstaller_args(platform: str = sys.platform) -> list[str]:
    """The PyInstaller command line for this build."""
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
        "--add-data", f"{ADDON}{sep}{ADDON_DESTINATION}",
        # bleak picks its Bluetooth backend (and the WinRT projections) at
        # run time, which PyInstaller's static analysis cannot see.
        "--collect-submodules", "bleak",
        "--distpath", str(HERE / "dist"),
        "--workpath", str(HERE / "build"),
        "--specpath", str(HERE / "build"),
    ]  # fmt: skip
    if platform == "win32":
        args += ["--collect-submodules", "winrt"]
        for module in WINRT_HIDDEN_IMPORTS:
            args += ["--hidden-import", module]
    return args


def main() -> int:
    try:
        import PyInstaller.__main__
    except ImportError:
        print('PyInstaller is not installed. Run: pip install -e ".[build]"', file=sys.stderr)
        return 1
    if not (ADDON / "Walka.toc").exists():
        print(f"addon folder not found at {ADDON}", file=sys.stderr)
        return 1
    PyInstaller.__main__.run(pyinstaller_args())
    return 0


if __name__ == "__main__":
    sys.exit(main())
