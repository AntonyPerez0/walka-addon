"""Where the sign-in is kept (walka_account.keystore): the names, what the
systems' answers mean, the order of the calls, the one-at-a-time face - and
that what is stored shows up in no error and no line of the log.

Two tests ask a real store, one on Windows and one on a Mac, each under a
name of its own. They are skipped everywhere else, and their run on the CI
machines is the only time that code runs before a person's computer runs
it.
"""

from __future__ import annotations

import asyncio
import ctypes
import logging
import sys
import threading
import types
import uuid

import pytest

from walka.walka_account import keystore
from walka.walka_account.keystore import (
    MOST_BLOB,
    SERVICE,
    THREAD_NAME,
    Keys,
    KeyStoreError,
    MacKeyStore,
    MemoryKeyStore,
    WindowsKeyStore,
    checked_name,
    decode,
    encode,
    entry_name,
    mac_names,
    mac_outcome,
    mark_name,
    open_keystore,
    valid_secret,
    windows_outcome,
)
from walka.walka_account.threads import OffLoopTimeout, off_loop
from walka_account_rig import LatestFirst

SECRET = "v4xk2j3h5l6m"
OTHER = "q9w8e7r6t5y4"
NAME = "Walka account - https://accounts.example"


def run(scenario, limit: float = 30):
    return asyncio.run(asyncio.wait_for(scenario(), limit))


def raised(call, *arguments) -> KeyStoreError:
    with pytest.raises(KeyStoreError) as caught:
        call(*arguments)
    return caught.value


def says_nothing(error: BaseException, *secrets: str) -> bool:
    """Neither the error nor anything it carries along holds one of the secrets."""
    seen = 0
    while error is not None and seen < 10:
        text = f"{error!r} {error} {error.args!r} {vars(error)!r}"
        if any(secret in text for secret in secrets):
            return False
        error = error.__cause__ or error.__context__
        seen += 1
    return True


def worker_threads() -> list[threading.Thread]:
    return [thread for thread in threading.enumerate() if thread.name == THREAD_NAME]


def wait_for_the_workers() -> None:
    for thread in worker_threads():
        thread.join(10)
    assert not worker_threads()


# ---- names, and what is stored -----------------------------------------------------------


def test_an_entry_is_named_after_the_address_it_was_made_at():
    assert entry_name("https://accounts.example") == "Walka account - https://accounts.example"
    assert entry_name("http://127.0.0.1:54321") == "Walka account - http://127.0.0.1:54321"
    assert entry_name("https://a.example") != entry_name("https://b.example")
    assert SERVICE == "Walka account"
    # What a store shows as the user is a fixed word: never the e-mail address, never the id.
    assert keystore.USER_NAME == "Walka"


def test_the_mark_has_an_entry_of_its_own_whose_name_begins_like_the_sign_ins():
    # "Its name begins Walka account": so say the card and the README of whatever Walka keeps in the store.
    assert mark_name("https://accounts.example") == "Walka account - https://accounts.example - sending"
    assert mark_name("http://127.0.0.1:54321") == "Walka account - http://127.0.0.1:54321 - sending"
    for address in ("https://accounts.example", "http://127.0.0.1:54321"):
        assert mark_name(address).startswith(SERVICE + " - ") and mark_name(address) != entry_name(address)
        assert mark_name(address).startswith(entry_name(address)) and checked_name(mark_name(address))
    assert mark_name("https://a.example") != mark_name("https://b.example")
    # In a keychain: the same service as the sign-in - what Keychain Access shows - and an account of its own.
    assert mac_names(mark_name("https://accounts.example")) == (b"Walka account", b"https://accounts.example - sending")
    assert mac_names(mark_name("https://accounts.example"))[1] != mac_names(NAME)[1]
    # The two are two entries: what is done to one leaves the other as it was.
    store = MemoryKeyStore()
    store.write(NAME, SECRET)
    store.write(mark_name("https://accounts.example"), OTHER)
    store.write(NAME, "a-renewed-key")  # (the sign-in is written anew at every refresh)
    assert store.read(mark_name("https://accounts.example")) == OTHER
    store.delete(mark_name("https://accounts.example"))
    assert store.read(NAME) == "a-renewed-key" and store.read(mark_name("https://accounts.example")) is None


def test_what_can_be_stored():
    for good in (SECRET, "a", "x" * 512, "AZaz09._~+/=-", "!~"):
        assert valid_secret(good), good
    for bad in ("", "x" * 513, "two words", "tab\there", "line\n", "nul\x00", "caf\u00e9", "\x7f", None, 12, b"bytes"):
        assert not valid_secret(bad), repr(bad)


def test_a_name_a_store_can_take():
    assert checked_name(NAME) == NAME
    assert checked_name("Walka test 4d6c2b0e-6f0c-4c58-9a44-0d1f3b1c2a55")
    for bad in ("", "x" * 301, "line\nend", "nul\x00", "caf\u00e9", None, 5, b"Walka"):
        assert raised(checked_name, bad).code == "failed"


def test_only_a_secret_of_walkas_goes_into_a_store_and_only_such_a_one_comes_out():
    assert encode(SECRET) == SECRET.encode("ascii")
    assert len(encode("x" * 512)) == 512 <= MOST_BLOB == 2560
    for bad in ("", "x" * 513, "two words", "x" * 2561, None, 7):
        error = raised(encode, bad)
        assert error.code == "failed" and error.status is None

    assert decode(SECRET.encode()) == SECRET
    # Whatever else lies under the name is as good as nothing: the person signs in again.
    for junk in (b"", b"\xff\xfe\x00", b"two words", b"nul\x00inside", "caf\u00e9".encode(), b"x" * 513, b"x" * 2561):
        assert decode(junk) is None, junk[:12]


def test_an_error_says_one_word_and_at_most_the_systems_number():
    plain = KeyStoreError("timeout")
    assert (plain.code, plain.status, str(plain)) == ("timeout", None, "timeout")
    numbered = KeyStoreError("refused", -25308)
    assert (numbered.code, numbered.status) == ("refused", -25308)
    assert "refused" in str(numbered) and "-25308" in str(numbered)


def test_what_windows_answers_and_what_it_means():
    assert windows_outcome(0) == "ok"
    assert windows_outcome(1168) == "absent"  # ERROR_NOT_FOUND
    assert windows_outcome(1312) == "refused"  # ERROR_NO_SUCH_LOGON_SESSION
    for other in (5, 87, 1004, 2202, -1, 1):
        assert windows_outcome(other) == "failed"
    assert keystore.CRED_TYPE_GENERIC == 1
    # On this computer only. (3 would travel with the person through a company's network.)
    assert keystore.CRED_PERSIST_LOCAL_MACHINE == 2


def test_what_a_mac_answers_and_what_it_means():
    assert mac_outcome(0) == "ok"
    assert mac_outcome(-25300) == "absent"
    assert mac_outcome(-25299) == "duplicate"
    for refusal in (-25308, -25293, -128, -25244):
        assert mac_outcome(refusal) == "refused"
    for none_here in (-25307, -25291):
        assert mac_outcome(none_here) == "unavailable"
    for other in (-50, -34018, -25295, 1, -1):
        assert mac_outcome(other) == "failed"


def test_how_an_entry_is_called_in_a_keychain():
    # The fixed part is what Keychain Access and the Mac's own box show; the address is the account.
    assert mac_names(NAME) == (b"Walka account", b"https://accounts.example")
    assert mac_names(entry_name("http://127.0.0.1:54321")) == (b"Walka account", b"http://127.0.0.1:54321")
    assert mac_names("Walka test 1234") == (b"Walka test 1234", b"Walka test 1234")
    assert mac_names("odd - ") == (b"odd - ", b"odd - ")
    assert raised(mac_names, "line\nend").code == "failed"


@pytest.mark.skipif(ctypes.sizeof(ctypes.c_void_p) != 8, reason="the sizes of a 64-bit system")
def test_the_struct_handed_to_windows_is_laid_out_as_windows_says():
    struct = keystore._CREDENTIALW
    assert ctypes.sizeof(struct) == 80
    offsets = {name: getattr(struct, name).offset for name, _kind in struct._fields_}
    assert offsets == {
        "Flags": 0,
        "Type": 4,
        "TargetName": 8,
        "Comment": 16,
        "LastWritten": 24,
        "CredentialBlobSize": 32,
        "CredentialBlob": 40,
        "Persist": 48,
        "AttributeCount": 52,
        "Attributes": 56,
        "TargetAlias": 64,
        "UserName": 72,
    }


# ---- which store, and the one in memory --------------------------------------------------


def test_each_system_gets_its_own_store_and_the_others_one_in_memory():
    windows, mac, linux = open_keystore("win32"), open_keystore("darwin"), open_keystore("linux")
    assert isinstance(windows, WindowsKeyStore) and (windows.kind, windows.lasting) == ("windows", True)
    assert isinstance(mac, MacKeyStore) and (mac.kind, mac.lasting) == ("mac", True)
    assert isinstance(linux, MemoryKeyStore) and (linux.kind, linux.lasting) == ("memory", False)
    assert isinstance(open_keystore("freebsd14"), MemoryKeyStore)
    assert open_keystore().kind == {"win32": "windows", "darwin": "mac"}.get(sys.platform, "memory")


@pytest.mark.skipif(sys.platform == "win32", reason="what happens off Windows")
def test_off_windows_there_is_no_credential_manager():
    with pytest.raises(OSError):
        keystore._load_windows()
    store = WindowsKeyStore()
    for call, arguments in ((store.read, (NAME,)), (store.write, (NAME, SECRET)), (store.delete, (NAME,))):
        error = raised(call, *arguments)
        assert error.code == "unavailable" and says_nothing(error, SECRET)


