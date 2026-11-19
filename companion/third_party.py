"""The licences of what is packed into Walka.exe (and Walka on a Mac).

The packaged program is not only Walka's code: it carries Python itself and
the libraries Walka uses - aiohttp, bleak, mss and what those need in turn.
Their licences (MIT, BSD, Apache 2.0, the Python licence) allow that, on one
condition they all share: the copyright notice and the licence text go with
every copy. This script collects them into one text file,

    python third_party.py [output file]

and ``build_exe.py`` packs that file into the program, where ``Walka.exe
licenses`` prints it. It is written from what is installed - the packages
the build is about to pack, in the versions it packs - so it cannot fall
behind them.

What is listed: Walka's dependencies in ``pyproject.toml`` and everything
they depend on, on this kind of computer. Not the tools that only build or
test (PyInstaller's own code, pytest, ruff): those are not in the program.
PyInstaller's bootloader is - it is the small program that starts the rest -
and has a paragraph of its own.
"""

from __future__ import annotations

import sys
import sysconfig
from importlib import metadata
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUTPUT_NAME = "THIRD-PARTY-LICENSES.txt"

# Files of a package that hold its licence or go with it.
_LICENCE_STEMS = ("license", "licence", "copying", "notice", "authors", "copyright")
RULE = "=" * 78

PYTHON_FALLBACK = (
    "Python is distributed under the Python Software Foundation License Version 2.\n"
    "The full text, with the licences of the software incorporated in Python:\n"
    "https://docs.python.org/3/license.html\n"
)
BOOTLOADER = (
    "PyInstaller's bootloader - the small program at the front of the file, which unpacks and starts\n"
    "the rest - is part of PyInstaller (https://pyinstaller.org), Copyright (c) 2010-2026 PyInstaller\n"
    "Development Team, Copyright (c) 2005-2009 Giovanni Bajo, based on previous work under copyright\n"
    "(c) 2002 McMillan Enterprises, Inc. PyInstaller is distributed under the GPL 2.0 or later with a\n"
    "special exception, which allows using it to build and distribute programs under any licence:\n"
    "the bootloader may be distributed with them without their source code having to be offered.\n"
)


_MIT_TERMS = """\
Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE."""

# Some packages are published without their licence file inside. Their
# licence still has to go with the program, so it is kept here: copied from
# the project's own repository (the file named, as it stood in November
# 2026), and used for a package whose name begins as given - but only when
# the package itself brings no licence file.
FALLBACK_LICENCES: dict[str, tuple[str, str]] = {
    # https://github.com/pywinrt/pywinrt/blob/main/LICENSE
    "winrt-": (
        "LICENSE (from the PyWinRT repository)",
        "MIT License\n\n"
        "Copyright (c) Microsoft Corporation. All rights reserved.\n"
        "Copyright (c) 2021-2026 David Lechner <david@pybricks.com>\n\n" + _MIT_TERMS,
    ),
    # https://github.com/ronaldoussoren/pyobjc/blob/master/pyobjc-core/License.txt
    "pyobjc-": (
        "License.txt (from the PyObjC repository)",
        "(This is the MIT license, note that libffi-src is a separate product with its own license)\n\n"
        "Copyright 2002, 2003 - Bill Bumgarner, Ronald Oussoren, Steve Majewski, Lele Gaifax, et.al.\n"
        "Copyright 2003-2025 - Ronald Oussoren\n\n" + _MIT_TERMS,
    ),
}


def _name(text: str) -> str:
    """A project's name as package indexes compare them."""
    return "-".join(part for part in text.lower().replace("_", "-").replace(".", "-").split("-") if part)


def runtime_distributions(pyproject: Path = HERE / "pyproject.toml") -> list[metadata.Distribution]:
    """Walka's dependencies and theirs, as installed here, sorted by name."""
    import tomllib
    from packaging.requirements import Requirement

    roots = tomllib.loads(pyproject.read_text("utf-8"))["project"]["dependencies"]
    found: dict[str, metadata.Distribution] = {}
    waiting = [Requirement(text) for text in roots]
    while waiting:
        requirement = waiting.pop()
        if requirement.marker is not None and not requirement.marker.evaluate({"extra": ""}):
            continue  # for another system, or an optional extra nobody asked for
        key = _name(requirement.name)
        if key in found:
            continue
        # (Not installed although it is needed here: let that stop the build.)
        found[key] = distribution = metadata.distribution(requirement.name)
        waiting += [Requirement(text) for text in distribution.requires or []]
    return [found[key] for key in sorted(found)]


