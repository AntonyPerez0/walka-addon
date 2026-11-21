"""The hidden command "walka httpscheck", and what the build runs it with
(tools/check_https.py): does HTTPS work in this program, and does it turn
back at a certificate nobody vouches for - before it has asked anything?

No test here asks walka.dev, or anybody else on the internet: the page is
faked, and the server with the home-made certificate is on 127.0.0.1.
"""

from __future__ import annotations

import ast
import asyncio
import importlib.util
import re
import socket
import ssl
import sys
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from walka import __version__, cli
from walka import config as config_module
from walka.walka_account import https
from walka.walka_account.https import Answer, Client, NetError, Origin

SRC = Path(cli.__file__).resolve().parent.parent
SECRET = "v4xk2j3h5l6m-the-bearer"
PAGE = Origin("https", "walka.dev", None)


def run(scenario):
    return asyncio.run(asyncio.wait_for(scenario(), 30))


# ---- the command, with nobody really asked -----------------------------------------------------------


class Asked:
    """Stands in for the client: answers from a list, and writes down who was asked what."""

    def __init__(self, monkeypatch, **answers: list) -> None:
        self.answers = {name.replace("_", "."): list(given) for name, given in answers.items()}
        self.calls: list[tuple] = []
        asked = self

        class FakeClient:
            def __init__(self, origin: Origin, **settings) -> None:
                assert settings == {}, "the command uses the client as the feature does: with nothing changed"
                self.origin = origin

            async def request(self, method: str, path: str, **how) -> Answer:
                asked.calls.append((self.origin, method, path, how))
                outcome = asked.answers[self.origin.host].pop(0)
                if isinstance(outcome, BaseException):
                    raise outcome
                return outcome

        monkeypatch.setattr(https, "Client", FakeClient)
        monkeypatch.setattr(cli, "HTTPSCHECK_PAUSE_S", 0.0)

    @property
    def who(self) -> list[Origin]:
        return [origin for origin, _method, _path, _how in self.calls]


def one_line(capsys) -> str:
    out = capsys.readouterr().out
    assert out.endswith("\n") and len(out.strip().splitlines()) == 1, out
    return out.strip()


def test_httpscheck_is_not_advertised():
    parser = cli.build_parser()
    assert "httpscheck" not in parser.format_help() and "refuse-port" not in parser.format_help()
    assert parser.parse_args(["httpscheck"]).command == "httpscheck"
    assert parser.parse_args(["httpscheck"]).refuse_port is None
    assert parser.parse_args(["httpscheck", "--refuse-port", "8443"]).refuse_port == 8443


def test_httpscheck_takes_no_address(capsys):
    """The page it fetches is a constant. Nothing typed after the command
    can make it ask anybody else."""
    for extra in (["https://elsewhere.example/"], ["--url", "https://elsewhere.example/"], ["--host", "elsewhere.example"]):
        with pytest.raises(SystemExit):
            cli.build_parser().parse_args(["httpscheck", *extra])
    capsys.readouterr()
    assert cli.HTTPSCHECK_HOST == "walka.dev"


def test_selfcheck_imports_what_asks_a_walka_account():
    assert {"ssl", "walka.walka_account.https", "walka.walka_account.threads"} <= set(cli.RUNTIME_MODULES)
    # (In the list for every system: they must import on every system.)
    assert {"ssl", "walka.walka_account.https"} <= set(cli.runtime_modules("linux"))


