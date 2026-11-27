"""Upload the published Walka-addon.zip to the addon's CurseForge project.

    python tools/curseforge_upload.py <Walka-addon.zip> <tag>

Run by the release workflow after a release is published on GitHub, with
the zip taken from that release (and checked against its SHA256SUMS.txt):
CurseForge gets the very file that was scanned and attested. Reads
CURSEFORGE_TOKEN (an API token of CurseForge's, from
https://legacy.curseforge.com/account/api-tokens) and CURSEFORGE_PROJECT_ID
(the number on the project's page) from the environment. With neither set,
it says so and uploads nothing. The game versions are those of the
"## Interface:" line of Walka/Walka.toc (16001 is 1.60.1). Needs nothing
but Python.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

API = "https://wow.curseforge.com/api"
TOC = Path(__file__).resolve().parent.parent / "Walka" / "Walka.toc"


def game_versions_of(toc: str) -> list[str]:
    """The game versions the addon is for, from its .toc: 16001 -> "1.60.1"."""
    found = re.search(r"(?m)^## Interface:\s*(.+?)\s*$", toc)
    if not found:
        raise ValueError("Walka.toc has no ## Interface: line")
    names = []
    for number in re.split(r"\s*,\s*", found.group(1)):
        if not re.fullmatch(r"\d{5,6}", number):
            raise ValueError(f"Walka.toc: {number!r} is not an interface number")
        value = int(number)
        names.append(f"{value // 10000}.{value // 100 % 100}.{value % 100}")
    return names


def version_ids(wanted: list[str], known: list[dict]) -> tuple[list[int], list[str]]:
    """CurseForge's ids of the versions wanted, and the names it does not know.
    A name CurseForge knows more than once is refused: the upload would guess."""
    ids, missing = [], []
    for name in wanted:
        matches = [version["id"] for version in known if version.get("name") == name]
        if len(matches) > 1:
            raise ValueError(f"CurseForge knows the game version {name} more than once: {matches}")
        if matches:
            ids.append(matches[0])
        else:
            missing.append(name)
    return ids, missing


def metadata(tag: str, ids: list[int], repository: str) -> dict:
    version = tag.removeprefix("v")
    return {
        "displayName": f"Walka {version}",
        "gameVersions": ids,
        # A trial (v0.2.0-rc1) is published on GitHub as a pre-release: here a beta.
        "releaseType": "beta" if "-" in version else "release",
        "changelogType": "markdown",
        "changelog": (
            f"Walka {version}. What is new, and the Walka app for Windows and Mac that records your walks "
            f"on a walking pad: https://github.com/{repository}/releases/tag/{tag}"
        ),
    }


def multipart(fields: dict[str, str], name: str, filename: str, data: bytes) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    parts = []
    for key, value in fields.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n'
            f"Content-Type: application/json\r\n\r\n{value}\r\n".encode()
        )
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
        "Content-Type: application/zip\r\n\r\n".encode()
        + data
        + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def upload(zip_path: Path, tag: str, *, token: str, project: str, repository: str, toc: str, opener=urllib.request.urlopen) -> int:
    """Uploads the zip; gives CurseForge's id of the new file."""
    headers = {"X-Api-Token": token, "User-Agent": "walka-release"}
    with opener(urllib.request.Request(f"{API}/game/versions", headers=headers), timeout=60) as answer:
        known = json.loads(answer.read())
    ids, missing = version_ids(game_versions_of(toc), known)
    for name in missing:
        print(f"::warning::CurseForge does not know the game version {name} (from Walka.toc): the file is not marked for it")
    if not ids:
        raise ValueError("CurseForge knows none of the game versions in Walka.toc: nothing was uploaded")
    meta = metadata(tag, ids, repository)
    body, content_type = multipart(
        {"metadata": json.dumps(meta)}, "file", f"Walka-{tag.removeprefix('v')}.zip", zip_path.read_bytes()
    )
    request = urllib.request.Request(
        f"{API}/projects/{project}/upload-file", body, {**headers, "Content-Type": content_type}, method="POST"
    )
    with opener(request, timeout=300) as answer:
        file_id = json.loads(answer.read())["id"]
    print(f"uploaded {zip_path.name} to CurseForge project {project} as {meta['displayName']} (file {file_id})")
    return file_id


def main(argv: list[str], environ=os.environ, opener=urllib.request.urlopen) -> int:
    if len(argv) != 2:
        print(__doc__.strip().splitlines()[2].strip())
        return 2
    zip_path, tag = Path(argv[0]), argv[1]
    if not re.fullmatch(r"v\d+\.\d+\.\d+(-[0-9A-Za-z.]+)?", tag):
        print(f"::error::{tag!r} is not a version tag")
        return 1
    token, project = environ.get("CURSEFORGE_TOKEN", ""), environ.get("CURSEFORGE_PROJECT_ID", "")
    if not token and not project:
        print("::warning::CurseForge is not set up (no CURSEFORGE_TOKEN, no CURSEFORGE_PROJECT_ID): nothing was uploaded")
        return 0
    if not token or not re.fullmatch(r"\d+", project):
        print("::error::CurseForge needs both CURSEFORGE_TOKEN and CURSEFORGE_PROJECT_ID (a number): nothing was uploaded")
        return 1
    try:
        upload(
            zip_path,
            tag,
            token=token,
            project=project,
            repository=environ.get("GITHUB_REPOSITORY", "AntonyPerez0/walka-addon"),
            toc=TOC.read_text("utf-8"),
            opener=opener,
        )
    except urllib.error.HTTPError as error:
        said = error.read().decode("utf-8", "replace")[:500]
        print(f"::error::CurseForge refused: HTTP {error.code} {said}")
        return 1
    except (OSError, ValueError, KeyError) as error:
        print(f"::error::{error}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
