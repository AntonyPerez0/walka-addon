"""Shared set-up for the companion test-suite.

Makes the suite runnable from any working directory and in any pytest import
mode: the helper modules next to this file (``walka_fakes``) and the
``walka`` package itself are put on ``sys.path`` if they are not already.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
SRC = HERE.parent / "src"

for path in (str(HERE), str(SRC)):
    if path not in sys.path:
        sys.path.insert(0, path)


def pytest_configure(config) -> None:
    config.addinivalue_line(
        "markers",
        "real_keystore: this test may open the system's own store for secrets (walka_account.keystore.open_keystore)",
    )
    config.addinivalue_line("markers", "real_browser: this test may open a page in the real web browser")


def tools_not_here(repo_root: Path = REPO) -> list[str]:
    """Three kinds of test need a tool that is not Python's, and are
    skipped on a computer that lacks it: what is missing here, in words -
    nothing, if all three are there. Each is looked for exactly as the
    tests that need it look."""
    missing = []
    # test_walka_account_page.py, test_share_page.py: the dashboard's own scripts, run by Node.
    if shutil.which("node") is None:
        missing.append('Node.js (the command "node"), which runs the dashboard\'s scripts')
    # test_walka_account_httpscheck.py: a home-made certificate, for the real client to refuse.
    spec = importlib.util.spec_from_file_location("walka_check_https", repo_root / "tools" / "check_https.py")
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    try:
        tool.find_openssl()
    except tool.CheckFailed:
        missing.append('the command "openssl", which makes the certificate that Walka must refuse')
    # test_walka_account_nokeys.py: every file git keeps is searched for the live address and key.
    try:
        listed = subprocess.run(["git", "ls-files", "-z"], cwd=repo_root, capture_output=True, timeout=60)
        kept = listed.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        kept = False
    if not kept:
        missing.append('git (the command "git", in a checkout it keeps), which says what files there are to search')
    return missing


@pytest.fixture(scope="session", autouse=True)
def _on_a_build_machine_no_proof_is_skipped_for_want_of_a_tool() -> None:
    """On somebody's own computer a test that lacks its tool is skipped,
    and says so. On a build machine (they all set ``CI``) nobody reads
    that list: a machine that lost Node, openssl or git would stay green
    with the proof gone. There the whole run stops, and says what is missing."""
    if not os.environ.get("CI"):
        return
    missing = tools_not_here()
    if missing:
        pytest.exit(
            "This is a build machine (CI is set), and tests would be skipped on it for want of: "
            + "; ".join(missing)
            + ". On a build machine that is a failure: no test was run.",
            returncode=1,
        )


@pytest.fixture(autouse=True)
def _no_walka_account_from_the_environment(monkeypatch) -> None:
    """Where the accounts are is read from the environment when Walka
    starts (cli._build_walka_account). Whoever runs the tests may have set
    it for a trial of their own: no test may pick that up - not in this
    process, and not in a Walka it starts as a process of its own."""
    monkeypatch.delenv("WALKA_ACCOUNT_URL", raising=False)
    monkeypatch.delenv("WALKA_ACCOUNT_KEY", raising=False)


@pytest.fixture(autouse=True)
def _no_real_keystore_and_no_real_browser(request, monkeypatch):
    """A test hands in a store for secrets in memory and an opener of its
    own. One that forgot would, on a developer's computer, write over their
    own sign-in in the Credential Manager or the keychain, or open tabs in
    their browser: here that fails the test instead.

    The failure is also written down and looked at again when the test is
    over: Walka swallows an error of the browser's, and the browser is
    opened on a thread of its own, where an error fails nothing.

    (A test that means the real thing says so: ``@pytest.mark.real_keystore``,
    ``@pytest.mark.real_browser``. The two round trips through a real store
    in test_walka_account_keystore.py make their store themselves and do
    not come this way.)
    """
    import webbrowser

    from walka.walka_account import keystore

    reached: list[str] = []

    def no_keystore(*_args, **_how):
        reached.append("the system's store for secrets was opened (walka_account.keystore.open_keystore)")
        raise AssertionError(reached[-1] + ": hand the test a store in memory")

    def no_browser(*_args, **_how):
        reached.append("the real web browser was opened (webbrowser.open)")
        raise AssertionError(reached[-1] + ": hand the test an opener of its own")

    if request.node.get_closest_marker("real_keystore") is None:
        monkeypatch.setattr(keystore, "open_keystore", no_keystore)
    if request.node.get_closest_marker("real_browser") is None:
        monkeypatch.setattr(webbrowser, "open", no_browser)
    yield reached
    assert reached == []


@pytest.fixture(autouse=True)
def message_boxes(monkeypatch) -> list[str]:
    """On Windows, without a console, Walka says some things in a message
    box - which waits for a click that no test is there to give. Here a box
    is only written down."""
    from walka import window

    shown: list[str] = []
    monkeypatch.setattr(window, "message_box", lambda text, **_how: shown.append(text))
    return shown


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO


@pytest.fixture(scope="session")
def addon_source() -> Path:
    """The addon's source folder in this checkout (read-only for tests)."""
    return REPO / "Walka"


@pytest.fixture
def lua():
    """A fresh Lua 5.1 runtime (the game's Lua version)."""
    from walka_fakes import new_lua

    return new_lua()


@pytest.fixture(autouse=True)
def _no_bluetooth_pacing(monkeypatch):
    """The reader leaves real-time gaps between Bluetooth subscriptions for
    the benefit of cheap radio modules; the emulated pad does not need them."""
    from walka.treadmill import ftms_ble

    monkeypatch.setattr(ftms_ble, "SUBSCRIBE_GAP_S", 0.0)