def test_packed_or_from_source(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    assert cli._is_packed() is False
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert cli._is_packed() is True
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    assert cli._is_packed() is False
    monkeypatch.setattr(sys, "_MEIPASS", "/tmp/_MEI123", raising=False)
    assert cli._is_packed() is True


def test_httpscheck_says_ok_when_the_page_answers(capsys, monkeypatch):
    asked = Asked(monkeypatch, walka_dev=[Answer(200, None, b"<html>")])
    assert cli.main(["httpscheck"]) == 0
    line = one_line(capsys)
    assert line.startswith("httpscheck OK: ")
    assert ssl.OPENSSL_VERSION in line and "run from source" in line
    assert re.search(r"[1-9]\d* root certificates from ", line)
    assert "https://walka.dev/ answered (200) on a checked connection" in line
    assert "127.0.0.1" not in line
    # One request: a GET of the front page, with no key, no bearer, no query and no body.
    assert asked.calls == [(PAGE, "GET", "/", {"timeout_s": cli.HTTPSCHECK_TRY_S})]


def test_httpscheck_says_whether_it_is_the_packed_program(capsys, monkeypatch):
    Asked(monkeypatch, walka_dev=[Answer(200, None, b"")])
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert cli.main(["httpscheck"]) == 0
    line = one_line(capsys)
    assert ", packed;" in line and "from source" not in line


def test_any_answer_of_the_page_will_do(capsys, monkeypatch):
    """It is the connection that is tried, not the website."""
    for outcome, said in (
        (Answer(404, None, b""), "answered (404)"),
        (Answer(301, None, b""), "answered (301)"),
        (Answer(503, 30.0, b""), "answered (503)"),
        (NetError("too_big"), "https://walka.dev/ answered on a checked connection"),
    ):
        Asked(monkeypatch, walka_dev=[outcome])
        assert cli.main(["httpscheck"]) == 0
        assert said in one_line(capsys)


def test_httpscheck_fails_without_root_certificates_and_asks_nobody(capsys, monkeypatch):
    asked = Asked(monkeypatch, walka_dev=[Answer(200, None, b"")])
    monkeypatch.setattr(https, "make_ssl_context", lambda: ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT))
    assert cli.main(["httpscheck", "--refuse-port", "8443"]) == 1
    line = one_line(capsys)
    assert line.startswith("httpscheck FAILED: ") and "no root certificates" in line
    assert asked.calls == []

    def not_there() -> ssl.SSLContext:
        raise FileNotFoundError(https.MAC_ROOTS)

    monkeypatch.setattr(https, "make_ssl_context", not_there)
    assert cli.main(["httpscheck"]) == 1
    line = one_line(capsys)
    assert line.startswith("httpscheck FAILED: ") and "could not be read (FileNotFoundError)" in line
    assert asked.calls == []


def test_a_certificate_of_the_page_that_cannot_be_checked_fails_at_once(capsys, monkeypatch):
    asked = Asked(monkeypatch, walka_dev=[NetError("certificate"), Answer(200, None, b"")])
    assert cli.main(["httpscheck", "--refuse-port", "8443"]) == 1
    line = one_line(capsys)
    assert line.startswith("httpscheck FAILED: ") and "certificate of https://walka.dev/ could not be checked" in line
    assert asked.who == [PAGE]  # not tried again, and the other server not asked at all


def test_a_page_that_does_not_answer_is_tried_three_times(capsys, monkeypatch):
    asked = Asked(monkeypatch, walka_dev=[NetError("no_answer"), NetError("no_answer"), Answer(200, None, b"")])
    assert cli.main(["httpscheck"]) == 0
    assert one_line(capsys).startswith("httpscheck OK: ") and asked.who == [PAGE] * 3

    asked = Asked(monkeypatch, walka_dev=[NetError("no_answer")] * 4)
    assert cli.main(["httpscheck", "--refuse-port", "8443"]) == 1
    line = one_line(capsys)
    assert line.startswith("httpscheck FAILED: ") and "did not answer (3 tries)" in line
    assert asked.who == [PAGE] * 3

    asked = Asked(monkeypatch, walka_dev=[NetError("address"), Answer(200, None, b"")])
    assert cli.main(["httpscheck"]) == 1
    assert one_line(capsys).startswith("httpscheck FAILED: ") and asked.who == [PAGE]


