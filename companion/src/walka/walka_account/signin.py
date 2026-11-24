"""One sign-in to a Walka account, through the person's own web browser (brief 5.2).

Walka opens the system's browser at the accounts' sign-in address and
waits on 127.0.0.1 for the browser to come back with a one-time code. The
code alone is worth nothing: it is exchanged together with a verifier
that only this object knows, and that was never anywhere but in memory.

The listener, exactly as the brief has it:

- it is a listener of its own, for this one sign-in, on 127.0.0.1 and a
  port the system gives. Not the dashboard's server.
- its path is this sign-in's "state": the accounts send back the code
  and nothing else that ties the answer to the question. So the path is
  a key. It is never written to the log, and it is forgotten when the
  sign-in is over.
- only a GET on exactly that path counts, and only once. Any other
  request - another path, another method, a second time - gets 404,
  changes nothing and does not use the listener up. Every request that
  can be read comes to ONE function here, with nothing of aiohttp's in
  between that could answer in its own words.
- of the query, ``code`` is read, or that ``error`` is there. Nothing
  else, and never ``error_description``.
- the code is exchanged BEFORE the browser is answered, and the browser
  is told what is true, with one of three plain pages of Walka's own.
- it is gone after the answer, or after a few minutes, or when it is
  cancelled.

Closing must never wait for the network (the walk is saved after the
tasks are cancelled, and whoever ends Walka waits four seconds at most).
So the exchange is a task of this object's own, and the request of the
browser only waits for it - or for ``cancel()``, which is synchronous and
answers the browser at once. ``close()`` then has nothing slow to do.

Nothing here stores a key or writes a file: what a sign-in gives is
handed to the caller's ``finish``, which keeps it or does not.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import secrets
import socket
import sys
from collections.abc import Awaitable, Callable
from typing import Any

from aiohttp import web

from . import calls
from .calls import CallError, Signed
from .https import Client, Origin
from .threads import wait_for_event

log = logging.getLogger(__name__)

WAYS = calls.WAYS  # ("discord", "github", "google")

LISTEN_FOR_S = 300.0  # the person has this long to come back from the browser
EXCHANGE_WITHIN_S = 10.0  # the browser waits no longer than this for its page
ANSWER_WITHIN_S = 0.5  # the page is given this long to leave before the listener goes
SHUTDOWN_S = 0.5  # a request still open when the listener goes is given this long, then cut
CLOSE_WITHIN_S = 2.0  # close() waits no longer than this, whatever happens

PAGE_SIGNED_IN = "Walka is signed in. You can close this tab."
PAGE_NOTHING = "Nothing was signed in. You can close this tab."
PAGE_FAILED = "Walka could not finish signing in. Go back to Walka and try again."

# How a sign-in can end. ``outcome`` is "" while it waits, then one of these.
OUTCOMES = ("signed_in", "refused", "failed", "timed_out", "cancelled", "certificate", "no_store")

_PAGES = {"signed_in": PAGE_SIGNED_IN, "refused": PAGE_NOTHING}  # every other end is PAGE_FAILED

# On every answer. no-store: the address of the page holds the code, and
# the page is not to be kept. no-referrer: that address goes to nobody.
# default-src 'none': the page loads nothing and runs nothing.
_HEADERS = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'none'",
}

# aiohttp writes a request it cannot read into its log, the request's own
# first line and all - and that line can hold the path and the code. So
# this listener's server gets a log that leads nowhere: it is not in the
# tree of loggers, and what it is told is dropped.
_NOBODY = logging.Logger("walka.walka_account.signin.listener")
_NOBODY.addHandler(logging.NullHandler())

# Windows only (the name is not there elsewhere; the number is Windows' own).
_EXCLUSIVE = getattr(socket, "SO_EXCLUSIVEADDRUSE", -5)


def _page(sentence: str) -> str:
    """A whole small page of Walka's own: no script, no link, no picture."""
    return (
        "<!doctype html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        "<title>Walka</title>\n"
        "</head>\n"
        "<body>\n"
        f"<p>{sentence}</p>\n"
        "</body>\n"
        "</html>\n"
    )


def _answer(status: int, text: str, kind: str) -> web.Response:
    response = web.Response(status=status, text=text, content_type=kind, charset="utf-8", headers=_HEADERS)
    # "Connection: close": no line stays open to the listener, so nothing
    # has to be waited for when it goes. Said outright as well: aiohttp
    # only writes the header by itself for a request of HTTP/1.1.
    response.headers["Connection"] = "close"
    response.force_close()
    return response


