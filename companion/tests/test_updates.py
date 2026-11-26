"""Updates: the check (on in the packaged program, nothing asked when it is
off), and "Update now" - download, checksum, swap, restart."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from test_app import Rig
from walka import __version__, config, updates, web
from walka.config import Config

RELEASE = "https://github.com/AntonyPerez0/walka-addon/releases/download/v99.0.0/"
NEW_EXE = b"MZ the new Walka"


@pytest.fixture
def rig(tmp_path):
    return Rig(tmp_path)


@pytest.fixture(autouse=True)
def _no_restart_left_waiting():
    yield
    updates._pending.clear()


def packed(monkeypatch, value: bool = True) -> None:
    if value:
        monkeypatch.setattr(sys, "frozen", True, raising=False)
    else:
        monkeypatch.delattr(sys, "frozen", raising=False)


# ---- on or off ---------------------------------------------------------------------------------


def test_on_in_the_packaged_program_off_when_run_from_source(monkeypatch):
    packed(monkeypatch, False)
    assert Config().updates.check is False
    packed(monkeypatch, True)
    assert Config().updates.check is True


@pytest.mark.parametrize(
    "written,is_packed,on",
    [
        ({"check": False}, True, True),  # up to 0.11: "false" was the default then, not a choice
        ({"check": True}, True, True),
        ({"check": False}, False, False),
        ({"check": True}, False, True),  # switched on, run from source: kept
        ({"check": False, "chosen": True}, True, False),  # switched off: kept off
        ({"check": True, "chosen": True}, False, True),
        (None, True, True),
    ],
)
def test_a_saved_file_keeps_a_choice_and_only_a_choice(tmp_path, monkeypatch, written, is_packed, on):
    packed(monkeypatch, is_packed)
    raw = {"units": "mph"} if written is None else {"units": "mph", "updates": written}
    (tmp_path / config.CONFIG_FILE).write_text(json.dumps(raw), "utf-8")
    assert config.load(tmp_path).updates.check is on


def test_switching_it_in_settings_is_a_choice(rig: Rig, tmp_path):
    rig.app.update_config({"updates": {"check": False}})
    saved = json.loads((tmp_path / "data" / config.CONFIG_FILE).read_text("utf-8"))
    assert saved["updates"] == {"check": False, "chosen": True}


@pytest.mark.parametrize(
    "latest,current,newer",
    [
        ("v0.9.3", "0.9.2", True),
        ("v0.10.0", "0.9.2", True),
        ("v1.0", "0.9.2", True),
        ("v0.9.2", "0.9.2", False),
        ("v0.9.1", "0.9.2", False),
        ("v1.0.0-rc1", "0.9.2", False),  # a trial is never "the newest"
        ("", "0.9.2", False),
        ("nonsense", "0.9.2", False),
    ],
)
def test_newer(latest, current, newer):
    assert updates.is_newer(latest, current) is newer


def sums_for(**files: bytes) -> bytes:
    return "".join(f"{hashlib.sha256(data).hexdigest()}  {name}\n" for name, data in files.items()).encode()


def a_release(**files: bytes) -> dict:
    files.setdefault("SHA256SUMS.txt", sums_for(**{n: d for n, d in files.items()}))
    return {
        "tag": "v99.0.0",
        "url": "https://github.com/AntonyPerez0/walka-addon/releases/tag/v99.0.0",
        "assets": {name: {"url": RELEASE + name, "size": len(data)} for name, data in files.items()},
    }


def counting_fetch(answers: list, release: dict | None = None):
    async def fetch():
        answers.append("asked")
        return release or a_release(**{"Walka.exe": NEW_EXE})

    return fetch


def test_switched_off_nothing_is_ever_asked(rig: Rig):
    asked: list = []
    rig.app.update_fetch = counting_fetch(asked)
    rig.app.update_config({"updates": {"check": False}})

    async def scenario():
        for _ in range(50):
            rig.clock.advance(3600)
            await rig.app.tick()
        await asyncio.sleep(0)

    asyncio.run(scenario())
    assert asked == [] and rig.app.snapshot()["update"] == {"enabled": False}


def test_switched_on_it_asks_at_once_then_once_a_day(rig: Rig):
    asked: list = []
    rig.app.update_fetch = counting_fetch(asked)

    async def scenario():
        rig.app.update_config({"updates": {"check": True}})
        await rig.app.tick()
        await rig.app._update_task
        for _ in range(23):
            rig.clock.advance(3600)
            await rig.app.tick()
            await asyncio.sleep(0)
        assert len(asked) == 1
        rig.clock.advance(3600)
        await rig.app.tick()
        await rig.app._update_task
        assert len(asked) == 2

    asyncio.run(scenario())
    update = rig.app.snapshot()["update"]
    assert update["enabled"] and update["newer"] and update["latest"] == "v99.0.0"
    assert update["url"].endswith("/releases/tag/v99.0.0")
    rig.app.update_config({"updates": {"check": False}})
    assert rig.app.snapshot()["update"] == {"enabled": False}


def test_no_answer_is_said_and_tried_again_later(rig: Rig):
    async def offline():
        raise OSError("no network")

    rig.app.update_fetch = offline

    async def scenario():
        rig.app.update_config({"updates": {"check": True}})
        await rig.app.tick()
        await rig.app._update_task

    asyncio.run(scenario())
    update = rig.app.snapshot()["update"]
    assert "Could not reach GitHub" in update["error"] and not update["newer"]
    assert rig.app.update.next_at - rig.clock.mono == pytest.approx(updates.RETRY_AFTER_S, abs=1)


def test_the_request_names_nobody():
    """What goes to GitHub: the program and its version - nothing about the person."""
    import inspect

    assert updates._headers() == {"User-Agent": f"Walka/{__version__}"}
    module = inspect.getsource(updates)  # (fetch_latest itself is a stand-in in the tests: see conftest.py)
    for name in ("fetch_latest", "download"):
        source = module[module.index(f"async def {name}(") :].split("\n\n\n", 1)[0]
        assert "_headers()" in source and "DummyCookieJar" in source, name
    assert updates.LATEST_URL == "https://api.github.com/repos/AntonyPerez0/walka-addon/releases/latest"


# ---- what is taken from a release -------------------------------------------------------------


def test_only_this_repositorys_own_files_are_taken():
    assets = updates._assets(
        [
            {"name": "Walka.exe", "browser_download_url": RELEASE + "Walka.exe", "size": 10},
            {"name": "SHA256SUMS.txt", "browser_download_url": RELEASE + "SHA256SUMS.txt", "size": 300},
            {"name": "evil.exe", "browser_download_url": "https://example.com/evil.exe", "size": 1},
            {"name": "x", "browser_download_url": "https://github.com/someone/else/releases/download/v1/x"},
            "nonsense",
        ]
    )
    assert set(assets) == {"Walka.exe", "SHA256SUMS.txt"}
    assert assets["Walka.exe"] == {"url": RELEASE + "Walka.exe", "size": 10}


def test_a_download_from_anywhere_else_is_refused_before_anything_is_asked(tmp_path):
    with pytest.raises(RuntimeError, match="not a file of Walka's releases"):
        asyncio.run(updates.download("https://example.com/Walka.exe", tmp_path / "x", 10, lambda _n: None))
    assert not (tmp_path / "x").exists()


def test_github_and_its_file_hosts_only():
    from yarl import URL

    assert updates._from_github(URL("https://github.com/x"))
    assert updates._from_github(URL("https://release-assets.githubusercontent.com/x"))
    assert updates._from_github(URL("https://objects.githubusercontent.com/x"))
    assert not updates._from_github(URL("http://objects.githubusercontent.com/x"))
    assert not updates._from_github(URL("https://githubusercontent.com.example.com/x"))
    assert not updates._from_github(URL("https://evilgithub.com/x"))


def test_the_checksums_file_is_read_as_sha256sum_writes_it():
    text = f"{'a' * 64}  Walka.exe\n{'B' * 64} *Walka-mac-intel.zip\nnot a line\n"
    assert updates.parse_sums(text) == {"Walka.exe": "a" * 64, "Walka-mac-intel.zip": "b" * 64}


@pytest.mark.parametrize(
    "system,machine,name",
    [
        ("win32", "AMD64", "Walka.exe"),
        ("darwin", "arm64", "Walka-mac-apple-silicon.zip"),
        ("darwin", "x86_64", "Walka-mac-intel.zip"),
        ("linux", "x86_64", None),
    ],
)
def test_the_file_for_this_kind_of_computer(system, machine, name):
    assert updates.release_file(system, machine) == name


def test_the_names_are_the_ones_the_release_publishes(repo_root: Path):
    workflow = (repo_root / ".github" / "workflows" / "ci.yml").read_text("utf-8")
    for name in (updates.WINDOWS_FILE, *updates.MAC_FILES.values(), updates.SUMS_FILE):
        assert f"assets/{name}" in workflow, name


# ---- where this Walka is ----------------------------------------------------------------------


def test_run_from_source_it_does_not_replace_itself():
    installed, why = updates.this_install(packed=False)
    assert installed is None and "source code" in why


def test_where_the_program_is(tmp_path):
    exe = tmp_path / "Walka.exe"
    installed, why = updates.this_install(packed=True, executable=str(exe), system="win32")
    assert installed == updates.Installed("win32", exe.resolve()) and why == ""

    app = tmp_path / "Applications" / "Walka.app"
    inside = app / "Contents" / "MacOS" / "Walka"
    installed, why = updates.this_install(packed=True, executable=str(inside), system="darwin")
    assert installed == updates.Installed("darwin", app.resolve()) and why == ""

    hidden = tmp_path / "AppTranslocation" / "ABC" / "d" / "Walka.app" / "Contents" / "MacOS" / "Walka"
    installed, why = updates.this_install(packed=True, executable=str(hidden), system="darwin")
    assert installed is None and "Applications folder" in why

    installed, why = updates.this_install(packed=True, executable=str(tmp_path / "walka"), system="linux")
    assert installed is None


# ---- "Update now", on Windows -----------------------------------------------------------------


def fake_downloads(files: dict[str, bytes], asked: list | None = None):
    async def fetch_file(url, path, max_bytes, progress):
        name = url.removeprefix(RELEASE)
        if asked is not None:
            asked.append(name)
        data = files[name]
        assert len(data) <= max_bytes
        path.write_bytes(data)
        progress(len(data))

    return fetch_file


def windows_walka(tmp_path: Path) -> updates.Installed:
    folder = tmp_path / "Downloads"
    folder.mkdir()
    (folder / "Walka.exe").write_bytes(b"MZ the old Walka")
    return updates.Installed("win32", folder / "Walka.exe")


def test_the_new_program_takes_the_old_ones_place(tmp_path):
    installed = windows_walka(tmp_path)
    release = a_release(**{"Walka.exe": NEW_EXE})
    state = updates.UpdateState(latest="v99.0.0", assets=release["assets"])
    files = {"Walka.exe": NEW_EXE, "SHA256SUMS.txt": sums_for(**{"Walka.exe": NEW_EXE})}

    restart = asyncio.run(updates.install(state, installed, tmp_path / "data", fetch_file=fake_downloads(files)))
    assert installed.path.read_bytes() == NEW_EXE
    assert (installed.path.parent / "Walka.exe.old").read_bytes() == b"MZ the old Walka"
    assert sorted(p.name for p in installed.path.parent.iterdir()) == ["Walka.exe", "Walka.exe.old"]
    assert (restart.from_version, restart.to_version) == (__version__, "99.0.0")
    assert state.received == len(NEW_EXE) and state.size == len(NEW_EXE)


@pytest.mark.parametrize("what", ["wrong checksum", "no checksum", "no file"])
def test_a_download_that_does_not_check_out_changes_nothing(tmp_path, what):
    installed = windows_walka(tmp_path)
    files = {"Walka.exe": NEW_EXE, "SHA256SUMS.txt": sums_for(**{"Walka.exe": b"something else"})}
    if what == "no checksum":
        files["SHA256SUMS.txt"] = sums_for(**{"Walka-mac-intel.zip": b"x"})
    release = a_release(**files)
    if what == "no file":
        del release["assets"]["Walka.exe"]
    state = updates.UpdateState(latest="v99.0.0", assets=release["assets"])

    with pytest.raises(RuntimeError, match="nothing was changed|no download"):
        asyncio.run(updates.install(state, installed, tmp_path / "data", fetch_file=fake_downloads(files)))
    assert installed.path.read_bytes() == b"MZ the old Walka"
    assert [p.name for p in installed.path.parent.iterdir()] == ["Walka.exe"]


def test_the_restart_starts_the_new_program_afresh(tmp_path):
    installed = windows_walka(tmp_path)
    started = []
    updates.restart_when_stopped(updates.Restart(installed, tmp_path / "data", "0.11.0", "99.0.0", argv=["--browser"]))
    assert updates.restart_if_updated(popen=lambda command, **how: started.append((command, how))) is True
    ((command, how),) = started
    assert command == [str(installed.path), "--browser"]
    assert how["env"]["PYINSTALLER_RESET_ENVIRONMENT"] == "1" and how["cwd"] == str(installed.path.parent)
    assert updates.take_done(tmp_path / "data") == {"from": "0.11.0", "to": "99.0.0"}
    assert updates.take_done(tmp_path / "data") is None  # (once)
    assert updates.restart_if_updated(popen=lambda *a, **k: started.append(a)) is False and len(started) == 1


def test_what_an_update_left_is_tidied_away(tmp_path):
    installed = windows_walka(tmp_path)
    (installed.path.parent / "Walka.exe.old").write_bytes(b"old")
    (installed.path.parent / "Walka.exe.update").write_bytes(b"half")
    updates.clean_up(installed)
    assert [p.name for p in installed.path.parent.iterdir()] == ["Walka.exe"]


# ---- on a Mac ---------------------------------------------------------------------------------


def test_a_mac_swaps_the_applications_once_walka_has_stopped(tmp_path):
    app = tmp_path / "Applications" / "Walka.app"
    (app / "Contents" / "MacOS").mkdir(parents=True)
    (app / "Contents" / "MacOS" / "Walka").write_text("old")
    work = app.parent / updates.MAC_WORK_DIR
    new = work / "new" / "Walka.app"
    (new / "Contents" / "MacOS").mkdir(parents=True)
    (new / "Contents" / "MacOS" / "Walka").write_text("new")
    started = []
    restart = updates.Restart(
        updates.Installed("darwin", app), tmp_path / "data", "0.11.0", "99.0.0", new_app=new, argv=[]
    )
    updates.restart_when_stopped(restart)
    assert updates.restart_if_updated(popen=lambda command, **how: started.append(command))
    assert (app / "Contents" / "MacOS" / "Walka").read_text() == "new"
    assert started == [["/usr/bin/open", "-n", str(app)]]
    updates.clean_up(updates.Installed("darwin", app))
    assert sorted(p.name for p in app.parent.iterdir()) == ["Walka.app"]


# ---- in the app, and from the dashboard ---------------------------------------------------------


def ready_to_update(rig: Rig, tmp_path: Path, asked: list | None = None) -> updates.Installed:
    installed = windows_walka(tmp_path)
    rig.app.installed = (installed, "")
    rig.app.update_fetch = counting_fetch([])
    files = {"Walka.exe": NEW_EXE, "SHA256SUMS.txt": sums_for(**{"Walka.exe": NEW_EXE})}
    rig.app.update_download = fake_downloads(files, asked)
    rig.app.update_config({"updates": {"check": True}})
    return installed


def test_update_now_installs_saves_and_restarts(rig: Rig, tmp_path):
    installed = ready_to_update(rig, tmp_path)
    quit_asked = []
    rig.app.quit = lambda: quit_asked.append(True)

    async def scenario():
        await rig.app.tick()
        await rig.app._update_task
        update = rig.app.snapshot()["update"]
        assert update["newer"] and update["can_install"] and update["self_update"] and update["install"]["stage"] == ""
        assert rig.app.install_update() == ""
        await rig.app._install_task

    asyncio.run(scenario())
    update = rig.app.snapshot()["update"]
    assert update["install"] == {"stage": "restarting", "progress": 1.0, "error": ""}
    assert quit_asked == [True] and installed.path.read_bytes() == NEW_EXE
    assert updates.pending_restart() is not None and updates.pending_restart().to_version == "99.0.0"


def test_nothing_is_downloaded_until_update_now_is_pressed(rig: Rig, tmp_path):
    asked: list = []
    ready_to_update(rig, tmp_path, asked)

    async def scenario():
        for _ in range(5):
            rig.clock.advance(3600)
            await rig.app.tick()
            await asyncio.sleep(0)

    asyncio.run(scenario())
    assert rig.app.snapshot()["update"]["newer"] and asked == []


def test_not_while_the_belt_is_moving(rig: Rig, tmp_path):
    asked: list = []
    ready_to_update(rig, tmp_path, asked)

    async def scenario():
        await rig.connect()
        await rig.walk()
        await rig.app._update_task
        assert "belt has stopped" in rig.app.install_update()
        update = rig.app.snapshot()["update"]
        assert not update["can_install"] and update["self_update"]  # (the button is there, greyed)

    asyncio.run(scenario())
    assert asked == [] and updates.pending_restart() is None


def test_run_from_source_the_page_says_why_and_offers_the_link(rig: Rig):
    rig.app.update_fetch = counting_fetch([])
    rig.app.update_config({"updates": {"check": True}})

    async def scenario():
        await rig.app.tick()
        await rig.app._update_task
        assert "source code" in rig.app.install_update()

    asyncio.run(scenario())
    update = rig.app.snapshot()["update"]
    assert update["newer"] and not update["can_install"] and not update["self_update"]
    assert "source code" in update["cannot_install"]


def test_a_failed_update_is_said_and_can_be_tried_again(rig: Rig, tmp_path):
    installed = ready_to_update(rig, tmp_path)

    async def broken(url, path, max_bytes, progress):
        raise OSError("connection reset")

    rig.app.update_download = broken

    async def scenario():
        await rig.app.tick()
        await rig.app._update_task
        assert rig.app.install_update() == ""
        await rig.app._install_task

    asyncio.run(scenario())
    update = rig.app.snapshot()["update"]
    assert update["install"]["stage"] == "failed" and "Nothing was changed" in update["install"]["error"]
    assert update["can_install"] and installed.path.read_bytes() == b"MZ the old Walka"
    assert updates.pending_restart() is None


def test_the_dashboard_asks_for_it_with_a_post(rig: Rig, tmp_path):
    ready_to_update(rig, tmp_path)
    rig.app.quit = lambda: None

    async def scenario():
        async with TestClient(TestServer(web.create_app(rig.app))) as client:
            headers = {"Content-Type": "application/json"}
            response = await client.post("/api/update/install", data="{}", headers=headers)
            assert response.status == 409 and "no newer version" in (await response.json())["error"]
            await rig.app.tick()
            await rig.app._update_task
            response = await client.post("/api/update/install", data="{}", headers=headers)
            assert response.status == 200
            await rig.app._install_task
            # Not from another web page.
            response = await client.post(
                "/api/update/install", data="{}", headers={**headers, "Origin": "https://example.com"}
            )
            assert response.status == 403

    asyncio.run(scenario())
    assert rig.app.update.stage == "restarting"


def test_after_an_update_it_says_so_and_brings_the_addon_up_to_date(rig: Rig, tmp_path):
    (tmp_path / "data").mkdir(exist_ok=True)
    (tmp_path / "data" / updates.DONE_FILE).write_text(json.dumps({"from": "0.11.0", "to": __version__}))
    (rig.addon / "CompanionData.lua").write_text("-- your walks")

    async def scenario():
        await rig.app.start()
        await rig.app.stop()

    asyncio.run(scenario())
    texts = [event["text"] for event in rig.app.events]
    assert any(f"updated to {__version__} (from 0.11.0)" in text for text in texts)
    assert any("game addon was updated too" in text for text in texts)
    assert (rig.addon / "Walka.lua").exists()  # the bundled addon's files
    assert "## Version:" in (rig.addon / "Walka.toc").read_text()
    assert not (tmp_path / "data" / updates.DONE_FILE).exists()


def test_a_trial_is_offered_its_final_release_and_anything_newer():
    from walka.updates import is_newer

    assert is_newer("v0.13.0", current="0.13.0-rc1")
    assert is_newer("v0.13.1", current="0.13.0-rc1")
    assert not is_newer("v0.12.9", current="0.13.0-rc1")
    assert not is_newer("v0.13.0", current="0.13.0")
    assert not is_newer("v0.13.0-rc2", current="0.13.0-rc1"), "a trial is never offered"