def test_the_home_made_certificate_must_be_refused(capsys, monkeypatch):
    there = Origin("https", "127.0.0.1", 8443)
    asked = Asked(monkeypatch, walka_dev=[Answer(200, None, b"")], **{"127_0_0_1": [NetError("certificate")]})
    assert cli.main(["httpscheck", "--refuse-port", "8443"]) == 0
    line = one_line(capsys)
    assert line.startswith("httpscheck OK: ") and "certificate on 127.0.0.1:8443 was refused" in line
    assert asked.calls == [
        (PAGE, "GET", "/", {"timeout_s": cli.HTTPSCHECK_TRY_S}),
        (there, "GET", "/", {"timeout_s": cli.HTTPSCHECK_TRY_S}),
    ]


@pytest.mark.parametrize(
    "outcome, said",
    [
        (Answer(200, None, b"home-made"), "was asked and answered (200): its certificate was not checked"),
        (Answer(500, None, b""), "was asked and answered (500)"),
        # A server that is not there, or does not speak TLS, was not turned down for its certificate.
        (NetError("no_answer"), "nothing answered on 127.0.0.1:8443, and that is not a certificate refused"),
        (NetError("too_big"), "was not refused for its certificate (too_big)"),
        (NetError("address"), "was not refused for its certificate (address)"),
    ],
)
def test_anything_but_a_refusal_fails(capsys, monkeypatch, outcome, said: str):
    asked = Asked(monkeypatch, walka_dev=[Answer(200, None, b"")], **{"127_0_0_1": [outcome, NetError("certificate")]})
    assert cli.main(["httpscheck", "--refuse-port", "8443"]) == 1
    line = one_line(capsys)
    assert line.startswith("httpscheck FAILED: ") and said in line
    assert len(asked.calls) == 2  # asked once: a refusal on a second try would prove nothing


@pytest.mark.parametrize("port", ["0", "-1", "65536", "99999999"])
def test_a_port_that_is_none_fails_before_anybody_is_asked(capsys, monkeypatch, port: str):
    asked = Asked(monkeypatch, walka_dev=[Answer(200, None, b"")])
    assert cli.main(["httpscheck", f"--refuse-port={port}"]) == 1
    assert one_line(capsys).startswith("httpscheck FAILED: ") and asked.calls == []


def test_an_error_nobody_expected_is_named_and_not_quoted(capsys, monkeypatch):
    """An error of the network can carry what was sent. Its name is enough."""
    Asked(monkeypatch, walka_dev=[RuntimeError(f"Authorization: Bearer {SECRET}")])
    assert cli.main(["httpscheck"]) == 1
    captured = capsys.readouterr()
    assert captured.out.strip() == "httpscheck FAILED: RuntimeError"
    assert SECRET not in captured.out and SECRET not in captured.err


def test_httpscheck_reads_no_folder_and_writes_no_log(capsys, monkeypatch, tmp_path: Path):
    Asked(monkeypatch, walka_dev=[Answer(200, None, b""), Answer(200, None, b"")])
    usual = tmp_path / "the usual folder"
    monkeypatch.setattr(config_module, "default_data_dir", lambda: usual)
    # (Were they read, these would say where the accounts are.)
    monkeypatch.setenv("WALKA_ACCOUNT_URL", "https://accounts.example")
    monkeypatch.setenv("WALKA_ACCOUNT_KEY", "test-key")
    monkeypatch.setenv("WALKA_DATA_DIR", str(tmp_path / "from the environment"))
    assert cli.main(["httpscheck"]) == 0
    named = tmp_path / "named"
    assert cli.main(["--data-dir", str(named), "-v", "httpscheck"]) == 0
    out = capsys.readouterr().out
    assert len(out.strip().splitlines()) == 2 and "accounts.example" not in out and "test-key" not in out
    assert list(tmp_path.iterdir()) == []


# ---- the same, on a real connection with a home-made certificate -----------------------------------


