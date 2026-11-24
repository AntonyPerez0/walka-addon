"""Has the packaged Walka its Walka account's part, as a download must have it?

    python tools/check_account_part.py companion/dist/Walka.exe
    python tools/check_account_part.py companion/dist/Walka.app/Contents/MacOS/Walka

Walka can sign in to a Walka account and send walks to it. The programs
people download have that part, made with the address that is built into
them - and with no other: somebody who runs Walka from its source code
can name a database of their own with two variables, and a packaged
program must not so much as read those (walka/cli.py). With the
simulated treadmill there is no such part at all: a made-up walk never
goes to a Walka account.

So the program is started twice here, each time on a data folder that is
made here and thrown away afterwards, and each time with both variables
set - for that one run only, to an address on this computer where nobody
listens.

First with a real treadmill asked for, as people start it. Then it must
have the part, with nobody signed in and sending off; the part must not
be made with the address the variables name; the card's button "Stop
sending" must be there and say that nobody is signed in; and merely
starting must have written no file of a Walka account.

Then as the demo. Then it must say nothing of a Walka account, and
every button of the card must answer "not here" (404).

Nothing here asks the accounts anything, and nothing here knows where
they are: nobody is signed in, and a Walka nobody is signed in to opens
no connection. One button is pressed in the first run, and it is one
that asks nobody. "Sign in" is never pressed, in either run: it would
open a web browser. What address the part shows is looked at and never
printed.

The program needs no Bluetooth for this, and no treadmill: it serves its
dashboard while it looks for one. Every wait here has an end, and the
program is ended from here, not asked to close.

Run by the build (.github/workflows/ci.yml); only the standard library.
"""

from __future__ import annotations

import http.client
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

STARTS_WITHIN_S = 60.0  # until the dashboard first answers (packed into one file, the program unpacks itself first)
ANSWERS_WITHIN_S = 10.0  # one question to the dashboard
WRITES_WITHIN_S = 10.0  # until the first file is in its data folder (its log, from a thread of its own)
ENDS_WITHIN_S = 10.0

# The two variables Walka reads when it is run from its source code
# (walka/cli.py), and what they are set to here - in the program's own
# environment and nowhere else. An address and a key that Walka would
# take, did it read them: with ones it turns down, the check would show
# nothing. Port 9 of this computer is where whatever arrives is thrown
# away, and as a rule nobody listens there at all.
URL_VARIABLE, KEY_VARIABLE = "WALKA_ACCOUNT_URL", "WALKA_ACCOUNT_KEY"
NOBODYS_ADDRESS, MADE_UP_KEY = "http://127.0.0.1:9", "test-key"
NOBODYS_HOST = "127.0.0.1:9"  # how the card would show that address
DATA_VARIABLE = "WALKA_DATA_DIR"

# Every button of the Walka-account card (walka/web.py).
SIGN_IN = "/api/walka-account/sign-in"  # never pressed here: it opens a web browser
STOP_SENDING = "/api/walka-account/sending/off"
ROUTES = (
    SIGN_IN,
    "/api/walka-account/sign-out",
    "/api/walka-account/look-again",
    "/api/walka-account/question",
    "/api/walka-account/sending",
    STOP_SENDING,
    "/api/walka-account/notice/ok",
)
# What "Stop sending" answers when the part is there and nobody is signed in: nothing is done, nobody is asked.
NOT_SIGNED_IN = (409, {"ok": False, "error": "not signed in"})
# What the part keeps in the data folder (walka/walka_account/ledger.py): its file, and the lock beside it.
FILES_BEGIN_WITH = "walka-account"


class CheckFailed(Exception):
    """The check cannot be made, or did not come out as it must. Its text says which."""


def say(text: str) -> None:
    """Print a line - now: the build's log then shows when each thing happened."""
    print(text, flush=True)


def free_port() -> int:
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        return taken.getsockname()[1]


