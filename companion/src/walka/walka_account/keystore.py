"""Where the sign-in to a Walka account is kept between two runs of Walka.

One thing is kept: the refresh token. Whoever has it has the whole Walka
account, so it goes to the system's own store for secrets and to no file:
Windows' Credential Manager, or the login keychain of a Mac. Where there
is neither (Linux), it is kept in memory and the sign-in lasts until
Walka is closed.

Both systems are asked through their own C libraries by way of
``ctypes``, as ``macos.py`` and ``winsession.py`` ask theirs, so nothing
needs to be installed for it. The module can be imported anywhere; the
calls only work on their own system.

The stores block (a Mac may sit on a call for as long as it likes), so
the rest of Walka reaches them only through ``Keys``: one call at a time,
each on a daemon thread of its own, each with a time limit for whoever
waits for it.

No error from here carries what was stored. An error says one word, and
at most the number the system answered.
"""

from __future__ import annotations

import ctypes
import logging
import sys
import threading
from collections.abc import Callable
from typing import Any, Protocol, TypeVar

from .threads import OffLoopTimeout, off_loop

log = logging.getLogger(__name__)

T = TypeVar("T")

# What an entry is called. It is read by people: in Credential Manager, in
# Keychain Access, and in the box a Mac shows when another program asks for it.
SERVICE = "Walka account"
# What stands where a store wants a user name. Never the e-mail address or
# the Walka account's id: Windows' Control Panel shows it to whoever looks.
USER_NAME = "Walka"
# The longest secret taken, in characters. (A refresh token is 12.)
MOST_SECRET = 512
# The longest entry name taken, in characters.
MOST_NAME = 300
# The most bytes Windows keeps in one entry (CRED_MAX_CREDENTIAL_BLOB_SIZE).
# More than that, read back from either system, is nothing of Walka's.
MOST_BLOB = 2560

THREAD_NAME = "walka-account-keys"

# The four words an error can say.
CODES = ("unavailable", "refused", "failed", "timeout")


class KeyStoreError(Exception):
    """The store did not do what was asked.

    ``code`` is one of: "unavailable" (no such store here), "refused"
    (the system would not let this program), "failed" (anything else),
    "timeout". ``status`` is the number the system answered, where it
    answered one: it is safe to show and to log. Nothing else is carried.
    """

    def __init__(self, code: str, status: int | None = None) -> None:
        super().__init__(code if status is None else f"{code} (the system answered {status})")
        self.code = code
        self.status = status


class KeyStore(Protocol):
    """What ``Keys`` asks of a store. Every call may block."""

    kind: str
    lasting: bool

    def read(self, name: str) -> str | None: ...

    def write(self, name: str, secret: str) -> None: ...

    def delete(self, name: str) -> None: ...


# ---- names, and what is stored (the same everywhere) -------------------------------------


def entry_name(origin_text: str) -> str:
    """The name of the entry for the accounts at one address.

    A stored sign-in belongs to the address it was made at, so the
    address is in the name.
    """
    return f"{SERVICE} - {origin_text}"


def valid_secret(text: object) -> bool:
    """Whether this can be stored: printable ASCII without spaces, 1 to 512 characters."""
    return isinstance(text, str) and 1 <= len(text) <= MOST_SECRET and all("!" <= char <= "~" for char in text)


def checked_name(name: object) -> str:
    """The name, if it is one a store can take: plain printable ASCII, not too long."""
    if not isinstance(name, str) or not 1 <= len(name) <= MOST_NAME or not all(" " <= char <= "~" for char in name):
        raise KeyStoreError("failed")
    return name


def encode(secret: object) -> bytes:
    """The bytes that go into the store. What is not a secret of Walka's is not stored at all."""
    if not valid_secret(secret):
        raise KeyStoreError("failed")
    blob = str(secret).encode("utf-8")
    if len(blob) > MOST_BLOB:
        raise KeyStoreError("failed")
    return blob


