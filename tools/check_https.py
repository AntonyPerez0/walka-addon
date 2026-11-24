"""Does the packaged Walka check who it is talking to before it asks anything?

    python tools/check_https.py companion/dist/Walka.exe
    python tools/check_https.py companion/dist/Walka.app/Contents/MacOS/Walka

Walka makes HTTPS requests where somebody has signed in to a Walka
account - and on no build machine, where nobody ever is. So what the
part that sends walks asks with is tried by itself: the hidden command
"httpscheck" (walka/cli.py) fetches https://walka.dev/ once, which must
work, and then asks a server whose certificate nobody vouches for, which
must not.

This starts that second server: on 127.0.0.1, with a key and a
certificate made here a moment ago and thrown away afterwards. It first
shows that the server does answer whoever trusts that certificate - a
server that answers nobody would look just like a refusal. Then it runs
the program and wants three things: the line "httpscheck OK", that the
program did come to the server, and that it turned back at the
certificate - before it had asked for anything.

It says each thing the moment it is done, and how many seconds it took:
where the time of this step goes can be read off the build's log.

Needs the "openssl" command (the build machines have it; on Windows the
copy that comes with Git will do). Run by the build
(.github/workflows/ci.yml); only the standard library.
"""

from __future__ import annotations

import http.client
import shutil
import socketserver
import ssl
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

RUNS_FOR_S = 60.0  # the program's whole check, the look at walka.dev included
HANDSHAKE_S = 10.0
ALT_NAMES = "IP:127.0.0.1"

# Where Git for Windows keeps its copy, if "openssl" is not a command by itself.
GITS_OPENSSL = (
    r"C:\Program Files\Git\usr\bin\openssl.exe",
    r"C:\Program Files\Git\mingw64\bin\openssl.exe",
)

# (No -subj and no -addext on the command line: the copies of openssl on
# the three kinds of build machine do not agree on them. A file they all read.)
CERTIFICATE_CONFIG = """\
[req]
distinguished_name = who
x509_extensions = what
prompt = no

[who]
CN = 127.0.0.1

[what]
subjectAltName = {alt_names}
"""


class CheckFailed(Exception):
    """The check cannot be made, or did not come out as it must. Its text says which."""


def say(text: str) -> None:
    """Print a line - now. Written to a build's log, Python keeps what is
    printed until there is a lot of it, or until it ends: every line then
    carries the time of the end, and nobody can tell which part took long."""
    print(text, flush=True)


class Seconds:
    """How long each part took, by a clock that is handed in."""

    def __init__(self, clock=time.monotonic) -> None:
        self.clock = clock
        self.took: list[tuple[str, float]] = []
        self._since = clock()

    def of(self, part: str) -> float:
        """The seconds since the part before this one ended: written down under ``part``, and given."""
        now = self.clock()
        self.took.append((part, max(0.0, now - self._since)))
        self._since = now
        return self.took[-1][1]

    def said(self) -> str:
        return ", ".join(f"{part} {seconds:.1f}" for part, seconds in self.took)


def find_openssl() -> str:
    found = shutil.which("openssl")
    if found:
        return found
    if sys.platform == "win32":
        for candidate in GITS_OPENSSL:
            if Path(candidate).is_file():
                return candidate
    raise CheckFailed(
        'there is no "openssl" command on this machine to make a certificate with'
        + (" (looked in Git's folder too)" if sys.platform == "win32" else "")
    )