def _not_found() -> web.Response:
    return _answer(404, "Not found", "text/plain")


def _listening_socket(
    platform: str = sys.platform, make: Callable[..., socket.socket] = socket.socket
) -> socket.socket:
    """A socket on 127.0.0.1, on a port the system gives - and nobody else's.

    Never SO_REUSEADDR: on Windows that lets another program listen on the
    very same port, and the browser's answer could go to it. There,
    SO_EXCLUSIVEADDRUSE says outright that the port is this socket's alone.
    """
    sock = make(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if platform == "win32":
            sock.setsockopt(socket.SOL_SOCKET, _EXCLUSIVE, 1)
        sock.bind(("127.0.0.1", 0))
    except BaseException:
        sock.close()
        raise
    return sock


async def _wait_for(event: asyncio.Event, seconds: float | None) -> bool:
    """Wait for ``event``, ``seconds`` at most. Did it come?"""
    return await wait_for_event(event, seconds)


def _this_task() -> asyncio.Task[Any] | None:
    try:
        return asyncio.current_task()
    except RuntimeError:  # (no loop is running: nobody is a task)
        return None


class SignIn:
    """One sign-in: a verifier, a listener, at most one exchange. Used once.

    ``opener(url)`` opens the person's browser and must not block (the
    caller hands in one that works on a thread of its own). ``finish(signed)``
    keeps the sign-in - the refresh token in the system's store, the rest
    in the Walka-account file - and says whether it could; it is awaited
    before the browser is answered. It may be cancelled at any ``await``
    (by ``cancel()``, or when the time is up), and should give its answer
    as soon as the sign-in is kept: what it wants to ask afterwards
    belongs in a task of the caller's.

    ``mono`` is the monotonic clock the limits are set by, and
    ``wait(event, seconds)`` the wait they are waited out with: it says
    whether the event came, and when it says no, the time is up (the
    caller's own; in tests one that goes by a made-up clock).

    ``outcome`` is "" while the sign-in waits. Then ``done`` is set and it
    is one of ``OUTCOMES``: "signed_in"; "refused" (the person said no);
    "failed" (the code was not taken, or anything else went wrong);
    "timed_out" (nobody came back); "cancelled"; "certificate" (the
    accounts' certificate could not be checked: nothing was asked);
    "no_store" (``finish`` could not keep the sign-in).

    Nothing here raises (but a cancellation of the caller's own task passes).
    """

    def __init__(
        self,
        client: Client,
        key: str,
        origin: Origin,
        way: str,
        *,
        opener: Callable[[str], None],
        finish: Callable[[Signed], Awaitable[bool]],
        mono: Callable[[], float],
        wait: Callable[[asyncio.Event, float | None], Awaitable[bool]] | None = None,
    ) -> None:
        self._client = client
        self._key = key
        self._origin = origin
        self._way = way
        self._opener = opener
        self._finish = finish
        self._mono = mono
        self._wait = wait if wait is not None else _wait_for
        self.done = asyncio.Event()
        self.outcome = ""
        self._started = False
        self._path = ""  # this sign-in's "state". A key: never logged, forgotten when it is over
        self._verifier = ""  # in memory only, and only until it is used
        self._taken = False  # the one GET that counts has come
        self._answering = False  # ... and its page has not left yet
        self._answered = asyncio.Event()
        self._changed = asyncio.Event()  # wakes the task below: it is over, or there is a new limit
        self._listen_until = 0.0
        self._exchange_until: float | None = None
        self._runner: web.ServerRunner | None = None
        self._server: asyncio.AbstractServer | None = None
        self._life: asyncio.Task[None] | None = None  # listens, watches the clock, takes the listener down
        self._work: asyncio.Task[None] | None = None  # the exchange, and the keeping

    # ---- what the caller does ----------------------------------------------------------------

    async def start(self) -> None:
        """Begin to listen, then open the browser. Whatever fails ends the sign-in as "failed"."""
        if self._started:
            return
        self._started = True
        if self.done.is_set():
            return  # cancelled before it began: nothing listens, no browser opens
        ready: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        self._life = asyncio.create_task(self._live(ready), name="walka-account-sign-in")
        self._life.add_done_callback(lambda _task: self._ended(ready))
        try:
            url = await ready
        except asyncio.CancelledError:
            self.cancel()  # (the task above takes down what it has put up)
            raise
        if not url or self.done.is_set():
            return
        try:
            self._opener(url)
        except Exception as exc:
            log.warning("Walka account: the web browser could not be opened (%s).", type(exc).__name__)
            self._settle("failed")
        else:
            log.info("Walka account: waiting for the web browser to come back from signing in.")

    def cancel(self) -> None:
        """It is over, now: nobody else is let in, and a browser that waits is answered at once.

        Synchronous, and safe to call twice. After a sign-in has ended by
        itself it changes nothing.
        """
        self._settle("cancelled")

    async def close(self) -> None:
        """Wait until the listener and everything of this sign-in is gone. Quick: it cancels first."""
        self.cancel()
        life = self._life
        if life is None or life.done():
            return
        # (asyncio.wait, not "await life": if the caller gives up waiting,
        # the task goes on taking the listener down, and is not cut in the middle.)
        await asyncio.wait({life}, timeout=CLOSE_WITHIN_S)
        if not life.done():
            life.cancel()
            await asyncio.wait({life}, timeout=SHUTDOWN_S)

    # ---- how it ends -------------------------------------------------------------------------

    def _settle(self, outcome: str) -> None:
        """The sign-in is over, this way. The first to say so is right; later words change nothing."""
        if self.done.is_set():
            return
        self.outcome = outcome
        self._path = ""
        self._verifier = ""
        self._exchange_until = None
        self._close_port()
        work = self._work
        if work is not None and not work.done() and work is not _this_task():
            work.cancel()  # an exchange nobody waits for any more
        self.done.set()
        self._changed.set()
        log.info("Walka account: the sign-in is over (%s).", outcome)

    def _ended(self, ready: asyncio.Future[str]) -> None:
        """The task that listens is gone, however it went: then the sign-in is over, and start() is told.

        (Told here and not by the task itself: a task that is cancelled
        before it ever ran says nothing at all.)
        """
        self._settle("cancelled")  # (if it is not over already)
        if not ready.done():
            ready.set_result("")

    def _close_port(self) -> None:
        """Stop listening, at once. (A browser that is already here still gets its page.)"""
        server, self._server = self._server, None
        if server is not None:
            server.close()

    # ---- the task that listens ---------------------------------------------------------------

    async def _live(self, ready: asyncio.Future[str]) -> None:
        try:
            url = ""
            try:
                if not self.done.is_set():
                    url = await self._listen()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("Walka account: could not listen for the web browser (%s).", type(exc).__name__)
                self._settle("failed")
            if not ready.done():
                ready.set_result(url)
            del url  # (the path is in it, and this task lives as long as the listener)
            await self._watch()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # (The clock or the wait that was handed in broke. The sign-in is over; the listener still has to go.)
            log.warning("Walka account: the sign-in's listener ended on an error (%s).", type(exc).__name__)
            self._settle("failed")
        try:
            await self._take_down()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("Walka account: the sign-in's listener did not close cleanly (%s).", type(exc).__name__)

    async def _listen(self) -> str:
        """Put the listener up. Gives the address the browser is to be opened at."""
        path = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        digest = hashlib.sha256(verifier.encode("ascii")).digest()
        challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
        sock = _listening_socket()
        try:
            back = f"http://127.0.0.1:{sock.getsockname()[1]}/{path}"
            # (ValueError for a way that is none, or an address that is not the accounts'.)
            url = calls.browser_address(self._origin, way=self._way, back=back, challenge=challenge)
            # aiohttp's server without its application and router: those answer
            # some requests themselves ("OPTIONS *", a CONNECT, an "Expect" they
            # do not know - even on the right path), in their own words, without
            # the three headers, and leave the line open. Here every request
            # that can be read comes to _handle, and to nothing else.
            # (lingering_time: a request whose body never comes is not waited for after its answer.)
            runner = web.ServerRunner(
                web.Server(self._handle, access_log=None, logger=_NOBODY, lingering_time=0.0),
                shutdown_timeout=SHUTDOWN_S,
            )
            await runner.setup()
            self._runner = runner
            # Not serving yet: so that nothing is waited for between making
            # the server and keeping hold of it, and a cancellation cannot
            # leave a listener behind that nobody can close.
            server = await asyncio.get_running_loop().create_server(runner.server, sock=sock, start_serving=False)
        except BaseException:
            sock.close()
            raise
        if self.done.is_set():  # cancelled while it was put up: it never listens
            server.close()
            return ""
        self._server = server  # from here on the socket is the server's to close, and _settle() closes it
        self._path = path
        self._verifier = verifier
        self._listen_until = self._mono() + LISTEN_FOR_S
        await server.start_serving()
        return url

    def _limit(self) -> float:
        """When the time is up, by ``mono``: for the browser to come back, or for the code to be exchanged."""
        if self._exchange_until is not None:
            return min(self._listen_until, self._exchange_until)
        return self._listen_until

    async def _watch(self) -> None:
        """Wait until the sign-in is over - and end it when the time is up."""
        while not self.done.is_set():
            until = self._limit()
            left = until - self._mono()
            if left > 0:
                self._changed.clear()
                came = await self._wait(self._changed, left)
                if came or self.done.is_set() or self._limit() != until:
                    continue  # it is over, or there is another limit now
            # The time is up: by the clock, or by the word of the wait that was handed in.
            # Nobody came back - or somebody did, and the accounts gave no answer in time.
            self._settle("failed" if self._taken else "timed_out")

    async def _take_down(self) -> None:
        """Everything of this sign-in goes: the page on its way out first, then the listener, then the work."""
        if self._answering and not self._answered.is_set():
            await _wait_for(self._answered, ANSWER_WITHIN_S)
        # The listener before the work: a keeper that is slow to stop must not keep a line open.
        runner, self._runner = self._runner, None
        if runner is not None:
            await runner.cleanup()
        work = self._work
        if work is not None and not work.done():
            await asyncio.wait({work}, timeout=SHUTDOWN_S)

    # ---- the browser comes back --------------------------------------------------------------

    def _counts(self, request: web.BaseRequest) -> bool:
        """Is this the one request that counts: a GET on exactly the path, the first, in time?"""
        path = self._path
        if self._taken or self.done.is_set() or not path or request.method != "GET":
            return False
        # What was asked for, letter for letter as it came, up to the query:
        # so a path with a "#..." after it, or a whole address in its place,
        # is not the path either.
        asked = request.raw_path.partition("?")[0].encode("utf-8", "backslashreplace")
        return secrets.compare_digest(asked, ("/" + path).encode("ascii"))

    async def _handle(self, request: web.BaseRequest) -> web.StreamResponse:
        try:
            counts = self._counts(request)
        except Exception:
            counts = False
        if not counts:
            return _not_found()  # and nothing is changed, used up or written down
        self._taken = True
        self._answering = True
        response: web.Response | None = None
        try:
            self._come_back(request)
            await self.done.wait()  # the exchange, or cancel(), or the clock
            response = _answer(200, _page(_PAGES.get(self.outcome, PAGE_FAILED)), "text/html")
            # Sent from here, not left to aiohttp: so that it is known when
            # the page has left, and the listener is not taken down before.
            await response.prepare(request)
            await response.write_eof()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # (Mostly: the tab was closed meanwhile. The sign-in is what it is all the same.)
            self._settle("failed")
            log.debug("Walka account: the web browser did not get its page (%s).", type(exc).__name__)
        finally:
            self._answering = False
            self._answered.set()
        return response if response is not None else _answer(200, _page(PAGE_FAILED), "text/html")

    def _come_back(self, request: web.BaseRequest) -> None:
        """Read what the browser brought: an error, a code, or neither. No waiting here."""
        query = request.query
        if "error" in query:
            self._settle("refused")  # (why is the other site's business: it is never read)
            return
        code = query.get("code")
        if not code:
            self._settle("failed")
            return
        verifier, self._verifier = self._verifier, ""
        self._exchange_until = self._mono() + EXCHANGE_WITHIN_S
        self._work = asyncio.create_task(self._exchange(code, verifier), name="walka-account-sign-in-exchange")
        self._changed.set()

    async def _exchange(self, code: str, verifier: str) -> None:
        """The code for a sign-in, and the sign-in to whoever keeps it. A task of this object's own."""
        outcome = "failed"
        try:
            try:
                signed = await calls.exchange(self._client, self._key, code=code, verifier=verifier)
            except CallError as error:
                if error.kind == "certificate":
                    outcome = "certificate"
                log.info("Walka account: the sign-in was not finished (%s).", error.kind)
            else:
                self._exchange_until = None  # answered in time; what follows is the caller's own work
                kept = await self._finish(signed)
                del signed
                outcome = "signed_in" if kept is True else "no_store"
        except asyncio.CancelledError:
            self._settle("cancelled")  # (if it is not over already: whoever cancels says how it ended)
            raise
        except Exception as exc:
            log.warning("Walka account: the sign-in was not finished (%s).", type(exc).__name__)
        self._settle(outcome)
