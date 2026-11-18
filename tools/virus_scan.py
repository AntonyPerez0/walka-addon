"""Have VirusTotal check the files of a release, and write down what it says.

VirusTotal (virustotal.com) runs a file past the scanners of about seventy
antivirus makers. People who are about to open a program from the internet
can look there first; this script does the looking for them, for the exact
files a release offers, so that the answer can stand next to the download.

    VT_API_KEY=...  python tools/virus_scan.py Walka.exe Walka-addon.zip \
        --badge virus-scan.json --notes release-notes.md

For each file it asks VirusTotal for a fresh check - a file it has not seen
is sent there, one it knows is checked again, because scanners learn and a
verdict changes with time - and waits for the answer. Then it writes:

* ``--badge``: the results as a small JSON file. Stored with the release,
  it is what the badge at the top of the README shows: shields.io reads
  its "badge" line, which is the result for the first file;
* ``--notes``: the release notes, with a "Virus scan" part put in (or the
  one that was there replaced): a table of every file, its result, a link
  to the full report and its SHA-256, so that anyone can see that the file
  they have is the file that was checked.

The same part is printed, for the log of the run.

It reports; it does not judge. A file that scanners flag still gets its
result written down, and the exit code is 0: a few scanners flag most small
unsigned programs, and whether to believe them is for a person to decide,
with the report in front of them. The exit code is 1 only when no answer
could be had (no key, a refused key, VirusTotal out of reach) - and then
nothing is written, so that an old result is not replaced by none.

The key is a free one (virustotal.com > your profile > API key). It allows
four requests a minute, which is why this takes its time.

Nothing but the standard library is used, so the release workflow can run
it as it is.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

API = "https://www.virustotal.com/api/v3"
REPORT_URL = "https://www.virustotal.com/gui/file/{sha256}"

# The free key allows four requests a minute.
SECONDS_BETWEEN_REQUESTS = 16.0
# Told "too many requests" all the same (the minute is counted on their
# side, not ours): wait this long, this many times, before giving up.
SECONDS_AFTER_TOO_MANY = 60.0
TRIES_AFTER_TOO_MANY = 5
# Files above this size are not taken at the ordinary address: VirusTotal
# gives out an address for each of them.
DIRECT_UPLOAD_LIMIT = 32 * 1024 * 1024
# How long to wait for the scanners. A new file usually takes a few minutes.
DEFAULT_WAIT_SECONDS = 15 * 60.0

# What a scanner can say about a file. Only these count as an answer: the
# rest ("type-unsupported", "timeout", "failure", ...) mean that the scanner
# did not look, so it is not among the scanners that found nothing.
ANSWERS = {"malicious", "suspicious", "undetected", "harmless"}
FLAGS = {"malicious", "suspicious"}

NOTES_START = "<!-- virus-scan:start -->"
NOTES_END = "<!-- virus-scan:end -->"

# send(method, url, headers, body) -> (HTTP status, response body)
Send = Callable[[str, str, dict[str, str], "bytes | None"], "tuple[int, bytes]"]


class ScanError(Exception):
    """No answer could be had from VirusTotal."""


@dataclass
class Report:
    """What VirusTotal says about one file."""

    name: str
    sha256: str
    # False: the scanners had not finished when the waiting ended.
    done: bool = False
    # (scanner, what it calls the file), for the scanners that flag it.
    flagged: list[tuple[str, str]] = field(default_factory=list)
    # How many scanners gave an answer.
    scanners: int = 0
    # When they did, in seconds since 1970 (0: never).
    checked: int = 0

    @property
    def url(self) -> str:
        return REPORT_URL.format(sha256=self.sha256)

    @property
    def result(self) -> str:
        if not self.done:
            return "still being checked"
        return f"{len(self.flagged)} of {self.scanners} scanners flag it"


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def send_over_https(method: str, url: str, headers: dict[str, str], body: bytes | None) -> tuple[int, bytes]:
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise ScanError(f"VirusTotal could not be reached: {error}") from error


class VirusTotal:
    """The few questions this script has for VirusTotal's API (version 3)."""

    def __init__(
        self,
        key: str,
        send: Send = send_over_https,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self.key = key
        self.send = send
        self.sleep = sleep
        self.now = now
        self._last_request: float | None = None

    def request(self, method: str, url: str, body: bytes | None = None, content_type: str = "") -> dict | None:
        """The answer as JSON; None when VirusTotal does not know the thing asked for."""
        headers = {"x-apikey": self.key, "accept": "application/json"}
        if content_type:
            headers["content-type"] = content_type
        for _ in range(TRIES_AFTER_TOO_MANY):
            if self._last_request is not None:
                wait = self._last_request + SECONDS_BETWEEN_REQUESTS - self.now()
                if wait > 0:
                    self.sleep(wait)
            status, answer = self.send(method, url, headers, body)
            self._last_request = self.now()
            if status != 429:
                break
            self.sleep(SECONDS_AFTER_TOO_MANY)
        if status == 404:
            return None
        if status in (401, 403):
            raise ScanError(
                f"VirusTotal refused the key (HTTP {status}). Is the VT_API_KEY secret the API key from your VirusTotal profile?"
            )
        if status == 429:
            raise ScanError("VirusTotal says the key has made too many requests. Its free allowance is 500 a day.")
        try:
            parsed = json.loads(answer)
        except ValueError:
            parsed = None
        if status >= 400 or not isinstance(parsed, dict):
            said = parsed.get("error", {}).get("message", "") if isinstance(parsed, dict) else ""
            raise ScanError(
                f"VirusTotal answered HTTP {status} to {method} {url.split('?')[0]}" + (f": {said}" if said else "")
            )
        return parsed

    def file(self, sha256: str) -> dict | None:
        """What is known about a file already, or None for a file never seen."""
        answer = self.request("GET", f"{API}/files/{sha256}")
        return answer["data"]["attributes"] if answer else None

    def check_again(self, sha256: str) -> str:
        """Ask for a fresh check of a file VirusTotal has. Gives the id of the check."""
        answer = self.request("POST", f"{API}/files/{sha256}/analyse")
        if answer is None:
            raise ScanError(f"VirusTotal no longer knows the file {sha256}")
        return answer["data"]["id"]

    def upload(self, path: Path) -> str:
        """Send a file to be checked. Gives the id of the check."""
        url = f"{API}/files"
        if path.stat().st_size > DIRECT_UPLOAD_LIMIT:
            answer = self.request("GET", f"{API}/files/upload_url")
            if answer is None:
                raise ScanError("VirusTotal gave no address for a large file")
            url = answer["data"]
        boundary = uuid.uuid4().hex
        # The file name is ours (Walka.exe), but it goes into a header:
        # nothing but plain characters.
        name = re.sub(r"[^A-Za-z0-9._-]", "_", path.name)
        body = (
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode() + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()  # fmt: skip
        answer = self.request("POST", url, body, f"multipart/form-data; boundary={boundary}")
        if answer is None:
            raise ScanError("VirusTotal did not take the file")
        return answer["data"]["id"]

    def check(self, check_id: str) -> dict:
        """How a check is going: its "status", and when "completed" its "results"."""
        answer = self.request("GET", f"{API}/analyses/{check_id}")
        if answer is None:
            raise ScanError("VirusTotal lost track of the check it was asked for")
        return answer["data"]["attributes"]


def read_results(report: Report, results: dict | None, checked: int | None) -> bool:
    """Put what the scanners said into the report. False if none of them said anything."""
    flagged: list[tuple[str, str]] = []
    scanners = 0
    for scanner, said in (results or {}).items():
        category = (said or {}).get("category")
        if category not in ANSWERS:
            continue
        scanners += 1
        if category in FLAGS:
            flagged.append((str(said.get("engine_name") or scanner), str(said.get("result") or category)))
    if not scanners:
        return False
    report.done = True
    report.flagged = sorted(flagged, key=lambda pair: pair[0].lower())
    report.scanners = scanners
    report.checked = int(checked or 0)
    return True


def scan(
    vt: VirusTotal,
    paths: list[Path],
    wait_seconds: float = DEFAULT_WAIT_SECONDS,
    log: Callable[[str], None] = lambda _line: None,
) -> list[Report]:
    """Have every file checked, and wait until all are or the time is up."""
    reports: list[Report] = []
    before: dict[str, dict] = {}
    waiting: dict[str, Report] = {}  # id of the check -> the file's report
    for path in paths:
        report = Report(name=path.name, sha256=sha256_of(path))
        reports.append(report)
        known = vt.file(report.sha256)
        if known is None:
            log(f"{report.name}: new to VirusTotal, sending it")
            waiting[vt.upload(path)] = report
        else:
            log(f"{report.name}: known to VirusTotal, asking for a fresh check")
            before[report.sha256] = known
            waiting[vt.check_again(report.sha256)] = report

    deadline = vt.now() + wait_seconds
    while waiting:
        for check_id, report in list(waiting.items()):
            state = vt.check(check_id)
            if state.get("status") == "completed" and read_results(report, state.get("results"), state.get("date")):
                log(f"{report.name}: {report.result}")
                del waiting[check_id]
        if vt.now() >= deadline:
            break

    for report in waiting.values():
        # Not finished in time. A file that was checked before still has
        # that result, with its own date; a new file has none yet.
        known = before.get(report.sha256, {})
        if read_results(report, known.get("last_analysis_results"), known.get("last_analysis_date")):
            log(f"{report.name}: the fresh check is not finished; the last one says: {report.result}")
        else:
            log(f"{report.name}: the check is not finished")
    return reports


# ---- writing it down ------------------------------------------------------------------------


def day(seconds: int) -> str:
    when = datetime.fromtimestamp(seconds, tz=timezone.utc)
    return f"{when.day} {when:%B %Y}"


def summary(reports: list[Report]) -> dict:
    """The results as data. "badge" is the line the README's badge shows: the first file's."""
    first = reports[0]
    return {
        "badge": f"{first.name}: {first.result}",
        "files": [
            {
                "name": report.name,
                "sha256": report.sha256,
                "report": report.url,
                "done": report.done,
                "flagged": len(report.flagged),
                "scanners": report.scanners,
                "checked": datetime.fromtimestamp(report.checked, tz=timezone.utc).strftime("%Y-%m-%d")
                if report.done and report.checked
                else None,
            }
            for report in reports
        ],
    }


def cell(text: str) -> str:
    """Text from outside (a scanner's name for a file) made safe for a line of the notes."""
    return re.sub(r"[^A-Za-z0-9 ._:/!()+-]", "", text)[:80]


def notes_section(reports: list[Report]) -> str:
    """The "Virus scan" part of the release notes."""
    lines = [
        NOTES_START,
        "### Virus scan",
        "",
        "[VirusTotal](https://www.virustotal.com/) runs a file past the scanners of about 70 antivirus makers. "
        "These are the files of this release. Click a result for the full report.",
        "",
        "| File | Result | Checked | SHA-256 |",
        "|---|---|---|---|",
    ]
    for report in reports:
        checked = day(report.checked) if report.done and report.checked else "-"
        lines.append(f"| `{report.name}` | [{report.result}]({report.url}) | {checked} | `{report.sha256}` |")
    for report in reports:
        if report.flagged:
            names = ", ".join(f"{cell(scanner)} ({cell(what)})" for scanner, what in report.flagged)
            lines += ["", f"`{report.name}` is flagged by: {names}."]
    if any(report.flagged for report in reports):
        lines += [
            "",
            "A few scanners flag most small programs that are not signed by a company, so one or two flags are "
            "usually a false alarm. The report shows which scanners they are and what they say: you decide.",
        ]
    lines.append(NOTES_END)
    return "\n".join(lines)


def merge_notes(notes: str, section: str) -> str:
    """The release notes with the section in place of the one that was there, or added at the end."""
    notes = notes.replace("\r\n", "\n")
    start, end = notes.find(NOTES_START), notes.find(NOTES_END)
    if 0 <= start < end:
        return notes[:start] + section + notes[end + len(NOTES_END) :]
    return (notes.rstrip() + "\n\n" if notes.strip() else "") + section + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Have VirusTotal check the files of a release.")
    parser.add_argument("files", nargs="+", type=Path, help="the files; the badge is about the first one")
    parser.add_argument("--badge", type=Path, help="write the results here as JSON, for the badge in the README")
    parser.add_argument("--notes", type=Path, help="release notes to put the 'Virus scan' part into")
    parser.add_argument("--wait", type=float, default=DEFAULT_WAIT_SECONDS, help="seconds to wait for the scanners")
    args = parser.parse_args(argv)

    def fail(message: str) -> int:
        # In GitHub Actions, "::error::" puts the line on the run's front page.
        print(("::error::" if os.environ.get("GITHUB_ACTIONS") else "error: ") + message, file=sys.stderr)
        return 1

    key = os.environ.get("VT_API_KEY", "").strip()
    if not key:
        return fail("VT_API_KEY is not set (a free API key from your VirusTotal profile)")
    for path in args.files:
        if not path.is_file():
            return fail(f"no such file: {path}")
    try:
        reports = scan(
            VirusTotal(key), args.files, args.wait, log=lambda line: print(line, file=sys.stderr, flush=True)
        )
    except ScanError as error:
        return fail(str(error))

    section = notes_section(reports)
    if args.badge:
        args.badge.write_text(json.dumps(summary(reports), indent=1) + "\n", "utf-8")
    if args.notes:
        before = args.notes.read_text("utf-8") if args.notes.exists() else ""
        args.notes.write_text(merge_notes(before, section), "utf-8")
    print(section)
    return 0


if __name__ == "__main__":
    sys.exit(main())
