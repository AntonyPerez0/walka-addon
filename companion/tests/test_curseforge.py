"""The addon on CurseForge: tools/curseforge_upload.py and the release job that runs it.

Nothing here reaches CurseForge: the tool is handed an opener of the test's own.
"""

from __future__ import annotations

import io
import json
import re
import urllib.error
from pathlib import Path

import pytest

from test_release import load_tool, workflow

TOKEN = "cf-token-that-must-never-be-printed"
VERSIONS = [
    {"id": 13000, "gameVersionTypeID": 517, "name": "1.15.7"},
    {"id": 13401, "gameVersionTypeID": 600, "name": "1.60.1"},
    {"id": 13402, "gameVersionTypeID": 600, "name": "1.60.2"},
    {"id": 13500, "gameVersionTypeID": 517, "name": "12.1.0"},
]


@pytest.fixture
def tool(repo_root: Path):
    return load_tool(repo_root, "curseforge_upload")


class CurseForge:
    """Stands in for wow.curseforge.com: answers, and keeps every request."""

    def __init__(self, versions=VERSIONS, refuse: int | None = None):
        self.versions, self.refuse, self.requests = versions, refuse, []

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        if request.full_url.endswith("/game/versions"):
            return io.BytesIO(json.dumps(self.versions).encode())
        if self.refuse:
            raise urllib.error.HTTPError(request.full_url, self.refuse, "no", {}, io.BytesIO(b'{"errorMessage": "nope"}'))
        return io.BytesIO(b'{"id": 4242}')

    def upload(self):
        (request,) = [r for r in self.requests if r.get_method() == "POST"]
        boundary = re.search(r"boundary=(\w+)", request.get_header("Content-type")).group(1).encode()
        parts = {}
        for part in request.data.split(b"--" + boundary)[1:-1]:
            head, _, body = part.partition(b"\r\n\r\n")
            name = re.search(rb'name="(\w+)"', head).group(1).decode()
            filename = re.search(rb'filename="([^"]+)"', head)
            parts[name] = (filename.group(1).decode() if filename else None, body[:-2])
        return request, parts


def environ(**more):
    return {"CURSEFORGE_TOKEN": TOKEN, "CURSEFORGE_PROJECT_ID": "123456", "GITHUB_REPOSITORY": "o/walka", **more}


def zipped(tmp_path: Path) -> Path:
    path = tmp_path / "Walka-addon.zip"
    path.write_bytes(b"PK\x03\x04 the addon \r\n--not-a-boundary\r\n" + bytes(range(256)))
    return path


def test_the_game_versions_are_the_tocs_interface_numbers(tool, repo_root: Path):
    assert tool.game_versions_of("## Title: Walka\n## Interface: 16001, 16002\n") == ["1.60.1", "1.60.2"]
    assert tool.game_versions_of("## Interface: 11507\n") == ["1.15.7"]
    assert tool.game_versions_of("## Interface: 120100\n") == ["12.1.0"]
    real = tool.game_versions_of((repo_root / "Walka" / "Walka.toc").read_text("utf-8"))
    assert real and all(re.fullmatch(r"\d+\.\d+\.\d+", name) for name in real)
    for broken in ("## Title: Walka\n", "## Interface: 1.60.1\n", "## Interface: 16001,\n"):
        with pytest.raises(ValueError):
            tool.game_versions_of(broken)


def test_the_published_zip_goes_up_as_it_is_for_the_tocs_versions(tool, tmp_path, capsys):
    curseforge, path = CurseForge(), zipped(tmp_path)
    assert tool.main([str(path), "v0.13.0"], environ(), curseforge) == 0
    request, parts = curseforge.upload()
    assert request.full_url == "https://wow.curseforge.com/api/projects/123456/upload-file"
    assert all(r.get_header("X-api-token") == TOKEN for r in curseforge.requests)
    assert parts["file"] == ("Walka-0.13.0.zip", path.read_bytes()), "the very bytes of the release's zip"
    meta = json.loads(parts["metadata"][1])
    assert meta["displayName"] == "Walka 0.13.0" and meta["releaseType"] == "release"
    toc = (Path(tool.__file__).parent.parent / "Walka" / "Walka.toc").read_text("utf-8")
    wanted = tool.game_versions_of(toc)
    assert meta["gameVersions"] == [v["id"] for name in wanted for v in VERSIONS if v["name"] == name]
    assert "https://github.com/o/walka/releases/tag/v0.13.0" in meta["changelog"]
    assert "file 4242" in capsys.readouterr().out


