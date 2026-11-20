"""What goes into a release besides Walka's own code: the libraries packed
into the program (their licences, and the exact versions the build takes),
and the workflow that builds and publishes it."""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest
import tomllib


def load(repo_root: Path, name: str):
    spec = importlib.util.spec_from_file_location(f"walka_{name}", repo_root / "companion" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


# ---- the licences of the packed libraries ------------------------------------------------------


def test_every_library_the_program_runs_on_is_listed_with_its_licence(repo_root: Path):
    third_party = load(repo_root, "third_party")
    distributions = third_party.runtime_distributions()
    names = {canonical(d.metadata["Name"]) for d in distributions}
    # What Walka asks for by name, and what those bring along.
    assert {"aiohttp", "bleak", "mss", "yarl", "multidict", "frozenlist", "aiosignal", "attrs", "idna"} <= names
    # Not the tools that only build or test: they are not in the program.
    assert not names & {"pytest", "ruff", "lupa", "pyinstaller", "pip", "setuptools", "packaging"}
    # Walka's window, where the program is packaged with one.
    assert ("pywebview" in names) == (sys.platform in ("win32", "darwin"))
    for distribution in distributions:
        files = third_party.licence_files(distribution)
        assert files, f"{distribution.metadata['Name']} has no licence file to pass on"
        assert all(len(text) > 100 for _name, text in files)
        assert third_party.declared_licence(distribution)
        assert third_party.home_page(distribution).startswith("http")


def test_the_licences_file_holds_the_texts_not_only_the_names(repo_root: Path, tmp_path: Path):
    third_party = load(repo_root, "third_party")
    text = third_party.write(tmp_path / "out" / "licences.txt").read_text("utf-8")
    assert text.startswith("THIRD-PARTY SOFTWARE IN WALKA")
    assert "Apache License" in text and "Version 2.0, January 2004" in text  # aiohttp and its family
    assert "Permission is hereby granted, free of charge" in text  # MIT: bleak, mss, attrs
    assert "PYTHON SOFTWARE FOUNDATION LICENSE" in text.upper() or "docs.python.org/3/license" in text
    assert "PyInstaller's bootloader" in text and "special exception" in text
    for distribution in third_party.runtime_distributions():
        assert f"{distribution.metadata['Name']} {distribution.version}" in text


# ---- the versions the build takes -----------------------------------------------------------------


def locked(path: Path) -> dict[str, list[str]]:
    """``{package: its hashes}`` of a lock file."""
    packages: dict[str, list[str]] = {}
    current = None
    for line in path.read_text("utf-8").splitlines():
        pinned = re.match(r"^([A-Za-z0-9_.-]+)==\S+", line)
        if pinned:
            current = canonical(pinned.group(1))
            packages[current] = []
        elif current and "--hash=sha256:" in line:
            packages[current].append(line.strip())
    return packages


@pytest.mark.parametrize("extra", ["build", "dev"])
def test_the_lock_files_pin_everything_with_a_hash(repo_root: Path, extra: str):
    """A release is built from these exact files of these exact versions:
    a library that is taken over tomorrow does not get into Walka.exe by
    being the newest."""
    project = tomllib.loads((repo_root / "companion" / "pyproject.toml").read_text("utf-8"))["project"]
    wanted = project["dependencies"] + project["optional-dependencies"][extra]
    packages = locked(repo_root / "companion" / f"requirements-{extra}.lock")
    for requirement in wanted:
        name = canonical(re.match(r"[A-Za-z0-9_.-]+", requirement).group(0))
        assert name in packages, f"{name} is in pyproject.toml but not in requirements-{extra}.lock: lock it again"
    assert len(packages) >= len(wanted)
    for name, hashes in packages.items():
        assert hashes, f"{name} is pinned without a hash"


def test_the_two_lock_files_agree_on_what_the_program_runs_on(repo_root: Path):
    """What is tested is what is packed."""
    lines = {}
    for extra in ("build", "dev"):
        text = (repo_root / "companion" / f"requirements-{extra}.lock").read_text("utf-8")
        lines[extra] = {canonical(m.group(1)): m.group(2) for m in re.finditer(r"(?m)^([A-Za-z0-9_.-]+)==(\S+)", text)}
    shared = set(lines["build"]) & set(lines["dev"])
    assert {"aiohttp", "bleak", "mss"} <= shared
    assert {name: lines["build"][name] for name in shared} == {name: lines["dev"][name] for name in shared}


# ---- the workflow ---------------------------------------------------------------------------------


def workflow(repo_root: Path) -> str:
    return (repo_root / ".github" / "workflows" / "ci.yml").read_text("utf-8")


def test_every_action_is_pinned_to_a_commit(repo_root: Path):
    """A tag like v4 can be moved to other code by whoever holds the
    action's repository; a commit cannot."""
    uses = re.findall(r"(?m)^\s*-?\s*uses:\s*(\S+)(.*)$", workflow(repo_root))
    assert len(uses) >= 10
    for action, rest in uses:
        assert re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", action), f"{action} is not pinned to a commit"
        assert re.search(r"#\s*v\d+\.\d+\.\d+", rest), f"{action}: say which version that commit is"


def test_the_builds_install_from_the_lock_files_only(repo_root: Path):
    text = workflow(repo_root)
    installs = re.findall(r"(?m)^\s*run:\s*(python -m pip install.*)$", text)
    assert len(installs) == 3
    for line in installs:
        assert "--require-hashes" in line and "requirements-" in line, line
        # What pip has to build itself (proxy_tools is published as source
        # only) is built with the setuptools of the same lock file.
        (lock,) = set(re.findall(r"companion/requirements-\w+\.lock", line))
        assert f"--build-constraint {lock} -r {lock}" in line, line
        assert "setuptools" in locked(repo_root / lock)
    assert 'pip install -e' not in text, "an editable install builds Walka with tools nobody pinned"


def test_a_published_release_is_never_changed(repo_root: Path):
    text = workflow(repo_root)
    publish = text[text.index("- name: Publish") :]
    assert "isDraft" in publish and "already published" in publish
    # Replacing files is for a draft that a failed first try left behind, and nothing else.
    assert publish.count("--clobber") == 1
    assert "SHA256SUMS.txt" in publish and "attest-build-provenance" in text


def test_a_package_published_without_its_licence_file_still_gets_its_licence(repo_root: Path):
    """The Windows Runtime packages and two of the Mac ones come without a
    licence file inside. Their licence goes with the program all the same."""
    third_party = load(repo_root, "third_party")

    class Bare:
        files: list = []

        def __init__(self, name: str) -> None:
            self.metadata = {"Name": name}

    for name, holder in (
        ("winrt-runtime", "David Lechner"),
        ("winrt-Windows.Devices.Bluetooth.GenericAttributeProfile", "Microsoft Corporation"),
        ("pyobjc-core", "Ronald Oussoren"),
        ("pyobjc-framework-libdispatch", "Ronald Oussoren"),
    ):
        ((_file, text),) = third_party.licence_files(Bare(name))
        assert holder in text and "Permission is hereby granted, free of charge" in text
        assert text.rstrip().endswith("SOFTWARE.")
    assert third_party.licence_files(Bare("some-other-package")) == []
    # proxy_tools (pywebview needs it) is published as source without its licence file.
    ((_file, text),) = third_party.licence_files(Bare("proxy_tools"))
    assert "Jonathan Tushman" in text and "Redistributions in binary form must reproduce" in text


def test_what_a_package_carries_goes_with_its_licence_too(repo_root: Path):
    """pywebview brings Microsoft's WebView2 libraries, which are packed
    into Walka.exe - and into nothing else."""
    third_party = load(repo_root, "third_party")

    class Pywebview:
        files: list = []
        metadata = {"Name": "pywebview"}

    ((name, text),) = third_party.licence_files(Pywebview(), "win32")
    assert "WebView2" in name and "Copyright (C) Microsoft Corporation" in text
    assert "Redistributions in binary form must reproduce" in text
    assert third_party.licence_files(Pywebview(), "darwin") == []