def load_tool(repo_root: Path):
    spec = importlib.util.spec_from_file_location("walka_check_https", repo_root / "tools" / "check_https.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def tool(repo_root: Path):
    """tools/check_https.py - skipped where there is no "openssl" command to make a certificate with."""
    module = load_tool(repo_root)
    try:
        module.find_openssl()
    except module.CheckFailed as missing:
        pytest.skip(str(missing))
    return module


def trusting(certificate: Path):
    def factory() -> ssl.SSLContext:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)  # (checks the name and the certificate)
        context.load_verify_locations(cafile=str(certificate))
        return context

    return factory


def page_faked(monkeypatch, **settings) -> None:
    """Everything is real but walka.dev: the page answers without being asked."""
    real = Client

    class PageFaked(real):
        def __init__(self, origin: Origin) -> None:
            super().__init__(origin, **settings)

        async def request(self, method: str, path: str, **how) -> Answer:
            if self.origin == PAGE:
                return Answer(200, None, b"")
            return await real.request(self, method, path, **how)

    monkeypatch.setattr(https, "Client", PageFaked)
    monkeypatch.setattr(cli, "HTTPSCHECK_PAUSE_S", 0.0)


def test_a_home_made_certificate_is_refused_before_anything_is_asked(tool, tmp_path: Path, caplog, capfd):
    server = tool.HomeMadeServer(*tool.make_certificate(tmp_path))
    try:
        assert tool.ask_trusting(server) == 200  # the server does answer one who trusts it

        async def scenario():
            client = Client(Origin("https", "127.0.0.1", server.port))
            with pytest.raises(NetError) as caught:
                await client.request(
                    "POST", "/rest/v1/walks", headers={"Authorization": "Bearer " + SECRET}, json_body=[{"k": SECRET}]
                )
            return caught.value

        with caplog.at_level("DEBUG"):
            error = run(scenario)
        counts = server.counts()
    finally:
        server.stop()
    assert error.kind == "certificate"
    # Walka came, saw the certificate and turned back: the one request is the trusting one's.
    assert counts == {"connections": 2, "handshakes": 1, "requests": 1}
    assert error.args == ("certificate",) and error.__context__ is None and error.__cause__ is None
    out, err = capfd.readouterr()
    assert SECRET not in caplog.text and SECRET not in out and SECRET not in err


def test_a_certificate_that_is_vouched_for_is_taken_and_the_name_is_looked_up_walkas_way(
    tool, tmp_path: Path, monkeypatch
):
    """The other half: with a certificate it can check, the same client
    does ask - so the refusals above are refusals, not a client that
    cannot do HTTPS. The name goes through Walka's own look-up."""
    certificate, key = tool.make_certificate(tmp_path, alt_names="DNS:accounts.example")
    server = tool.HomeMadeServer(certificate, key)
    looked_up: list[str] = []
    real = socket.getaddrinfo

    def this_computer(host, *args, **kwargs):
        looked_up.append(host)
        return real("127.0.0.1" if host == "accounts.example" else host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", this_computer)
    try:

        async def scenario():
            named = Client(Origin("https", "accounts.example", server.port), context_factory=trusting(certificate))
            answer = await named.request("GET", "/auth/v1/user")
            # The same server, asked by number: the certificate is for the name, not for that.
            numbered = Client(Origin("https", "127.0.0.1", server.port), context_factory=trusting(certificate))
            with pytest.raises(NetError) as caught:
                await numbered.request("GET", "/auth/v1/user")
            return answer, caught.value.kind

        answer, by_number = run(scenario)
        counts = server.counts()
    finally:
        server.stop()
    assert (answer.status, answer.body) == (200, b"home-made\n")
    assert by_number == "certificate"
    assert counts == {"connections": 2, "handshakes": 1, "requests": 1}
    assert looked_up == ["accounts.example"]


def test_by_name_on_a_checked_connection_a_request_says_the_same_and_carries_no_cookie(
    tool, tmp_path: Path, monkeypatch
):
    """What goes on the wire, once more - this time as the accounts are
    asked: by name, on a checked connection. (The other tests ask
    127.0.0.1 in the clear.) Each request is a connection and a memory of
    its own, so a cookie handed out with one answer has nowhere to stay
    until the next; that the client also keeps none on purpose is pinned
    with its settings, in test_walka_account_https.py."""
    certificate, key = tool.make_certificate(tmp_path, alt_names="DNS:accounts.example")
    real = socket.getaddrinfo
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, *args, **kwargs: real("127.0.0.1" if host == "accounts.example" else host, *args, **kwargs),
    )
    seen: list[list[tuple[str, str]]] = []

    async def handle(request: web.Request) -> web.Response:
        seen.append(list(request.headers.items()))
        return web.json_response({}, headers={"Set-Cookie": "tracked=yes; Path=/"})

    async def scenario():
        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", handle)
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(str(certificate), str(key))
        server = TestServer(app, host="127.0.0.1")
        await server.start_server(ssl=tls)
        try:
            client = Client(Origin("https", "accounts.example", server.port), context_factory=trusting(certificate))
            statuses = [(await client.request("GET", "/rest/v1/walks")).status for _ in range(2)]
            return statuses, server.port
        finally:
            await server.close()

    statuses, port = run(scenario)
    assert statuses == [200, 200] and len(seen) == 2
    for headers in seen:
        assert dict(headers) == {
            "Host": f"accounts.example:{port}",
            "User-Agent": f"Walka/{__version__}",
            "Accept": "application/json",
            "Accept-Encoding": "identity",
            "Connection": "close",
        }
        assert len(headers) == 5