def make_certificate(folder: Path, openssl: str | None = None, alt_names: str = ALT_NAMES) -> tuple[Path, Path]:
    """A key and a certificate that vouches for itself, good for two days: ``(certificate, key)`` in ``folder``."""
    openssl = openssl or find_openssl()
    # (As bytes: the same file on every system, whatever that system ends a line with.)
    (folder / "certificate.cnf").write_bytes(CERTIFICATE_CONFIG.format(alt_names=alt_names).encode("ascii"))
    # (File names, not paths: Git's openssl reads a Windows path its own way.)
    command = [
        openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2",
        "-config", "certificate.cnf", "-keyout", "key.pem", "-out", "certificate.pem",
    ]  # fmt: skip
    try:
        made = subprocess.run(command, cwd=folder, stdin=subprocess.DEVNULL, capture_output=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CheckFailed(f"openssl could not be run to make a certificate ({type(exc).__name__})") from None
    certificate, key = folder / "certificate.pem", folder / "key.pem"
    if made.returncode != 0 or not certificate.is_file() or not key.is_file():
        said = made.stderr.decode("utf-8", "replace").strip()
        raise CheckFailed(f"openssl did not make a certificate (exit code {made.returncode}): {said}")
    return certificate, key


class _Handler(BaseHTTPRequestHandler):
    """Answers anybody who gets as far as asking - and the server counts how far each one got."""

    server: HomeMadeServer
    agreed = False

    def setup(self) -> None:
        self.server.count("connections")
        try:
            # Here, on this connection's own thread: somebody who connects
            # and then says nothing holds up nobody else.
            self.request.settimeout(HANDSHAKE_S)
            self.request = self.server.tls.wrap_socket(self.request, server_side=True)
            self.agreed = True
            self.server.count("handshakes")
        except OSError as exc:  # (ssl's errors are OSErrors) - turned back at the certificate, or never spoke TLS
            self.server.turned_back(type(exc).__name__)
        super().setup()

    def handle(self) -> None:
        if self.agreed:
            super().handle()

    def finish(self) -> None:
        try:
            super().finish()
        except OSError:
            pass  # (gone already)
        finally:
            self.server.count("done")

    def answer(self) -> None:
        self.server.count("requests")
        body = b"home-made\n"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    do_GET = do_HEAD = do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = answer

    def log_message(self, *_args) -> None:
        pass


class HomeMadeServer(ThreadingHTTPServer):
    """An HTTPS server on 127.0.0.1, on a port the system gives, with a certificate nobody vouches for."""

    daemon_threads = True

    def __init__(self, certificate: Path, key: Path) -> None:
        self.tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.tls.load_cert_chain(str(certificate), str(key))
        # Nothing to come back with later ("session tickets"). A server
        # sends those once a visitor has taken its certificate, and counts
        # the visitor only when they are sent. One who takes the certificate
        # and leaves at once is gone by then: on Windows the sending fails,
        # and the visitor would be counted as one who turned back. With no
        # tickets, whoever took the certificate is counted the moment they did.
        self.tls.num_tickets = 0
        self.certificate = certificate
        self._lock = threading.Lock()
        self._counts = {"connections": 0, "handshakes": 0, "requests": 0, "done": 0}
        self.left_with: list[str] = []  # the kind of error each one ended with who did not take the certificate
        super().__init__(("127.0.0.1", 0), _Handler)
        self.port: int = self.server_port
        self._thread = threading.Thread(target=self.serve_forever, name="home-made-server", daemon=True)
        self._thread.start()

    def server_bind(self) -> None:
        # Not the way Python's own HTTP server does it: that one also asks
        # the system what this computer is called in full (socket.getfqdn),
        # for a name nobody here reads - and a system that has to go and
        # find out can sit on that question for seconds, every time a
        # server is started.
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]

    def count(self, what: str) -> None:
        with self._lock:
            self._counts[what] += 1

    def turned_back(self, kind: str) -> None:
        with self._lock:
            self.left_with.append(kind)

    def counts(self) -> dict[str, int]:
        """How many connected, how many of them took the certificate, and how many requests were made.

        Waits (a little) until everybody who connected is dealt with: a
        request that is still on its way in must not go uncounted.
        """
        deadline = time.monotonic() + HANDSHAKE_S + 5
        while True:
            with self._lock:
                seen = dict(self._counts)
            if seen.pop("done") >= seen["connections"] or time.monotonic() > deadline:
                return seen
            time.sleep(0.05)

    def handle_error(self, request, client_address) -> None:
        pass  # (a visitor who hangs up is no news: the counts say what happened)

    def stop(self) -> None:
        self.shutdown()
        self.server_close()
        self._thread.join(5)


def ask_trusting(server: HomeMadeServer) -> int:
    """Ask the server as somebody who trusts its certificate - and still checks it. The status it answers."""
    trusting = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)  # (checks the name and the certificate)
    trusting.load_verify_locations(cafile=str(server.certificate))
    connection = http.client.HTTPSConnection("127.0.0.1", server.port, context=trusting, timeout=HANDSHAKE_S)
    try:
        connection.request("GET", "/")
        return connection.getresponse().status
    except OSError as exc:
        raise CheckFailed(f"the home-made server does not answer one who trusts it ({type(exc).__name__})") from None
    finally:
        connection.close()