def decode(blob: bytes) -> str | None:
    """What the bytes of an entry say, or ``None`` if Walka did not write them.

    An entry that holds something else (empty, not text, with a space in
    it) is as good as no entry: the person signs in again.
    """
    if not blob or len(blob) > MOST_BLOB:
        return None
    try:
        text = blob.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return None
    return text if valid_secret(text) else None


# ---- in memory ---------------------------------------------------------------------------


class MemoryKeyStore:
    """For a system without a store for secrets, and for the tests: gone when Walka is closed."""

    kind = "memory"
    lasting = False

    def __init__(self) -> None:
        self._entries: dict[str, str] = {}

    def read(self, name: str) -> str | None:
        return self._entries.get(checked_name(name))

    def write(self, name: str, secret: str) -> None:
        name = checked_name(name)
        encode(secret)  # the same refusals as the real stores
        self._entries[name] = secret

    def delete(self, name: str) -> None:
        self._entries.pop(checked_name(name), None)


# ---- Windows: Credential Manager ---------------------------------------------------------

CRED_TYPE_GENERIC = 1
# Stays on this computer. Never 3 (CRED_PERSIST_ENTERPRISE): that one
# travels to every computer the person logs on to in a company's network.
CRED_PERSIST_LOCAL_MACHINE = 2

ERROR_NOT_FOUND = 1168
# Windows has nowhere to keep it for this user: they reached the computer
# over the network, or a company's rule forbids storing passwords.
ERROR_NO_SUCH_LOGON_SESSION = 1312

# Windows' DWORD and BOOL. (ctypes.wintypes says "C long" for DWORD, which
# is 32 bits on Windows and 64 elsewhere. Said this way the struct below is
# the same on every system, so its layout can be checked on any of them.)
_DWORD = ctypes.c_uint32
_BOOL = ctypes.c_int


class _FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", _DWORD), ("dwHighDateTime", _DWORD)]


class _CREDENTIALW(ctypes.Structure):
    """CREDENTIALW of wincred.h: 80 bytes on 64-bit Windows, the blob's pointer 40 bytes in."""

    _fields_ = [
        ("Flags", _DWORD),
        ("Type", _DWORD),
        ("TargetName", ctypes.c_wchar_p),
        ("Comment", ctypes.c_wchar_p),
        ("LastWritten", _FILETIME),
        ("CredentialBlobSize", _DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
        ("Persist", _DWORD),
        ("AttributeCount", _DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", ctypes.c_wchar_p),
        ("UserName", ctypes.c_wchar_p),
    ]


def windows_outcome(status: int) -> str:
    """What one of Windows' error numbers means here: "ok", "absent", "refused" or "failed"."""
    if status == 0:
        return "ok"
    if status == ERROR_NOT_FOUND:
        return "absent"
    if status == ERROR_NO_SUCH_LOGON_SESSION:
        return "refused"
    return "failed"


class _WindowsCalls:
    """The calls into advapi32, and nothing else: each answers 0, or Windows' error number.

    None of them shows anything on the screen.
    """

    def __init__(self, library: Any, last_error: Callable[[], int]) -> None:
        self.library = library
        self._last_error = last_error

    def _error(self) -> int:
        # (Asked on the thread that made the call, straight after it. A
        # failure that names no number must still not read as "it worked".)
        return int(self._last_error()) or -1

    def write(self, name: str, blob: bytes) -> int:
        """Make the entry, or replace it: Windows does either in the one call."""
        buffer = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob)
        credential = _CREDENTIALW()
        credential.Type = CRED_TYPE_GENERIC
        credential.TargetName = name
        credential.CredentialBlobSize = len(blob)
        credential.CredentialBlob = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))
        credential.Persist = CRED_PERSIST_LOCAL_MACHINE
        credential.UserName = USER_NAME
        if self.library.CredWriteW(ctypes.byref(credential), 0):
            return 0
        return self._error()

    def read(self, name: str) -> tuple[int, bytes]:
        found = ctypes.POINTER(_CREDENTIALW)()
        if not self.library.CredReadW(name, CRED_TYPE_GENERIC, 0, ctypes.byref(found)):
            return self._error(), b""
        if not found:
            return -1, b""
        try:
            size = int(found.contents.CredentialBlobSize)
            blob = found.contents.CredentialBlob
            if not blob or not 0 < size <= MOST_BLOB:
                return 0, b""
            return 0, ctypes.string_at(blob, size)
        finally:
            # Windows made the copy, so Windows has to free it.
            self.library.CredFree(ctypes.cast(found, ctypes.c_void_p))

    def delete(self, name: str) -> int:
        if self.library.CredDeleteW(name, CRED_TYPE_GENERIC, 0):
            return 0
        return self._error()


