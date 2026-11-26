"""Are the two lock files what their own commands write today?

    python tools/check_locks.py

Each lock file says at its top how it is written (``uv pip compile ...``).
That command is run again here - into a copy of the file, so that uv keeps
every version pinned there that still fits ``pyproject.toml`` - and the
result is compared with the file. A version that pyproject.toml has
raised, a package it added or dropped, or a marker that changed then
shows as a difference, and the check fails (exit 1). Needs uv
(companion/requirements-locktool.lock) and the package index.
"""

from __future__ import annotations

import difflib
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

COMPANION = Path(__file__).resolve().parent.parent / "companion"
LOCKS = ("requirements-build.lock", "requirements-dev.lock")


def header_of(text: str) -> str:
    """The comment at the top of a lock file (the tool writes none: --no-header)."""
    lines = text.splitlines(keepends=True)
    end = next((i for i, line in enumerate(lines) if not line.startswith("#")), len(lines))
    return "".join(lines[:end])


def command_for(header: str, lock: str) -> list[str]:
    """The ``uv pip compile`` command the header gives for ``lock``."""
    for line in header.splitlines():
        found = re.match(r"#\s+(uv pip compile .*-o (\S+))\s*$", line)
        if found and found.group(2) == lock:
            return found.group(1).split()
    raise SystemExit(f"::error::{lock} does not say how it is written")


def check(uv: str = "uv") -> list[str]:
    wrong: list[str] = []
    with tempfile.TemporaryDirectory() as folder:
        for lock in LOCKS:
            committed = (COMPANION / lock).read_text("utf-8")
            header = header_of(committed)
            again = Path(folder) / lock
            shutil.copy(COMPANION / lock, again)
            command = command_for(header, lock)
            command[0] = uv
            command[command.index("-o") + 1] = str(again)
            done = subprocess.run(command, cwd=COMPANION, capture_output=True, text=True, timeout=600)
            if done.returncode != 0:
                wrong.append(f"{lock}: the command failed: {done.stderr.strip()[-800:]}")
                continue
            written = header + again.read_text("utf-8")
            if written != committed:
                diff = "".join(
                    difflib.unified_diff(committed.splitlines(True), written.splitlines(True), lock, "written now", n=1)
                )
                wrong.append(f"{lock} is not what its command writes today:\n{diff}")
    return wrong


def main() -> int:
    uv = shutil.which("uv")
    if uv is None:
        print("::error::uv is not installed (pip install --require-hashes -r companion/requirements-locktool.lock)")
        return 2
    wrong = check(uv)
    for text in wrong:
        print(f"::error::{text}")
    if not wrong:
        print("both lock files are what their commands write today")
    return 1 if wrong else 0


if __name__ == "__main__":
    sys.exit(main())
