"""tools/virus_scan.py: having VirusTotal check the files of a release, and
writing down what it says (the file the README's badge reads, the "Virus
scan" part of the release notes). VirusTotal itself is played by a fake here."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def load_script():
    spec = importlib.util.spec_from_file_location("walka_virus_scan", REPO_ROOT / "tools" / "virus_scan.py")
    module = importlib.util.module_from_spec(spec)
    # dataclasses looks the module up by name while the class is being made.
    import sys

    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


vs = load_script()


def results(clean: int = 3, flagged: dict[str, str] | None = None, did_not_look: int = 2) -> dict:
    """The answers of the scanners, as VirusTotal gives them."""
    out = {f"Clean{i}": {"category": "undetected", "engine_name": f"Clean{i}", "result": None} for i in range(clean)}
    for name, what in (flagged or {}).items():
        out[name] = {"category": "malicious", "engine_name": name, "result": what}
    for i in range(did_not_look):
        out[f"Blind{i}"] = {"category": "type-unsupported", "engine_name": f"Blind{i}", "result": None}
    return out


class FakeVirusTotal:
    """Answers like the API does, and keeps the time: sleeping costs none."""

    def __init__(self) -> None:
        self.time = 0.0
        self.calls: list[tuple[str, str]] = []
        self.request_times: list[float] = []
        self.known: dict[str, dict] = {}  # sha256 -> the file's attributes
        self.checks: dict[str, dict] = {}  # id of a check -> {"polls_until_done", "results", "date"}
        self.uploaded: list[bytes] = []
        self.too_many = 0  # answer 429 this many times first
        self.refuse_key = False
        self.fresh_results = results()

    def sleep(self, seconds: float) -> None:
        self.time += seconds

    def now(self) -> float:
        return self.time

    def client(self):
        return vs.VirusTotal("the-key", send=self.send, sleep=self.sleep, now=self.now)

    def send(self, method: str, url: str, headers: dict[str, str], body: bytes | None) -> tuple[int, bytes]:
        assert headers["x-apikey"] == "the-key"
        self.calls.append((method, url))
        self.request_times.append(self.time)
        if self.refuse_key:
            return 401, json.dumps({"error": {"code": "WrongCredentialsError", "message": "Wrong API key"}}).encode()
        if self.too_many:
            self.too_many -= 1
            return 429, b'{"error": {"code": "QuotaExceededError"}}'
        path = url.removeprefix(vs.API)
        if method == "GET" and path == "/files/upload_url":
            return 200, json.dumps({"data": "https://big.example/upload"}).encode()
        if method == "POST" and (path == "/files" or url == "https://big.example/upload"):
            assert headers["content-type"].startswith("multipart/form-data; boundary=")
            self.uploaded.append(body)
            return 200, json.dumps({"data": {"type": "analysis", "id": self.new_check()}}).encode()
        if method == "POST" and path.endswith("/analyse"):
            assert path.split("/")[2] in self.known
            return 200, json.dumps({"data": {"type": "analysis", "id": self.new_check()}}).encode()
        if method == "GET" and path.startswith("/files/"):
            attributes = self.known.get(path.split("/")[2])
            if attributes is None:
                return 404, b'{"error": {"code": "NotFoundError"}}'
            return 200, json.dumps({"data": {"attributes": attributes}}).encode()
        if method == "GET" and path.startswith("/analyses/"):
            check = self.checks[path.split("/")[2]]
            if check["polls_until_done"] > 0:
                check["polls_until_done"] -= 1
                return 200, json.dumps({"data": {"attributes": {"status": "queued", "results": {}}}}).encode()
            attributes = {"status": "completed", "results": check["results"], "date": check["date"]}
            return 200, json.dumps({"data": {"attributes": attributes}}).encode()
        raise AssertionError(f"unexpected request: {method} {url}")

    polls_until_done = 2

    def new_check(self) -> str:
        check_id = f"check{len(self.checks)}"
        self.checks[check_id] = {
            "polls_until_done": self.polls_until_done,
            "results": self.fresh_results,
            "date": 1_795_000_000,
        }
        return check_id


@pytest.fixture
def fake() -> FakeVirusTotal:
    return FakeVirusTotal()


@pytest.fixture
def exe(tmp_path: Path) -> Path:
    path = tmp_path / "Walka.exe"
    path.write_bytes(b"MZ this is the program")
    return path


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---- asking VirusTotal ------------------------------------------------------------------------


def test_a_new_file_is_sent_and_its_result_read(fake, exe):
    (report,) = vs.scan(fake.client(), [exe])
    assert fake.uploaded and b"MZ this is the program" in fake.uploaded[0]
    assert b'filename="Walka.exe"' in fake.uploaded[0]
    assert report.name == "Walka.exe" and report.sha256 == sha(exe)
    assert report.done and report.flagged == [] and report.checked == 1_795_000_000
    # Scanners that did not look at the file are not among those that found nothing.
    assert report.scanners == 3
    assert report.result == "0 of 3 scanners flag it"
    assert report.url == f"https://www.virustotal.com/gui/file/{sha(exe)}"


def test_the_file_is_sent_as_a_form_any_server_can_read(fake, exe):
    """The form is put together by hand: have the standard library take it apart."""
    import email

    sent: dict[str, str] = {}
    send = fake.send

    def recording(method, url, headers, body):
        if body:
            sent.update(headers)
        return send(method, url, headers, body)

    fake.send = recording
    vs.scan(fake.client(), [exe])
    message = email.message_from_bytes(
        b"Content-Type: " + sent["content-type"].encode() + b"\r\n\r\n" + fake.uploaded[0]
    )
    (part,) = message.get_payload()
    assert part.get_param("name", header="content-disposition") == "file"
    assert part.get_filename() == "Walka.exe"
    assert part.get_payload(decode=True) == exe.read_bytes()


def test_a_known_file_is_checked_again_not_sent_again(fake, exe):
    fake.known[sha(exe)] = {"last_analysis_results": results(flagged={"Old": "Stale.Verdict"}), "last_analysis_date": 1}
    (report,) = vs.scan(fake.client(), [exe])
    assert not fake.uploaded
    assert ("POST", f"{vs.API}/files/{sha(exe)}/analyse") in fake.calls
    # The fresh answer, not the one from before.
    assert report.flagged == [] and report.checked == 1_795_000_000


def test_scanners_that_flag_the_file_are_named(fake, exe):
    fake.fresh_results = results(clean=4, flagged={"Zeta": "Trojan.Generic", "alpha": "Suspicious!ml"})
    (report,) = vs.scan(fake.client(), [exe])
    assert report.flagged == [("alpha", "Suspicious!ml"), ("Zeta", "Trojan.Generic")]
    assert report.scanners == 6
    assert report.result == "2 of 6 scanners flag it"


def test_a_suspicious_verdict_is_a_flag_too(fake, exe):
    fake.fresh_results = {"S": {"category": "suspicious", "engine_name": "S", "result": None}, **results(clean=1)}
    (report,) = vs.scan(fake.client(), [exe])
    assert report.flagged == [("S", "suspicious")]


def test_a_large_file_goes_to_the_address_virustotal_gives_for_it(fake, exe, monkeypatch):
    monkeypatch.setattr(vs, "DIRECT_UPLOAD_LIMIT", 4)
    vs.scan(fake.client(), [exe])
    assert ("POST", "https://big.example/upload") in fake.calls
    assert ("POST", f"{vs.API}/files") not in fake.calls


def test_every_file_is_asked_for_before_any_is_waited_for(fake, exe, tmp_path):
    addon = tmp_path / "Walka-addon.zip"
    addon.write_bytes(b"PK the addon")
    reports = vs.scan(fake.client(), [exe, addon])
    assert [report.name for report in reports] == ["Walka.exe", "Walka-addon.zip"]
    assert all(report.done for report in reports)
    first_poll = next(i for i, (_, url) in enumerate(fake.calls) if "/analyses/" in url)
    assert sum(1 for method, _ in fake.calls[:first_poll] if method == "POST") == 2


def test_requests_keep_to_four_a_minute(fake, exe):
    vs.scan(fake.client(), [exe])
    gaps = [later - earlier for earlier, later in zip(fake.request_times, fake.request_times[1:], strict=False)]
    assert len(gaps) >= 3 and min(gaps) >= 15


def test_too_many_requests_is_waited_out(fake, exe):
    fake.too_many = 2
    (report,) = vs.scan(fake.client(), [exe])
    assert report.done
    assert fake.time >= 2 * vs.SECONDS_AFTER_TOO_MANY


def test_too_many_requests_without_end_is_an_error(fake, exe):
    fake.too_many = 1000
    with pytest.raises(vs.ScanError, match="too many requests"):
        vs.scan(fake.client(), [exe])


def test_a_refused_key_is_an_error_that_says_so(fake, exe):
    fake.refuse_key = True
    with pytest.raises(vs.ScanError, match="VT_API_KEY"):
        vs.scan(fake.client(), [exe])


def test_a_new_file_not_finished_in_time_is_reported_as_still_being_checked(fake, exe):
    fake.polls_until_done = 10_000
    (report,) = vs.scan(fake.client(), [exe], wait_seconds=120)
    assert not report.done and report.result == "still being checked"
    assert fake.time < 200  # it stopped waiting


def test_a_known_file_not_finished_in_time_keeps_its_last_result_and_date(fake, exe):
    fake.polls_until_done = 10_000
    fake.known[sha(exe)] = {"last_analysis_results": results(clean=5), "last_analysis_date": 1_700_000_000}
    (report,) = vs.scan(fake.client(), [exe], wait_seconds=120)
    assert report.done and report.scanners == 5 and report.checked == 1_700_000_000


def test_a_finished_check_in_which_no_scanner_answered_is_not_a_clean_result(fake, exe):
    fake.fresh_results = results(clean=0, did_not_look=4)
    (report,) = vs.scan(fake.client(), [exe], wait_seconds=60)
    assert not report.done


# ---- writing it down --------------------------------------------------------------------------


def a_report(flagged: list[tuple[str, str]] | None = None, done: bool = True, name: str = "Walka.exe"):
    return vs.Report(
        name=name, sha256="ab" * 32, done=done, flagged=flagged or [], scanners=70 if done else 0, checked=1_795_000_000
    )


def test_the_summary_has_the_line_for_the_badge_and_every_file():
    data = vs.summary([a_report(), a_report(name="Walka-addon.zip", flagged=[("A", "x")])])
    assert data["badge"] == "Walka.exe: 0 of 70 scanners flag it"
    assert data["files"][0] == {
        "name": "Walka.exe",
        "sha256": "ab" * 32,
        "report": f"https://www.virustotal.com/gui/file/{'ab' * 32}",
        "done": True,
        "flagged": 0,
        "scanners": 70,
        "checked": "2026-11-18",
    }
    assert data["files"][1]["name"] == "Walka-addon.zip" and data["files"][1]["flagged"] == 1


def test_the_badge_says_what_is_so_when_scanners_flag_the_file_or_have_not_answered():
    assert vs.summary([a_report(flagged=[("A", "x")])])["badge"] == "Walka.exe: 1 of 70 scanners flag it"
    pending = vs.summary([a_report(done=False)])
    assert pending["badge"] == "Walka.exe: still being checked"
    assert pending["files"][0]["done"] is False and pending["files"][0]["checked"] is None


def test_the_notes_have_a_line_for_each_file_with_its_report_and_sha256():
    section = vs.notes_section([a_report(), a_report(name="Walka-addon.zip")])
    assert section.startswith(vs.NOTES_START) and section.endswith(vs.NOTES_END)
    link = f"https://www.virustotal.com/gui/file/{'ab' * 32}"
    day = "18 November 2026"
    assert f"| `Walka.exe` | [0 of 70 scanners flag it]({link}) | {day} | `{'ab' * 32}` |" in section
    assert "| `Walka-addon.zip` |" in section
    assert "flagged by" not in section and "false alarm" not in section


def test_the_notes_name_the_scanners_that_flag_a_file():
    section = vs.notes_section([a_report(flagged=[("Acme", "Trojan.Gen"), ("Bolt", "Susp")])])
    assert "`Walka.exe` is flagged by: Acme (Trojan.Gen), Bolt (Susp)." in section
    assert "you decide" in section


def test_a_scanners_words_cannot_put_markup_into_the_notes():
    section = vs.notes_section([a_report(flagged=[("Evil](http://x) <img>", "a|b\n### c `d`")])])
    line = next(line for line in section.splitlines() if "flagged by" in line)
    for character in "[]<>|`#":
        assert character not in line.split("flagged by:")[1]


def test_a_file_still_being_checked_has_no_date_in_the_notes():
    section = vs.notes_section([a_report(done=False)])
    assert "| [still being checked](" in section and "| - |" in section


def test_the_section_is_added_to_notes_that_have_none():
    notes = "**To get Walka:** click it.\r\n\r\nEarly version.\r\n"
    merged = vs.merge_notes(notes, vs.notes_section([a_report()]))
    assert merged.startswith("**To get Walka:** click it.\n\nEarly version.\n\n" + vs.NOTES_START)
    assert merged.endswith(vs.NOTES_END + "\n")


def test_the_section_replaces_the_one_from_the_last_check_and_nothing_else():
    old = vs.notes_section([a_report(flagged=[("Acme", "Trojan.Gen")])])
    notes = f"Before.\n\n{old}\n\nAfter, written by hand."
    merged = vs.merge_notes(notes, vs.notes_section([a_report()]))
    assert merged.count(vs.NOTES_START) == 1 and "Acme" not in merged
    assert merged.startswith("Before.\n\n") and merged.endswith("\n\nAfter, written by hand.")
    assert vs.merge_notes("", "SECTION") == "SECTION\n"


# ---- the command ------------------------------------------------------------------------------


def test_the_command_writes_the_badge_and_the_notes(fake, exe, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("VT_API_KEY", "the-key")
    client = fake.client()
    monkeypatch.setattr(vs, "VirusTotal", lambda key: client)
    badge, notes = tmp_path / "virus-scan.json", tmp_path / "notes.md"
    notes.write_text("The notes.\n", "utf-8")
    assert vs.main([str(exe), "--badge", str(badge), "--notes", str(notes)]) == 0
    assert json.loads(badge.read_text("utf-8"))["badge"] == "Walka.exe: 0 of 3 scanners flag it"
    written = notes.read_text("utf-8")
    assert written.startswith("The notes.\n\n" + vs.NOTES_START) and sha(exe) in written
    assert vs.NOTES_START in capsys.readouterr().out


def test_a_flagged_file_is_reported_not_failed(fake, exe, tmp_path, monkeypatch):
    monkeypatch.setenv("VT_API_KEY", "the-key")
    client = fake.client()
    monkeypatch.setattr(vs, "VirusTotal", lambda key: client)
    fake.fresh_results = results(flagged={"Acme": "Trojan.Gen"})
    badge = tmp_path / "virus-scan.json"
    assert vs.main([str(exe), "--badge", str(badge)]) == 0
    assert json.loads(badge.read_text("utf-8"))["badge"] == "Walka.exe: 1 of 4 scanners flag it"


def test_without_an_answer_nothing_is_written(fake, exe, tmp_path, monkeypatch, capsys):
    badge, notes = tmp_path / "virus-scan.json", tmp_path / "notes.md"
    notes.write_text("The notes, with the last result.\n", "utf-8")
    arguments = [str(exe), "--badge", str(badge), "--notes", str(notes)]

    monkeypatch.delenv("VT_API_KEY", raising=False)
    assert vs.main(arguments) == 1
    assert "VT_API_KEY is not set" in capsys.readouterr().err

    monkeypatch.setenv("VT_API_KEY", "the-key")
    client = fake.client()
    monkeypatch.setattr(vs, "VirusTotal", lambda key: client)
    fake.refuse_key = True
    assert vs.main(arguments) == 1
    assert "refused the key" in capsys.readouterr().err

    assert not badge.exists() and notes.read_text("utf-8") == "The notes, with the last result.\n"


def test_the_key_is_never_printed(fake, exe, monkeypatch, capsys):
    monkeypatch.setenv("VT_API_KEY", "the-key")
    client = fake.client()
    monkeypatch.setattr(vs, "VirusTotal", lambda key: client)
    fake.refuse_key = True
    vs.main([str(exe)])
    captured = capsys.readouterr()
    assert "the-key" not in captured.out + captured.err
