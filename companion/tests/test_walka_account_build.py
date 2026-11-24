"""What a build machine checks of the Walka account's part, besides HTTPS
(test_walka_account_httpscheck.py):

* that the program it has packed has no such part, whatever it is started
  with - tools/check_no_account.py, run by both build jobs on the program
  itself;
* that no proof is skipped there for want of a tool (conftest.py).

Nothing here asks another computer. Walka is started from its source
code, with a treadmill that is not there; the address its two variables
name is one on this computer where nobody listens.
"""

from __future__ import annotations

import ast
import importlib.util
import os
import re
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest

import conftest
from walka import cli
from walka.walka_account import https, ledger

SRC = Path(cli.__file__).resolve().parent.parent


@pytest.fixture
def tool(repo_root: Path):
    """tools/check_no_account.py, as the build has it."""
    spec = importlib.util.spec_from_file_location("walka_check_no_account", repo_root / "tools" / "check_no_account.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---- the check, on Walka itself ----------------------------------------------------------------------

# Walka's own command line, whole, in a process of its own - with the one
# thing no test machine has stood in for: a treadmill that is listened to
# over Bluetooth. This one is of the real kind and is never found, which
# is what Walka meets on a build machine. Asked for the simulated one - by
# a switch, or by its settings - Walka gets that, as ever. (And the store
# for secrets is one in memory: no test touches the system's own.)
WALKA = """\
import sys

sys.path.insert(0, {src!r})
from walka import cli
from walka.treadmill import TreadmillError, TreadmillStatus
from walka.walka_account import keystore

how = sys.argv[1]


class Pad:
    kind = "ftms"

    def __init__(self):
        self.status = TreadmillStatus()
        self.address = None
        self.units = "auto"
        self.on_event = None

    async def connect(self):
        raise TreadmillError("no treadmill found")

    async def disconnect(self):
        pass

    async def discover(self, timeout):
        return []


walkas_own = cli._build_treadmill
cli._build_treadmill = lambda cfg, simulate: (
    walkas_own(cfg, simulate) if simulate or cfg.treadmill.kind == "simulated" else Pad()
)
keystore.open_keystore = keystore.MemoryKeyStore
# What a packed program has that Walka from its source code has not (PyInstaller sets both).
if how == "frozen":
    sys.frozen = True
if how == "_MEIPASS":
    sys._MEIPASS = {src!r}
sys.exit(cli.main(sys.argv[2:]))
"""


def walka(tmp_path: Path, how: str, *more: str) -> list[str]:
    script = tmp_path / "walka_child.py"
    script.write_text(WALKA.format(src=str(SRC)), "utf-8")
    return [sys.executable, str(script), how, *more]


def port_of(said: str) -> int:
    return int(re.search(r"--port (\d+)", said).group(1))


def nobody_listens_on(port: int) -> bool:
    try:
        socket.create_connection(("127.0.0.1", port), timeout=5).close()
    except OSError:
        return True
    return False


@pytest.mark.parametrize("how", ["frozen", "_MEIPASS"])
def test_the_check_passes_a_walka_that_is_packed(tool, tmp_path: Path, capsys, how: str):
    """With either mark of a packed program Walka has no part, though a
    real treadmill is asked for and both variables are set: nothing in
    the state, no button, no file."""
    found = tool.check(walka(tmp_path, how))
    assert found.startswith("started with a real treadmill asked for and both variables set, the program says nothing")
    said = capsys.readouterr().out
    # Not as the demo, and with neither its window nor a look at the screen: nothing that could ask this machine's user.
    assert re.search(r" --data-dir \S+ --no-capture --no-window --port \d+  \(with ", said)
    assert 'treadmill of the kind "ftms"' in said
    for route in tool.ROUTES:
        assert f"{route} 404" in said
    assert len(re.findall(r"/api/walka-account/\S+ 404", said)) == 7
    # It did start as Walka does, on the folder it was given: its own log says so.
    assert "in its data folder: walka.log" in said and " is running: dashboard at http://127.0.0.1:" in said
    assert "Walka account's part" not in said  # (nothing is said of a part that is not there)
    # The program is ended, and what was set for it was set for it alone.
    assert nobody_listens_on(port_of(said))
    assert tool.URL_VARIABLE not in os.environ and tool.KEY_VARIABLE not in os.environ
    assert tool.DATA_VARIABLE not in os.environ
    assert tool.MADE_UP_KEY not in said and tool.NOBODYS_ADDRESS not in said


def test_the_check_fails_walka_from_its_source_code_because_there_the_part_is(tool, tmp_path: Path, capsys):
    """The other half, without which the check above would prove nothing:
    started the very same way, Walka from its source code HAS the part -
    so the two variables did reach it, what they hold is taken, and the
    check sees a part where there is one. Every button is one that
    answers there: the list is the card's."""
    with pytest.raises(tool.CheckFailed) as caught:
        tool.check(walka(tmp_path, "from source"))
    wrong = str(caught.value)
    assert 'the dashboard\'s state has a "walka_account" key' in wrong
    assert "a button of the Walka-account card is there (not 404): " in wrong
    there = re.findall(r"(/api/walka-account/\S+) (\d+)", wrong.split("(not 404): ")[1])
    assert [route for route, _status in there] == list(tool.ROUTES)
    assert all(status != "404" and int(status) < 500 for _route, status in there), there
    said = capsys.readouterr().out
    assert "Walka has its Walka account's part: it is run from its source code" in said
    assert nobody_listens_on(port_of(said))
    assert tool.MADE_UP_KEY not in said and tool.NOBODYS_ADDRESS not in said


@pytest.mark.parametrize("switch", ["--simulate", "--demo"])
def test_the_check_says_so_when_it_was_given_the_simulated_treadmill(tool, tmp_path: Path, capsys, switch: str):
    """With the simulated treadmill Walka has no part from its source code
    either: a check made like that - as the demo, say, which is how the
    smoke test starts the program - would pass and show nothing. It fails,
    and says why. (Here even Walka from source would pass for the rest.)"""
    with pytest.raises(tool.CheckFailed) as caught:
        tool.check(walka(tmp_path, "from source", switch))
    assert 'the treadmill in use is of the kind "simulated", not a real one' in str(caught.value)
    assert "nothing was checked" in str(caught.value)
    assert nobody_listens_on(port_of(capsys.readouterr().out))


def test_what_the_check_sets_is_what_walka_reads_and_would_take(tool, repo_root: Path):
    """An address or a key that Walka turns down gives no part from source
    either, and a variable under another name is not read at all: the
    check would pass, and show nothing."""
    assert (tool.URL_VARIABLE, tool.KEY_VARIABLE) == (cli.ACCOUNT_URL_VARIABLE, cli.ACCOUNT_KEY_VARIABLE)
    origin = https.parse_origin(tool.NOBODYS_ADDRESS)
    assert origin is not None and origin.host == "127.0.0.1"  # (and it is on this computer)
    assert https.key_is_usable(tool.MADE_UP_KEY)
    config_text = (SRC / "walka" / "config.py").read_text("utf-8")
    assert f'os.environ.get("{tool.DATA_VARIABLE}")' in config_text
    # Every button of the card, and no other; the part's two files begin as the check says.
    web_text = (SRC / "walka" / "web.py").read_text("utf-8")
    assert sorted(re.findall(r'@walka_account_route\("([^"]+)"\)', web_text)) == sorted(tool.ROUTES)
    assert len(tool.ROUTES) == 7
    assert ledger.FILE_NAME.startswith(tool.FILES_BEGIN_WITH) and ledger.LOCK_NAME.startswith(tool.FILES_BEGIN_WITH)


# ---- the check, on programs that get it wrong -------------------------------------------------------

# A dashboard that is not Walka's, to get one thing wrong at a time. (It
# finds its port and its data folder as Walka does. Not Python's HTTP
# server whole: that one asks for this computer's name when it starts.)
STAND_IN = """\
import json
import socketserver
import sys
import time
from http.server import BaseHTTPRequestHandler
from pathlib import Path

how = sys.argv[1]
port = int(sys.argv[sys.argv.index("--port") + 1])
data = Path(sys.argv[sys.argv.index("--data-dir") + 1])
if how != "writes nothing":
    data.mkdir(parents=True, exist_ok=True)
    (data / "walka.log").write_text("up")
if how == "keeps a file":
    (data / "walka-account.json").write_text("{}")
if how == "keeps a lock":
    (data / "sub").mkdir()
    (data / "sub" / "walka-account.lock").write_text("")
if how == "ends at once":
    sys.exit(3)
if how == "never serves":
    time.sleep(60)


class Dashboard(BaseHTTPRequestHandler):
    def answer(self, status, said):
        body = json.dumps(said).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path != "/api/state" or how == "has no state":
            return self.answer(404, {})
        state = {"version": "0", "treadmill": {"kind": "simulated" if how == "simulated" else "ftms"}}
        if how == "says it in the state":
            state["walka_account"] = None
        if how == "says something else":
            state = ["not", "Walka"]
        self.answer(200, state)

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        there = how == "has one button" and self.path == "/api/walka-account/sending/off"
        self.answer(409 if there else 404, {"ok": False})

    def log_message(self, *_args):
        pass


class Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


Server(("127.0.0.1", port), Dashboard).serve_forever()
"""


def stand_in(tmp_path: Path, how: str) -> list[str]:
    script = tmp_path / "stand_in.py"
    script.write_text(STAND_IN, "utf-8")
    return [sys.executable, str(script), how]


def test_the_check_passes_a_program_that_has_none_of_it(tool, tmp_path: Path, capsys):
    """The stand-in is a program the check passes, when it gets nothing
    wrong: each failure below is of the one thing that was changed."""
    assert tool.check(stand_in(tmp_path, "all is well")).startswith("started with a real treadmill asked for")
    assert nobody_listens_on(port_of(capsys.readouterr().out))


GETS_IT_WRONG = [
    ("says it in the state", 'the dashboard\'s state has a "walka_account" key'),
    ("has one button", "a button of the Walka-account card is there (not 404): /api/walka-account/sending/off 409"),
    ("simulated", 'the treadmill in use is of the kind "simulated", not a real one'),
    ("keeps a file", "the data folder holds walka-account.json"),
    ("keeps a lock", "the data folder holds sub/walka-account.lock"),
    ("writes nothing", "the program wrote nothing into the data folder it was given"),
    ("says something else", "the dashboard's state is not what Walka's is (status 200)"),
    ("ends at once", "the program ended before its dashboard answered (exit code 3)"),
]


@pytest.mark.parametrize("how, said", GETS_IT_WRONG, ids=[how for how, _said in GETS_IT_WRONG])
def test_the_check_fails_a_program_that_gets_one_thing_wrong(
    tool, tmp_path: Path, capsys, monkeypatch, how: str, said: str
):
    monkeypatch.setattr(tool, "WRITES_WITHIN_S", 0.5)
    with pytest.raises(tool.CheckFailed) as caught:
        tool.check(stand_in(tmp_path, how))
    assert str(caught.value) == said or str(caught.value).startswith(said + ":"), str(caught.value)
    capsys.readouterr()


@pytest.mark.parametrize("how", ["never serves", "has no state"])
def test_the_check_does_not_wait_for_ever_and_leaves_no_program_behind(tool, tmp_path: Path, capsys, how: str):
    """A program that hangs before its dashboard is up - on a question
    of the system's that nobody is there to answer, say - is waited for
    so long and no longer, and is then ended."""
    with pytest.raises(tool.CheckFailed) as caught:
        tool.check(stand_in(tmp_path, how), starts_within_s=1.0)
    assert str(caught.value) == "the dashboard did not answer within 1 seconds"
    said = capsys.readouterr().out
    assert "the program was ended in " in said and nobody_listens_on(port_of(said))

    with pytest.raises(tool.CheckFailed) as caught:
        tool.check([str(tmp_path / "no-such-program")])
    assert "could not be started" in str(caught.value)
    capsys.readouterr()


def test_as_a_command_it_says_one_line_at_the_end_and_the_build_stops_on_a_failure(
    tool, tmp_path: Path, capsys, monkeypatch
):
    monkeypatch.setattr(sys, "argv", ["check_no_account.py", *stand_in(tmp_path, "all is well")])
    assert tool.main() == 0
    last = capsys.readouterr().out.strip().splitlines()[-1]
    assert last.startswith("No Walka account in the program: OK - started with ") and last.endswith(" no file of one.")

    monkeypatch.setattr(sys, "argv", ["check_no_account.py", *stand_in(tmp_path, "says it in the state")])
    assert tool.main() == 1
    said = capsys.readouterr().out
    assert said.strip().splitlines()[-1] == (
        'No Walka account in the program: FAILED - the dashboard\'s state has a "walka_account" key.'
    )
    # ... with what the program wrote, for whoever reads the build's log.
    assert "--- walka.log\nup\n---" in said

    monkeypatch.setattr(sys, "argv", ["check_no_account.py"])
    assert tool.main() == 2
    assert "python tools/check_no_account.py companion/dist/Walka.exe" in capsys.readouterr().out


def test_the_check_needs_nothing_but_python(repo_root: Path):
    """It is run by the build machine's own Python, before and beside
    everything that is installed there."""
    tree = ast.parse((repo_root / "tools" / "check_no_account.py").read_text("utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0
            imported.add(node.module.split(".")[0])
    assert imported <= set(sys.stdlib_module_names) | {"__future__"}, imported


# ---- the build runs it ---------------------------------------------------------------------------------


def job(text: str, name: str) -> str:
    start = text.index(f"\n  {name}:\n")
    following = re.search(r"\n  [a-z][\w-]*:\n", text[start + 1 :])
    return text[start : start + 1 + following.start()] if following else text[start:]


def test_both_builds_look_for_the_part_in_the_packed_program(repo_root: Path):
    """Walka.exe and both Mac programs (one job, two kinds of Mac) - each
    is started once with everything set that the part reads, after it is
    known to start at all and before it is packed up to be handed out."""
    text = (repo_root / ".github" / "workflows" / "ci.yml").read_text("utf-8")
    windows, mac = job(text, "windows-exe"), job(text, "mac-app")
    for built, command in (
        (windows, "run: python tools/check_no_account.py companion/dist/Walka.exe\n"),
        (mac, "run: python tools/check_no_account.py companion/dist/Walka.app/Contents/MacOS/Walka\n"),
    ):
        assert built.count("run: python tools/check_no_account.py") == 1 and command in built
        assert built.index("- name: Smoke test") < built.index(command) < built.index("- name: Package")
    assert text.count("run: python tools/check_no_account.py") == 2
    assert "macos-latest" in mac and "macos-15-intel" in mac
    # A build that could not look must fail: never passed over.
    assert "continue-on-error" not in text
    # What is set for that one run is set by the helper, for the program alone: the workflow names
    # neither variable, and nothing of where the accounts are.
    assert "WALKA_ACCOUNT" not in text and "supabase" not in text.lower() and "sb_publishable" not in text
    # It needs nothing installed for it.
    assert len(re.findall(r"(?m)^\s*run:\s*python -m pip install", text)) == 3


# ---- on a build machine no proof is skipped for want of a tool ----------------------------------------


def test_what_a_proof_needs_is_looked_for_as_the_tests_look_for_it(repo_root: Path, tmp_path: Path, monkeypatch):
    # (Whatever this computer has: with every look turned away, each of the three is named.)
    real_run = subprocess.run
    monkeypatch.setattr(shutil, "which", lambda _name, *_how, **_more: None)

    def no_git(command, *args, **how):
        if command[0] == "git":
            raise FileNotFoundError("git")
        return real_run(command, *args, **how)

    monkeypatch.setattr(subprocess, "run", no_git)
    missing = conftest.tools_not_here(repo_root)
    assert any(text.startswith("Node.js ") for text in missing)
    assert any(text.startswith("git ") for text in missing)
    if sys.platform != "win32":  # (there a copy of openssl that Git brought will do, and is looked for by its path)
        assert any('"openssl"' in text for text in missing) and len(missing) == 3

    # A folder that git does not keep: the test that searches "every file git keeps" would be skipped.
    monkeypatch.setattr(subprocess, "run", real_run)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))  # (wherever the folder for tests lies)
    (tmp_path / "tools").mkdir()
    shutil.copy(repo_root / "tools" / "check_https.py", tmp_path / "tools" / "check_https.py")
    missing = conftest.tools_not_here(tmp_path)
    assert any(text.startswith("git ") for text in missing)


def run_one_test(repo_root: Path, **environment: str) -> tuple[int, str]:
    """One small test of this suite, in a pytest of its own, on a machine that is made out to be another."""
    env = {name: value for name, value in os.environ.items() if name != "CI"}
    env.update(environment)
    ran = subprocess.run(
        [
            sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
            "companion/tests/test_walka_account_httpscheck.py::test_httpscheck_is_not_advertised",
        ],  # fmt: skip
        cwd=repo_root,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=300,
    )
    return ran.returncode, ran.stdout.decode("utf-8", "replace") + ran.stderr.decode("utf-8", "replace")


def test_on_a_build_machine_a_missing_tool_stops_the_run_and_elsewhere_it_only_skips(repo_root: Path, tmp_path: Path):
    """Twenty-three tests of the card need Node, nine need openssl and one
    needs git: without its tool each is skipped, and pytest stays green. On
    somebody's own computer that is right. On a build machine nobody reads
    the list of what was skipped - so there it is a failure, of the whole run."""
    nothing_here = tmp_path / "an empty folder"
    nothing_here.mkdir()

    code, said = run_one_test(repo_root, CI="true", PATH=str(nothing_here))
    assert code == 1, said
    assert "This is a build machine (CI is set), and tests would be skipped on it for want of: " in said
    assert "Node.js" in said and 'git (the command "git"' in said and "no test was run" in said
    assert "1 passed" not in said

    # Not a build machine: the same computer, and the test that needs none of the three runs.
    code, said = run_one_test(repo_root, PATH=str(nothing_here))
    assert code == 0 and "1 passed" in said, said

    # A build machine that has all three (as every one of them has today): nothing is said.
    if not conftest.tools_not_here(repo_root):
        code, said = run_one_test(repo_root, CI="true")
        assert code == 0 and "1 passed" in said, said
