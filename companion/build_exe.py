"""Build the stand-alone program with PyInstaller.

    pip install -e ".[build]"
    python build_exe.py

Produces dist/Walka.exe on Windows and dist/Walka.app on a Mac: the
companion, its dashboard page, a copy of the addon (so the dashboard's
"Install addon" button works) and the licences of the libraries packed with
it (third_party.py). On Windows that is one file; on a Mac an application,
which is a folder that macOS shows as one thing. It is built for the kind
of computer it is built on - on a Mac, for that Mac's processor (Apple
silicon or Intel).

It is a windowed program: opened with a double-click it shows Walka's
window (walka/window.py) and no console. Started from a command prompt
with a command - "Walka.exe probe" - it writes there (walka/cli.py).
"""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ADDON = HERE.parent / "Walka"
# The program's icon: drawn by tools/gen_icons.py and kept in the repository.
ICONS = {"win32": HERE / "icons" / "walka.ico", "darwin": HERE / "icons" / "walka.icns"}
# The licences of what is packed into the program (written by third_party.py
# just before the build), and where they go inside it: next to the package's
# own files, where "Walka.exe licenses" looks for them.
LICENCES = HERE / "build" / "THIRD-PARTY-LICENSES.txt"

# Where the addon goes inside the bundle (walka.addon_sync looks for it
# there). Not "Walka" at the top level: file names on Windows - and on a
# Mac - ignore case, so that would be the same folder as the package's own
# "walka", and "Install addon" would copy the dashboard's files into the
# game along with the addon.
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


# What only a Mac build needs: the screen capture's macOS backend and the
# companion's own questions to macOS. "Walka selfcheck" imports them too.
MACOS_HIDDEN_IMPORTS = (
    "mss.darwin",
    "walka.macos",
    "bleak.backends.corebluetooth.scanner",
    "bleak.backends.corebluetooth.client",
)


# Walka's window (walka/window.py): pywebview picks what draws it at run
# time - Edge WebView2 by way of .NET on Windows, WebKit on a Mac.
# "Walka selfcheck" imports these too.
WINDOWS_WINDOW_IMPORTS = ("webview.platforms.winforms", "webview.platforms.edgechromium")
MACOS_WINDOW_IMPORTS = ("webview.platforms.cocoa",)

# What macOS knows the application as (its settings, its permissions).
MAC_BUNDLE_ID = "io.github.antonyperez0.walka"
MAC_APP = HERE / "dist" / "Walka.app"
BLUETOOTH_REASON = (
    "Walka listens to your walking pad over Bluetooth: its speed, distance and steps. It sends the pad nothing."
)


def mac_info(version: str) -> dict:
    """What is added to the application's Info.plist."""
    return {
        "CFBundleDisplayName": "Walka",
        "CFBundleShortVersionString": version,
        "CFBundleVersion": version,
        # macOS ends a program that touches Bluetooth without having said
        # what for. This is the sentence in the box that asks.
        "NSBluetoothAlwaysUsageDescription": BLUETOOTH_REASON,
        "NSBluetoothPeripheralUsageDescription": BLUETOOTH_REASON,  # (the same, for macOS 10.15)
        # The window shows a page from this computer, over plain http.
        "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
        "LSApplicationCategoryType": "public.app-category.healthcare-fitness",
        "NSHumanReadableCopyright": "Walka is free software under the MIT licence.",
    }


def walka_version() -> str:
    import tomllib

    return tomllib.loads((HERE / "pyproject.toml").read_text("utf-8"))["project"]["version"]


def finish_mac_app(app: Path = MAC_APP) -> None:
    """Put Walka's own entries into the application's Info.plist - the
    PyInstaller command line has no way to - and sign it again: the
    signature PyInstaller gave it covered the Info.plist as it was. (Signed
    "ad hoc", as PyInstaller does: with no developer's name, which is all
    an Apple-silicon Mac asks for to run a program at all.)"""
    plist = app / "Contents" / "Info.plist"
    with plist.open("rb") as file:
        info = plistlib.load(file)
    info.update(mac_info(walka_version()))
    with plist.open("wb") as file:
        plistlib.dump(info, file)
    subprocess.run(["codesign", "--force", "--deep", "--sign", "-", str(app)], check=True)
    subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)], check=True)


def pyinstaller_args(platform: str = sys.platform) -> list[str]:
    """The PyInstaller command line for this build."""
    sep = os.pathsep  # ';' on Windows, ':' elsewhere
    args = [
        str(HERE / "walka_launcher.py"),
        "--name", "Walka",
        # No console window. (On a Mac this is also what makes it an
        # application, Walka.app, and not a bare program.)
        "--windowed",
        "--noconfirm",
        "--clean",
        "--paths", str(HERE / "src"),
        "--add-data", f"{HERE / 'src' / 'walka' / 'static'}{sep}walka/static",
        "--add-data", f"{ADDON}{sep}{ADDON_DESTINATION}",
        "--add-data", f"{LICENCES}{sep}walka",
        # bleak picks its Bluetooth backend (WinRT on Windows, CoreBluetooth
        # on a Mac) at run time, which PyInstaller's static analysis cannot see.
        "--collect-submodules", "bleak",
        "--distpath", str(HERE / "dist"),
        "--workpath", str(HERE / "build"),
        "--specpath", str(HERE / "build"),
    ]  # fmt: skip
    if platform == "darwin":
        # An application is a folder anyway: nothing is gained by packing
        # a single file into it that unpacks itself at every start.
        args += ["--onedir", "--osx-bundle-identifier", MAC_BUNDLE_ID]
    else:
        args += ["--onefile"]
    if platform in ICONS:
        args += ["--icon", str(ICONS[platform])]
    if platform == "win32":
        args += ["--collect-submodules", "winrt"]
        for module in WINRT_HIDDEN_IMPORTS + WINDOWS_WINDOW_IMPORTS:
            args += ["--hidden-import", module]
    if platform == "darwin":
        # Only imported where sys.platform says so, and from inside functions.
        for module in MACOS_HIDDEN_IMPORTS + MACOS_WINDOW_IMPORTS:
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
    import third_party

    # (After PyInstaller has been found: its --clean empties build/ only
    # of its own work folders, and this file must be there when it packs.)
    third_party.write(LICENCES)
    PyInstaller.__main__.run(pyinstaller_args())
    if sys.platform == "darwin":
        finish_mac_app()
    return 0


if __name__ == "__main__":
    sys.exit(main())