def run_program(command: list[str], folder: Path, runs_for_s: float = RUNS_FOR_S) -> tuple[int | None, str, str]:
    """Run the program: its exit code (``None`` if it had to be ended), and what it wrote to stdout and to stderr.

    Into files, as the build's smoke test does it: the packed program for
    Windows is a windowed one, and writes where it is sent.
    """
    out, err = folder / "walka-out.txt", folder / "walka-err.txt"
    with open(out, "wb") as to_out, open(err, "wb") as to_err:
        try:
            program = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=to_out, stderr=to_err)
        except OSError as exc:
            raise CheckFailed(f"{command[0]} could not be started ({type(exc).__name__}: {exc})") from None
        code: int | None
        try:
            code = program.wait(runs_for_s)
        except subprocess.TimeoutExpired:
            code = None
            if sys.platform == "win32":
                # (Packed into one file, Walka.exe is two processes: end both.)
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(program.pid)], capture_output=True, check=False)
            program.kill()
            try:
                program.wait(10)
            except subprocess.TimeoutExpired:
                pass
    return code, out.read_text("utf-8", "replace"), err.read_text("utf-8", "replace")


def check(command: list[str], runs_for_s: float = RUNS_FOR_S, clock=time.monotonic) -> str:
    """Run ``command`` + ``httpscheck --refuse-port <port>`` against a home-made server.

    Gives the program's own line if all is as it must be; raises ``CheckFailed`` if not.
    """
    seconds = Seconds(clock)
    # (ignore_cleanup_errors: a program that had to be ended may still hold its files, on Windows.)
    if Path(command[0]).is_file():
        # Its whole path, whatever folder this is run from. (Not "resolved":
        # a link to a program is not always the program it leads to.)
        command = [str(Path(command[0]).absolute()), *command[1:]]
    with tempfile.TemporaryDirectory(prefix="walka-https-", ignore_cleanup_errors=True) as temp:
        folder = Path(temp)
        certificate, key = make_certificate(folder)
        say(f"a key and a certificate were made in {seconds.of('the certificate'):.1f} s")
        try:
            server = HomeMadeServer(certificate, key)
        except OSError as exc:  # (ssl's errors are OSErrors)
            raise CheckFailed(f"the home-made server could not be started ({type(exc).__name__}: {exc})") from None
        started_in = seconds.of("the start of the server")
        say(f"the home-made server was started on 127.0.0.1:{server.port} in {started_in:.1f} s")
        try:
            # First: this server does answer. Without that, a server that
            # is simply broken would pass for a certificate refused.
            status = ask_trusting(server)
            asked_in = seconds.of("one who trusts it")
            before = server.counts()
            if status != 200 or before != {"connections": 1, "handshakes": 1, "requests": 1}:
                raise CheckFailed(f"the home-made server is not working as it should (status {status}, {before})")
            say(
                f"the home-made server on 127.0.0.1:{server.port} answers one who trusts its certificate "
                f"(asked in {asked_in:.1f} s, counted in {seconds.of('the count before'):.1f} s)"
            )

            program = [*command, "httpscheck", "--refuse-port", str(server.port)]
            say(f"> {' '.join(program)}")
            code, out, err = run_program(program, folder, runs_for_s)
            ran_for = seconds.of("the program")
            ended = "it had to be ended" if code is None else f"exit code {code}"
            say(f"the program ran for {ran_for:.1f} s ({ended}), and said:")
            say(out.rstrip())
            if err.strip():
                say(err.rstrip())
            after = server.counts()
            counted_in = seconds.of("the count after")
            left_with = list(server.left_with)
        finally:
            server.stop()
            seconds.of("the end of the server")
            # (In every case: a check that fails for being slow says where it was slow.)
            say(f"seconds, part by part: {seconds.said()}")
    if code is None:
        raise CheckFailed(f"the program was still running after {runs_for_s:.0f} seconds")
    came = after["connections"] - before["connections"]
    say(
        f"the home-made server saw from the program: {came} connection(s), "
        f"{after['handshakes'] - before['handshakes']} that took its certificate, "
        f"{after['requests'] - before['requests']} request(s) (counted in {counted_in:.1f} s)"
    )
    if left_with:
        # (Only the kind of error: it tells a refusal from a line that broke, should the count ever be doubted.)
        say(f"whoever did not take it left the server with: {', '.join(left_with)}")
    if after["requests"] != before["requests"] or after["handshakes"] != before["handshakes"]:
        raise CheckFailed("the program took a certificate that nobody vouches for")
    lines = [line for line in out.splitlines() if line.startswith("httpscheck OK")]
    if code != 0 or not lines:
        raise CheckFailed("the program did not say that HTTPS works in it")
    if came < 1:
        raise CheckFailed("the program says OK, but it never came to the home-made server")
    return lines[0]


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
        check(sys.argv[1:])
    except CheckFailed as failed:
        say(f"HTTPS in the program: FAILED - {failed}.")
        return 1
    say("The program checks certificates: it reached walka.dev, and it asked the home-made server nothing.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
