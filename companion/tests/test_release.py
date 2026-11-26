"""What goes into a release besides Walka's own code: the libraries packed
into the program (their licences, and the exact versions the build takes),
and the workflow that builds and publishes it."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
import tomllib
from pathlib import Path

import pytest


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
    assert {"aiohttp", "bleak", "yarl", "multidict", "frozenlist", "aiosignal", "attrs", "idna"} <= names
    # Not the tools that only build or test: they are not in the program (build_exe.py leaves
    # them out, and tools/check_packed_licences.py checks what IS packed against this list).
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
    assert "Permission is hereby granted, free of charge" in text  # MIT: bleak, attrs
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
    assert {"aiohttp", "bleak"} <= shared
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


def test_a_tag_of_the_version_in_the_code_passes_the_release_jobs_own_look(repo_root: Path):
    """A release is published only if its tag is the version that stands in
    three files - each in a line of its own, which the release job looks
    for whole. That look is made here, on the same three lines: a version
    changed in two files and not in the third shows on every push, and not
    only when the tag is there and nothing is published. (And a trial
    version, with "-", goes out as a pre-release: never "the latest".)"""
    text = workflow(repo_root)
    step = text[text.index("- name: The tag is the version written in the code") :]
    step = step[: step.index("\n      - ")]
    (shape,) = re.findall(r"shape='(\^v\S+\$)'", step)
    looks = re.findall(r'grep -Fqx "((?:[^"\\]|\\")*)" (\S+) \|\| fail', step)
    assert [file for _line, file in looks] == [
        "companion/pyproject.toml",
        "companion/src/walka/__init__.py",
        "Walka/Walka.toc",
    ]
    version = tomllib.loads((repo_root / "companion" / "pyproject.toml").read_text("utf-8"))["project"]["version"]
    assert re.fullmatch(shape, f"v{version}"), version
    for line, file in looks:
        assert "$version" in line
        wanted = line.replace('\\"', '"').replace("$version", version)
        assert (repo_root / file).read_text("utf-8").splitlines().count(wanted) == 1, f"{file}: no line {wanted!r}"
    from walka import __version__

    assert __version__ == version
    assert 'case "$TAG" in *-*) pre="--prerelease" ;; esac' in text


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


# ---- the release job's own looks, run as the job runs them -------------------------------------


def release_step(repo_root: Path, name: str) -> str:
    """The script of a step of the release job, as bash gets it."""
    text = workflow(repo_root)
    job = text[text.index("\n  release:") :]
    step = job[job.index(f"- name: {name}") :]
    body = step[step.index("run: |\n") + len("run: |\n") :]
    end = re.search(r"\n      - |\n  \w|\Z", body)
    return textwrap.dedent(body[: end.start()])


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def checkout(repo_root: Path, tmp_path: Path):
    """A checkout as the release job has it: an origin with the main line,
    and a commit on another branch that was never merged."""
    if sys.platform == "win32" or shutil.which("bash") is None or shutil.which("git") is None:
        pytest.skip("the release job runs on Linux: needs bash and git (on Windows, bash may be WSL's)")
    origin = tmp_path / "origin.git"
    work = tmp_path / "work"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    git(tmp_path, "init", "-q", "-b", "main", str(work))
    for relative in ("companion/pyproject.toml", "companion/src/walka/__init__.py", "Walka/Walka.toc"):
        (work / relative).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(repo_root / relative, work / relative)
    git(work, "add", "-A")
    git(work, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "main")
    main = git(work, "rev-parse", "HEAD")
    git(work, "remote", "add", "origin", str(origin))
    git(work, "push", "-q", "origin", "main")
    git(work, "checkout", "-q", "-b", "side")
    git(work, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "side")
    side = git(work, "rev-parse", "HEAD")
    git(work, "push", "-q", "origin", "side")
    return work, main, side


def run_step(script: str, cwd: Path, **env: str) -> subprocess.CompletedProcess:
    full = {**os.environ, "GITHUB_EVENT_NAME": "push", "GITHUB_REF_NAME": "v0", "DEFAULT_BRANCH": "main", **env}
    return subprocess.run(["bash", "-eo", "pipefail", "-c", script], cwd=cwd, env=full, capture_output=True,
                          text=True, timeout=60)  # fmt: skip


def code_version(repo_root: Path) -> str:
    return tomllib.loads((repo_root / "companion" / "pyproject.toml").read_text("utf-8"))["project"]["version"]


def test_a_tag_is_published_only_from_a_commit_on_the_main_line(repo_root: Path, checkout):
    work, main, side = checkout
    script = release_step(repo_root, "The tag is the version written in the code")
    tag = f"v{code_version(repo_root)}"
    assert run_step(script, work, TAG=tag, GITHUB_SHA=main).returncode == 0
    # A tag pushed on a commit of another branch: refused.
    refused = run_step(script, work, TAG=tag, GITHUB_SHA=side)
    assert refused.returncode != 0 and "is not on the branch 'main'" in refused.stdout


@pytest.mark.parametrize("value", ["{tag}\n{tag}", "{tag}\nx", "x\n{tag}", "{tag} ", "v1.2", "{tag}\n"])
def test_a_tag_of_more_than_one_line_or_another_shape_is_refused(repo_root: Path, checkout, value: str):
    work, main, _side = checkout
    tag = value.format(tag=f"v{code_version(repo_root)}")
    result = run_step(release_step(repo_root, "The tag is the version written in the code"), work, TAG=tag,
                      GITHUB_SHA=main)  # fmt: skip
    assert result.returncode != 0 and "is not a version like v0.1.0" in result.stdout


FAKE_GH = """#!/usr/bin/env bash
echo "gh $*" >> "$GH_LOG"
if [ "$1 $2" = "release view" ]; then
  [ -n "$GH_FOUND" ] || exit 1
  echo "$GH_FOUND"
