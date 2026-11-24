"""What a build machine checks of the Walka account's part, besides HTTPS
(test_walka_account_httpscheck.py):

* that the program it has packed has the part as a download must have it
  - there with a real treadmill, made with the address that is built in
  whatever the two variables say, nobody signed in; and not there at all
  as the demo - tools/check_account_part.py, run by both build jobs on
  the program itself;
* that no proof is skipped there for want of a tool (conftest.py).

Nothing here asks another computer. Walka is started from its source
code, with a treadmill that is not there; the address that is "built in"
is a made-up one on this computer, and so is the one its two variables
name: nobody listens at either, and nobody is ever signed in.
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
    """tools/check_account_part.py, as the build has it."""
    spec = importlib.util.spec_from_file_location(
        "walka_check_account_part", repo_root / "tools" / "check_account_part.py"
    )
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
#
# A process of its own does not come by conftest.py: left alone, this
# Walka would know where the Walka accounts really are. So the first
# thing it is given is a built-in address of this test's own, on this
# computer, where nobody listens.
BUILT_IN_ADDRESS, BUILT_IN_KEY = "http://127.0.0.1:19", "built-in-test-key"
WALKA = """\
import sys

sys.path.insert(0, {src!r})
from walka import cli
from walka.treadmill import TreadmillError, TreadmillStatus
from walka.walka_account import keystore, project

project.ADDRESS, project.PUBLISHABLE_KEY = {built_in!r}
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
if how in ("frozen", "packed and knows no address"):
    sys.frozen = True
if how == "_MEIPASS":
    sys._MEIPASS = {src!r}
if how == "packed and knows no address":
    project.ADDRESS = project.PUBLISHABLE_KEY = ""