def test_the_command_against_a_real_server(tool, tmp_path: Path, capsys, monkeypatch):
    page_faked(monkeypatch)
    certificate, key = tool.make_certificate(tmp_path)
    server = tool.HomeMadeServer(certificate, key)
    try:
        assert cli.main(["httpscheck", "--refuse-port", str(server.port)]) == 0
        line = one_line(capsys)
        assert line.startswith("httpscheck OK: ") and f"certificate on 127.0.0.1:{server.port} was refused" in line
        assert server.counts() == {"connections": 1, "handshakes": 0, "requests": 0}

        # Were the certificate one that Walka trusts, it would ask - and the command says so.
        page_faked(monkeypatch, context_factory=trusting(certificate))
        assert cli.main(["httpscheck", "--refuse-port", str(server.port)]) == 1
        line = one_line(capsys)
        assert line.startswith("httpscheck FAILED: ") and "was asked and answered (200)" in line
        assert server.counts() == {"connections": 2, "handshakes": 1, "requests": 1}
    finally:
        server.stop()


def test_a_server_that_is_not_there_or_speaks_no_tls_is_not_a_refusal(capsys, monkeypatch):
    page_faked(monkeypatch)
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        nobody = taken.getsockname()[1]
    assert cli.main(["httpscheck", "--refuse-port", str(nobody)]) == 1
    line = one_line(capsys)
    assert line.startswith("httpscheck FAILED: ") and "nothing answered" in line

    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread

    class Plain(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *_args) -> None:
            pass

    plain = ThreadingHTTPServer(("127.0.0.1", 0), Plain)
    plain.handle_error = lambda *_args: None
    thread = Thread(target=plain.serve_forever, daemon=True)
    thread.start()
    try:
        assert cli.main(["httpscheck", "--refuse-port", str(plain.server_address[1])]) == 1
        line = one_line(capsys)
        assert line.startswith("httpscheck FAILED: ") and "nothing answered" in line
    finally:
        plain.shutdown()
        plain.server_close()
        thread.join(5)


# ---- the build's helper, tools/check_https.py ---------------------------------------------------------


WALKA_WITH_THE_PAGE_FAKED = """\
import sys

sys.path.insert(0, {src!r})
from walka import cli
from walka.walka_account import https

real = https.Client


class PageFaked(real):
    async def request(self, method, path, **how):
        if self.origin.host == "walka.dev":
            return https.Answer(200, None, b"")
        return await real.request(self, method, path, **how)


https.Client = PageFaked
sys.exit(cli.main(sys.argv[1:]))
"""

