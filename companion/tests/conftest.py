"""Shared set-up for the companion test-suite.

Makes the suite runnable from any working directory and in any pytest import
mode: the helper modules next to this file (``walka_fakes``) and the
``walka`` package itself are put on ``sys.path`` if they are not already.
"""

from __future__ import annotations

import importlib.util
import ipaddress
import os
import shutil
import socket
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
    config.addinivalue_line(
        "markers",
        "real_address: this test reads where the Walka accounts really are (walka_account.project) - and asks nobody",
    )


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
    """A Walka that runs from its source code reads two variables when it
    starts: they name a database of its developer's own
    (cli._build_walka_account). Whoever runs the tests may have set them
    for a trial of their own: no test may pick that up - not in this
    process, and not in a Walka it starts as a process of its own."""
    monkeypatch.delenv("WALKA_ACCOUNT_URL", raising=False)
    monkeypatch.delenv("WALKA_ACCOUNT_KEY", raising=False)


def _take_the_real_address_away() -> None:
    """Walka knows where the Walka accounts really are
    (walka_account.project). The tests do not: the two are emptied here,
    the moment this file is read - before any test file is read, before
    any fixture of whatever scope, and for as long as the tests run. So
    nothing that runs in this process finds them by coming too early or
    too late: a file of tests that reads them as it is loaded, a fixture
    of a whole file, a thread that a test left behind."""
    from walka.walka_account import project

    project.ADDRESS = project.PUBLISHABLE_KEY = ""


_take_the_real_address_away()


def _the_real_two_as_the_file_says_them() -> tuple[str, str]:
    """Read anew from the one file that holds them (and kept nowhere here)."""
    from walka.walka_account import project

    spec = importlib.util.spec_from_file_location("walka_account_project_as_it_stands", project.__file__)
    as_it_stands = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(as_it_stands)
    return as_it_stands.ADDRESS, as_it_stands.PUBLISHABLE_KEY


@pytest.fixture(autouse=True)
def _no_walka_account_built_in(request, monkeypatch) -> None:
    """Walka has its part by default, made with the address that is built
    in. In a test it knows no such place (see above): a Walka that a test
    starts has no part unless the test hands one in, sets the two
    variables to a stand-in on this computer, or gives made-up constants
    of its own. So no test can come to ask the real accounts by
    forgetting something. (Emptied once more for each test: one that set
    them and did not put them back hands nothing on to the next.)

    (One test reads the real two, to see that they are of the right
    kind: ``@pytest.mark.real_address``. For that test alone they are
    there, and it uses neither. And a Walka that a test starts as a
    process of its own does not come this way: such a test gives its
    child made-up constants itself, or none, or the simulated treadmill.)
    """
    from walka.walka_account import project

    address = key = ""
    if request.node.get_closest_marker("real_address") is not None:
        address, key = _the_real_two_as_the_file_says_them()
    monkeypatch.setattr(project, "ADDRESS", address)
    monkeypatch.setattr(project, "PUBLISHABLE_KEY", key)


def on_this_computer(host: object) -> bool:
    """Is ``host`` this computer itself - "localhost", or an address that begins 127 (or ::1)?"""
    if isinstance(host, (bytes, bytearray)):
        host = bytes(host).decode("ascii", "replace")
    if not isinstance(host, str):
        return False
    if host.lower() in ("localhost", "localhost."):
        return True
    try:
        address = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return address.is_loopback


def is_an_address(host: object) -> bool:
    """Numbers already, so that nobody has to be asked whose name it is?"""
    if isinstance(host, (bytes, bytearray)):
        host = bytes(host).decode("ascii", "replace")
    if not isinstance(host, str):
        return False
    try:
        ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return False
    return True


class Reached(list):
    """What was reached for outside this computer, in words. ``ways``:
    what each way out hands on to once it has let something through - the
    system's own connect and look-up. (The test of the look itself puts
    stand-ins there: it must not depend on the look to stay at home.)"""

    ways: dict


# No test asks another computer anything - and since Walka knows where
# the Walka accounts really are, there is somebody to ask. Every
# connection of this process that goes anywhere but to this computer
# itself (127.0.0.1, ::1), and every look-up of a name other than
# "localhost", is refused before it is made, and fails a test.
#
# Where the look stands: on every socket of Python's (whatever thread it
# is connected on, or sends from without connecting), on the event loop's
# own way of connecting where that goes round the socket (Windows), and
# on the look-ups. A program that a test starts as a process of its own
# is not reached from here.
#
# It is put there once, when this file is read, and stays for as long as
# the tests run - not for each test and gone again after it. What a test
# leaves behind (a thread that still waits, a task) gets to the network
# when the test is over, and so does a fixture of a whole file before
# the first test of it: those are refused like everything else, written
# down in _BETWEEN_TESTS, and fail the test that comes next.
_THE_SYSTEMS_OWN: dict = {}
_BETWEEN_TESTS = Reached()
_looking: Reached = _BETWEEN_TESTS  # where a refusal is written down: the running test's list, or that one