@pytest.mark.skipif(sys.platform == "darwin", reason="what happens off a Mac")
def test_off_a_mac_there_is_no_keychain():
    with pytest.raises(OSError):
        keystore._load_mac()
    store = MacKeyStore()
    for call, arguments in ((store.read, (NAME,)), (store.write, (NAME, SECRET)), (store.delete, (NAME,))):
        error = raised(call, *arguments)
        assert error.code == "unavailable" and says_nothing(error, SECRET)


def test_the_store_in_memory_keeps_reads_and_forgets():
    store = MemoryKeyStore()
    assert store.read(NAME) is None
    store.delete(NAME)  # absent is fine
    store.write(NAME, SECRET)
    assert store.read(NAME) == SECRET
    store.write(NAME, OTHER)
    assert store.read(NAME) == OTHER and store.read(entry_name("https://other.example")) is None
    store.delete(NAME)
    assert store.read(NAME) is None
    # The same refusals as the real ones, so that a test with this store proves something.
    assert raised(store.write, NAME, "two words").code == "failed"
    assert raised(store.write, "", SECRET).code == "failed"
    assert MemoryKeyStore().read(NAME) is None  # nothing is shared between two of them


# ---- Windows: the order of the calls, against a layer that answers what it is told ----------


class FakeWindows:
    """Credential Manager, as the store sees it: three calls that answer 0 or an error number."""

    def __init__(self) -> None:
        self.entries: dict[str, bytes] = {}
        self.answers: list[int] = []  # used up one a call; none left: it simply works
        self.log: list[str] = []

    def _told(self) -> int:
        return self.answers.pop(0) if self.answers else 0

    def write(self, name: str, blob: bytes) -> int:
        self.log.append("write")
        status = self._told()
        if status == 0:
            self.entries[name] = blob
        return status

    def read(self, name: str) -> tuple[int, bytes]:
        self.log.append("read")
        status = self._told()
        if status:
            return status, b""
        if name not in self.entries:
            return 1168, b""
        return 0, self.entries[name]

    def delete(self, name: str) -> int:
        self.log.append("delete")
        status = self._told()
        if status:
            return status
        return 0 if self.entries.pop(name, None) is not None else 1168


def test_windows_one_call_each_and_a_missing_entry_is_no_error():
    calls = FakeWindows()
    store = WindowsKeyStore(calls)
    assert store.read(NAME) is None  # 1168
    store.delete(NAME)  # 1168: absent is fine
    store.write(NAME, SECRET)
    assert calls.entries == {NAME: SECRET.encode()}
    assert store.read(NAME) == SECRET
    store.write(NAME, OTHER)  # the one call replaces
    assert store.read(NAME) == OTHER and len(calls.entries) == 1
    store.delete(NAME)
    assert calls.entries == {} and store.read(NAME) is None
    store.delete(NAME)
    assert calls.log == ["read", "delete", "write", "read", "write", "read", "delete", "read", "delete"]


def test_windows_what_is_not_walkas_under_the_name_reads_as_nothing():
    calls = FakeWindows()
    store = WindowsKeyStore(calls)
    for junk in (b"", b"\xff\xfe", b"a password with spaces", b"x" * 600):
        calls.entries[NAME] = junk
        assert store.read(NAME) is None


def test_windows_refusals_and_failures_say_the_number_and_never_the_secret():
    for status, code in ((1312, "refused"), (5, "failed"), (87, "failed"), (-1, "failed")):
        calls = FakeWindows()
        calls.entries[NAME] = SECRET.encode()
        store = WindowsKeyStore(calls)
        for call, arguments in ((store.read, (NAME,)), (store.write, (NAME, OTHER)), (store.delete, (NAME,))):
            calls.answers = [status]
            error = raised(call, *arguments)
            assert (error.code, error.status) == (code, status)
            assert says_nothing(error, SECRET, OTHER)
        assert calls.entries == {NAME: SECRET.encode()}
    # "Not found" in answer to a write is no success either.
    calls = FakeWindows()
    calls.answers = [1168]
    assert raised(WindowsKeyStore(calls).write, NAME, SECRET).code == "failed"


def test_windows_is_not_asked_about_what_cannot_be_stored():
    calls = FakeWindows()
    store = WindowsKeyStore(calls)
    assert raised(store.write, NAME, "two words").code == "failed"
    assert raised(store.write, NAME, "x" * 513).code == "failed"
    assert raised(store.write, "bad\nname", SECRET).code == "failed"
    assert raised(store.read, "").code == "failed"
    assert raised(store.delete, "x" * 301).code == "failed"
    assert calls.log == []


# ---- Windows: the calls themselves, against a library made of callbacks -------------------


class WindowsLibrary:
    """advapi32's four functions as ctypes callbacks with Windows' own
    signatures, so that the code which fills the struct, follows the
    pointers and frees what Windows made runs on any system."""

    def __init__(self) -> None:
        self.vault: dict[str, bytes] = {}
        self.written: list[dict] = []
        self.handed_out: list[int] = []
        self.freed: list[int] = []
        self.errors: list[int] = [0]
        self.fail_with: int | None = None
        self.read_gives_nothing = False
        self._kept: list[object] = []
        credential = ctypes.POINTER(keystore._CREDENTIALW)
        dword, wide = ctypes.c_uint32, ctypes.c_wchar_p

        def refused() -> bool:
            if self.fail_with is None:
                return False
            self.errors.append(self.fail_with)
            return True

        @ctypes.CFUNCTYPE(ctypes.c_int, credential, dword)
        def cred_write(pointer, flags):
            if refused():
                return 0
            given = pointer.contents
            blob = ctypes.string_at(given.CredentialBlob, given.CredentialBlobSize)
            self.written.append(
                {
                    "call_flags": flags,
                    "Flags": given.Flags,
                    "Type": given.Type,
                    "TargetName": given.TargetName,
                    "Comment": given.Comment,
                    "blob": blob,
                    "Persist": given.Persist,
                    "AttributeCount": given.AttributeCount,
                    "Attributes": given.Attributes,
                    "TargetAlias": given.TargetAlias,
                    "UserName": given.UserName,
                }
            )
            self.vault[given.TargetName] = blob
            return 1

        @ctypes.CFUNCTYPE(ctypes.c_int, wide, dword, dword, ctypes.POINTER(credential))
        def cred_read(name, kind, flags, out):
            assert (kind, flags) == (1, 0)
            if refused():
                return 0
            if name not in self.vault:
                self.errors.append(1168)
                return 0
            if self.read_gives_nothing:
                return 1
            blob = self.vault[name]
            buffer = (ctypes.c_ubyte * max(len(blob), 1)).from_buffer_copy(blob or b"\x00")
            made = keystore._CREDENTIALW()
            made.Type = 1
            made.TargetName = name
            made.CredentialBlobSize = len(blob)
            made.CredentialBlob = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))
            self._kept.append((made, buffer))
            self.handed_out.append(ctypes.addressof(made))
            out[0] = ctypes.pointer(made)
            return 1

        @ctypes.CFUNCTYPE(ctypes.c_int, wide, dword, dword)
        def cred_delete(name, kind, flags):
            assert (kind, flags) == (1, 0)
            if refused():
                return 0
            if self.vault.pop(name, None) is None:
                self.errors.append(1168)
                return 0
            return 1

        @ctypes.CFUNCTYPE(None, ctypes.c_void_p)
        def cred_free(address):
            self.freed.append(address)

        self.CredWriteW, self.CredReadW, self.CredDeleteW, self.CredFree = cred_write, cred_read, cred_delete, cred_free

    def calls(self) -> keystore._WindowsCalls:
        return keystore._WindowsCalls(self, lambda: self.errors[-1])


def test_windows_gets_a_credential_that_stays_on_this_computer_under_a_fixed_user_name():
    library = WindowsLibrary()
    store = WindowsKeyStore(library.calls())
    store.write(NAME, SECRET)
    assert library.written == [
        {
            "call_flags": 0,
            "Flags": 0,
            "Type": 1,  # a generic credential
            "TargetName": NAME,
            "Comment": None,
            "blob": SECRET.encode("utf-8"),
            "Persist": 2,  # on this computer only
            "AttributeCount": 0,
            "Attributes": None,
            "TargetAlias": None,
            "UserName": "Walka",
        }
    ]
    longest = "k" * 512
    store.write(NAME, longest)
    assert library.written[-1]["blob"] == longest.encode() and len(library.vault) == 1


def test_windows_what_is_read_is_copied_and_then_freed_by_windows_exactly_once():
    library = WindowsLibrary()
    store = WindowsKeyStore(library.calls())
    assert store.read(NAME) is None and library.freed == []  # nothing was made, nothing to free
    store.write(NAME, SECRET)
    assert store.read(NAME) == SECRET
    assert library.freed == library.handed_out and len(library.freed) == 1
    # Junk under the name is freed just the same.
    for junk in (b"\xff\xfe", b"", b"y" * 3000):
        library.vault[NAME] = junk
        assert store.read(NAME) is None
    assert library.freed == library.handed_out and len(library.freed) == 4
    # Nothing of Walka's is that long: not copied at all, but freed.
    assert library.calls().read(NAME) == (0, b"") and library.freed == library.handed_out and len(library.freed) == 5
    library.vault[NAME] = b"y" * 2560
    assert library.calls().read(NAME) == (0, b"y" * 2560) and library.freed == library.handed_out
    store.delete(NAME)
    assert library.vault == {} and store.read(NAME) is None
    store.delete(NAME)  # 1168