def _load_windows() -> _WindowsCalls:
    """advapi32, with the signatures of what is used (without them ctypes
    would pass pointers as 32-bit ints)."""
    if sys.platform != "win32":
        raise OSError("this only works on Windows")
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    credential = ctypes.POINTER(_CREDENTIALW)
    advapi32.CredWriteW.argtypes = [credential, _DWORD]
    advapi32.CredWriteW.restype = _BOOL
    advapi32.CredReadW.argtypes = [ctypes.c_wchar_p, _DWORD, _DWORD, ctypes.POINTER(credential)]
    advapi32.CredReadW.restype = _BOOL
    advapi32.CredDeleteW.argtypes = [ctypes.c_wchar_p, _DWORD, _DWORD]
    advapi32.CredDeleteW.restype = _BOOL
    advapi32.CredFree.argtypes = [ctypes.c_void_p]
    advapi32.CredFree.restype = None
    return _WindowsCalls(advapi32, ctypes.get_last_error)


class WindowsKeyStore:
    """Windows' Credential Manager: a "generic credential" that stays on this computer.

    Every program that runs as the same Windows user can read it: Windows
    has no guard for one program alone.
    """

    kind = "windows"
    lasting = True

    def __init__(self, calls: Any = None) -> None:
        self._calls = calls

    def _layer(self) -> Any:
        if self._calls is None:
            try:
                self._calls = _load_windows()
            except (OSError, AttributeError):
                raise KeyStoreError("unavailable") from None
        return self._calls

    def read(self, name: str) -> str | None:
        status, blob = self._layer().read(checked_name(name))
        outcome = windows_outcome(status)
        if outcome == "ok":
            return decode(blob)
        if outcome == "absent":
            return None
        raise KeyStoreError(outcome, status)

    def write(self, name: str, secret: str) -> None:
        name, blob = checked_name(name), encode(secret)
        status = self._layer().write(name, blob)
        outcome = windows_outcome(status)
        if outcome != "ok":
            raise KeyStoreError("refused" if outcome == "refused" else "failed", status)

    def delete(self, name: str) -> None:
        status = self._layer().delete(checked_name(name))
        outcome = windows_outcome(status)
        if outcome not in ("ok", "absent"):
            raise KeyStoreError(outcome, status)


# ---- Mac: the login keychain -------------------------------------------------------------
#
# The OLDER calls (SecKeychain...), on purpose. A program that is signed ad
# hoc, as Walka is, can only reach the keychain that is a file, and there
# the newer calls (SecItem...) cannot be kept from showing a password box.
# These can: SecKeychainSetUserInteractionAllowed. Items in that keychain
# never go to iCloud.

_SECURITY = "/System/Library/Frameworks/Security.framework/Security"
_CORE_FOUNDATION = "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"