def ask(method: str, address: str) -> tuple[int, bytes]:
    """Ask the dashboard: the status it answers, and the answer. ``OSError`` if it does not answer."""
    # (No proxy, whatever this machine is set up with: the dashboard is on this computer.)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request(address, method=method)
    if method != "GET":
        # As the page presses a button that needs nothing said.
        request.data = b"{}"
        request.add_header("Content-Type", "application/json")
    try:
        with opener.open(request, timeout=ANSWERS_WITHIN_S) as answer:
            return answer.status, answer.read()
    except urllib.error.HTTPError as refused:  # an answer too - one that says no
        try:
            return refused.code, refused.read()
        finally:
            refused.close()
    except http.client.HTTPException as exc:  # (an answer that is no HTTP at all)
        raise OSError(type(exc).__name__) from None


def end(program: subprocess.Popen) -> None:
    """End the program, now. (No walk of anybody's is being recorded: nothing is lost.)"""
    if program.poll() is not None:
        return
    if sys.platform == "win32":
        # (Packed into one file, Walka.exe is two processes: end both.)
        try:
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(program.pid)], capture_output=True, check=False, timeout=30
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
    program.kill()
    try:
        program.wait(ENDS_WITHIN_S)
    except subprocess.TimeoutExpired:
        pass


def state_of(there: str) -> tuple[dict, str]:
    """The dashboard's state, and the kind of treadmill it says is in use."""
    try:
        status, body = ask("GET", there + "/api/state")
        state = json.loads(body) if status == 200 else None
    except (OSError, ValueError) as exc:
        raise CheckFailed(f"the dashboard's state could not be read ({type(exc).__name__})") from None
    if not isinstance(state, dict) or not isinstance(state.get("treadmill"), dict):
        raise CheckFailed(f"the dashboard's state is not what Walka's is (status {status})")
    version, kind = state.get("version"), state["treadmill"].get("kind")
    say(f'the dashboard answers: Walka {version}, treadmill of the kind "{kind}"')
    return state, kind if isinstance(kind, str) else ""


def press(there: str, route: str) -> tuple[int, object]:
    """Press a button of the card: the status, and what was answered (None if that is no JSON)."""
    if route == SIGN_IN:
        raise CheckFailed('"Sign in" is never pressed by this check: it would open a web browser')
    try:
        status, body = ask("POST", there + route)
    except OSError as exc:
        raise CheckFailed(f"POST {route} was not answered ({type(exc).__name__})") from None
    try:
        return status, json.loads(body)
    except ValueError:
        return status, None


def look_for_the_part(port: int) -> list[str]:
    """Started as people start it: what is not as it must be - nothing, if all is.

    (What is found wrong is said in words of this file's own. Nothing the
    card shows is printed: where the accounts are is among it.)
    """
    there = f"http://127.0.0.1:{port}"
    wrong = []
    state, kind = state_of(there)
    if not kind or kind == "simulated":
        wrong.append(
            f'the treadmill in use is of the kind "{kind}", not a real one: started like that, no Walka has a '
            "Walka account's part, and nothing was checked"
        )
    piece = state.get("walka_account")
    nobody = isinstance(piece, dict) and piece.get("state") == "signed_out" and piece.get("sending") is False
    if "walka_account" not in state:
        wrong.append('the dashboard\'s state has no "walka_account" key: the program has no Walka account\'s part')
    elif not isinstance(piece, dict):
        wrong.append('what the dashboard\'s state has under "walka_account" is not the card\'s piece')
    else:
        if piece.get("state") != "signed_out" or piece.get("email") != "":
            wrong.append("the card does not say that nobody is signed in")
        if piece.get("sending") is not False:
            wrong.append("the card does not say that sending is off")
        host = piece.get("host")
        if not isinstance(host, str) or not host:
            wrong.append("the card does not say where the accounts are")
        elif host == NOBODYS_HOST:
            wrong.append(
                f"the part is made with the address that {URL_VARIABLE} names ({NOBODYS_HOST}): "
                "a packaged program must not read that variable"
            )
        else:
            say(f"the card says that nobody is signed in and sending is off, and the address it shows is not {NOBODYS_HOST}")
    # One button, and one that asks nobody: with nobody signed in, "Stop sending" does nothing but say so.
    status, answer = press(there, STOP_SENDING)
    say(f"the button of the card that was pressed answers, to a POST: {STOP_SENDING} {status}")
    if status == 404:
        wrong.append(f"the buttons of the Walka-account card are not there: {STOP_SENDING} 404")
    elif (status, answer) != NOT_SIGNED_IN:
        wrong.append(f'"Stop sending" does not answer that nobody is signed in: {STOP_SENDING} {status}')
    # ... and the press changed nothing.
    if nobody:
        after, _kind = state_of(there)
        piece = after.get("walka_account")
        if not (isinstance(piece, dict) and piece.get("state") == "signed_out" and piece.get("sending") is False):
            wrong.append("after that one press the card no longer says that nobody is signed in and sending is off")
    return wrong