def test_windows_a_failure_gives_windows_own_number_and_one_without_a_number_is_still_a_failure():
    library = WindowsLibrary()
    calls = library.calls()
    store = WindowsKeyStore(calls)
    store.write(NAME, SECRET)
    library.fail_with = 1312
    assert calls.write(NAME, b"x") == 1312 and calls.read(NAME) == (1312, b"") and calls.delete(NAME) == 1312
    for call, arguments in ((store.read, (NAME,)), (store.write, (NAME, OTHER)), (store.delete, (NAME,))):
        error = raised(call, *arguments)
        assert (error.code, error.status) == ("refused", 1312) and says_nothing(error, SECRET, OTHER)
    library.fail_with = 0  # "it failed", and no number to go with it
    assert calls.write(NAME, b"x") == -1 and calls.read(NAME) == (-1, b"") and calls.delete(NAME) == -1
    assert raised(store.read, NAME).code == "failed"
    library.fail_with = None
    library.read_gives_nothing = True  # "it worked", and no credential to go with it
    assert calls.read(NAME) == (-1, b"") and library.freed == []
    assert library.vault == {NAME: SECRET.encode()}


# ---- Mac: the order of the calls, against a keychain that answers what it is told ---------


class FakeMac:
    """The keychain, as the store sees it. Every call is written down, and every item given out is counted."""

    def __init__(self) -> None:
        self.items: dict[tuple[bytes, bytes], bytes] = {}
        # Items that another build of the program made: this one may not take out what they hold.
        self.of_another_build: set[tuple[bytes, bytes]] = set()
        self.answers: dict[str, list[int | None]] = {}  # per call, used up in order; None: answer honestly
        self.log: list[str] = []
        self.given: dict[int, tuple[bytes, bytes]] = {}
        self.released: list[int | None] = []
        self._next = 7000

    def _told(self, call: str) -> int | None:
        self.log.append(call)
        waiting = self.answers.get(call)
        return waiting.pop(0) if waiting else None

    def quiet(self) -> int:
        return self._told("quiet") or 0

    def find_data(self, service: bytes, account: bytes) -> tuple[int, bytes]:
        status = self._told("find_data")
        if status is not None:
            return status, b""
        if (service, account) not in self.items:
            return -25300, b""
        if (service, account) in self.of_another_build:
            return -25308, b""  # the Mac would have to ask the person, and may not
        return 0, self.items[(service, account)]

    def find_item(self, service: bytes, account: bytes) -> tuple[int, int | None]:
        status = self._told("find_item")
        if status is not None:
            return status, None
        if (service, account) not in self.items:
            return -25300, None
        self._next += 1
        self.given[self._next] = (service, account)
        return 0, self._next

    def add(self, service: bytes, account: bytes, blob: bytes) -> int:
        status = self._told("add")
        if status is not None:
            return status
        if (service, account) in self.items:
            return -25299
        self.items[(service, account)] = blob
        return 0

    def modify(self, item: int, blob: bytes) -> int:
        status = self._told("modify")
        if status is not None:
            return status
        if self.given[item] not in self.items:
            return -25300
        self.items[self.given[item]] = blob
        return 0

    def delete(self, item: int) -> int:
        status = self._told("delete")
        if status is not None:
            return status
        self.of_another_build.discard(self.given[item])
        return 0 if self.items.pop(self.given[item], None) is not None else -25300

    def release(self, item: int | None) -> None:
        self.released.append(item)

    def in_order(self) -> bool:
        """No box by surprise: the Mac is told so before EVERY call. And every item given out went back, once."""
        asked = [call for call in self.log if call != "quiet"]
        told_first = self.log == [word for call in asked for word in ("quiet", call)]
        return told_first and sorted(self.released) == sorted(self.given)


KEY = (b"Walka account", b"https://accounts.example")


def mac_store() -> tuple[MacKeyStore, FakeMac]:
    calls = FakeMac()
    return MacKeyStore(calls), calls


def test_mac_a_first_write_adds_the_entry():
    store, calls = mac_store()
    store.write(NAME, SECRET)
    assert calls.items == {KEY: SECRET.encode()}
    assert calls.log == ["quiet", "find_item", "quiet", "add"] and calls.in_order()


def test_mac_a_later_write_changes_the_entry_in_place():
    store, calls = mac_store()
    calls.items[KEY] = SECRET.encode()
    store.write(NAME, OTHER)
    assert calls.items == {KEY: OTHER.encode()}
    # Found without what it holds, changed, given back: never deleted, so no other Walka ever finds it gone.
    # And then looked at once, to know that this build can read what it has just put there.
    assert calls.log == ["quiet", "find_item", "quiet", "modify", "quiet", "find_data"]
    assert calls.in_order() and len(calls.released) == 1


def test_mac_an_entry_that_another_walka_made_meanwhile_is_changed_instead():
    store, calls = mac_store()
    calls.items[KEY] = b"the-other-walkas"
    calls.answers["find_item"] = [-25300]  # not there yet when looked for ...
    store.write(NAME, SECRET)  # ... but there when added: -25299
    assert calls.items == {KEY: SECRET.encode()}
    assert calls.log == [
        *("quiet", "find_item", "quiet", "add"),
        *("quiet", "find_item", "quiet", "modify", "quiet", "find_data"),
    ]
    assert calls.in_order() and len(calls.released) == 1


def test_mac_an_entry_this_build_may_not_change_is_removed_and_made_anew():
    for refusal in (-25308, -25293, -25244, -25300):
        store, calls = mac_store()
        calls.items[KEY] = b"an-older-builds"
        calls.answers["modify"] = [refusal]
        store.write(NAME, SECRET)
        assert calls.items == {KEY: SECRET.encode()}
        assert calls.log == ["quiet", "find_item", "quiet", "modify", "quiet", "delete", "quiet", "add"]
        assert calls.in_order() and len(calls.released) == 1


def test_mac_an_entry_this_build_can_change_and_cannot_read_is_removed_and_made_anew():
    """After an update of Walka (or of Python) the entry of the build
    before is still there. If the Mac lets this build change it and then
    refuses every read, signing in again would never help - unless the
    write makes the entry this build's own."""
    store, calls = mac_store()
    calls.items[KEY] = b"an-older-builds"
    calls.of_another_build.add(KEY)
    assert raised(store.read, NAME).code == "refused"  # which is what tells the person to sign in again
    calls.log.clear()
    store.write(NAME, SECRET)
    assert calls.log == [
        *("quiet", "find_item", "quiet", "modify"),  # it could be changed ...
        *("quiet", "find_data"),  # ... and not read: -25308
        *("quiet", "delete", "quiet", "add"),
    ]
    assert calls.in_order() and len(calls.released) == 1
    assert calls.items == {KEY: SECRET.encode()} and store.read(NAME) == SECRET
    # From then on it is changed in place like any other.
    calls.log.clear()
    store.write(NAME, OTHER)
    assert calls.log == ["quiet", "find_item", "quiet", "modify", "quiet", "find_data"] and store.read(NAME) == OTHER

    # Each of the Mac's ways to say "you may not read this" does it; any other answer to the look leaves the entry be.
    for answer, remade in ((-25308, True), (-25293, True), (-128, True), (-25244, True), (-25300, False), (-50, False)):
        store, calls = mac_store()
        calls.items[KEY] = b"what-was-there"
        calls.answers["find_data"] = [answer]
        store.write(NAME, SECRET)
        assert ("delete" in calls.log, "add" in calls.log) == (remade, remade), answer
        assert calls.items == {KEY: SECRET.encode()} and calls.in_order()

    # It cannot be removed either: the write says so, with the number that the look gave.
    store, calls = mac_store()
    calls.items[KEY] = b"an-older-builds"
    calls.of_another_build.add(KEY)
    calls.answers["delete"] = [-25244]
    error = raised(store.write, NAME, SECRET)
    assert (error.code, error.status) == ("refused", -25308) and says_nothing(error, SECRET)
    assert calls.in_order() and len(calls.released) == 1
    # Removed, and the new one cannot be made: said as well.
    store, calls = mac_store()
    calls.items[KEY] = b"an-older-builds"
    calls.of_another_build.add(KEY)
    calls.answers["add"] = [-61]
    error = raised(store.write, NAME, SECRET)
    assert (error.code, error.status) == ("failed", -61) and calls.items == {} and calls.in_order()


def test_mac_a_write_that_cannot_be_made_says_which_number_stopped_it():
    # The entry cannot be changed and cannot be removed either: what the change answered is what is told.
    store, calls = mac_store()
    calls.items[KEY] = b"an-older-builds"
    calls.answers.update(modify=[-25308], delete=[-25244])
    error = raised(store.write, NAME, SECRET)
    assert (error.code, error.status) == ("refused", -25308) and says_nothing(error, SECRET)
    assert calls.items == {KEY: b"an-older-builds"} and calls.in_order() and len(calls.released) == 1

    # Each call in turn fails; nothing is half done without an error.
    for answers, code, status in (
        ({"find_item": [-25308]}, "refused", -25308),
        ({"find_item": [-25307]}, "unavailable", -25307),
        ({"find_item": [-50]}, "failed", -50),
        ({"find_item": [-25300], "add": [-25293]}, "refused", -25293),
        ({"find_item": [-25300], "add": [-61]}, "failed", -61),
        ({"find_item": [-25300, -25300], "add": [-25299]}, "failed", -25300),
        ({"modify": [-25308], "delete": [None], "add": [-25291]}, "unavailable", -25291),
    ):
        store, calls = mac_store()
        calls.items[KEY] = b"what-was-there"
        calls.answers.update({call: list(told) for call, told in answers.items()})
        error = raised(store.write, NAME, SECRET)
        assert (error.code, error.status) == (code, status), answers
        assert says_nothing(error, SECRET) and calls.in_order()

    # "Found", but nothing to hold on to: not a success.
    store, calls = mac_store()
    calls.answers["find_item"] = [0]
    assert raised(store.write, NAME, SECRET).code == "failed" and calls.items == {}