ERR_SEC_ITEM_NOT_FOUND = -25300
ERR_SEC_DUPLICATE_ITEM = -25299
# The Mac would have had to ask the person (the item was made by another
# build of the program, or the keychain is locked), and it may not ask.
ERR_SEC_INTERACTION_NOT_ALLOWED = -25308
ERR_SEC_AUTH_FAILED = -25293
ERR_SEC_USER_CANCELED = -128
# "This program may not change this item": after the program was moved or renamed.
ERR_SEC_INVALID_OWNER_EDIT = -25244
ERR_SEC_NO_DEFAULT_KEYCHAIN = -25307
ERR_SEC_NOT_AVAILABLE = -25291

_MAC_REFUSED = (
    ERR_SEC_INTERACTION_NOT_ALLOWED,
    ERR_SEC_AUTH_FAILED,
    ERR_SEC_USER_CANCELED,
    ERR_SEC_INVALID_OWNER_EDIT,
)
_MAC_NO_STORE = (ERR_SEC_NO_DEFAULT_KEYCHAIN, ERR_SEC_NOT_AVAILABLE)


def mac_outcome(status: int) -> str:
    """What one of the Mac's answers means here: "ok", "absent", "duplicate", "refused", "unavailable" or "failed"."""
    if status == 0:
        return "ok"
    if status == ERR_SEC_ITEM_NOT_FOUND:
        return "absent"
    if status == ERR_SEC_DUPLICATE_ITEM:
        return "duplicate"
    if status in _MAC_REFUSED:
        return "refused"
    if status in _MAC_NO_STORE:
        return "unavailable"
    return "failed"


def _mac_error(status: int) -> KeyStoreError:
    outcome = mac_outcome(status)
    return KeyStoreError(outcome if outcome in ("refused", "unavailable") else "failed", status)


def mac_names(name: str) -> tuple[bytes, bytes]:
    """An entry's name as the keychain wants it, in the two fields it calls "service" and "account".

    "Walka account - <address>" is kept as service "Walka account" - what
    Keychain Access and the Mac's own box show - with the address in the
    other field. A name with no " - " in it stands in both.
    """
    name = checked_name(name)
    service, dash, account = name.partition(" - ")
    if not (dash and service and account):
        service = account = name
    return service.encode("utf-8"), account.encode("utf-8")


class _MacCalls:
    """The calls into Security.framework, and nothing else: each answers the Mac's status (0 is "done").

    An item that ``find_item`` gives belongs to the caller, who hands it
    to ``release`` when done with it - also after ``delete``.
    """

    def __init__(self, security: Any, core_foundation: Any) -> None:
        self.security = security
        self.core_foundation = core_foundation

    def quiet(self) -> int:
        """Tell the Mac that it may show no box for what this program asks of the keychain.

        It holds for the whole program, and it is not switched back:
        nothing else in Walka asks the keychain.
        """
        return int(self.security.SecKeychainSetUserInteractionAllowed(0))

    def find_data(self, service: bytes, account: bytes) -> tuple[int, bytes]:
        """What the entry holds."""
        length = ctypes.c_uint32(0)
        data = ctypes.c_void_p(None)
        try:
            status = int(
                self.security.SecKeychainFindGenericPassword(
                    None, len(service), service, len(account), account, ctypes.byref(length), ctypes.byref(data), None
                )
            )
            if status != 0:
                return status, b""
            if not data.value or not 0 < length.value <= MOST_BLOB:
                return 0, b""
            return 0, ctypes.string_at(data.value, length.value)
        finally:
            # The Mac made the copy, so the Mac has to free it.
            if data.value:
                self.security.SecKeychainItemFreeContent(None, data)

    def find_item(self, service: bytes, account: bytes) -> tuple[int, int | None]:
        """The entry itself, without what it holds: nothing is decrypted for this."""
        item = ctypes.c_void_p(None)
        status = int(
            self.security.SecKeychainFindGenericPassword(
                None, len(service), service, len(account), account, None, None, ctypes.byref(item)
            )
        )
        if status != 0:
            self.release(item.value)
            return status, None
        return 0, item.value

    def add(self, service: bytes, account: bytes, blob: bytes) -> int:
        return int(
            self.security.SecKeychainAddGenericPassword(
                None, len(service), service, len(account), account, len(blob), blob, None
            )
        )

    def modify(self, item: int, blob: bytes) -> int:
        return int(self.security.SecKeychainItemModifyAttributesAndData(item, None, len(blob), blob))

    def delete(self, item: int) -> int:
        return int(self.security.SecKeychainItemDelete(item))

    def release(self, item: int | None) -> None:
        if item:
            self.core_foundation.CFRelease(item)