# Programs that get it wrong, each in its own way. (They find the port as Walka does.)
TAKES_ANYTHING = """\
import http.client, ssl, sys

port = int(sys.argv[sys.argv.index("--refuse-port") + 1])
connection = http.client.HTTPSConnection("127.0.0.1", port, context=ssl._create_unverified_context(), timeout=10)
connection.request("GET", "/")
connection.getresponse().read()
print("httpscheck OK: took whatever came")
"""
TAKES_IT_AND_ASKS_NOTHING = """\
import socket, ssl, sys

port = int(sys.argv[sys.argv.index("--refuse-port") + 1])
with socket.create_connection(("127.0.0.1", port), timeout=10) as plain:
    with ssl._create_unverified_context().wrap_socket(plain) as checked:
        checked.version()
print("httpscheck OK: took the certificate, asked nothing")
"""
NEVER_COMES = 'print("httpscheck OK: said without looking")\n'
SAYS_FAILED = 'import sys\nprint("httpscheck FAILED: no root certificates")\nsys.exit(1)\n'
SAYS_OK_BUT_EXITS_BADLY = 'import sys\nprint("httpscheck OK: and yet")\nsys.exit(3)\n'
SAYS_NOTHING = "import sys\nsys.exit(0)\n"
NEVER_ENDS = "import time\ntime.sleep(60)\n"


def program(tmp_path: Path, text: str) -> list[str]:
    script = tmp_path / "program.py"
    script.write_text(text, "utf-8")
    return [sys.executable, str(script)]


def test_the_helper_passes_walka_itself(tool, tmp_path: Path, capsys):
    """Walka's own command and client, in a process of their own as the
    build runs them - with only the look at walka.dev faked."""
    line = tool.check(program(tmp_path, WALKA_WITH_THE_PAGE_FAKED.format(src=str(SRC))))
    assert line.startswith("httpscheck OK: ") and "was refused" in line
    said = capsys.readouterr().out
    assert "1 connection(s), 0 that took its certificate, 0 request(s)" in said


@pytest.mark.parametrize(
    "text, said",
    [
        (TAKES_ANYTHING, "took a certificate that nobody vouches for"),
        (TAKES_IT_AND_ASKS_NOTHING, "took a certificate that nobody vouches for"),
        (NEVER_COMES, "never came to the home-made server"),
        (SAYS_FAILED, "did not say that HTTPS works"),
        (SAYS_OK_BUT_EXITS_BADLY, "did not say that HTTPS works"),
        (SAYS_NOTHING, "did not say that HTTPS works"),
    ],
    ids=["takes anything", "takes it and asks nothing", "never comes", "says failed", "exits badly", "says nothing"],
)
def test_the_helper_fails_a_program_that_gets_it_wrong(tool, tmp_path: Path, capsys, text: str, said: str):
    with pytest.raises(tool.CheckFailed) as caught:
        tool.check(program(tmp_path, text))
    assert said in str(caught.value)
    capsys.readouterr()


def test_the_helper_does_not_wait_for_ever(tool, tmp_path: Path, capsys):
    with pytest.raises(tool.CheckFailed) as caught:
        tool.check(program(tmp_path, NEVER_ENDS), runs_for_s=1.0)
    assert "still running after 1 seconds" in str(caught.value)
    with pytest.raises(tool.CheckFailed) as caught:
        tool.check([str(tmp_path / "no-such-program")])
    assert "could not be started" in str(caught.value)
    capsys.readouterr()