def test_mac_reading():
    store, calls = mac_store()
    assert store.read(NAME) is None  # -25300
    calls.items[KEY] = SECRET.encode()
    assert store.read(NAME) == SECRET
    assert calls.log == ["quiet", "find_data"] * 2 and calls.given == {}
    for junk in (b"", b"\xff\xfe", b"a password with spaces"):
        calls.items[KEY] = junk
        assert store.read(NAME) is None
    # The Mac would have had to ask the person, and it may not.
    for status in (-25308, -25293, -128):
        calls.answers["find_data"] = [status]
        error = raised(store.read, NAME)
        assert (error.code, error.status) == ("refused", status)
    for status, code in ((-25307, "unavailable"), (-25291, "unavailable"), (-50, "failed"), (-25299, "failed")):
        calls.answers["find_data"] = [status]
        error = raised(store.read, NAME)
        assert (error.code, error.status) == (code, status)
    assert calls.in_order()


def test_mac_deleting():
    store, calls = mac_store()
    store.delete(NAME)  # absent is fine
    assert calls.log == ["quiet", "find_item"]
    calls.items[KEY] = SECRET.encode()
    calls.items[(b"Walka account", b"https://other.example")] = b"another-addresses"
    store.delete(NAME)
    assert calls.items == {(b"Walka account", b"https://other.example"): b"another-addresses"}
    assert calls.log[2:] == ["quiet", "find_item", "quiet", "delete"] and calls.in_order() and len(calls.released) == 1

    # Gone between the two calls: fine too, and the item still goes back.
    store, calls = mac_store()
    calls.items[KEY] = SECRET.encode()
    calls.answers["delete"] = [-25300]
    store.delete(NAME)
    assert calls.in_order() and len(calls.released) == 1

    for answers, code, status in (
        ({"find_item": [-25308]}, "refused", -25308),
        ({"find_item": [-25291]}, "unavailable", -25291),
        ({"delete": [-25244]}, "refused", -25244),
        ({"delete": [-25308]}, "refused", -25308),
        ({"delete": [-36]}, "failed", -36),
    ):
        store, calls = mac_store()
        calls.items[KEY] = SECRET.encode()
        calls.answers.update(answers)
        error = raised(store.delete, NAME)
        assert (error.code, error.status) == (code, status) and says_nothing(error, SECRET)
        assert calls.items == {KEY: SECRET.encode()} and calls.in_order()


def test_mac_if_it_cannot_be_told_to_show_no_box_nothing_is_asked_at_all():
    for call, arguments in (("read", (NAME,)), ("write", (NAME, SECRET)), ("delete", (NAME,))):
        store, calls = mac_store()
        calls.items[KEY] = OTHER.encode()
        calls.answers["quiet"] = [-25291]
        error = raised(getattr(store, call), *arguments)
        assert (error.code, error.status) == ("unavailable", -25291)
        assert calls.log == ["quiet"] and calls.items == {KEY: OTHER.encode()}
    # In the middle of a write: the item found before still goes back.
    store, calls = mac_store()
    calls.items[KEY] = OTHER.encode()
    calls.answers["quiet"] = [0, -50]
    assert raised(store.write, NAME, SECRET).status == -50
    assert calls.log == ["quiet", "find_item", "quiet"] and sorted(calls.released) == sorted(calls.given)
    # And before the look at what was written: no look, then.
    store, calls = mac_store()
    calls.items[KEY] = OTHER.encode()
    calls.answers["quiet"] = [0, 0, -50]
    assert raised(store.write, NAME, SECRET).status == -50
    assert calls.log == ["quiet", "find_item", "quiet", "modify", "quiet"]
    assert sorted(calls.released) == sorted(calls.given) and len(calls.released) == 1


def test_mac_is_not_asked_about_what_cannot_be_stored():
    store, calls = mac_store()
    assert raised(store.write, NAME, "two words").code == "failed"
    assert raised(store.write, "bad\nname", SECRET).code == "failed"
    assert raised(store.read, "").code == "failed"
    assert raised(store.delete, "x" * 301).code == "failed"
    assert calls.log == []


# ---- Mac: the calls themselves, against libraries made of callbacks -----------------------

# An item's address, as a 64-bit Mac would give it: it must come through whole.
HANDLE = (1 << 40) + 0x1234 if ctypes.sizeof(ctypes.c_void_p) == 8 else 0x123400


class MacLibraries:
    """Security's six functions and CFRelease as ctypes callbacks with the
    Mac's own signatures, so that the code which passes the lengths,
    follows the pointers and frees what the Mac made runs on any system."""

    def __init__(self) -> None:
        self.vault: dict[tuple[bytes, bytes], bytes] = {}
        self.log: list[tuple] = []
        self.freed: list[int] = []
        self.handed_out: list[int] = []
        self.released: list[int] = []
        self.fail_with: int | None = None
        self.find_gives_nothing = False
        self._kept: list[object] = []
        self._items: dict[int, tuple[bytes, bytes]] = {}
        status, pointer, length, text = ctypes.c_int32, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_char_p

        @ctypes.CFUNCTYPE(status, ctypes.c_ubyte)
        def allowed(state):
            self.log.append(("allowed", state))
            return 0

        @ctypes.CFUNCTYPE(
            status,
            pointer,
            length,
            text,
            length,
            text,
            ctypes.POINTER(length),
            ctypes.POINTER(pointer),
            ctypes.POINTER(pointer),
        )
        def find(keychain, service_length, service, account_length, account, out_length, out_data, out_item):
            assert keychain is None and (service_length, account_length) == (len(service), len(account))
            self.log.append(("find", service, account, bool(out_length), bool(out_data), bool(out_item)))
            if self.fail_with is not None:
                return self.fail_with
            if (service, account) not in self.vault:
                return -25300
            if self.find_gives_nothing:
                return 0
            if out_data:
                blob = self.vault[(service, account)]
                buffer = ctypes.create_string_buffer(blob, max(len(blob), 1))
                self._kept.append(buffer)
                self.handed_out.append(ctypes.addressof(buffer))
                out_length[0] = len(blob)
                out_data[0] = ctypes.addressof(buffer)
            if out_item:
                handle = HANDLE + len(self._items)
                self._items[handle] = (service, account)
                out_item[0] = handle
            return 0

        @ctypes.CFUNCTYPE(status, pointer, length, text, length, text, length, text, ctypes.POINTER(pointer))
        def add(keychain, service_length, service, account_length, account, blob_length, blob, out_item):
            assert keychain is None and not out_item
            assert (service_length, account_length, blob_length) == (len(service), len(account), len(blob))
            self.log.append(("add", service, account))
            if self.fail_with is not None:
                return self.fail_with
            if (service, account) in self.vault:
                return -25299
            self.vault[(service, account)] = blob
            return 0

        @ctypes.CFUNCTYPE(status, pointer, pointer, length, text)
        def modify(item, attributes, blob_length, blob):
            assert attributes is None and blob_length == len(blob)
            self.log.append(("modify", item))
            if self.fail_with is not None:
                return self.fail_with
            self.vault[self._items[item]] = blob
            return 0

        @ctypes.CFUNCTYPE(status, pointer)
        def delete(item):
            self.log.append(("delete", item))
            if self.fail_with is not None:
                return self.fail_with
            return 0 if self.vault.pop(self._items[item], None) is not None else -25300

        @ctypes.CFUNCTYPE(status, pointer, pointer)
        def free_content(attributes, data):
            assert attributes is None
            self.freed.append(data)
            return 0

        @ctypes.CFUNCTYPE(None, pointer)
        def release(item):
            self.released.append(item)

        self.SecKeychainSetUserInteractionAllowed = allowed
        self.SecKeychainFindGenericPassword = find
        self.SecKeychainAddGenericPassword = add
        self.SecKeychainItemModifyAttributesAndData = modify
        self.SecKeychainItemDelete = delete
        self.SecKeychainItemFreeContent = free_content
        self.CFRelease = release

    def calls(self) -> keystore._MacCalls:
        return keystore._MacCalls(self, self)


def test_mac_the_whole_round_through_the_calls_themselves():
    libraries = MacLibraries()
    store = MacKeyStore(libraries.calls())
    service, account = KEY
    assert store.read(NAME) is None
    store.write(NAME, SECRET)  # not there: added
    assert libraries.vault == {KEY: SECRET.encode()}
    assert store.read(NAME) == SECRET
    store.write(NAME, "k" * 512)  # there: changed in place
    assert store.read(NAME) == "k" * 512
    store.write(NAME, OTHER)  # and a shorter one after a longer one
    assert store.read(NAME) == OTHER
    store.delete(NAME)
    assert libraries.vault == {} and store.read(NAME) is None
    store.delete(NAME)

    quiet = ("allowed", 0)  # "show no box", before every single call
    with_data = ("find", service, account, True, True, False)  # asks for what it holds, and for no item
    item_only = ("find", service, account, False, False, True)  # asks for the item, and decrypts nothing
    assert libraries.log == [
        *(quiet, with_data),
        *(quiet, item_only, quiet, ("add", service, account)),
        *(quiet, with_data),
        *(quiet, item_only, quiet, ("modify", HANDLE), quiet, with_data),  # changed, and looked at
        *(quiet, with_data),
        *(quiet, item_only, quiet, ("modify", HANDLE + 1), quiet, with_data),
        *(quiet, with_data),
        *(quiet, item_only, quiet, ("delete", HANDLE + 2)),
        *(quiet, with_data),
        *(quiet, item_only),
    ]
    # What the Mac copied out it freed, once each; every item went back, once each, whole.
    assert libraries.freed == libraries.handed_out and len(libraries.freed) == 5
    assert libraries.released == [HANDLE, HANDLE + 1, HANDLE + 2]