def test_a_trial_goes_up_as_a_beta(tool, tmp_path):
    curseforge = CurseForge()
    assert tool.main([str(zipped(tmp_path)), "v0.14.0-rc1"], environ(), curseforge) == 0
    assert json.loads(curseforge.upload()[1]["metadata"][1])["releaseType"] == "beta"


def test_not_set_up_uploads_nothing_and_says_so(tool, tmp_path, capsys):
    curseforge = CurseForge()
    assert tool.main([str(zipped(tmp_path)), "v0.13.0"], {}, curseforge) == 0
    assert curseforge.requests == [] and "not set up" in capsys.readouterr().out


@pytest.mark.parametrize(
    "half", [{"CURSEFORGE_TOKEN": ""}, {"CURSEFORGE_PROJECT_ID": ""}, {"CURSEFORGE_PROJECT_ID": "walka"}]
)
def test_half_set_up_is_an_error(tool, tmp_path, capsys, half):
    curseforge = CurseForge()
    assert tool.main([str(zipped(tmp_path)), "v0.13.0"], environ(**half), curseforge) == 1
    assert curseforge.requests == [] and "::error::" in capsys.readouterr().out


@pytest.mark.parametrize("tag", ["0.13.0", "v0.13", "v0.13.0\nv9.9.9", "main"])
def test_only_a_version_tag_is_uploaded(tool, tmp_path, tag):
    curseforge = CurseForge()
    assert tool.main([str(zipped(tmp_path)), tag], environ(), curseforge) == 1
    assert curseforge.requests == []


def test_versions_curseforge_does_not_know(tool, tmp_path, capsys, monkeypatch):
    """One it does not know is left off, with a warning; none known, or a name known twice, uploads nothing."""
    monkeypatch.setattr(tool, "TOC", tmp_path / "Walka.toc")
    tool.TOC.write_text("## Interface: 16001, 16002\n", "utf-8")
    some = CurseForge([v for v in VERSIONS if v["name"] != "1.60.2"])
    assert tool.main([str(zipped(tmp_path)), "v0.13.0"], environ(), some) == 0
    assert "::warning::CurseForge does not know the game version 1.60.2" in capsys.readouterr().out
    assert json.loads(some.upload()[1]["metadata"][1])["gameVersions"] == [13401]
    for versions, said in (([VERSIONS[0]], "knows none"), ([*VERSIONS, {"id": 9, "name": "1.60.1"}], "more than once")):
        curseforge = CurseForge(versions)
        assert tool.main([str(zipped(tmp_path)), "v0.13.0"], environ(), curseforge) == 1
        assert [r for r in curseforge.requests if r.get_method() == "POST"] == []
        assert said in capsys.readouterr().out


def test_a_refusal_fails_the_job_and_never_shows_the_token(tool, tmp_path, capsys):
    assert tool.main([str(zipped(tmp_path)), "v0.13.0"], environ(), CurseForge(refuse=403)) == 1
    out = capsys.readouterr()
    assert "HTTP 403" in out.out and "nope" in out.out
    assert TOKEN not in out.out + out.err


def job_text(text: str, name: str) -> str:
    found = re.search(rf"(?ms)^  {name}:\n(.*?)(?=^  [\w-]+:\n|\Z)", text)
    assert found, name
    return found.group(1)


def test_the_job_uploads_the_published_zip_after_the_release_and_alone_holds_the_token(repo_root: Path):
    text = workflow(repo_root)
    job, release = job_text(text, "curseforge"), job_text(text, "release")
    assert re.search(r"(?m)^    needs: \[release\]$", job) and re.search(r"(?m)^    environment: curseforge$", job)
    (when,) = re.findall(r"(?m)^    if: (.+)$", job)
    assert re.search(rf"(?m)^    if: {re.escape(when)}$", release), "it runs exactly when a release is published"
    assert re.search(r"(?m)^    permissions:\n      contents: read\n    \S", job), "it can read, and change nothing"
    assert 'gh release download "$TAG" --pattern Walka-addon.zip --pattern SHA256SUMS.txt' in job
    assert "sha256sum --check" in job
    assert job.index("sha256sum --check") < job.index("python3 tools/curseforge_upload.py published/Walka-addon.zip")
    # The token is handed to the upload step of this job, and to nothing else.
    assert text.count("secrets.CURSEFORGE_TOKEN") == 1
    assert re.search(
        r"- name: Upload\n        env:\n          CURSEFORGE_TOKEN: \$\{\{ secrets.CURSEFORGE_TOKEN \}\}", job
    )