fi
"""


@pytest.mark.parametrize("made_from", ["main", "side", "branch"])
def test_a_leftover_draft_is_finished_only_by_a_run_of_its_own_commit(repo_root: Path, checkout, tmp_path, made_from):
    work, main, side = checkout
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "gh").write_text(FAKE_GH)
    (bin_dir / "gh").chmod(0o755)
    (work / "assets").mkdir()
    for name in ("Walka.exe", "Walka-mac-apple-silicon.zip", "Walka-mac-intel.zip", "Walka-addon.zip",
                 "virus-scan.json", "SHA256SUMS.txt"):  # fmt: skip
        (work / "assets" / name).write_text("x")
    target = {"main": main, "side": side, "branch": "main"}[made_from]
    log = tmp_path / "gh.log"
    result = run_step(release_step(repo_root, "Publish"), work, TAG="v9.9.9", GITHUB_SHA=main, GH_REPO="o/r",
                      GH_LOG=str(log), GH_FOUND=f"true {target}", PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}")  # fmt: skip
    calls = log.read_text().splitlines()
    if made_from == "main":
        assert result.returncode == 0, result.stdout + result.stderr
        assert any(call.startswith("gh release upload v9.9.9") for call in calls)
        assert "gh release edit v9.9.9 --draft=false" in calls
    else:
        assert result.returncode != 0 and "Delete the draft" in result.stdout
        assert not any(call.startswith(("gh release upload", "gh release edit")) for call in calls)


def test_a_published_release_is_left_alone(repo_root: Path, checkout, tmp_path):
    work, main, _side = checkout
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "gh").write_text(FAKE_GH)
    (bin_dir / "gh").chmod(0o755)
    (work / "assets").mkdir()
    for name in ("Walka.exe", "Walka-mac-apple-silicon.zip", "Walka-mac-intel.zip", "Walka-addon.zip",
                 "virus-scan.json", "SHA256SUMS.txt"):  # fmt: skip
        (work / "assets" / name).write_text("x")
    log = tmp_path / "gh.log"
    result = run_step(release_step(repo_root, "Publish"), work, TAG="v9.9.9", GITHUB_SHA=main, GH_REPO="o/r",
                      GH_LOG=str(log), GH_FOUND=f"false {main}", PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}")  # fmt: skip
    assert result.returncode != 0 and "already published" in result.stdout
    assert log.read_text().splitlines() == [line for line in log.read_text().splitlines() if "view" in line]


def test_the_release_job_waits_for_a_reviewer_and_runs_once_per_version(repo_root: Path):
    text = workflow(repo_root)
    job = text[text.index("\n  release:") :]
    head = job[: job.index("    steps:")]
    assert "\n    environment: release\n" in head
    assert "\n    concurrency:\n      group: release-${{" in head and "cancel-in-progress: false" in head
    assert "fetch-depth: 0" in job[: job.index("- name: The tag is the version")]


# ---- the Python packed into the program ---------------------------------------------------------


def test_the_build_refuses_a_program_with_an_older_python_than_expected(repo_root: Path):
    check = load_tool(repo_root, "check_bundled_python")
    line = "selfcheck OK: 17 modules, ...; Python {}, OpenSSL 3.5.4 30 Sep 2025"
    assert check.problem(line.format("3.14.2"), (3, 14, 2)) == ""
    assert "older than 3.14.0" in check.problem(line.format("3.12.10"), (3, 12, 10))
    assert "built with 3.14.2" in check.problem(line.format("3.14.1"), (3, 14, 2))
    assert "did not say" in check.problem("selfcheck OK", (3, 14, 2))


def test_the_builds_and_tests_run_on_the_python_the_check_expects(repo_root: Path):
    check = load_tool(repo_root, "check_bundled_python")
    versions = re.findall(r'python-version:\s*"([\d.]+)"', workflow(repo_root))
    assert len(versions) >= 3 and set(versions) == {".".join(map(str, check.MINIMUM[:2]))}, versions
    for lock in ("requirements-build.lock", "requirements-dev.lock"):
        header = (repo_root / "companion" / lock).read_text("utf-8")[:2000]
        assert header.count(f"--python-version {versions[0]}") == 2, lock
    text = workflow(repo_root)
    assert text.count("python tools/check_bundled_python.py") == 2, "the Windows and both Mac builds"


def load_tool(repo_root: Path, name: str):
    spec = importlib.util.spec_from_file_location(f"walka_tool_{name}", repo_root / "tools" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---- what the words say goes over the internet --------------------------------------------------


def test_nobody_is_told_that_walka_asks_nobody(repo_root: Path):
    """Walka asks GitHub for the newest release (at every start, then once a
    day): "sends nothing and asks nobody" is true of the Walka account part
    only, and is said of that only. And "once a day" never stands alone."""
    texts = {
        path: path.read_text("utf-8")
        for path in [*repo_root.glob("*.md"), *(repo_root / "docs").glob("*.md"), repo_root / ".github/workflows/ci.yml"]
    }
    for path, text in texts.items():
        flat = " ".join(text.split())
        for found in re.finditer(r"sends nothing and asks nobody", flat):
            before = flat[max(0, found.start() - 80) : found.start()]
            assert "Walka account part" in before, f"{path.name}: {before!r}"
        for found in re.finditer(r"once a day", flat):
            around = flat[max(0, found.start() - 60) : found.end()]
            if "GitHub" in flat[max(0, found.start() - 200) : found.end() + 100]:
                assert "every start" in around, f"{path.name}: {around!r}"
    development = " ".join(texts[repo_root / "docs" / "DEVELOPMENT.md"].split())
    assert "the only part that asks anything over the internet" not in development


def test_the_walka_account_page_says_what_stopping_and_signing_out_do(repo_root: Path):
    """Neither cuts short a request already started, and a sign-out tells
    walka.dev only with a sign-in in hand: the page says both, plainly."""
    text = " ".join((repo_root / "docs" / "WALKA-ACCOUNT.md").read_text("utf-8").split())
    assert "takes effect at once" not in text
    assert "A request that was already on its way is not called back" in text
    assert "Signing out in Walka does not always end the sign-in at walka.dev" in text


def test_the_build_refuses_a_packed_library_without_its_licence(repo_root: Path):
    check = load_tool(repo_root, "check_packed_licences")
    text = (
        "Contents of 'Walka' (PKG/CArchive):\n struct\n walka_launcher\n aiohttp/_http_parser.so\n"
        " packaging-26.4.dist-info/RECORD\n walka/static/app.js\n PYZ.pyz\nContents of 'PYZ.pyz' (PYZ):\n"
        " aiohttp.client\n setuptools.dist\n json.decoder\n walka.app\n"
    )
    names = {n for n in check.top_level_names(text) if not any(n.startswith(o) for o in check.OURS)}
    names -= set(sys.stdlib_module_names)
    packed = check.packed_distributions(names)
    assert {"aiohttp", "packaging", "setuptools"} <= set(packed) and "json" not in packed
    wrong = check.problems(packed, check.listed_distributions())
    assert [line.split()[0] for line in wrong] == ["packaging", "setuptools"]
    assert check.problems({"aiohttp": {"aiohttp"}}, check.listed_distributions()) == []
    text = workflow(repo_root)
    assert text.count("python tools/check_packed_licences.py") == 2, "the Windows and both Mac builds"
    build = (repo_root / "companion" / "build_exe.py").read_text("utf-8")
    for module in ("setuptools", "pkg_resources", "packaging"):
        assert f'"--exclude-module", "{module}"' in build


def test_the_lock_files_are_written_again_and_compared_by_the_build(repo_root: Path):
    text = workflow(repo_root)
    step = text[text.index("- name: The lock files are up to date") :]
    step = step[: step.index("\n      - ")]
    assert "python tools/check_locks.py" in step and "requirements-locktool.lock" in step
    assert "--require-hashes -r companion/requirements-locktool.lock" in step
    tool = locked(repo_root / "companion" / "requirements-locktool.lock")
    assert set(tool) == {"uv"}
    check = load_tool(repo_root, "check_locks")
    for lock in check.LOCKS:
        header = check.header_of((repo_root / "companion" / lock).read_text("utf-8"))
        command = check.command_for(header, lock)
        assert command[:3] == ["uv", "pip", "compile"] and "--generate-hashes" in command


def test_the_pins_are_watched_for_advisories(repo_root: Path):
    check = load_tool(repo_root, "check_advisories")
    pinned = check.all_pins()
    assert ("aiohttp", locked_version(repo_root, "aiohttp")) in pinned and ("uv", "0.13.10") in pinned

    class Answer:
        def __init__(self, body):
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return self.body

    asked = []

    def opener(request, timeout):
        asked.append(json.loads(request.data))
        return Answer(json.dumps({"results": [{"vulns": [{"id": "GHSA-x"}]}, {}]}).encode())

    assert check.ask([("aiohttp", "3.9.0"), ("idna", "3.10")], opener) == [["GHSA-x"], []]
    (query,) = asked
    assert query == {"queries": [{"package": {"name": "aiohttp", "ecosystem": "PyPI"}, "version": "3.9.0"},
                                 {"package": {"name": "idna", "ecosystem": "PyPI"}, "version": "3.10"}]}  # fmt: skip
    text = (repo_root / ".github" / "workflows" / "advisories.yml").read_text("utf-8")
    assert "schedule:" in text and "python tools/check_advisories.py" in text
    for action, rest in re.findall(r"(?m)^\s*-?\s*uses:\s*(\S+)(.*)$", text):
        assert re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", action) and re.search(r"#\s*v\d+\.\d+\.\d+", rest)


def locked_version(repo_root: Path, name: str) -> str:
    text = (repo_root / "companion" / "requirements-build.lock").read_text("utf-8")
    return re.search(rf"(?m)^{name}==(\S+)", text).group(1)


def test_text_is_checked_out_with_lf_everywhere(repo_root: Path):
    """The addon's zip is packed from a Windows checkout: without this its
    files had Windows line endings, and the Mac bundle's did not."""
    if shutil.which("git") is None or not (repo_root / ".git").exists():
        pytest.skip("needs git and the repository")
    files = ["Walka/Walka.lua", "Walka/Walka.toc", "companion/src/walka/static/app.js", "docs/demo/index.html",
             "companion/icons/walka.ico", "docs/images/demo.webp"]  # fmt: skip
    said = subprocess.run(["git", "check-attr", "eol", "binary", "--", *files], cwd=repo_root, capture_output=True,
                          text=True, check=True).stdout  # fmt: skip
    attrs = {}
    for line in said.splitlines():
        path, name, value = line.split(": ")
        attrs.setdefault(path, {})[name] = value
    for text in files[:4]:
        assert attrs[text]["eol"] == "lf", text
    for binary in files[4:]:
        assert attrs[binary]["binary"] == "set", binary