def test_mac_a_status_comes_back_as_the_mac_gave_it_and_nothing_is_freed_that_was_not_made():
    libraries = MacLibraries()
    calls = libraries.calls()
    libraries.vault[KEY] = SECRET.encode()
    for status in (-25308, -25293, -128, -25307, -50):
        libraries.fail_with = status
        assert calls.find_data(*KEY) == (status, b"")
        assert calls.find_item(*KEY) == (status, None)
        assert calls.add(*KEY, b"x") == status
        assert calls.modify(HANDLE, b"x") == status
        assert calls.delete(HANDLE) == status
    assert calls.quiet() == 0
    assert libraries.freed == [] and libraries.released == [] and libraries.vault == {KEY: SECRET.encode()}
    calls.release(None)  # nothing to give back
    calls.release(0)
    assert libraries.released == []
    libraries.fail_with = None
    libraries.find_gives_nothing = True  # "found", with nothing to show for it
    assert calls.find_data(*KEY) == (0, b"") and calls.find_item(*KEY) == (0, None)
    store = MacKeyStore(calls)
    assert store.read(NAME) is None
    assert raised(store.write, NAME, OTHER).code == "failed"
    assert libraries.freed == [] and libraries.released == []
    libraries.find_gives_nothing = False
    libraries.vault[KEY] = b"y" * 3000  # nothing of Walka's is that long: not copied, but freed
    assert calls.find_data(*KEY) == (0, b"") and libraries.freed == libraries.handed_out != []


# ---- the signatures told to ctypes ---------------------------------------------------------


class Declared:
    """Stands in for a system library while the loader runs: it keeps what is declared for each function."""

    def __init__(self) -> None:
        self.functions: dict[str, types.SimpleNamespace] = {}

    def __getattr__(self, name: str) -> types.SimpleNamespace:
        if name.startswith("_"):
            raise AttributeError(name)
        return self.functions.setdefault(name, types.SimpleNamespace())


def as_declared(loaded: Declared, played: object) -> None:
    """The loader declares, for each function, exactly what the callbacks
    above are made with - so the calls that are tried on every system are
    the calls the real library will get - and it leaves no function out."""
    for name, function in loaded.functions.items():
        callback = getattr(played, name)
        assert list(function.argtypes) == list(callback.argtypes), name
        assert function.restype is callback.restype, name
    assert all(len(vars(function)) == 2 for function in loaded.functions.values())  # argtypes and restype, both


def test_windows_every_function_is_declared_with_windows_own_signature(monkeypatch):
    opened: list[tuple] = []
    library = Declared()

    def win_dll(name: str, **how):
        opened.append((name, how))
        return library

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(ctypes, "WinDLL", win_dll, raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 1312, raising=False)
    calls = keystore._load_windows()
    monkeypatch.undo()
    # Loaded apart from anybody else's copy, with Windows' error number kept for this thread.
    assert opened == [("advapi32", {"use_last_error": True})]
    assert sorted(library.functions) == ["CredDeleteW", "CredFree", "CredReadW", "CredWriteW"]
    as_declared(library, WindowsLibrary())
    assert calls.library is library and calls._error() == 1312


def test_mac_every_function_is_declared_with_the_macs_own_signature(monkeypatch):
    opened: list[str] = []
    security, core_foundation = Declared(), Declared()

    def cdll(path: str):
        opened.append(path)
        return security if path.endswith("/Security") else core_foundation

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(ctypes, "CDLL", cdll)
    calls = keystore._load_mac()
    monkeypatch.undo()
    assert opened == [
        "/System/Library/Frameworks/Security.framework/Security",
        "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation",
    ]
    assert sorted(security.functions) == [
        "SecKeychainAddGenericPassword",
        "SecKeychainFindGenericPassword",
        "SecKeychainItemDelete",
        "SecKeychainItemFreeContent",
        "SecKeychainItemModifyAttributesAndData",
        "SecKeychainSetUserInteractionAllowed",
    ]
    assert sorted(core_foundation.functions) == ["CFRelease"]
    played = MacLibraries()
    as_declared(security, played)
    as_declared(core_foundation, played)
    assert calls.security is security and calls.core_foundation is core_foundation
    # Every status is a signed 32-bit number (-25300 must not arrive as 4294941996).
    assert all(
        function.restype is ctypes.c_int32 for name, function in security.functions.items() if name.startswith("Sec")
    )


def test_a_library_that_is_not_there_or_has_dropped_a_call_means_no_store(monkeypatch):
    def gone(*_arguments, **_how):
        raise OSError("no such library")

    class Bare:
        def __getattr__(self, name: str):
            raise AttributeError(f"function '{name}' not found")  # what ctypes says of a missing symbol

    for platform, loader, make in (("darwin", "CDLL", MacKeyStore), ("win32", "WinDLL", WindowsKeyStore)):
        for library in (gone, lambda *_arguments, **_how: Bare()):
            monkeypatch.setattr(sys, "platform", platform)
            monkeypatch.setattr(ctypes, loader, library, raising=False)
            store = make()
            errors = [raised(store.read, NAME), raised(store.write, NAME, SECRET), raised(store.delete, NAME)]
            monkeypatch.undo()
            assert [(error.code, error.status) for error in errors] == [("unavailable", None)] * 3
            assert all(says_nothing(error, SECRET) for error in errors)


# ---- the face the rest of Walka sees -----------------------------------------------------


class Watched:
    """A store in memory that writes down who called it, and can be told to sit on a call."""

    kind = "memory"
    lasting = False

    def __init__(self) -> None:
        self.inner = MemoryKeyStore()
        self.gate = threading.Event()
        self.gate.set()
        self.arrived = threading.Event()
        self.log: list[str] = []
        self.threads: list[threading.Thread] = []
        self.inside = 0
        self.most_inside = 0
        self.linger = 0.0

    def _through(self, what: str) -> None:
        self.inside += 1
        self.most_inside = max(self.most_inside, self.inside)
        self.threads.append(threading.current_thread())
        self.arrived.set()
        try:
            assert self.gate.wait(20)
            if self.linger:
                threading.Event().wait(self.linger)  # long enough for another call to overlap, if it could
            self.log.append(what)
        finally:
            self.inside -= 1

    def read(self, name: str) -> str | None:
        self._through("read")
        return self.inner.read(name)

    def write(self, name: str, secret: str) -> None:
        self._through("write")
        self.inner.write(name, secret)

    def delete(self, name: str) -> None:
        self._through("delete")
        self.inner.delete(name)


class Broken:
    """A store whose every call fails the way it is told to."""

    kind = "mac"
    lasting = True

    def __init__(self, error: Exception) -> None:
        self.error = error

    def read(self, name: str) -> str | None:
        raise self.error

    def write(self, name: str, secret: str) -> None:
        raise self.error

    def delete(self, name: str) -> None:
        raise self.error


async def until(condition, limit: float = 10.0) -> None:
    loop = asyncio.get_running_loop()
    give_up = loop.time() + limit
    while not condition():
        assert loop.time() < give_up
        await asyncio.sleep(0.005)


def test_keys_say_what_kind_of_store_is_behind_them():
    for store in (MemoryKeyStore(), WindowsKeyStore(FakeWindows()), MacKeyStore(FakeMac())):
        keys = Keys(store)
        assert (keys.kind, keys.lasting) == (store.kind, store.lasting)
    assert Keys(MemoryKeyStore()).lasting is False


def test_keys_read_write_and_delete_on_a_daemon_thread_of_their_own():
    store = Watched()
    keys = Keys(store)

    async def scenario():
        loop_thread = threading.current_thread()
        assert await keys.read(NAME) is None
        assert await keys.write(NAME, SECRET) is True
        assert await keys.read(NAME) == SECRET
        assert await keys.write(NAME, OTHER) is True
        assert await keys.read(NAME) == OTHER
        assert await keys.delete(NAME) is True
        assert await keys.read(NAME) is None
        assert await keys.delete(NAME) is True  # it is gone now: that is what was asked
        return loop_thread

    loop_thread = run(scenario)
    assert store.log == ["read", "write", "read", "write", "read", "delete", "read", "delete"]
    assert all(thread.daemon and thread.name == THREAD_NAME and thread is not loop_thread for thread in store.threads)
    wait_for_the_workers()


def test_keys_ask_the_store_one_call_at_a_time():
    store = Watched()
    store.linger = 0.01
    keys = Keys(store)

    async def scenario():
        # (The last write goes to another name: which of these six gets the lock first is not
        # said, and a delete stands down when a LATER write under its own name was made before it.)
        await asyncio.gather(
            keys.write(NAME, SECRET),
            keys.read(NAME),
            keys.delete(NAME),
            keys.write(entry_name("https://other.example"), OTHER),
            keys.read(NAME),
            keys.read(entry_name("https://other.example")),
        )

    run(scenario)
    assert len(store.log) == 6 and store.most_inside == 1
    assert len(set(store.threads)) == 6  # a thread for each call: none of them the loop's own


def test_keys_give_up_on_a_store_that_does_not_answer(caplog):
    store = Watched()
    store.inner.write(NAME, SECRET)
    keys = Keys(store, timeout_s=0.25)

    async def scenario():
        store.gate.clear()
        with pytest.raises(KeyStoreError) as caught:
            await keys.read(NAME)  # the store sits on this one ...
        assert caught.value.code == "timeout" and says_nothing(caught.value, SECRET)
        # ... and whatever comes behind it gives up too, without ever reaching the store.
        with pytest.raises(KeyStoreError) as caught:
            await keys.write(NAME, OTHER)
        assert caught.value.code == "timeout" and says_nothing(caught.value, OTHER)
        with pytest.raises(KeyStoreError) as caught:
            await keys.read(NAME)
        assert caught.value.code == "timeout"
        assert await keys.delete(NAME) is False  # "could not", said without an error
        # The write and the second read have stopped waiting for their turn by now, or do so in
        # a moment. The delete has not: whoever asked was told "could not", but its thread stays
        # in the line behind the call the store sits on.
        await until(lambda: len(worker_threads()) == 2)
        await asyncio.sleep(0.3)  # (longer than the time limit: it does not give up)
        assert len(worker_threads()) == 2 and store.log == []
        store.gate.set()  # the first call comes back at last, with the secret, to nobody
        await until(lambda: not worker_threads())
        await asyncio.sleep(0.05)

    with caplog.at_level(logging.DEBUG):
        run(scenario)
    # Nothing was written late - and the delete was made when the store came back: signing out takes the key away.
    assert store.log == ["read", "delete"] and store.inner.read(NAME) is None
    assert SECRET not in caplog.text and OTHER not in caplog.text
    assert "did not answer a read in time" in caplog.text