sys.exit(cli.main(sys.argv[2:]))
"""


def walka(tmp_path: Path, how: str, *more: str) -> list[str]:
    script = tmp_path / "walka_child.py"
    script.write_text(WALKA.format(src=str(SRC), built_in=(BUILT_IN_ADDRESS, BUILT_IN_KEY)), "utf-8")
    return [sys.executable, str(script), how, *more]


def ports_of(said: str) -> list[int]:
    return [int(port) for port in re.findall(r"--port (\d+)", said)]


def nobody_listens_on(port: int) -> bool:
    try:
        socket.create_connection(("127.0.0.1", port), timeout=5).close()
    except OSError:
        return True
    return False


def both_programs_are_ended(said: str) -> bool:
    """The check starts the program twice. Each time it is ended, and nothing answers where it served."""
    ports = ports_of(said)
    return len(ports) == 2 and ports[0] != ports[1] and all(nobody_listens_on(port) for port in ports)


def runs_of(said: str) -> tuple[str, str]:
    """What the check said of its first run (as people start it), and of its second (as the demo)."""
    assert said.count("--- as people start it\n") == 1 and said.count("--- as the demo\n") == 1
    first, second = said.split("--- as the demo\n")
    return first, second


@pytest.mark.parametrize("how", ["frozen", "_MEIPASS"])
def test_the_check_passes_a_walka_that_is_packed(tool, tmp_path: Path, capsys, how: str):
    """With either mark of a packed program Walka has the part that is
    built in, though both variables name another address: nobody signed
    in, sending off, "Stop sending" there, no file. And as the demo it
    has none: nothing in the state, no button."""
    found = tool.check(walka(tmp_path, how))
    assert found.startswith("started with a real treadmill asked for and both variables set, the program has its Walka")
    assert "made with another address than the variables name" in found and found.endswith("is never pressed)")
    said = capsys.readouterr().out
    first, second = runs_of(said)
    # Not as the demo, and with neither its window nor a look at the screen: nothing that could ask this machine's user.
    assert re.search(r" --no-capture --data-dir \S+ --no-window --port \d+  \(with ", first) and "--demo" not in first
    assert 'treadmill of the kind "ftms"' in first and "simulated" not in first
    assert "the card says that nobody is signed in and sending is off, and the address it shows is not 127.0.0.1:9" in first
    # One button was pressed, and it is one that asks nobody.
    assert re.findall(r"(/api/walka-account/\S+) (\d+)", first) == [("/api/walka-account/sending/off", "409")]
    # It did start as Walka does, on the folder it was given: its own log says so. And no file of the part's.
    assert "in its data folder: walka.log" in first and " is running: dashboard at http://127.0.0.1:" in first
    # As the demo: the simulated treadmill, and every button but "Sign in" says "not here".
    assert re.search(r" --demo --data-dir \S+ --no-window --port \d+  \(with ", second)
    assert 'treadmill of the kind "simulated"' in second
    pressed = re.findall(r"(/api/walka-account/\S+?),? (\d+)", second.split("answer, to a POST: ")[1].splitlines()[0])
    assert pressed == [(route, "404") for route in tool.ROUTES if route != tool.SIGN_IN] and len(pressed) == 6
    assert "walka-account" not in "".join(line for line in said.splitlines() if line.startswith("in its data folder: "))
    # "Sign in" was never pressed, and nothing is said of a part (it is there, quietly) or of the variables' key.
    assert tool.SIGN_IN not in said and "Walka account's part" not in said
    assert tool.MADE_UP_KEY not in said and BUILT_IN_KEY not in said and BUILT_IN_ADDRESS[7:] not in said
    # The programs are ended, and what was set for them was set for them alone.
    assert both_programs_are_ended(said)
    assert tool.URL_VARIABLE not in os.environ and tool.KEY_VARIABLE not in os.environ
    assert tool.DATA_VARIABLE not in os.environ


def test_the_check_fails_walka_from_its_source_code_because_there_the_variables_are_read(tool, tmp_path: Path, capsys):
    """The other half, without which the check above would prove nothing:
    started the very same way, Walka from its source code takes the two
    variables - so they did reach it, what they hold is one Walka takes,
    and the check sees whose address the part was made with."""
    with pytest.raises(tool.CheckFailed) as caught:
        tool.check(walka(tmp_path, "from source"))
    assert str(caught.value) == (
        "the part is made with the address that WALKA_ACCOUNT_URL names (127.0.0.1:9): "
        "a packaged program must not read that variable"
    )
    said = capsys.readouterr().out
    first, second = runs_of(said)
    assert "Walka has its Walka account's part: it is run from its source code" in first
    # (With the simulated treadmill there is none from source either, and the log says why.)
    assert "Walka has no Walka account's part: the treadmill is the simulated one" in second
    assert both_programs_are_ended(said)
    assert tool.MADE_UP_KEY not in said and tool.SIGN_IN not in said


def test_the_check_fails_a_packed_walka_that_has_no_part(tool, tmp_path: Path, capsys):
    """What every download was until this release: packed, and nothing of
    a Walka account in it. The check that passed it then fails it now."""
    with pytest.raises(tool.CheckFailed) as caught:
        tool.check(walka(tmp_path, "packed and knows no address"))
    assert str(caught.value) == (
        'the dashboard\'s state has no "walka_account" key: the program has no Walka account\'s part; '
        "the buttons of the Walka-account card are not there: /api/walka-account/sending/off 404"
    )
    assert both_programs_are_ended(capsys.readouterr().out)


@pytest.mark.parametrize("switch", ["--simulate", "--demo"])
def test_the_check_says_so_when_it_was_given_the_simulated_treadmill(tool, tmp_path: Path, capsys, switch: str):
    """With the simulated treadmill no Walka has the part: a check that is
    itself started like that - as the demo, say, which is how the smoke
    test starts the program - looks at nothing in its first run. It
    fails, and says why."""
    with pytest.raises(tool.CheckFailed) as caught:
        tool.check(walka(tmp_path, "frozen", switch))
    assert 'the treadmill in use is of the kind "simulated", not a real one' in str(caught.value)
    assert "nothing was checked" in str(caught.value)
    assert 'the dashboard\'s state has no "walka_account" key' in str(caught.value)
    assert both_programs_are_ended(capsys.readouterr().out)


def test_what_the_check_sets_is_what_walka_reads_and_would_take(tool, repo_root: Path):
    """An address or a key that Walka turns down gives no part from source
    either, and a variable under another name is not read at all: a
    packed program that did read them would pass, and nothing be shown."""
    assert (tool.URL_VARIABLE, tool.KEY_VARIABLE) == (cli.ACCOUNT_URL_VARIABLE, cli.ACCOUNT_KEY_VARIABLE)
    origin = https.parse_origin(tool.NOBODYS_ADDRESS)
    assert origin is not None and origin.host == "127.0.0.1"  # (and it is on this computer)
    assert tool.NOBODYS_HOST == f"{origin.host}:{origin.port}"  # (as the card shows an address: service._snapshot)
    assert https.key_is_usable(tool.MADE_UP_KEY)
    config_text = (SRC / "walka" / "config.py").read_text("utf-8")
    assert f'os.environ.get("{tool.DATA_VARIABLE}")' in config_text
    # Every button of the card, and no other; the part's two files begin as the check says.
    web_text = (SRC / "walka" / "web.py").read_text("utf-8")
    assert sorted(re.findall(r'@walka_account_route\("([^"]+)"\)', web_text)) == sorted(tool.ROUTES)
    assert len(tool.ROUTES) == 7 and tool.SIGN_IN in tool.ROUTES and tool.STOP_SENDING in tool.ROUTES
    assert ledger.FILE_NAME.startswith(tool.FILES_BEGIN_WITH) and ledger.LOCK_NAME.startswith(tool.FILES_BEGIN_WITH)
    # What this file's own Walka is given as "built in" is an address Walka takes - on this computer,
    # and not the one the variables name.
    built_in = https.parse_origin(BUILT_IN_ADDRESS)
    assert built_in is not None and built_in.host == "127.0.0.1" and f"127.0.0.1:{built_in.port}" != tool.NOBODYS_HOST
    assert https.key_is_usable(BUILT_IN_KEY)


def test_the_check_knows_nothing_of_where_the_accounts_are_and_never_presses_sign_in(tool, repo_root: Path):
    """The check is run on the program people download, which knows the
    real address. The check itself knows none, asks nobody but the
    dashboard on this computer, and prints nothing the card shows."""
    text = (repo_root / "tools" / "check_account_part.py").read_text("utf-8")
    assert "https://" not in text and "walka.dev" not in text and "supabase" not in text.lower()
    assert "sb_publishable" not in text and "sb_secret" not in text
    assert sorted(set(re.findall(r"http://[^\s\"'{]+", text))) == ["http://127.0.0.1:", "http://127.0.0.1:9"]
    with pytest.raises(tool.CheckFailed, match="never pressed"):
        tool.press("http://127.0.0.1:9", tool.SIGN_IN)
    # Nothing the card says is printed: of the piece, only single values are ever looked at.
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "say":
            names = {inner.id for inner in ast.walk(node) if isinstance(inner, ast.Name)}
            assert not names & {"piece", "state", "host", "answer", "after", "body"}, ast.unparse(node)
    assert not (repo_root / "tools" / "check_no_account.py").exists()


# ---- the check, on programs that get it wrong -------------------------------------------------------

# A dashboard that is not Walka's, to get one thing wrong at a time. (It
# finds its port and its data folder as Walka does, and whether it is
# started as the demo. Every request it gets is written down, for the test
# to read. Not Python's HTTP server whole: that one asks for this
# computer's name when it starts.)
STAND_IN = """\
import json
import socketserver
import sys
import time
from http.server import BaseHTTPRequestHandler
from pathlib import Path