def _load_mac() -> _MacCalls:
    """Security and CoreFoundation, with the signatures of what is used
    (without them ctypes would pass pointers as 32-bit ints)."""
    if sys.platform != "darwin":
        raise OSError("this only works on macOS")
    security = ctypes.CDLL(_SECURITY)
    core_foundation = ctypes.CDLL(_CORE_FOUNDATION)
    pointer = ctypes.c_void_p
    length = ctypes.c_uint32
    status = ctypes.c_int32  # OSStatus
    security.SecKeychainSetUserInteractionAllowed.argtypes = [ctypes.c_ubyte]  # Boolean
    security.SecKeychainSetUserInteractionAllowed.restype = status
    security.SecKeychainFindGenericPassword.argtypes = [
        pointer,  # which keychain: none named, so the person's own
        length,
        ctypes.c_char_p,  # the service
        length,
        ctypes.c_char_p,  # the account
        ctypes.POINTER(length),  # how long what it holds is
        ctypes.POINTER(pointer),  # what it holds
        ctypes.POINTER(pointer),  # the item
    ]
    security.SecKeychainFindGenericPassword.restype = status
    security.SecKeychainAddGenericPassword.argtypes = [
        pointer,
        length,
        ctypes.c_char_p,
        length,
        ctypes.c_char_p,
        length,
        ctypes.c_char_p,  # what it is to hold (const void *: bytes, with their length before them)
        ctypes.POINTER(pointer),
    ]
    security.SecKeychainAddGenericPassword.restype = status
    security.SecKeychainItemModifyAttributesAndData.argtypes = [pointer, pointer, length, ctypes.c_char_p]
    security.SecKeychainItemModifyAttributesAndData.restype = status
    security.SecKeychainItemDelete.argtypes = [pointer]
    security.SecKeychainItemDelete.restype = status
    security.SecKeychainItemFreeContent.argtypes = [pointer, pointer]
    security.SecKeychainItemFreeContent.restype = status
    core_foundation.CFRelease.argtypes = [pointer]
    core_foundation.CFRelease.restype = None
    return _MacCalls(security, core_foundation)