def test_keys_a_delete_behind_a_write_the_store_sits_on_takes_away_what_that_write_put_there():
    store = Watched()
    store.inner.write(NAME, SECRET)
    keys = Keys(store, timeout_s=0.2)

    async def scenario():
        store.gate.clear()
        # A refresh's write is inside the system's call, past every look at whether it is still wanted ...
        with pytest.raises(KeyStoreError) as caught:
            await keys.write(NAME, OTHER)
        assert caught.value.code == "timeout"
        # ... the person signs out, and the store goes on sitting for longer than the delete's caller waits.
        assert await keys.delete(NAME) is False
        await asyncio.sleep(0.3)
        assert store.log == [] and len(worker_threads()) == 2
        store.gate.set()  # the store comes back: the write lands, and the delete after it
        await until(lambda: not worker_threads())

    run(scenario)
    assert store.log == ["write", "delete"] and store.inner.read(NAME) is None  # no key outlives the sign-out


def test_keys_a_late_delete_never_takes_out_the_key_of_a_later_sign_in():
    store = Watched()
    store.inner.write(NAME, SECRET)
    keys = Keys(store)
    keys._lock = lock = LatestFirst()

    async def scenario():
        store.gate.clear()
        read = asyncio.ensure_future(keys.read(NAME))  # the store sits on this one
        await until(store.arrived.is_set)
        delete = asyncio.ensure_future(keys.delete(NAME))  # a sign-out: it waits for its turn
        await until(lambda: len(lock.waiting) == 1)
        write = asyncio.ensure_future(keys.write(NAME, OTHER))  # the person signs in again: asked AFTER the delete
        await until(lambda: len(lock.waiting) == 2)
        store.gate.set()  # the store comes back, and the write gets its turn before the delete
        assert await read == SECRET
        assert await write is True
        # What the delete was asked to take away is gone - written over. The new key is not its to remove.
        assert await delete is True
        await until(lambda: not worker_threads())

    run(scenario)
    assert store.log == ["read", "write"] and store.inner.read(NAME) == OTHER


def test_keys_write_only_if_it_still_holds_and_ask_that_as_the_last_thing_under_the_lock():
    store = Watched()
    keys = Keys(store)
    seen: list[tuple] = []

    def still() -> bool:
        seen.append((threading.current_thread().name, keys._lock.locked(), list(store.log)))
        return True

    async def scenario():
        assert await keys.write(NAME, SECRET, if_still=still) is True
        assert await keys.write(NAME, OTHER, if_still=lambda: False) is False
        assert await keys.read(NAME) == SECRET

    run(scenario)
    # Asked on the worker's thread, with the lock held, before the store was called at all.
    assert seen == [(THREAD_NAME, True, [])]
    assert store.log == ["write", "read"]  # the refused write never reached the store


def test_keys_a_write_that_waited_behind_a_sign_out_does_not_bring_the_sign_in_back():
    store = Watched()
    store.inner.write(NAME, SECRET)
    keys = Keys(store)
    signed_in = [True]

    async def scenario():
        store.gate.clear()
        # The sign-out's delete is at the store, and a refresh's write waits behind it.
        delete = asyncio.ensure_future(keys.delete(NAME))
        await until(store.arrived.is_set)
        write = asyncio.ensure_future(keys.write(NAME, OTHER, if_still=lambda: signed_in[0]))
        await asyncio.sleep(0.05)
        signed_in[0] = False
        store.gate.set()
        assert await delete is True
        assert await write is False

    run(scenario)
    assert store.log == ["delete"] and store.inner.read(NAME) is None


def test_keys_a_write_nobody_waits_for_any_more_is_not_made():
    store = Watched()
    store.inner.write(NAME, SECRET)
    keys = Keys(store)
    asked: list[int] = []

    def still() -> bool:
        asked.append(1)
        return True

    async def scenario():
        store.gate.clear()
        read = asyncio.ensure_future(keys.read(NAME))  # the store sits on this one
        await until(store.arrived.is_set)
        write = asyncio.ensure_future(keys.write(NAME, OTHER, if_still=still))
        await asyncio.sleep(0.05)
        write.cancel()  # Walka is closing, or the person signed out
        with pytest.raises(asyncio.CancelledError):
            await write
        store.gate.set()
        assert await read == SECRET
        await until(lambda: not worker_threads())

    run(scenario)
    assert store.log == ["read"] and asked == [] and store.inner.read(NAME) == SECRET


def test_keys_a_delete_nobody_waits_for_any_more_is_still_made():
    store = Watched()
    store.inner.write(NAME, SECRET)
    keys = Keys(store)

    async def scenario():
        store.gate.clear()
        read = asyncio.ensure_future(keys.read(NAME))
        await until(store.arrived.is_set)
        delete = asyncio.ensure_future(keys.delete(NAME))
        await asyncio.sleep(0.05)
        delete.cancel()
        with pytest.raises(asyncio.CancelledError):
            await delete  # being cancelled is not swallowed by "never an error"
        store.gate.set()
        await read
        await until(lambda: not worker_threads())

    run(scenario)
    assert store.log == ["read", "delete"] and store.inner.read(NAME) is None  # signing out takes the key away


def test_keys_whoever_waits_can_be_cancelled_at_once():
    store = Watched()
    keys = Keys(store)

    async def scenario():
        loop = asyncio.get_running_loop()
        store.gate.clear()
        read = asyncio.ensure_future(keys.read(NAME))
        await until(store.arrived.is_set)
        started = loop.time()
        read.cancel()
        with pytest.raises(asyncio.CancelledError):
            await read
        waited = loop.time() - started
        store.gate.set()
        await until(lambda: not worker_threads())
        return waited

    assert run(scenario) < 3.0  # "at once", on a busy machine too: not the store's fifteen seconds


def test_keys_deleting_never_raises(caplog):
    async def scenario():
        for error in (
            KeyStoreError("refused", -25308),
            KeyStoreError("unavailable"),
            RuntimeError(SECRET),
            OSError(5, SECRET),
        ):
            assert await Keys(Broken(error)).delete(NAME) is False
        assert await Keys(MemoryKeyStore()).delete("bad\nname") is False

    with caplog.at_level(logging.DEBUG):
        run(scenario)
    assert SECRET not in caplog.text
    assert "did not delete: refused, -25308" in caplog.text


class WillNotDelete(Watched):
    """A store that says no to the first ``refusals`` deletes - and, if told so, to every write."""

    def __init__(self, refusals: int = 1, *, writes_too: bool = False) -> None:
        super().__init__()
        self.refusals = refusals
        self.writes_too = writes_too

    def write(self, name: str, secret: str) -> None:
        if self.writes_too:
            self.log.append("write refused")
            raise KeyStoreError("refused", -25308)
        super().write(name, secret)

    def delete(self, name: str) -> None:
        if self.refusals > 0:
            self.refusals -= 1
            self.log.append("delete refused")
            raise KeyStoreError("refused", -25308)
        super().delete(name)


def test_keys_a_delete_that_may_write_instead_writes_only_when_the_store_will_not_delete(caplog):
    async def scenario():
        # The store lets go: the entry is deleted, and nothing is written.
        store = Watched()
        store.inner.write(NAME, SECRET)
        assert await Keys(store).delete(NAME, or_write=OTHER) is True
        assert store.log == ["delete"] and store.inner.read(NAME) is None
        # Nothing was there: the same.
        assert await Keys(store).delete(NAME, or_write=OTHER) is True
        assert store.log == ["delete", "delete"] and store.inner.read(NAME) is None

        # The store will not let go: the other text is written over the entry - and that counts as gone.
        store = WillNotDelete()
        store.inner.write(NAME, SECRET)
        assert await Keys(store).delete(NAME, or_write=OTHER) is True
        assert store.log == ["delete refused", "write"] and store.inner.read(NAME) == OTHER

        # It will neither delete nor write: "could not" - and never an error.
        store = WillNotDelete(writes_too=True)
        store.inner.write(NAME, SECRET)
        assert await Keys(store).delete(NAME, or_write=OTHER) is False
        assert store.log == ["delete refused", "write refused"] and store.inner.read(NAME) == SECRET

        # Without the word nothing is ever written by a delete: the key of a sign-in is never written over.
        store = WillNotDelete()
        store.inner.write(NAME, SECRET)
        assert await Keys(store).delete(NAME) is False
        assert store.log == ["delete refused"] and store.inner.read(NAME) == SECRET

        # What cannot be stored is not written over anything either.
        store = WillNotDelete()
        store.inner.write(NAME, SECRET)
        assert await Keys(store).delete(NAME, or_write="two words") is False
        assert store.inner.read(NAME) == SECRET

    with caplog.at_level(logging.DEBUG):
        run(scenario)
    assert SECRET not in caplog.text and OTHER not in caplog.text


def test_keys_a_late_delete_that_may_write_instead_stands_down_behind_a_later_write():
    store = WillNotDelete()
    store.inner.write(NAME, SECRET)
    keys = Keys(store)
    keys._lock = lock = LatestFirst()

    async def scenario():
        store.gate.clear()
        read = asyncio.ensure_future(keys.read(NAME))  # the store sits on this one
        await until(store.arrived.is_set)
        # "Stop sending": the mark is to go - and, if the store will not delete, to be written over.
        delete = asyncio.ensure_future(keys.delete(NAME, or_write="written-over"))
        await until(lambda: len(lock.waiting) == 1)
        write = asyncio.ensure_future(keys.write(NAME, OTHER))  # the question is answered anew: asked AFTER
        await until(lambda: len(lock.waiting) == 2)
        store.gate.set()  # the store comes back, and the write gets its turn before the delete
        assert await read == SECRET
        assert await write is True and await delete is True
        await until(lambda: not worker_threads())

    run(scenario)
    # The new mark is neither deleted nor written over: the delete stood down before it asked the store.
    assert store.log == ["read", "write"] and store.inner.read(NAME) == OTHER


