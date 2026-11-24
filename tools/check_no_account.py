"""Is the Walka account's part really left out of the packaged Walka?

    python tools/check_no_account.py companion/dist/Walka.exe
    python tools/check_no_account.py companion/dist/Walka.app/Contents/MacOS/Walka

Walka can sign in to a Walka account and send walks to it - when it is
run from its source code, with two variables set that say where the
accounts are. The programs people download have no such part, whatever
those variables say (walka/cli.py): no download may reach the accounts
before the website has said what Walka does with them.

This starts the program with both variables set - for this one run only,
to an address on this computer where nobody listens - on a data folder
that is made here and thrown away afterwards. Not as the demo: with the
simulated treadmill there is no such part from source either, and the
check would show nothing. It waits until the dashboard answers, and then
wants four things: that the treadmill in use is not the simulated one,
that the dashboard's state says nothing of a Walka account, that every
button of the Walka-account card answers "not here" (404), and that the
data folder holds no file of a Walka account once the program is ended.

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

# The two variables Walka reads when it starts (walka/cli.py), and what
# they are set to here - in the program's own environment and nowhere
# else. An address and a key that Walka would take, were its part there:
# with ones it turns down, the check would show nothing. Port 9 of this
# computer is where whatever arrives is thrown away, and as a rule nobody
# listens there at all.
URL_VARIABLE, KEY_VARIABLE = "WALKA_ACCOUNT_URL", "WALKA_ACCOUNT_KEY"
NOBODYS_ADDRESS, MADE_UP_KEY = "http://127.0.0.1:9", "test-key"
DATA_VARIABLE = "WALKA_DATA_DIR"

# Every button of the Walka-account card (walka/web.py).
ROUTES = (
    "/api/walka-account/sign-in",
    "/api/walka-account/sign-out",
    "/api/walka-account/look-again",
    "/api/walka-account/question",
    "/api/walka-account/sending",
    "/api/walka-account/sending/off",
    "/api/walka-account/notice/ok",
)
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
    """End the program, now. (No walk is being recorded: nothing is lost.)"""
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


def look(port: int) -> list[str]:
    """Ask the dashboard everything, and give what is not as it must be - nothing, if all is."""
    there = f"http://127.0.0.1:{port}"
    wrong = []
    try:
        status, body = ask("GET", there + "/api/state")
        state = json.loads(body) if status == 200 else None
    except (OSError, ValueError) as exc:
        raise CheckFailed(f"the dashboard's state could not be read ({type(exc).__name__})") from None
    if not isinstance(state, dict) or not isinstance(state.get("treadmill"), dict):
        raise CheckFailed(f"the dashboard's state is not what Walka's is (status {status})")
    kind = state["treadmill"].get("kind")
    say(f'the dashboard answers: Walka {state.get("version")}, treadmill of the kind "{kind}"')
    if not isinstance(kind, str) or not kind or kind == "simulated":
        wrong.append(
            f'the treadmill in use is of the kind "{kind}", not a real one: started like that, Walka has no '
            "Walka account's part from its source code either, and nothing was checked"
        )
    if "walka_account" in state:
        wrong.append('the dashboard\'s state has a "walka_account" key')
    answered, there_all_the_same = [], []
    for route in ROUTES:
        try:
            status, _body = ask("POST", there + route)
        except OSError as exc:
            raise CheckFailed(f"POST {route} was not answered ({type(exc).__name__})") from None
        answered.append(f"{route} {status}")
        if status != 404:
            there_all_the_same.append(answered[-1])
    say(f"the buttons of the Walka-account card answer, to a POST: {', '.join(answered)}")
    if there_all_the_same:
        wrong.append(f"a button of the Walka-account card is there (not 404): {', '.join(there_all_the_same)}")
    return wrong


def check(command: list[str], starts_within_s: float = STARTS_WITHIN_S, clock=time.monotonic) -> str:
    """Run ``command`` as Walka with a real treadmill asked for, with both variables set, and look.

    Gives one sentence if the program has no Walka account's part; raises ``CheckFailed`` if it
    has one, or if that could not be looked at.
    """
    if Path(command[0]).is_file():
        # Its whole path, whatever folder this is run from. (Not "resolved":
        # a link to a program is not always the program it leads to.)
        command = [str(Path(command[0]).absolute()), *command[1:]]
    # (ignore_cleanup_errors: a program that was ended a moment ago may still hold its files, on Windows.)
    with tempfile.TemporaryDirectory(prefix="walka-no-account-", ignore_cleanup_errors=True) as temp:
        data, port = Path(temp) / "data", free_port()
        # Without the game's screen being read, without a window, on a port
        # and a data folder of its own. (The folder is said twice: by the
        # option, and by the variable - the smoke test's way.)
        command = [*command, "--data-dir", str(data), "--no-capture", "--no-window", "--port", str(port)]
        environment = dict(os.environ)
        environment.update({URL_VARIABLE: NOBODYS_ADDRESS, KEY_VARIABLE: MADE_UP_KEY, DATA_VARIABLE: str(data)})
        out, err = Path(temp) / "walka-out.txt", Path(temp) / "walka-err.txt"
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
        wrong += [f"the data folder holds {name}" for name in of_the_part]
        # What the program said, for whoever reads the build's log: its own
        # log always (it says how the start without a treadmill went on this
        # machine), the rest when something is wrong. (A run of a moment ago,
        # on a folder made here: nothing of anybody's is in any of it.)
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
    if wrong:
        raise CheckFailed("; ".join(wrong))
    return (
        f"started with a real treadmill asked for and both variables set, the program says nothing of a "
        f"Walka account, its {len(ROUTES)} buttons answer 404, and its data folder holds no file of one"
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
        say(f"No Walka account in the program: FAILED - {failed}.")
        return 1
    say(f"No Walka account in the program: OK - {found}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
