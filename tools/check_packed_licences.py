"""Does every library packed into the program have its licence in it?

    python tools/check_packed_licences.py companion/dist/Walka.exe
    python tools/check_packed_licences.py companion/dist/Walka.app/Contents/MacOS/Walka

Run with the Python the program was built with (the build's own): what is
packed is looked up among the packages installed there. The program's
contents are listed by PyInstaller's own archive viewer; every module and
file in them is put down to the package it came from. A package that is
packed but is not in the list the licences are written from
(companion/third_party.py: what Walka runs on, by pyproject.toml) fails
the check - as setuptools and packaging once were, packed with no
licence text. Exits 1 then.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from importlib import metadata
from pathlib import Path

HERE = Path(__file__).resolve().parent
COMPANION = HERE.parent / "companion"

# Packed, and not a package of anybody's: Walka itself, PyInstaller's own
# loader, and what the program carries beside its code.
OURS = {"walka", "walka_launcher", "_addon", "base_library", "pyi_rth", "pyimod", "pyiboot", "struct", "PYZ"}


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def listing(program: Path) -> str:
    done = subprocess.run(
        [sys.executable, "-m", "PyInstaller.utils.cliutils.archive_viewer", "-r", "-b", str(program)],
        capture_output=True,
        text=True,
        timeout=300,
    )
    if done.returncode != 0:
        raise SystemExit(f"::error::the program's contents could not be listed: {done.stderr.strip()[-500:]}")
    return done.stdout


def top_level_names(text: str) -> set[str]:
    """The first part of every module and file that is packed."""
    names: set[str] = set()
    for line in text.splitlines():
        entry = line.strip()
        if not entry or entry.endswith(":") or entry.startswith(("Options in", "Contents of", "pyi-")):
            continue
        first = re.split(r"[/\\]", entry, maxsplit=1)[0]
        if not first.endswith(".dist-info"):  # ("attrs-26.1.0.dist-info": a package's own name for itself)
            first = first.split(".", 1)[0]
        if first:
            names.add(first)
    return names


def packed_distributions(names: set[str]) -> dict[str, set[str]]:
    """Package name -> the top-level names of it that are packed."""
    owners = metadata.packages_distributions()
    found: dict[str, set[str]] = {}
    for name in names:
        dist_info = re.fullmatch(r"(.+?)-\d[^-]*\.dist-info", name)
        if dist_info:
            found.setdefault(canonical(dist_info.group(1)), set()).add(name)
            continue
        for owner in owners.get(name, ()):
            found.setdefault(canonical(owner), set()).add(name)
    return found


def listed_distributions() -> set[str]:
    spec = importlib.util.spec_from_file_location("walka_third_party", COMPANION / "third_party.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return {canonical(d.metadata["Name"]) for d in module.runtime_distributions()}


def problems(packed: dict[str, set[str]], listed: set[str]) -> list[str]:
    return [
        f"{name} is packed ({', '.join(sorted(parts))}) and its licence is not in the program"
        for name, parts in sorted(packed.items())
        if name not in listed and name != "walka"
    ]


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__.strip().splitlines()[2].strip())
        return 2
    names = {name for name in top_level_names(listing(Path(argv[0]))) if not any(name.startswith(o) for o in OURS)}
    names -= set(sys.stdlib_module_names)
    packed = packed_distributions(names)
    wrong = problems(packed, listed_distributions())
    for line in wrong:
        print(f"::error::{line}")
    if wrong:
        return 1
    print(f"every package packed into the program has its licence in it: {', '.join(sorted(packed))}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