def test_keys_what_was_written_instead_of_a_delete_does_not_stop_a_delete_that_still_waits():
    store = WillNotDelete()
    store.inner.write(NAME, SECRET)
    keys = Keys(store)
    keys._lock = lock = LatestFirst()

    async def scenario():
        store.gate.clear()
        read = asyncio.ensure_future(keys.read(NAME))  # the store sits on this one
        await until(store.arrived.is_set)
        first = asyncio.ensure_future(keys.delete(NAME, or_write="written-over-1"))
        await until(lambda: len(lock.waiting) == 1)
        second = asyncio.ensure_future(keys.delete(NAME, or_write="written-over-2"))
        await until(lambda: len(lock.waiting) == 2)
        store.gate.set()  # the second goes first: the store says no once, and it writes over the entry
        assert await read == SECRET
        assert await second is True and await first is True
        await until(lambda: not worker_threads())

    run(scenario)
    # ... and the first, which was asked before that, still takes the entry out: nothing is left behind.
    assert store.log == ["read", "delete refused", "write", "delete"] and store.inner.read(NAME) is None


def test_keys_pass_on_the_stores_word_and_number_and_nothing_else(caplog):
    async def scenario():
        errors = []
        for error in (KeyStoreError("refused", -25308), KeyStoreError("failed", 1312), KeyStoreError("unavailable")):
            keys = Keys(Broken(error))
            for call in (keys.read(NAME), keys.write(NAME, SECRET)):
                with pytest.raises(KeyStoreError) as caught:
                    await call
                assert (caught.value.code, caught.value.status) == (error.code, error.status)
                assert caught.value is not error
                errors.append(caught.value)
        return errors

    with caplog.at_level(logging.DEBUG):
        errors = run(scenario)
    assert all(says_nothing(error, SECRET) for error in errors) and SECRET not in caplog.text


def test_keys_let_no_word_or_number_through_that_is_not_one_of_the_stores_own(caplog):
    """A store that hangs something else on its error - here the secret
    itself - gets it no further than ``Keys``."""

    class Loud(KeyStoreError):
        def __init__(self, code: object, status: object = None) -> None:
            Exception.__init__(self, SECRET)
            self.code, self.status, self.extra = code, status, SECRET

    async def scenario():
        got = []
        for code, status in ((SECRET, 5), ("refused", SECRET), ("failed", True), (None, 2.5), (["refused"], -25308)):
            keys = Keys(Broken(Loud(code, status)))
            for call in (keys.read(NAME), keys.write(NAME, SECRET)):
                with pytest.raises(KeyStoreError) as caught:
                    await call
                assert type(caught.value) is KeyStoreError
                got.append(caught.value)
            assert await keys.delete(NAME) is False
        return got

    with caplog.at_level(logging.DEBUG):
        got = run(scenario)
    assert [(error.code, error.status) for error in got[::2]] == [
        ("failed", 5),
        ("refused", None),
        ("failed", None),
        ("failed", None),
        ("failed", -25308),
    ]
    assert all(says_nothing(error, SECRET) for error in got)
    for text in (caplog.text, " ".join(str(record.args) for record in caplog.records)):
        assert SECRET not in text


def test_keys_a_mistake_inside_a_store_comes_out_as_one_word_without_its_own(caplog):
    """Whatever goes wrong in there may have the secret in its words (ctypes
    names the argument it objects to): none of it gets out."""

    async def scenario():
        errors = []
        for error in (RuntimeError(f"cannot store {SECRET}"), ValueError(SECRET), ctypes.ArgumentError(SECRET)):
            keys = Keys(Broken(error))
            for call in (keys.read(NAME), keys.write(NAME, SECRET)):
                with pytest.raises(KeyStoreError) as caught:
                    await call
                errors.append(caught.value)

        def still() -> bool:
            raise RuntimeError(SECRET)

        store = MemoryKeyStore()
        with pytest.raises(KeyStoreError) as caught:
            await Keys(store).write(NAME, SECRET, if_still=still)
        errors.append(caught.value)
        assert store.read(NAME) is None
        return errors

    with caplog.at_level(logging.DEBUG):
        errors = run(scenario)
    for error in errors:
        assert (error.code, error.status) == ("failed", None)
        assert error.__cause__ is None and error.__context__ is None and says_nothing(error, SECRET)
    assert SECRET not in caplog.text
    # What kind of error it was is all that is said of it: on somebody's computer it is all there is to go by.
    for kind, call in (("RuntimeError", "read"), ("ValueError", "write"), ("ArgumentError", "read")):
        assert f"the store for secrets (mac) broke off a {call} with an error of the kind {kind}" in caplog.text
    assert "the store for secrets (memory) broke off a write with an error of the kind RuntimeError" in caplog.text
    own = [record for record in caplog.records if record.name == keystore.log.name]
    assert len(own) == 14 and all(record.exc_info is None and record.levelno == logging.WARNING for record in own)


def test_no_error_of_any_store_and_no_line_of_the_log_holds_what_was_stored(caplog, capfd):
    """Every way to fail, with a secret in play each time."""
    errors: list[BaseException] = []

    def note(call, *arguments) -> None:
        try:
            call(*arguments)
        except KeyStoreError as error:
            errors.append(error)

    async def scenario():
        for status in (1312, 5, 1168, -1):
            calls = FakeWindows()
            calls.entries[NAME] = SECRET.encode()
            for call, arguments in (("read", (NAME,)), ("write", (NAME, OTHER)), ("delete", (NAME,))):
                calls.answers = [status]
                note(getattr(WindowsKeyStore(calls), call), *arguments)
                calls.answers = [status]
                keys = Keys(WindowsKeyStore(calls))
                try:
                    await getattr(keys, call)(*arguments)
                except KeyStoreError as error:
                    errors.append(error)
        for status in (-25308, -25293, -128, -25244, -25307, -25291, -25299, -50):
            for failing in ("quiet", "find_data", "find_item", "add", "modify", "delete"):
                for call, arguments in (("read", (NAME,)), ("write", (NAME, OTHER)), ("delete", (NAME,))):
                    for there in (True, False):
                        calls = FakeMac()
                        if there:
                            calls.items[KEY] = SECRET.encode()
                        calls.answers[failing] = [status]
                        note(getattr(MacKeyStore(calls), call), *arguments)
                        calls.answers[failing] = [status]
                        keys = Keys(MacKeyStore(calls))
                        try:
                            await getattr(keys, call)(*arguments)
                        except KeyStoreError as error:
                            errors.append(error)
        for store in (WindowsKeyStore(), MacKeyStore(), MemoryKeyStore()):
            note(store.write, NAME, f"{SECRET} {OTHER}")  # not a secret of Walka's: refused, and not repeated
            note(store.write, f"{SECRET}\n", OTHER)
        slow = Watched()
        slow.inner.write(NAME, SECRET)
        slow.gate.clear()
        keys = Keys(slow, timeout_s=0.05)
        for call, arguments in (("read", (NAME,)), ("write", (NAME, OTHER))):
            try:
                await getattr(keys, call)(*arguments)
            except KeyStoreError as error:
                errors.append(error)
        slow.gate.set()
        await until(lambda: not worker_threads())
        await asyncio.sleep(0.05)  # the late answer - the secret itself - arrives, and nobody takes it

    with caplog.at_level(logging.DEBUG):
        run(scenario)
    assert len(errors) > 200
    assert {error.code for error in errors} == {"unavailable", "refused", "failed", "timeout"}
    for error in errors:
        assert says_nothing(error, SECRET, OTHER)
    out, err = capfd.readouterr()
    for text in (caplog.text, out, err, " ".join(str(record.args) for record in caplog.records)):
        assert SECRET not in text and OTHER not in text
    assert "Traceback" not in err


# ---- the real stores: one round trip each, under a name of its own ------------------------


