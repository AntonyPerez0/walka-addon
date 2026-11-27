"""The Walka account's maker of requests to another machine: one address, checked, and nothing kept.

Every request of the Walka account part is made here, so everything
about how one is made stands here, in one place. (The other part of
Walka that asks anything over the internet is updates.py: it asks GitHub
for the newest release, at every start and once a day, unless that is
switched off.)

- a client asks ONE address, the one it was made for, and no other. A
  redirect is not followed: it comes back as the answer it is.
- the certificate of the other side is always checked, against the root
  certificates the system keeps. Nothing here can switch that off - not
  a setting, not a proxy, not an argument. A certificate that cannot be
  checked means no request.
- no proxy is read from the environment (a proxy is another machine), no
  cookie is kept, and a request says ``Walka/<version>`` and nothing
  about the computer.
- an answer is read up to ``MOST_BYTES`` and no further, and is never
  unpacked: a small answer that unpacks into a huge one stays small.
- whatever goes wrong comes out as a ``NetError`` that carries one word.
  The errors of aiohttp carry the request's headers - the keys of a
  sign-in among them - so none of them leaves this module, not even as
  the cause of another.

``calls.py`` is the only user: it knows the paths. The hidden command
``walka httpscheck`` (cli.py) tries this same client in a packed program.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import ipaddress
import json
import math
import os
import re
import socket
import ssl
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

import aiohttp
import aiohttp.abc
from yarl import URL  # (aiohttp's own library for addresses: it is always there with it)

from .. import __version__
from .threads import OffLoopTimeout, off_loop

USER_AGENT = f"Walka/{__version__}"
MOST_BYTES = 262_144  # an answer is read up to this and no further
MAC_ROOTS = "/etc/ssl/cert.pem"  # macOS's own list of root certificates, kept up to date with macOS
CONTEXT_S = 15.0  # reading the system's root certificates, at most
LOOK_UP_S = 10.0  # looking a name up, at most

# A few root certificates that many servers' certificates hang from, by name.
# Only told, never required: "walka httpscheck" says which of them are there.
WELL_KNOWN_ROOTS = ("ISRG Root X1", "GTS Root R1", "GTS Root R4", "GlobalSign Root CA")

# (re.ASCII: without it "ignore case" takes a few letters from outside ASCII for
# "s" and "k" - the long s, the sign for kelvin - and a host would hold them.)
_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_HTTPS = re.compile(rf"https://({_LABEL}(?:\.{_LABEL})*)(?::([0-9]{{1,5}}))?/?", re.IGNORECASE | re.ASCII)
_THIS_COMPUTER = re.compile(r"http://127\.0\.0\.1:([0-9]{1,5})/?", re.IGNORECASE | re.ASCII)
_A_NUMBER = re.compile(r"[0-9]+|0x[0-9a-f]*")
_JWT = re.compile(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*")
_PATH = re.compile(r"/[A-Za-z0-9/_.~-]*")
_HEADER_NAME = re.compile(r"[A-Za-z0-9-]{1,64}")
_HEADER_VALUE = re.compile(r"[\x20-\x7e]{0,8192}")
_WHOLE_SECONDS = re.compile(r"[0-9]{1,4}")

# What a request of Walka's is: the list of the brief (5.4) has no other method.
_METHODS = ("GET", "POST")
# Headers that are the client's own to set: a caller cannot say who is asking, or carry a cookie.
_NOT_THE_CALLERS = frozenset(
    {
        "host",
        "user-agent",
        "accept-encoding",
        "connection",
        "content-length",
        "transfer-encoding",
        "cookie",
        "te",
        "upgrade",
        "expect",
        "proxy-authorization",
    }
)


# ---- the address ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Origin:
    """Where the accounts are: scheme, host and port, and nothing else of an address."""

    scheme: str
    host: str
    port: int | None

    @property
    def text(self) -> str:
        """``https://host``, ``https://host:8443``, ``http://127.0.0.1:54321``: lower case, no slash at the end.

        The one spelling of an address: the entry in the system's store
        for secrets and the part of ``walka-account.json`` are named by it.
        """
        return f"{self.scheme}://{self.host}" + (f":{self.port}" if self.port is not None else "")


def parse_origin(text: object) -> Origin | None:
    """The address in ``text``, or ``None`` if it is not one Walka will ask.

    Taken: ``https://<host>[:port][/]``, the host a name or an address in
    plain ASCII; and ``http://127.0.0.1:<port>[/]``, a database on this
    computer. Everything else is refused, whole: a name and password
    before the host, a path, a query, a fragment, a space, another
    scheme, ``localhost`` by name. "Begins with https://" would not be a
    check: ``https://accounts.example@elsewhere.example`` begins so too.
    """
    if not isinstance(text, str):
        return None
    local = _THIS_COMPUTER.fullmatch(text)
    if local:
        port = _port(local.group(1))
        return Origin("http", "127.0.0.1", port) if port is not None else None
    match = _HTTPS.fullmatch(text)
    if not match:
        return None
    host = match.group(1).lower()
    if len(host) > 253:
        return None
    if _A_NUMBER.fullmatch(host.rsplit(".", 1)[-1]):
        # It ends in a number: an address then, not a name, and it must be a
        # whole one in plain numbers. ("127.1", "2130706433" and "0x7f.0.0.1"
        # are spellings of 127.0.0.1 that some systems take.)
        try:
            host = str(ipaddress.IPv4Address(host))
        except ValueError:
            return None
    port = 443 if match.group(2) is None else _port(match.group(2))
    if port is None:
        return None
    # (443 is where https goes anyway: one address, one spelling.)
    return Origin("https", host, None if port == 443 else port)


def _port(digits: str) -> int | None:
    number = int(digits)
    return number if 1 <= number <= 65535 else None


def key_is_usable(key: object) -> bool:
    """Is ``key`` one the program may carry - the accounts' PUBLISHABLE key?

    Never a secret key: one that begins ``sb_secret_``, or one of the
    older kind (three parts with dots between) that says it is the
    ``service_role``. Such a key passes every rule of the database, and
    has no business on anybody's computer.
    """
    if not isinstance(key, str) or not 1 <= len(key) <= 512:
        return False
    if any(not "!" <= char <= "~" for char in key):  # printable ASCII, no space
        return False
    if key.lower().startswith("sb_secret_"):
        return False
    if _JWT.fullmatch(key):
        middle = key.split(".")[1]
        try:
            said = json.loads(base64.urlsafe_b64decode(middle + "=" * (-len(middle) % 4)))
        except (ValueError, binascii.Error, RecursionError):
            return True  # not that kind of key after all
        if isinstance(said, dict) and said.get("role") == "service_role":
            return False
    return True


# ---- the certificates ----------------------------------------------------------------------------


def make_ssl_context(platform: str = sys.platform) -> ssl.SSLContext:
    """What checks the other side's certificate. Blocking (it reads the system's list): call it off the loop.

    On Windows, Python reads the root certificates that are in the
    system's store. On a Mac it reads none by itself: a packed program
    would look where the BUILD machine's Python kept them, find a file
    there on the machine that built it and nothing on anybody else's. So
    macOS's own list is named here - and with a file named, Python looks
    nowhere else, so the build machine checks what people get.

    The check itself cannot be switched off from here: there is no
    argument for it.
    """
    if platform == "darwin":
        context = ssl.create_default_context(cafile=MAC_ROOTS)
    else:
        # Not create_default_context(): it also reads SSL_CERT_FILE and
        # SSL_CERT_DIR, and with them whoever can set a variable chooses
        # whom Walka trusts. The system's own roots, and no others.
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        for flag in ("VERIFY_X509_PARTIAL_CHAIN", "VERIFY_X509_STRICT"):  # (as create_default_context sets)
            if hasattr(ssl, flag):
                context.verify_flags |= getattr(ssl, flag)
        if platform == "win32":
            _load_windows_store(context)
        else:
            paths = ssl.get_default_verify_paths()  # (openssl_*: where OpenSSL was built to look, not the variables)
            if paths.openssl_cafile and os.path.isfile(paths.openssl_cafile):
                context.load_verify_locations(cafile=paths.openssl_cafile)
            if paths.openssl_capath and os.path.isdir(paths.openssl_capath):
                context.load_verify_locations(capath=paths.openssl_capath)
            if not context.cert_store_stats()["x509_ca"]:
                _load_folder(context)
    # (What create_default_context sets already. Said again, so that it is not left to a default.)
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    # Nothing older than TLS 1.2: some systems still let the older ones through.
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context


def _load_windows_store(context: ssl.SSLContext) -> None:
    """The root and intermediate certificates of Windows' own store that
    are trusted for servers - what Python's default context loads, read
    here without the variables it reads as well."""
    found = bytearray()
    for store in ("CA", "ROOT"):
        try:
            entries = ssl.enum_certificates(store)  # type: ignore[attr-defined]  # (Windows only)
        except (AttributeError, OSError):
            continue
        for certificate, encoding, trust in entries:
            if encoding == "x509_asn" and (trust is True or ssl.Purpose.SERVER_AUTH.oid in trust):
                found += certificate
    if found:
        context.load_verify_locations(cadata=bytes(found))


def _load_folder(context: ssl.SSLContext) -> None:
    """Some systems keep their root certificates one to a file, in a
    folder that OpenSSL reads only when it needs one - so a count says
    none. Read them now: then what is counted is what is there."""
    folder = ssl.get_default_verify_paths().openssl_capath  # (not .capath: that one is SSL_CERT_DIR's, if it is set)
    if not folder or not os.path.isdir(folder):
        return
    seen: set[str] = set()
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return
    for name in names:
        file = os.path.realpath(os.path.join(folder, name))  # (the folder holds several names for one file)
        if file in seen or not os.path.isfile(file):
            continue
        seen.add(file)
        try:
            context.load_verify_locations(cafile=file)
        except (ssl.SSLError, OSError):
            pass  # not a certificate: the folder may hold other things


def roots_report(context: ssl.SSLContext, platform: str = sys.platform) -> dict:
    """Where the root certificates in ``context`` came from, and how many there are.

    ``named``: which of a few well-known ones are among them (the
    accounts' certificate and walka.dev's do not hang from the same one).
    """
    source = {"darwin": "macOS's own list", "win32": "the system's store"}.get(platform, "OpenSSL's default paths")
    names: set[str] = set()
    for certificate in context.get_ca_certs():
        for part in certificate.get("subject", ()):
            for key, value in part:
                if key == "commonName":
                    names.add(value)
    return {
        "source": source,
        "count": int(context.cert_store_stats().get("x509_ca", 0)),
        "named": [name for name in WELL_KNOWN_ROOTS if name in names],
    }


def _checks(context: object) -> bool:
    """Is ``context`` one that checks? (A client takes no other, whoever made it.)"""
    return isinstance(context, ssl.SSLContext) and context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname


# ---- looking a name up ---------------------------------------------------------------------------


def _look_up(host: str, port: int, family: int) -> list[tuple[Any, ...]]:
    try:
        return socket.getaddrinfo(host, port, family=family, type=socket.SOCK_STREAM, flags=socket.AI_ADDRCONFIG)
    except socket.gaierror:
        # (A computer with no network at all still has itself.)
        return socket.getaddrinfo(host, port, family=family, type=socket.SOCK_STREAM)


class DaemonResolver(aiohttp.abc.AbstractResolver):
    """Looks a name up on a thread of its own.

    aiohttp's own way uses the loop's threads, and Python waits for
    those when the program closes: a look-up that never answers would
    keep Walka from quitting. This thread is simply left behind.
    """

    async def resolve(self, host: str, port: int = 0, family: int = socket.AF_INET) -> list[dict[str, Any]]:
        try:
            found = await off_loop(_look_up, host, port, family, timeout=LOOK_UP_S, name="walka-account-names")
        except OffLoopTimeout:
            raise OSError("the name was not looked up in time") from None
        hosts: list[dict[str, Any]] = []
        for kind, _type, proto, _name, address in found:
            if kind == socket.AF_INET6 and (len(address) < 4 or address[3]):
                continue  # not usable here, or an address of one cable only: no server of the accounts is there
            hosts.append(
                {
                    "hostname": host,
                    "host": address[0],
                    "port": address[1],
                    "family": kind,
                    "proto": proto,
                    "flags": socket.AI_NUMERICHOST | socket.AI_NUMERICSERV,
                }
            )
        if not hosts:
            raise OSError("the name has no address")
        return hosts

    async def close(self) -> None:
        """Nothing to close: no thread is kept."""


# ---- asking --------------------------------------------------------------------------------------


class NetError(Exception):
    """No answer that can be used. ``kind`` is one word, and there is nothing else in it:

    ``"no_answer"`` (no connection, no answer in time, or one that was
    not HTTP), ``"certificate"`` (the other side's certificate could not
    be checked: nothing was asked), ``"too_big"`` (the answer is longer
    than Walka reads), ``"address"`` (a request this client does not
    make: nothing was asked).
    """

    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


def _no_constant(_name: str) -> Any:
    raise ValueError("not a number")


def _a_number(text: str) -> float:
    number = float(text)
    if not math.isfinite(number):  # ("1e999" is JSON by the letter - and Python makes infinity of it)
        raise ValueError("not a number")
    return number


@dataclass
class Answer:
    """What came back: the status, how long to wait if it says so, and the body as it came."""

    status: int
    retry_after: float | None
    body: bytes = field(repr=False)  # (it may hold the keys of a sign-in: never shown by accident)

    def json(self) -> Any:
        """The body as JSON. ``ValueError`` if it is none - and the body is not in that error."""
        try:
            value = json.loads(self.body.decode("utf-8"), parse_constant=_no_constant, parse_float=_a_number)
        except (ValueError, RecursionError):  # not text, not JSON, NaN, a number without end, or nested without end
            pass
        else:
            # Nested without end is refused here, not left to the
            # interpreter: a newer Python may parse 100,000 levels without a
            # RecursionError (and what reads the answer then would not).
            if _depth(value) <= MOST_DEPTH:
                return value
        raise ValueError("the answer is not JSON")


# How deep the accounts' answers may be nested (theirs are three or four levels).
MOST_DEPTH = 64


def _depth(value: Any) -> int:
    """How deeply ``value`` is nested, counted without recursion."""
    deepest, waiting = 0, [(value, 1)]
    while waiting:
        item, level = waiting.pop()
        if isinstance(item, (list, dict)):
            deepest = max(deepest, level)
            if level > MOST_DEPTH:
                return level
            waiting.extend((child, level + 1) for child in (item.values() if isinstance(item, dict) else item))
    return deepest


def _dumps(body: Any) -> str:
    return json.dumps(body, allow_nan=False, separators=(",", ":"))


def _retry_after(value: str | None) -> float | None:
    """Whole seconds, up to an hour - the only form taken. (A date would be read against this computer's clock.)"""
    if value is None or not _WHOLE_SECONDS.fullmatch(value.strip()):
        return None
    seconds = int(value.strip())
    return float(seconds) if seconds <= 3600 else None


def _kind_of(exc: BaseException) -> str:
    if isinstance(exc, (aiohttp.ClientConnectorCertificateError, ssl.SSLCertVerificationError)):
        return "certificate"
    return "no_answer"


class Client:
    """Asks one address. Each request is a connection of its own, closed
    when the answer is read: nothing stays open to the accounts between
    two walks."""

    def __init__(
        self,
        origin: Origin,
        *,
        context_factory: Callable[[], ssl.SSLContext] = make_ssl_context,
        resolver_factory: Callable[[], aiohttp.abc.AbstractResolver] = DaemonResolver,
    ) -> None:
        self.origin = origin
        # An address put together by hand must be one parse_origin would have given.
        self._usable = isinstance(origin, Origin) and parse_origin(origin.text) == origin
        self._context_factory = context_factory
        self._resolver_factory = resolver_factory
        self._context: ssl.SSLContext | None = None

    async def request(
        self,
        method: str,
        path: str,
        *,
        query: Sequence[tuple[str, str]] = (),
        headers: Mapping[str, str] | None = None,
        json_body: Any = None,
        timeout_s: float = 20.0,
    ) -> Answer:
        """One request to ``path`` of this client's address, and its answer - whatever the status.

        Raises ``NetError`` and nothing else (but lets a cancellation through).
        """
        url = self._address(method, path, query, headers, json_body)
        # (To aiohttp a limit of 0 means no limit at all. Every request of Walka's has one.)
        timed = isinstance(timeout_s, (int, float)) and not isinstance(timeout_s, bool) and 0 < timeout_s <= 300
        if url is None or not timed:
            raise NetError("address")
        outcome = await self._ask(method, url, dict(headers or {}), json_body, timeout_s)
        if isinstance(outcome, Answer):
            return outcome
        # Raised here, outside every "except": the error that led to it -
        # aiohttp's, with the request's headers in it - is not kept, not
        # even out of sight.
        raise NetError(outcome)

    def _address(
        self,
        method: str,
        path: str,
        query: Sequence[tuple[str, str]],
        headers: Mapping[str, str] | None,
        json_body: Any,
    ) -> URL | None:
        """The whole address of a request, or ``None`` for one that is not made."""
        if not self._usable or method not in _METHODS:
            return None
        if not isinstance(path, str) or not _PATH.fullmatch(path) or ".." in path or "//" in path:
            return None
        try:
            pairs = [(key, value) for key, value in query]
        except (TypeError, ValueError):
            return None
        if any(not isinstance(key, str) or not isinstance(value, str) for key, value in pairs):
            return None
        for name, value in (headers or {}).items():
            if not isinstance(name, str) or not isinstance(value, str):
                return None
            if not _HEADER_NAME.fullmatch(name) or not _HEADER_VALUE.fullmatch(value):
                return None
            if name.lower() in _NOT_THE_CALLERS:
                return None
        if json_body is not None:
            try:
                _dumps(json_body)
            except (TypeError, ValueError, RecursionError):
                return None
        text = self.origin.text + path + ("?" + urlencode(pairs) if pairs else "")
        try:
            # (encoded: sent as it is written here, not written anew.)
            url = URL(text, encoded=True)
        except (TypeError, ValueError):
            return None
        # Once more, on the address as it will be asked: this address and no other.
        port = self.origin.port if self.origin.port is not None else 443
        if (url.scheme, url.host, url.port) != (self.origin.scheme, self.origin.host, port) or url.user or url.password:
            return None
        return url

    async def _tls(self) -> ssl.SSLContext | None:
        """What checks certificates - made once, off the loop - or ``None`` if there is nothing that does."""
        if self._context is None:
            try:
                context = await off_loop(self._context_factory, timeout=CONTEXT_S, name="walka-account-roots")
            except asyncio.CancelledError:
                raise
            except Exception:
                return None
            if not _checks(context):
                return None
            if self._context is None:
                self._context = context
        return self._context

    async def _ask(self, method: str, url: URL, headers: dict[str, str], json_body: Any, timeout_s: float) -> Answer | str:
        """The answer - or the one word of what went wrong. No error leaves here but a cancellation."""
        try:
            settings: dict[str, Any] = {}
            if self.origin.scheme == "https":
                context = await self._tls()
                if context is None:
                    return "certificate"
                settings["ssl"] = context
            # (use_dns_cache: this connector lives for one request, so it has
            # nothing to remember - and with its memory on, aiohttp looks a
            # name up in a task of its own, which older versions leave
            # running when the request is cancelled.)
            connector = aiohttp.TCPConnector(
                force_close=True, use_dns_cache=False, resolver=self._resolver_factory(), **settings
            )
            try:
                session = aiohttp.ClientSession(
                    connector=connector,
                    headers={
                        "User-Agent": USER_AGENT,
                        "Accept": "application/json",
                        "Accept-Encoding": "identity",
                        # (Said outright: older versions of aiohttp close without saying so.)
                        "Connection": "close",
                    },
                    cookie_jar=aiohttp.DummyCookieJar(),
                    trust_env=False,
                    # Asked for as it is, and read as it comes: an answer
                    # packed all the same is not unpacked into memory.
                    auto_decompress=False,
                    json_serialize=_dumps,
                    timeout=aiohttp.ClientTimeout(total=timeout_s, sock_connect=min(10.0, timeout_s)),
                )
            except BaseException:
                await connector.close()
                raise
            try:
                # (Without a body aiohttp would still name a kind of body on a POST: there is none to name.)
                body: dict[str, Any] = {"skip_auto_headers": ("Content-Type",)} if json_body is None else {"json": json_body}
                async with session.request(method, url, headers=headers, allow_redirects=False, **body) as response:
                    said = response.content_length
                    if said is not None and said > MOST_BYTES:
                        return "too_big"
                    parts: list[bytes] = []
                    size = 0
                    while True:
                        part = await response.content.read(MOST_BYTES + 1 - size)
                        if not part:
                            break
                        parts.append(part)
                        size += len(part)
                        if size > MOST_BYTES:
                            return "too_big"
                    return Answer(response.status, _retry_after(response.headers.get("Retry-After")), b"".join(parts))
            finally:
                await session.close()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return _kind_of(exc)