class MacKeyStore:
    """The login keychain of a Mac. It never shows a password box.

    An item belongs to the build of the program that made it. Another
    build - Walka after an update, another Python - is refused, where the
    Mac would otherwise ask the person for their password in the middle
    of whatever they are doing. Being refused only ever means "sign in
    again" - and signing in again has to work, so ``write`` leaves no
    item behind that this build cannot read.
    """

    kind = "mac"
    lasting = True

    def __init__(self, calls: Any = None) -> None:
        self._calls = calls

    def _layer(self) -> Any:
        if self._calls is None:
            try:
                self._calls = _load_mac()
            except (OSError, AttributeError):  # no such library here, or the Mac has dropped a call
                raise KeyStoreError("unavailable") from None
        return self._calls

    def _ask(self, call: str, *arguments: Any) -> Any:
        """One call into the keychain - after telling the Mac, every time, to show no box for it."""
        layer = self._layer()
        status = layer.quiet()
        if status != 0:
            # Then a box could come up. Rather not ask at all.
            raise _mac_error(status)
        return getattr(layer, call)(*arguments)

    def read(self, name: str) -> str | None:
        service, account = mac_names(name)
        status, blob = self._ask("find_data", service, account)
        outcome = mac_outcome(status)
        if outcome == "ok":
            return decode(blob)
        if outcome == "absent":
            return None
        raise _mac_error(status)

    def write(self, name: str, secret: str) -> None:
        (service, account), blob = mac_names(name), encode(secret)
        status, item = self._ask("find_item", service, account)
        if mac_outcome(status) == "absent":
            status = self._ask("add", service, account, blob)
            if mac_outcome(status) != "duplicate":
                if status != 0:
                    raise _mac_error(status)
                return
            # Another Walka made the entry between the two calls: change that one.
            status, item = self._ask("find_item", service, account)
        if status != 0 or not item:
            raise _mac_error(status or -1)
        try:
            # In place: there is no moment at which another Walka finds no entry.
            changed = self._ask("modify", item, blob)
            if changed == 0:
                # By the Mac's usual rules any program may put something
                # new into an item, and only the build that made the item
                # may take it out. An item of an older build could then
                # be changed here, and still every later read of it be
                # refused - for good, however often the person signs in
                # again. Hence this look. (No test can be another build:
                # this is written from the rules, not from a trial.)
                changed, _ = self._ask("find_data", service, account)
                if mac_outcome(changed) != "refused":
                    return
            # This build may not change the entry, or may not read it:
            # another build made it. With it in the way no sign-in could
            # be kept here again, so it is removed and made anew: the new
            # one is this build's own.
            removed = self._ask("delete", item)
        finally:
            self._layer().release(item)
        if mac_outcome(removed) not in ("ok", "absent"):
            raise _mac_error(changed)
        status = self._ask("add", service, account, blob)
        if status != 0:
            raise _mac_error(status)

    def delete(self, name: str) -> None:
        service, account = mac_names(name)
        status, item = self._ask("find_item", service, account)
        if mac_outcome(status) == "absent":
            return
        if status != 0 or not item:
            raise _mac_error(status or -1)
        try:
            status = self._ask("delete", item)
        finally:
            self._layer().release(item)
        if mac_outcome(status) not in ("ok", "absent"):
            raise _mac_error(status)


# ---- which store ---------------------------------------------------------------------------


def open_keystore(platform: str = sys.platform) -> MemoryKeyStore | WindowsKeyStore | MacKeyStore:
    """The store of this system. Nothing is asked of it yet: that happens at the first call.

    Elsewhere than on Windows and a Mac there is none that is always
    there, and the sign-in then lasts until Walka is closed.
    """
    if platform == "win32":
        return WindowsKeyStore()
    if platform == "darwin":
        return MacKeyStore()
    return MemoryKeyStore()


# ---- the face the rest of Walka sees -----------------------------------------------------