def _refuse_whatever_leaves_this_computer() -> None:
    from asyncio import proactor_events  # (there on every system; used on Windows)

    _THE_SYSTEMS_OWN.update(
        {
            "connect": socket.socket.connect,
            "connect_ex": socket.socket.connect_ex,
            "sendto": socket.socket.sendto,
            "getaddrinfo": socket.getaddrinfo,
            "gethostbyname": socket.gethostbyname,
            "gethostbyname_ex": socket.gethostbyname_ex,
            "sock_connect": proactor_events.BaseProactorEventLoop.sock_connect,
        }
    )
    _BETWEEN_TESTS.ways = dict(_THE_SYSTEMS_OWN)

    def check(family: object, address: object) -> Reached:
        reached = _looking
        if family not in (socket.AF_INET, socket.AF_INET6):
            return reached  # (a socket between two ends on this computer: what the event loop wakes itself with)
        host = address[0] if isinstance(address, tuple) and address else address
        if not on_this_computer(host):
            reached.append("a connection was opened to somewhere that is not this computer")
            # (Not where to: it could be where the accounts are, and this text is printed.)
            raise AssertionError(reached[-1] + ": a test asks 127.0.0.1 only")
        return reached

    def name(host: object) -> Reached:
        reached = _looking
        if host is None or host == "" or host == b"" or is_an_address(host) or on_this_computer(host):
            return reached  # (no name, or numbers already: nobody is asked whose they are)
        reached.append("a name was looked up that is not this computer's")
        raise AssertionError(reached[-1] + ": a test asks 127.0.0.1 only, by its number")

    def guarded_connect(sock, address, *more):
        return check(sock.family, address).ways["connect"](sock, address, *more)

    def guarded_connect_ex(sock, address, *more):
        return check(sock.family, address).ways["connect_ex"](sock, address, *more)

    # (Without connecting first, as a look-up of a name goes when it is done by hand: where to comes last.)
    def guarded_sendto(sock, data, *more):
        return check(sock.family, more[-1] if more else None).ways["sendto"](sock, data, *more)

    def guarded_getaddrinfo(host, *more, **how):
        return name(host).ways["getaddrinfo"](host, *more, **how)

    def guarded_gethostbyname(host, *more):
        return name(host).ways["gethostbyname"](host, *more)

    def guarded_gethostbyname_ex(host, *more):
        return name(host).ways["gethostbyname_ex"](host, *more)

    # Windows' own event loop connects by the socket's number, past the socket's "connect".
    async def guarded_sock_connect(loop, sock, address):
        return await check(sock.family, address).ways["sock_connect"](loop, sock, address)

    socket.socket.connect = guarded_connect  # type: ignore[method-assign]
    socket.socket.connect_ex = guarded_connect_ex  # type: ignore[method-assign]
    socket.socket.sendto = guarded_sendto  # type: ignore[method-assign]
    socket.getaddrinfo = guarded_getaddrinfo
    socket.gethostbyname = guarded_gethostbyname
    socket.gethostbyname_ex = guarded_gethostbyname_ex
    proactor_events.BaseProactorEventLoop.sock_connect = guarded_sock_connect  # type: ignore[method-assign]


_refuse_whatever_leaves_this_computer()


def reached_between_tests() -> list[str]:
    """What was reached for while no test was running, since this was last asked."""
    late = list(_BETWEEN_TESTS)
    _BETWEEN_TESTS.clear()
    return late


@pytest.fixture(scope="session", autouse=True)
def _nothing_leaves_this_computer_after_the_last_test_either():
    yield
    late = reached_between_tests()
    assert late == [], "after a test was over, something it left behind reached for another computer"


@pytest.fixture(autouse=True)
def _nothing_leaves_this_computer():
    """What this test reaches for outside this computer (see above) is
    refused, and written down here. As with the store and the browser
    (below), that is looked at again when the test is over: Walka takes
    a connection that was refused for "no answer" and goes on.

    And what was reached for since the test before this one ended - by
    something that test left behind, or by a fixture that ran before
    this test began - fails this one, before it starts.
    """
    global _looking
    late = reached_between_tests()
    assert late == [], "before this test began, something reached for another computer: a fixture, or what a test left behind"
    reached = Reached()
    reached.ways = dict(_THE_SYSTEMS_OWN)
    _looking = reached
    try:
        yield reached
    finally:
        _looking = _BETWEEN_TESTS
    assert list(reached) == []


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