def look_for_no_part(port: int) -> list[str]:
    """Started as the demo: what is not as it must be - nothing, if all is."""
    there = f"http://127.0.0.1:{port}"
    wrong = []
    state, kind = state_of(there)
    if kind != "simulated":
        wrong.append(
            f'started as the demo, the treadmill in use is of the kind "{kind}", not the simulated one: '
            "nothing was checked"
        )
    if "walka_account" in state:
        wrong.append('as the demo, the dashboard\'s state has a "walka_account" key')
    answered, there_all_the_same = [], []
    for route in ROUTES:
        if route == SIGN_IN:
            continue  # (never pressed: were the part there after all, it could open a web browser)
        status, _answer = press(there, route)
        answered.append(f"{route} {status}")
        if status != 404:
            there_all_the_same.append(answered[-1])
    say(f"the buttons of the Walka-account card answer, to a POST: {', '.join(answered)}")
    if there_all_the_same:
        wrong.append(
            f"as the demo, a button of the Walka-account card is there (not 404): {', '.join(there_all_the_same)}"
        )
    return wrong


def run(command: list[str], how: str, look, starts_within_s: float, clock) -> list[str]:
    """Start the program once, look at it with ``look``, end it, and look at its data folder.

    Gives what is not as it must be; raises ``CheckFailed`` if that could not be looked at.
    """
    # (ignore_cleanup_errors: a program that was ended a moment ago may still hold its files, on Windows.)
    with tempfile.TemporaryDirectory(prefix="walka-account-part-", ignore_cleanup_errors=True) as temp:
        data, port = Path(temp) / "data", free_port()
        # Without a window, on a port and a data folder of its own. (The
        # folder is said twice: by the option, and by the variable - the
        # smoke test's way.)
        command = [*command, "--data-dir", str(data), "--no-window", "--port", str(port)]
        environment = dict(os.environ)
        environment.update({URL_VARIABLE: NOBODYS_ADDRESS, KEY_VARIABLE: MADE_UP_KEY, DATA_VARIABLE: str(data)})
        out, err = Path(temp) / "walka-out.txt", Path(temp) / "walka-err.txt"
        say(f"--- {how}")
        say(f"> {' '.join(command)}  (with {URL_VARIABLE} and {KEY_VARIABLE} set, for this run only)")
        began = clock()
        # (Into files, as the build's smoke test does it: the packed program
        # for Windows is a windowed one, and writes where it is sent.)
        with open(out, "wb") as to_out, open(err, "wb") as to_err:
            try:
                program = subprocess.Popen(
                    command, stdin=subprocess.DEVNULL, stdout=to_out, stderr=to_err, env=environment
                )
            except OSError as exc:
                raise CheckFailed(f"{command[0]} could not be started ({type(exc).__name__}: {exc})") from None
            failed: CheckFailed | None = None
            wrong: list[str] = []
            try:
                while True:
                    code = program.poll()
                    if code is not None:
                        raise CheckFailed(f"the program ended before its dashboard answered (exit code {code})")
                    try:
                        status, _body = ask("GET", f"http://127.0.0.1:{port}/api/state")
                    except OSError:
                        status = None
                    if status == 200:
                        break
                    if clock() - began > starts_within_s:
                        raise CheckFailed(f"the dashboard did not answer within {starts_within_s:.0f} seconds")
                    time.sleep(0.25)
                say(f"the program's dashboard answered after {clock() - began:.1f} s")
                wrong = look(port)
                # Its log is written from the start. Were the folder not the
                # one it uses, that no file of a Walka account is in it
                # would say nothing. (Looked at before the program is ended:
                # what is written then is not cut off.)
                asked = clock()
                while not any(path.is_file() for path in data.rglob("*")):
                    if clock() - asked > WRITES_WITHIN_S:
                        raise CheckFailed("the program wrote nothing into the data folder it was given")
                    time.sleep(0.25)
            except CheckFailed as exc:
                failed = exc
            finally:
                asked = clock()
                end(program)
                say(f"the program was ended in {clock() - asked:.1f} s, {clock() - began:.1f} s after it was started")
        kept = sorted(path.relative_to(data).as_posix() for path in data.rglob("*") if path.is_file())
        say(f"in its data folder: {', '.join(kept) or 'nothing'}")
        of_the_part = [name for name in kept if name.rsplit("/", 1)[-1].startswith(FILES_BEGIN_WITH)]
        # (Nobody signed in, and nothing was switched: a start by itself writes no file of a Walka account.)
        wrong += [f"the data folder holds {name}" for name in of_the_part]
        # What the program said, for whoever reads the build's log: its own
        # log always (it says how the start without a treadmill went on this
        # machine), the rest when something is wrong. (A run of a moment ago,
        # on a folder made here: nothing of anybody's is in any of it, and
        # Walka's log never says where the accounts are.)
        shown = [("walka.log", data / "walka.log")]
        if failed is not None or wrong:
            shown += [("what the program printed", out), ("what it printed as errors", err)]
        for name, file in shown:
            try:
                text = file.read_text("utf-8", "replace").strip()
            except OSError:
                continue
            if text:
                say(f"--- {name}\n{text}\n---")
    if failed is not None:
        raise failed
    return wrong


