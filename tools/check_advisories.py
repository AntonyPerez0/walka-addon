"""Is any package pinned in the lock files named in a security advisory?

    python tools/check_advisories.py

Every ``name==version`` of companion/requirements-*.lock is looked up in
OSV.dev (the open database that the advisories of PyPI and GitHub feed),
in one request. Exits 1 and names each advisory if any version pinned is
affected: then the pin is to be raised (write the lock files again with
--upgrade-package, see their headers). Only the names and versions of
public packages are sent; nothing of anybody's. Needs nothing but Python.
"""

from __future__ import annotations

import json
import re
import sys
import urllib.request
from pathlib import Path

COMPANION = Path(__file__).resolve().parent.parent / "companion"
OSV = "https://api.osv.dev/v1/querybatch"


def pins(text: str) -> set[tuple[str, str]]:
    return {(m.group(1).lower(), m.group(2)) for m in re.finditer(r"(?m)^([A-Za-z0-9_.-]+)==([^\s;\\]+)", text)}


def all_pins() -> list[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for lock in sorted(COMPANION.glob("requirements-*.lock")):
        found |= pins(lock.read_text("utf-8"))
    return sorted(found)


def ask(packages: list[tuple[str, str]], opener=urllib.request.urlopen) -> list[list[str]]:
    """The ids of the advisories for each package, in order."""
    body = {"queries": [{"package": {"name": name, "ecosystem": "PyPI"}, "version": v} for name, v in packages]}
    request = urllib.request.Request(OSV, json.dumps(body).encode(), {"Content-Type": "application/json"})
    with opener(request, timeout=60) as answer:
        results = json.loads(answer.read())["results"]
    return [[vuln["id"] for vuln in result.get("vulns", [])] for result in results]


def main() -> int:
    packages = all_pins()
    found = ask(packages)
    affected = [(name, version, ids) for (name, version), ids in zip(packages, found, strict=True) if ids]
    for name, version, ids in affected:
        print(f"::error::{name} {version}: {', '.join(ids)} (https://osv.dev/vulnerability/{ids[0]})")
    if not affected:
        print(f"no advisory names any of the {len(packages)} versions pinned")
    return 1 if affected else 0


if __name__ == "__main__":
    sys.exit(main())