how, seen = sys.argv[1], Path(sys.argv[2])
port = int(sys.argv[sys.argv.index("--port") + 1])
data = Path(sys.argv[sys.argv.index("--data-dir") + 1])
demo = "--demo" in sys.argv
if how != "writes nothing":
    data.mkdir(parents=True, exist_ok=True)
    (data / "walka.log").write_text("up")
if how == "keeps a file" and not demo:
    (data / "walka-account.json").write_text("{}")
if how == "keeps a lock" and not demo:
    (data / "sub").mkdir()
    (data / "sub" / "walka-account.lock").write_text("")
if how == "keeps a file as the demo" and demo:
    (data / "walka-account.json").write_text("{}")
if how == "ends at once":
    sys.exit(3)
if how == "ends at once as the demo" and demo:
    sys.exit(4)
if how == "never serves":
    time.sleep(60)
pressed = []


class Dashboard(BaseHTTPRequestHandler):
    def answer(self, status, said):
        body = json.dumps(said).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def note(self, method):
        with open(seen, "a") as book:
            book.write(("demo " if demo else "") + method + " " + self.path + "\\n")

    def do_GET(self):
        self.note("GET")
        if self.path != "/api/state" or how == "has no state":
            return self.answer(404, {})
        simulated = how != "the demo is not simulated" if demo else how == "simulated"
        state = {"version": "0", "treadmill": {"kind": "simulated" if simulated else "ftms"}}
        piece = {"state": "signed_out", "email": "", "sending": False, "host": "accounts.example", "store": "lasting"}
        if how == "somebody is signed in":
            piece.update(state="signed_in", email="walker@mail.example")
        if how == "an address is remembered":
            piece.update(email="walker@mail.example")
        if how == "sending is on":
            piece.update(sending=True)
        if how == "sending is not said":
            del piece["sending"]
        if how == "made with the variable's address":
            piece.update(host="127.0.0.1:9")
        if how == "says no address":
            piece.update(host="")
        if how == "the press signs somebody in" and pressed:
            piece.update(state="signed_in")
        if how == "the piece is no piece":
            piece = None
        if not demo and how != "has no part":
            state["walka_account"] = piece
        if demo and how == "says it as the demo":
            state["walka_account"] = piece
        if how == "says something else":
            state = ["not", "Walka"]
        self.answer(200, state)

    def do_POST(self):
        self.note("POST")
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        pressed.append(self.path)
        if demo or how in ("has no part", "has no buttons"):
            there = how == "has one button as the demo" and self.path == "/api/walka-account/notice/ok"
            return self.answer(200 if there else 404, {"ok": there})
        if self.path != "/api/walka-account/sending/off":
            return self.answer(500, {"ok": False})
        if how == "the button does something":
            return self.answer(200, {"ok": True})
        if how == "the button says something else":
            return self.answer(409, {"ok": False, "error": "busy"})
        if how == "the button answers no JSON":
            self.send_response(409)
            self.send_header("Content-Length", "2")
            self.end_headers()
            return self.wfile.write(b"no")
        self.answer(409, {"ok": False, "error": "not signed in"})

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
    return [sys.executable, str(script), how, str(tmp_path / "seen.txt")]