def check(command: list[str], starts_within_s: float = STARTS_WITHIN_S, clock=time.monotonic) -> str:
    """Run ``command`` as Walka, once as people start it and once as the demo, and look.

    Gives one sentence if the program has its Walka account's part as it must; raises
    ``CheckFailed`` if it has not, or if that could not be looked at.
    """
    if Path(command[0]).is_file():
        # Its whole path, whatever folder this is run from. (Not "resolved":
        # a link to a program is not always the program it leads to.)
        command = [str(Path(command[0]).absolute()), *command[1:]]
    # Not as the demo, and without the game's screen being read: a real treadmill is asked for.
    wrong = run([*command, "--no-capture"], "as people start it", look_for_the_part, starts_within_s, clock)
    # (The demo reads no screen: its character is made up, like its walker.)
    wrong += run([*command, "--demo"], "as the demo", look_for_no_part, starts_within_s, clock)
    if wrong:
        raise CheckFailed("; ".join(wrong))
    return (
        "started with a real treadmill asked for and both variables set, the program has its Walka account's "
        "part with nobody signed in and sending off, made with another address than the variables name, and "
        f"its data folder holds no file of one; started as the demo it says nothing of a Walka account and "
        f"{len(ROUTES) - 1} of its {len(ROUTES)} buttons answer 404 (\"Sign in\" is never pressed)"
    )


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        say(__doc__)
        return 2
    for stream in (sys.stdout, sys.stderr):
        # What the program wrote is shown here as it came. A letter that the
        # build machine's console cannot show must not end the check (Windows).
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="backslashreplace")
    try:
        found = check(sys.argv[1:])
    except CheckFailed as failed:
        say(f"The Walka account's part in the program: FAILED - {failed}.")
        return 1
    say(f"The Walka account's part in the program: OK - {found}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