def test_the_helper_says_so_when_it_cannot_make_a_certificate(repo_root: Path, tmp_path: Path, monkeypatch, capsys):
    tool = load_tool(repo_root)
    monkeypatch.setattr(tool.shutil, "which", lambda _name: None)
    monkeypatch.setattr(tool, "GITS_OPENSSL", (str(tmp_path / "no-git" / "openssl.exe"),))
    with pytest.raises(tool.CheckFailed) as caught:
        tool.find_openssl()
    assert 'no "openssl" command' in str(caught.value)
    # ... and as a command: one line that says what is wrong, and the build stops.
    monkeypatch.setattr(sys, "argv", ["check_https.py", str(tmp_path / "Walka.exe")])
    assert tool.main() == 1
    assert 'FAILED - there is no "openssl" command' in capsys.readouterr().out
    monkeypatch.setattr(sys, "argv", ["check_https.py"])
    assert tool.main() == 2
    assert "python tools/check_https.py companion/dist/Walka.exe" in capsys.readouterr().out
    # A copy that Git brought is found where "openssl" alone is not a command (Windows).
    gits = tmp_path / "Git" / "usr" / "bin" / "openssl.exe"
    gits.parent.mkdir(parents=True)
    gits.write_bytes(b"")
    monkeypatch.setattr(tool, "GITS_OPENSSL", (str(tmp_path / "elsewhere.exe"), str(gits)))
    monkeypatch.setattr(tool.sys, "platform", "win32")
    assert tool.find_openssl() == str(gits)


def test_the_helper_says_so_when_its_server_cannot_start(repo_root: Path, monkeypatch, capsys):
    tool = load_tool(repo_root)
    monkeypatch.setattr(tool, "make_certificate", lambda folder: (folder / "none.pem", folder / "none-key.pem"))
    with pytest.raises(tool.CheckFailed) as caught:
        tool.check([sys.executable, "-c", "pass"])
    assert "the home-made server could not be started (FileNotFoundError" in str(caught.value)
    capsys.readouterr()


WRITES_WHAT_A_CONSOLE_CANNOT_SHOW = """\
import sys

sys.stdout.buffer.write(b"httpscheck FAILED: caf\\xe9 \\xff\\n")
sys.stderr.buffer.write("\\u2603\\n".encode("utf-8"))
sys.exit(1)
"""


def test_the_helper_shows_whatever_the_program_wrote(tool, repo_root: Path, tmp_path: Path):
    """A build machine's console for Windows cannot show every letter.
    What the program wrote is shown all the same, and the check ends with
    its own sentence - not with an error about a letter."""
    import os
    import subprocess

    script = tmp_path / "program.py"
    script.write_text(WRITES_WHAT_A_CONSOLE_CANNOT_SHOW, "utf-8")
    ran = subprocess.run(
        [sys.executable, str(repo_root / "tools" / "check_https.py"), sys.executable, str(script)],
        env={**os.environ, "PYTHONIOENCODING": "ascii"},
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=120,
    )
    out, err = ran.stdout.decode("ascii"), ran.stderr.decode("ascii", "replace")
    assert ran.returncode == 1 and "Traceback" not in err, err
    assert "httpscheck FAILED: caf" in out
    assert "HTTPS in the program: FAILED - the program did not say that HTTPS works in it." in out


def test_the_helper_needs_nothing_but_python(repo_root: Path):
    """It is run by the build machine's own Python, before and beside
    everything that is installed there."""
    tree = ast.parse((repo_root / "tools" / "check_https.py").read_text("utf-8"))
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


def test_both_builds_try_https_in_the_packed_program(repo_root: Path):
    text = (repo_root / ".github" / "workflows" / "ci.yml").read_text("utf-8")
    windows, mac = job(text, "windows-exe"), job(text, "mac-app")
    for built, command, after in (
        (windows, "run: python tools/check_https.py companion/dist/Walka.exe\n", "tools/check_console.py"),
        (mac, "run: python tools/check_https.py companion/dist/Walka.app/Contents/MacOS/Walka\n", "- name: Smoke test"),
    ):
        assert built.count("tools/check_https.py") == 1 and command in built
        # After the program is known to start, and before it is packed up to be handed out.
        assert built.index(after) < built.index(command) < built.index("- name: Package")
    assert text.count("tools/check_https.py") == 2
    # A build that could not check must fail: never passed over.
    assert "continue-on-error" not in text
    # Where the accounts are is no business of a workflow: the check asks walka.dev and this machine.
    assert "supabase" not in text.lower() and "WALKA_ACCOUNT" not in text and "sb_publishable" not in text