def licence_files(distribution: metadata.Distribution) -> list[tuple[str, str]]:
    """``(file name, text)`` of every licence file the package was installed with."""
    texts: list[tuple[str, str]] = []
    for file in distribution.files or []:
        parts = [part.lower() for part in file.parts]
        in_metadata = any(part.endswith(".dist-info") for part in parts[:-1])
        if not in_metadata:
            continue
        stem = parts[-1].split(".")[0]
        if "licenses" in parts[:-1] or stem.startswith(_LICENCE_STEMS):
            try:
                text = Path(distribution.locate_file(file)).read_text("utf-8", errors="replace")
            except OSError:
                continue
            texts.append((file.name, text.strip("\n")))
    if not texts:
        name = _name(distribution.metadata["Name"]) + "-"
        texts = [entry for prefix, entry in FALLBACK_LICENCES.items() if name.startswith(prefix)]
    return sorted(texts)


def declared_licence(distribution: metadata.Distribution) -> str:
    """What the package's own description says its licence is."""
    meta = distribution.metadata
    expression = meta.get("License-Expression")
    if expression:
        return expression
    classifiers = [c.split("::")[-1].strip() for c in meta.get_all("Classifier") or [] if c.startswith("License ::")]
    if classifiers:
        return ", ".join(classifiers)
    declared = (meta.get("License") or "").strip()
    return declared.splitlines()[0][:120] if declared else "see the package's page"


def home_page(distribution: metadata.Distribution) -> str:
    meta = distribution.metadata
    for entry in meta.get_all("Project-URL") or []:
        label, _, url = entry.partition(",")
        if label.strip().lower() in ("homepage", "home", "repository", "source", "source code", "github"):
            return url.strip()
    return (meta.get("Home-page") or "").strip() or f"https://pypi.org/project/{meta['Name']}/"


def python_licence() -> str:
    """The licence Python itself was installed with."""
    for folder in (sys.base_prefix, sysconfig.get_path("stdlib") or "", str(Path(sysconfig.get_path("stdlib") or "").parent)):
        for name in ("LICENSE.txt", "LICENSE"):
            candidate = Path(folder) / name
            if folder and candidate.is_file():
                return candidate.read_text("utf-8", errors="replace").strip("\n") + "\n"
    return PYTHON_FALLBACK


def build_text() -> str:
    distributions = runtime_distributions()
    lines = [
        "THIRD-PARTY SOFTWARE IN WALKA",
        "",
        "Walka's own code is published under the MIT licence. The packaged program (Walka.exe, or Walka",
        "on a Mac) also contains the software listed here, each under its own licence, reproduced below.",
        "This file is written by the build, from the packages it packs.",
        "",
        "Contents:",
        f"  Python {sys.version.split()[0]}",
        "  PyInstaller's bootloader",
        *[f"  {d.metadata['Name']} {d.version} - {declared_licence(d)}" for d in distributions],
        "",
        RULE,
        f"Python {sys.version.split()[0]}",
        "https://www.python.org",
        RULE,
        "",
        python_licence(),
        RULE,
        "PyInstaller's bootloader",
        RULE,
        "",
        BOOTLOADER,
    ]
    for distribution in distributions:
        lines += [
            RULE,
            f"{distribution.metadata['Name']} {distribution.version}",
            f"Licence: {declared_licence(distribution)}",
            home_page(distribution),
            RULE,
            "",
        ]
        files = licence_files(distribution)
        if not files:
            lines += ["(This package was installed without a licence file: see its page, above.)", ""]
        for name, text in files:
            if len(files) > 1:
                lines += [f"--- {name} ---", ""]
            lines += [text, ""]
    return "\n".join(lines) + "\n"


def write(output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(build_text(), "utf-8")
    return output


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "build" / OUTPUT_NAME
    print(f"wrote {write(target)} ({len(runtime_distributions())} packages)")