def seen_by_the_stand_in(tmp_path: Path) -> list[str]:
    return (tmp_path / "seen.txt").read_text("utf-8").splitlines()


def test_the_check_passes_a_program_that_gets_it_all_right(tool, tmp_path: Path, capsys):
    """The stand-in is a program the check passes, when it gets nothing
    wrong: each failure below is of the one thing that was changed. And
    what the check asked of it is all it ever asks of a program."""
    assert tool.check(stand_in(tmp_path, "all is well")).startswith("started with a real treadmill asked for")
    assert both_programs_are_ended(capsys.readouterr().out)
    seen = [line for line in seen_by_the_stand_in(tmp_path) if not line.endswith("GET /api/state")]
    assert seen == ["POST /api/walka-account/sending/off"] + [
        f"demo POST {route}" for route in tool.ROUTES if route != tool.SIGN_IN
    ]
    assert not [line for line in seen_by_the_stand_in(tmp_path) if "sign-in" in line]


STOP = "/api/walka-account/sending/off"
GETS_IT_WRONG = [
    # ... as people start it:
    (
        "has no part",
        'the dashboard\'s state has no "walka_account" key: the program has no Walka account\'s part; '
        f"the buttons of the Walka-account card are not there: {STOP} 404",
    ),
    ("has no buttons", f"the buttons of the Walka-account card are not there: {STOP} 404"),
    ("the piece is no piece", 'what the dashboard\'s state has under "walka_account" is not the card\'s piece'),
    ("somebody is signed in", "the card does not say that nobody is signed in"),
    ("an address is remembered", "the card does not say that nobody is signed in"),
    ("sending is on", "the card does not say that sending is off"),
    ("sending is not said", "the card does not say that sending is off"),
    (
        "made with the variable's address",
        "the part is made with the address that WALKA_ACCOUNT_URL names (127.0.0.1:9): "
        "a packaged program must not read that variable",
    ),
    ("says no address", "the card does not say where the accounts are"),
    ("the button does something", f'"Stop sending" does not answer that nobody is signed in: {STOP} 200'),
    ("the button says something else", f'"Stop sending" does not answer that nobody is signed in: {STOP} 409'),
    ("the button answers no JSON", f'"Stop sending" does not answer that nobody is signed in: {STOP} 409'),
    (
        "the press signs somebody in",
        "after that one press the card no longer says that nobody is signed in and sending is off",
    ),
    ("simulated", 'the treadmill in use is of the kind "simulated", not a real one'),
    ("keeps a file", "the data folder holds walka-account.json"),
    ("keeps a lock", "the data folder holds sub/walka-account.lock"),
    ("writes nothing", "the program wrote nothing into the data folder it was given"),
    ("says something else", "the dashboard's state is not what Walka's is (status 200)"),
    ("ends at once", "the program ended before its dashboard answered (exit code 3)"),
    # ... as the demo:
    ("says it as the demo", 'as the demo, the dashboard\'s state has a "walka_account" key'),
    (
        "has one button as the demo",
        "as the demo, a button of the Walka-account card is there (not 404): /api/walka-account/notice/ok 200",
    ),
    (
        "the demo is not simulated",
        'started as the demo, the treadmill in use is of the kind "ftms", not the simulated one',
    ),
    ("keeps a file as the demo", "the data folder holds walka-account.json"),
    ("ends at once as the demo", "the program ended before its dashboard answered (exit code 4)"),
]


