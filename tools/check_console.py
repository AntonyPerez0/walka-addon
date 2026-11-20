"""Does the packaged Walka.exe write to the command prompt it is started from?

    python tools/check_console.py companion/dist/Walka.exe

Walka.exe is a windowed program, and Windows gives a windowed program no
console - not even the one of the command prompt it was typed into. Walka
takes that console itself (walka/cli.py), because its commands - "Walka.exe
probe", "Walka.exe beacon" - are all printing. This checks that it does, the
only way that can be checked: by looking at what is on the console.

It opens a command prompt of its own (one with a real console; the build
machine's steps have none), has it run "Walka.exe --version", and then
reads the text off that console's screen. Windows only; run by the build
(.github/workflows/ci.yml).
"""

from __future__ import annotations

import ctypes
import subprocess
import sys
import time
from ctypes import wintypes

CREATE_NEW_CONSOLE = 0x00000010
GENERIC_READ, GENERIC_WRITE = 0x80000000, 0x40000000
FILE_SHARE_READ, FILE_SHARE_WRITE = 0x1, 0x2
OPEN_EXISTING = 3
ROWS, COLUMNS = 30, 120
STAYS_OPEN_S = 40


def screen_of(kernel32, pid: int) -> str | None:
    """What is on the console of process ``pid`` - ``None`` if it cannot be read (yet)."""
    kernel32.FreeConsole()
    if not kernel32.AttachConsole(pid):
        return None
    try:
        handle = kernel32.CreateFileW(
            "CONOUT$", GENERIC_READ | GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE, None, OPEN_EXISTING, 0, None
        )
        if not handle or handle == wintypes.HANDLE(-1).value:
            return None
        try:
            buffer = ctypes.create_unicode_buffer(ROWS * COLUMNS)
            read = wintypes.DWORD(0)
            ok = kernel32.ReadConsoleOutputCharacterW(
                handle, buffer, ROWS * COLUMNS, wintypes._COORD(0, 0), ctypes.byref(read)
            )
            return buffer[: read.value] if ok else None
        finally:
            kernel32.CloseHandle(handle)
    finally:
        kernel32.FreeConsole()


def main() -> int:
    if sys.platform != "win32" or len(sys.argv) != 2:
        print(__doc__)
        return 2
    exe = sys.argv[1]
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.AttachConsole.argtypes = [wintypes.DWORD]
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]  # fmt: skip
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.ReadConsoleOutputCharacterW.argtypes = [
        wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes._COORD, ctypes.POINTER(wintypes.DWORD),
    ]  # fmt: skip
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    # A command prompt does not wait for a windowed program: the second
    # command keeps its console there to be read.
    command = f'cmd.exe /d /c ""{exe}" --version & ping -n {STAYS_OPEN_S} 127.0.0.1 > NUL"'
    prompt = subprocess.Popen(command, creationflags=CREATE_NEW_CONSOLE)
    seen = None
    try:
        deadline = time.monotonic() + STAYS_OPEN_S - 5
        while time.monotonic() < deadline:
            time.sleep(1)
            seen = screen_of(kernel32, prompt.pid)
            if seen and "walka " in seen:
                break
    finally:
        prompt.kill()
    # (The screen comes as one long row of characters, mostly spaces.)
    print(f"on the command prompt's screen: {' '.join((seen or '').split())!r}")
    if seen is None:
        print("The command prompt's console could not be read.")
        return 1
    if "walka " not in seen:
        print("Walka.exe did not write its version to the command prompt it was started from.")
        return 1
    print("Walka.exe writes to the command prompt it is started from.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