def made_up(length: int = 12) -> str:
    """A secret that is nobody's, new for each run."""
    return "".join(uuid.uuid4().hex for _ in range(length // 32 + 1))[:length]


def must_be(got: str | None, wanted: str | None, what: str) -> None:
    """Compared without pytest's own report, which would print both: a failure says what happened, not what was stored."""
    if got != wanted:
        seen = "nothing" if got is None else f"{len(got)} characters"
        hoped = "nothing" if wanted is None else f"{len(wanted)} characters"
        pytest.fail(f"{what}: the store gave {seen} where {hoped} were expected (and not the same ones)", pytrace=False)


@pytest.mark.skipif(sys.platform != "win32", reason="asks the real Credential Manager")
def test_a_real_credential_manager_keeps_reads_and_forgets_a_made_up_secret():
    name = f"Walka test {uuid.uuid4()}"
    beside = f"{name} - sending"  # as the mark's entry is named beside the sign-in's
    first, longest, last = made_up(12), made_up(512), made_up(20)
    store = WindowsKeyStore()

    def step(what: str, call, *arguments):
        try:
            return call(*arguments)
        except KeyStoreError as error:
            pytest.fail(
                f"{what}: Credential Manager answered '{error.code}', Windows' error {error.status}", pytrace=False
            )

    def as_windows_keeps_it() -> dict | None:
        """The entry as Windows itself has it, asked without the store in between."""
        calls = keystore._load_windows()
        found = ctypes.POINTER(keystore._CREDENTIALW)()
        if not calls.library.CredReadW(name, 1, 0, ctypes.byref(found)):
            error = ctypes.get_last_error()
            assert error == 1168, f"looking the entry up failed with Windows' error {error}"
            return None
        try:
            entry = found.contents
            return {
                "Type": entry.Type,
                "Persist": entry.Persist,
                "TargetName": entry.TargetName,
                "UserName": entry.UserName,
                "size": entry.CredentialBlobSize,
                "AttributeCount": entry.AttributeCount,
            }
        finally:
            calls.library.CredFree(ctypes.cast(found, ctypes.c_void_p))

    def kept_as(size: int) -> dict:
        return {"Type": 1, "Persist": 2, "TargetName": name, "UserName": "Walka", "size": size, "AttributeCount": 0}

    if ctypes.sizeof(ctypes.c_void_p) == 8:
        assert ctypes.sizeof(keystore._CREDENTIALW) == 80
    from ctypes import wintypes

    assert ctypes.sizeof(wintypes.DWORD) == ctypes.sizeof(keystore._DWORD) == 4
    assert ctypes.sizeof(wintypes.BOOL) == ctypes.sizeof(keystore._BOOL)
    assert ctypes.sizeof(wintypes.FILETIME) == ctypes.sizeof(keystore._FILETIME) == 8

    try:
        must_be(step("reading before anything was written", store.read, name), None, "before anything was written")
        step("deleting what is not there", store.delete, name)
        assert as_windows_keeps_it() is None

        step("the first write", store.write, name, first)
        must_be(step("reading the first", store.read, name), first, "after the first write")
        # A generic credential that stays on this computer, under the fixed user name, the secret as plain bytes.
        assert as_windows_keeps_it() == kept_as(12)

        step("writing over it, the longest secret there is", store.write, name, longest)
        must_be(step("reading the longest", store.read, name), longest, "after writing over it")
        assert as_windows_keeps_it() == kept_as(512)

        step("writing a shorter one over the longest", store.write, name, last)
        must_be(step("reading the shorter one", store.read, name), last, "after the shorter one")
        assert as_windows_keeps_it() == kept_as(20)

        # The same entry through the face the rest of Walka uses, off the loop.
        async def through_keys():
            keys = Keys(WindowsKeyStore(), timeout_s=20.0)
            assert (keys.kind, keys.lasting) == ("windows", True)

            async def ask(what: str, call):
                try:
                    return await call
                except KeyStoreError as error:
                    pytest.fail(f"{what}: answered '{error.code}', Windows' error {error.status}", pytrace=False)

            must_be(await ask("reading through Keys", keys.read(name)), last, "through Keys")
            if await ask("writing through Keys", keys.write(name, first)) is not True:
                pytest.fail("writing through Keys: not written", pytrace=False)
            if (
                await ask("a write that no longer holds", keys.write(name, longest, if_still=lambda: False))
                is not False
            ):
                pytest.fail("a write that no longer holds was made all the same", pytrace=False)
            must_be(await ask("reading through Keys again", keys.read(name)), first, "through Keys, after its write")
            # A mark beside it, under a name that begins the same: an entry of its own.
            if await ask("writing the mark beside it", keys.write(beside, last)) is not True:
                pytest.fail("writing the mark beside it: not written", pytrace=False)
            must_be(await ask("reading the mark", keys.read(beside)), last, "the mark beside it")
            must_be(await ask("reading the entry beside the mark", keys.read(name)), first, "beside the mark")
            if await keys.delete(beside, or_write=longest) is not True:
                pytest.fail("deleting the mark: answered 'could not'", pytrace=False)
            must_be(await ask("reading the mark after its delete", keys.read(beside)), None, "the mark, deleted")
            must_be(await ask("reading the entry once the mark went", keys.read(name)), first, "after the mark went")
            await ask("deleting through Keys", keys._delete(name))
            must_be(await ask("reading after the delete", keys.read(name)), None, "after the delete")
            if await keys.delete(name) is not True:
                pytest.fail("deleting what is gone already: answered 'could not'", pytrace=False)

        run(through_keys, 120)
        assert as_windows_keeps_it() is None
        must_be(step("reading after the delete", store.read, name), None, "after the delete, asked directly")
        step("deleting again", store.delete, name)
    finally:
        for left in (name, beside):
            try:
                store.delete(left)
            except KeyStoreError:
                pass


@pytest.mark.skipif(sys.platform != "darwin", reason="asks the real keychain of a Mac")
def test_a_real_keychain_keeps_reads_and_forgets_a_made_up_secret():
    """Through ``Keys``, with 20 seconds for each call: a password box that
    nobody sees (it must never come up) fails here with a clear word
    instead of holding the machine for hours."""
    one = uuid.uuid4()
    name = f"Walka test {one}"
    split = f"Walka test {one} - https://accounts.example"  # service and account apart, as the real entry is
    beside = f"{split} - sending"  # the same service and another account, as the mark's entry is
    first, longest, last = made_up(12), made_up(512), made_up(20)
    store = MacKeyStore()
    keys = Keys(store, timeout_s=20.0)
    assert (keys.kind, keys.lasting) == ("mac", True)

    async def ask(what: str, call):
        try:
            return await call
        except KeyStoreError as error:
            if error.code == "timeout":
                pytest.fail(f"{what}: no answer from the keychain in 20 seconds (a box on the screen?)", pytrace=False)
            pytest.fail(f"{what}: the keychain answered '{error.code}', status {error.status}", pytrace=False)

    async def scenario():
        try:
            must_be(await ask("reading before anything was written", keys.read(name)), None, "before anything")
            await ask("deleting what is not there", keys._delete(name))

            if await ask("the first write (adds the item)", keys.write(name, first)) is not True:
                pytest.fail("the first write: not written", pytrace=False)
            must_be(await ask("reading the first", keys.read(name)), first, "after the first write")

            await ask("writing over it (changes the item in place), the longest", keys.write(name, longest))
            must_be(await ask("reading the longest", keys.read(name)), longest, "after writing over it")
            await ask("writing a shorter one over the longest", keys.write(name, last))
            must_be(await ask("reading the shorter one", keys.read(name)), last, "after the shorter one")
            if await ask("a write that no longer holds", keys.write(name, first, if_still=lambda: False)) is not False:
                pytest.fail("a write that no longer holds was made all the same", pytrace=False)
            must_be(await ask("reading after the write that was not made", keys.read(name)), last, "after no write")

            # The calls themselves, one by one: the item is found without what it holds, changed in place, read by
            # the build that changed it (so a write never has to remove an item of its own), and not added twice.
            def by_hand() -> list[tuple[str, int]]:
                calls = store._layer()
                service, account = mac_names(name)
                answers = [("quiet", calls.quiet())]
                status, item = calls.find_item(service, account)
                answers.append(("find_item", status))
                try:
                    answers.append(("quiet", calls.quiet()))
                    answers.append(("modify", calls.modify(item, last.encode()) if item else -1))
                finally:
                    calls.release(item)
                answers.append(("quiet", calls.quiet()))
                status, held = calls.find_data(service, account)
                answers.append(("find_data", status))
                answers.append(("it holds what was put there", int(held == last.encode())))
                answers.append(("quiet", calls.quiet()))
                answers.append(("add", calls.add(service, account, b"never-kept")))
                answers.append(("quiet", calls.quiet()))
                answers.append(("find_data of another name", calls.find_data(service + b" no", account + b" no")[0]))
                return answers

            try:
                answers = await off_loop(by_hand, timeout=20.0, name=THREAD_NAME)
            except OffLoopTimeout:
                pytest.fail("the calls by hand: no answer from the keychain in 20 seconds", pytrace=False)
            assert answers == [
                ("quiet", 0),
                ("find_item", 0),
                ("quiet", 0),
                ("modify", 0),
                ("quiet", 0),
                ("find_data", 0),
                ("it holds what was put there", 1),
                ("quiet", 0),
                ("add", -25299),
                ("quiet", 0),
                ("find_data of another name", -25300),
            ]
            must_be(await ask("reading after the refused add", keys.read(name)), last, "after the refused add")

            # A second entry under the same day's name with an address: its own item, and it does not touch the first.
            await ask("writing the entry with an address", keys.write(split, first))
            must_be(await ask("reading the entry with an address", keys.read(split)), first, "with an address")
            must_be(await ask("reading the first entry again", keys.read(name)), last, "beside the other")
            # And a mark beside that one, as beside the real sign-in: an item of its own in the same service.
            assert mac_names(beside)[0] == mac_names(split)[0] and mac_names(beside)[1] != mac_names(split)[1]
            await ask("writing the mark beside it", keys.write(beside, last))
            must_be(await ask("reading the mark", keys.read(beside)), last, "the mark beside it")
            must_be(await ask("reading the entry beside the mark", keys.read(split)), first, "beside the mark")
            if await keys.delete(beside, or_write=longest) is not True:
                pytest.fail("deleting the mark: answered 'could not'", pytrace=False)
            must_be(await ask("reading the mark after its delete", keys.read(beside)), None, "the mark, deleted")
            must_be(await ask("reading the entry once the mark went", keys.read(split)), first, "after the mark went")
            await ask("deleting the entry with an address", keys._delete(split))
            must_be(await ask("reading the deleted entry", keys.read(split)), None, "the entry with an address")
            must_be(await ask("reading the first entry once more", keys.read(name)), last, "after the other went")

            await ask("deleting", keys._delete(name))
            must_be(await ask("reading after the delete", keys.read(name)), None, "after the delete")
            await ask("deleting again", keys._delete(name))
            if await keys.delete(name) is not True:
                pytest.fail("deleting what is gone already: answered 'could not'", pytrace=False)
        finally:
            await keys.delete(name)
            await keys.delete(split)
            await keys.delete(beside)

    try:
        run(scenario, 300)
    except asyncio.TimeoutError:
        pytest.fail("the keychain held the test for five minutes", pytrace=False)