class Keys:
    """A store, asked from the loop without holding it up.

    One call at a time: the Mac's "show no box" holds for the whole
    program, and a sign-out's delete and a refresh's write must not
    overtake each other. The lock is taken inside the worker thread. A
    read or a write waits for it with a time limit, so a call that the
    system sits on holds up nothing but the calls behind it - and those
    give up too.

    A delete does not give up. Whoever asked for it stops waiting after
    the time limit like everybody else, and is told "could not"; but its
    thread (a daemon: closing Walka does not wait for it) stays in the
    line for as long as it takes, and the delete is made when the store
    comes back. Signing out must take the key away if it can at all - also
    a key that a write put there while the store sat on it.

    A lock promises no order among those who wait for it. So each call
    gets a number when it is asked, and a delete stands down when a write
    that was asked AFTER it has been made already: that key belongs to a
    later sign-in, and is not this delete's to take away.
    """

    def __init__(self, store: KeyStore, *, timeout_s: float = 15.0) -> None:
        self._store = store
        self.kind: str = store.kind
        self.lasting: bool = store.lasting
        self._timeout_s = timeout_s
        self._lock = threading.Lock()
        self._asked = 0  # how many calls were asked so far: each one's number
        self._newest_write: dict[str, int] = {}  # name -> the number of the newest write the store took

    async def read(self, name: str) -> str | None:
        """What is stored under the name, or ``None``. Raises ``KeyStoreError``."""
        return await self._off("read", lambda _mine: self._store.read(name))

    async def write(self, name: str, secret: str, *, if_still: Callable[[], bool] | None = None) -> bool:
        """Store it. Raises ``KeyStoreError``.

        ``if_still`` is asked inside the thread, under the lock, as the
        last thing before the system is called: a write that had to wait
        behind a sign-out must not bring the sign-in back. If it says no,
        nothing is written and the answer is ``False``.

        A write whose caller has stopped waiting (it took too long, or
        the caller was cancelled) and that has not reached the system by
        then is not made at all.
        """

        def work(mine: int) -> bool:
            if if_still is not None and not if_still():
                return False
            self._store.write(name, secret)
            self._newest_write[name] = max(self._newest_write.get(name, 0), mine)  # (still under the lock)
            return True

        return await self._off("write", work)

    async def delete(self, name: str) -> bool:
        """Remove what is stored under the name. A failure is ``False``, never an error.

        ``True``: what this delete was asked to take away is gone now -
        deleted, or written over meanwhile by a later sign-in, whose key
        stays. ``False``: it may still be there (and if the store only
        sat on a call, the delete is still made when the store comes back).
        """
        try:
            await self._delete(name)
        except KeyStoreError:
            return False
        return True

    async def _delete(self, name: str) -> None:
        # (A delete is made even when nobody waits for it any more:
        # signing out must take the key away if it can at all.)
        def work(mine: int) -> None:
            if mine < self._newest_write.get(name, 0):
                return  # a write that was asked after this delete got there first: that key is not this delete's
            self._store.delete(name)

        await self._off("delete", work, though_unheard=True)

    async def _off(self, what: str, work: Callable[[int], T], *, though_unheard: bool = False) -> T:
        """``though_unheard``: the call is made even when nobody waits for it any more (a delete)."""
        unheard = threading.Event()  # set once nobody waits for the answer any more
        self._asked += 1
        mine = self._asked  # this call's number: taken here, on the loop, in the order the calls are asked

        def locked() -> T:
            if though_unheard:
                # No time limit of its own: behind a call the system sits on,
                # it is made when the store comes back, however late.
                self._lock.acquire()
            elif not self._lock.acquire(timeout=self._timeout_s):
                raise KeyStoreError("timeout")
            failure: KeyStoreError
            try:
                if unheard.is_set() and not though_unheard:
                    # Whoever asked has been told "timeout" and has gone on
                    # as if nothing was done. So nothing is done.
                    raise KeyStoreError("timeout")
                return work(mine)
            except KeyStoreError as error:
                # Made anew, and only from one of the four words and a
                # number: whatever else a store hung on its error stays here.
                code, status = error.code, error.status
                failure = KeyStoreError(
                    code if isinstance(code, str) and code in CODES else "failed",
                    status if type(status) is int else None,
                )
            except Exception as error:
                # A mistake in this module, or ctypes objecting. Its words
                # could hold what was being stored: they go no further.
                # What kind of error it was can be said, and it is all
                # there is to go by when this happens on somebody's computer.
                log.warning(
                    "the store for secrets (%s) broke off a %s with an error of the kind %s",
                    self.kind,
                    what,
                    type(error).__name__,
                )
                failure = KeyStoreError("failed")
            finally:
                self._lock.release()
            # (Raised out here, so that the first error does not travel along inside it.)
            raise failure

        try:
            return await off_loop(locked, timeout=self._timeout_s, name=THREAD_NAME)
        except OffLoopTimeout:
            log.warning("the store for secrets (%s) did not answer a %s in time", self.kind, what)
            raise KeyStoreError("timeout") from None
        except KeyStoreError as error:
            # The word, and the system's number: neither says what was stored.
            log.warning("the store for secrets (%s) did not %s: %s, %s", self.kind, what, error.code, error.status)
            raise
        finally:
            unheard.set()