@pytest.mark.parametrize("how, said", GETS_IT_WRONG, ids=[how for how, _said in GETS_IT_WRONG])
def test_the_check_fails_a_program_that_gets_one_thing_wrong(
    tool, tmp_path: Path, capsys, monkeypatch, how: str, said: str
):
    monkeypatch.setattr(tool, "WRITES_WITHIN_S", 0.5)
    with pytest.raises(tool.CheckFailed) as caught:
        tool.check(stand_in(tmp_path, how))
    assert str(caught.value) == said or str(caught.value).startswith(said + ":"), str(caught.value)
    # Whatever was wrong: "Sign in" was not pressed, and nothing the card shows was printed.
    printed = capsys.readouterr().out
    if (tmp_path / "seen.txt").exists():
        assert not [line for line in seen_by_the_stand_in(tmp_path) if "sign-in" in line]
    assert "accounts.example" not in printed and "walker@mail.example" not in printed


@pytest.mark.parametrize("how", ["never serves", "has no state"])
def test_the_check_does_not_wait_for_ever_and_leaves_no_program_behind(tool, tmp_path: Path, capsys, how: str):
    """A program that hangs before its dashboard is up - on a question
    of the system's that nobody is there to answer, say - is waited for
    so long and no longer, and is then ended."""
    with pytest.raises(tool.CheckFailed) as caught:
        tool.check(stand_in(tmp_path, how), starts_within_s=1.0)
    assert str(caught.value) == "the dashboard did not answer within 1 seconds"
    said = capsys.readouterr().out
    (port,) = ports_of(said)  # (the first run could not be looked at: there is no second)
    assert "the program was ended in " in said and nobody_listens_on(port)

    with pytest.raises(tool.CheckFailed) as caught:
        tool.check([str(tmp_path / "no-such-program")])
    assert "could not be started" in str(caught.value)
    capsys.readouterr()


def test_as_a_command_it_says_one_line_at_the_end_and_the_build_stops_on_a_failure(
    tool, tmp_path: Path, capsys, monkeypatch
):
    monkeypatch.setattr(sys, "argv", ["check_account_part.py", *stand_in(tmp_path, "all is well")])
    assert tool.main() == 0
    last = capsys.readouterr().out.strip().splitlines()[-1]
    assert last.startswith("The Walka account's part in the program: OK - started with ")
    assert last.endswith('buttons answer 404 ("Sign in" is never pressed).')

    monkeypatch.setattr(sys, "argv", ["check_account_part.py", *stand_in(tmp_path, "has no buttons")])
    assert tool.main() == 1
    said = capsys.readouterr().out
    assert said.strip().splitlines()[-1] == (
        "The Walka account's part in the program: FAILED - "
        "the buttons of the Walka-account card are not there: /api/walka-account/sending/off 404."
    )
    # ... with what the program wrote, for whoever reads the build's log.
    assert "--- walka.log\nup\n---" in said

    monkeypatch.setattr(sys, "argv", ["check_account_part.py"])
    assert tool.main() == 2
    assert "python tools/check_account_part.py companion/dist/Walka.exe" in capsys.readouterr().out


def test_the_check_needs_nothing_but_python(repo_root: Path):
    """It is run by the build machine's own Python, before and beside
    everything that is installed there."""
    tree = ast.parse((repo_root / "tools" / "check_account_part.py").read_text("utf-8"))
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


def test_both_builds_look_at_the_part_in_the_packed_program(repo_root: Path):
    """Walka.exe and both Mac programs (one job, two kinds of Mac) - each
    is started as people start it and as the demo, after it is known to
    start at all and before it is packed up to be handed out."""
    text = (repo_root / ".github" / "workflows" / "ci.yml").read_text("utf-8")
    windows, mac = job(text, "windows-exe"), job(text, "mac-app")
    for built, command in (
        (windows, "run: python tools/check_account_part.py companion/dist/Walka.exe\n"),
        (mac, "run: python tools/check_account_part.py companion/dist/Walka.app/Contents/MacOS/Walka\n"),
    ):
        assert built.count("run: python tools/check_account_part.py") == 1 and command in built
        assert built.index("- name: Smoke test") < built.index(command) < built.index("- name: Package")
        # The step is named for what it now proves.
        named = built[: built.index(command)].rstrip().splitlines()[-1].strip()
        assert named == "- name: The program has its Walka account's part, and none as the demo"
    assert text.count("run: python tools/check_account_part.py") == 2
    assert "check_no_account" not in text and "has no Walka account's part" not in text
    assert "macos-latest" in mac and "macos-15-intel" in mac
    # A build that could not look must fail: never passed over.
    assert "continue-on-error" not in text
    # What is set for those runs is set by the helper, for the program alone: the workflow names
    # neither variable, and nothing of where the accounts are. No step of it asks them anything.
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
