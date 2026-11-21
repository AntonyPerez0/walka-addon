"""The Walka account's part as it is handed to the App (walka_account.service.WalkaAccount).

Signing in through the real listener and the stand-in's "browser",
signing out, the question that stands before sending, what the card is
told when a sign-in ends, and closing in the middle of things.

After EVERY test the rig's fixture looks again: nothing outside the list
of what Walka may ask, no key of a sign-in in any file, in the snapshot
or in the log, no task left with an error nobody read.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import threading
from urllib.parse import parse_qsl, urlsplit

import aiohttp
import pytest

import walka_account_rig
from walka.walka_account import calls, ledger, sender, service, signin
from walka.walka_account.service import LAST_WORDS, PROBLEMS, QUESTION_GOOD_FOR_S, WalkaAccount
from walka_account_rig import OK, Rig, run, strings_in


@pytest.fixture(autouse=True)
def nothing_outside_the_list_and_nothing_told(caplog):
    walka_account_rig.before_test(caplog)
    yield
    walka_account_rig.after_test(caplog)


NOT_AN_ANSWER = (400, {"ok": False, "error": "not an answer to the question"})
CLOSING = (409, {"ok": False, "error": "closing"})
NOT_SIGNED_IN = (409, {"ok": False, "error": "not signed in"})

SNAPSHOT_KEYS = {
    "state",
    "host",
    "store",
    "problem",
    "notice",
    "email",
    "way",
    "first_step",
    "can_send",
    "sending",
    "chosen",
    "from",
    "busy",
    "counts",
    "last",
    "aside",
    "held",
    "question",
    "rev",
}
QUESTION_KEYS = {"id", "email", "way", "waiting", "unnamed", "held", "public", "all_shows"}


async def a_pass(rig: Rig) -> str:
    outcome = await rig.account.sender.one_pass()
    await rig.idle()
    return outcome


def refreshes(rig: Rig) -> int:
    return sum(1 for seen in rig.asked("/auth/v1/token") if ("grant_type", "refresh_token") in seen.query)


def bearer(seen) -> str:
    return (seen.header("Authorization") or "")[len("Bearer ") :]


# ---- signing in --------------------------------------------------------------------------------


def test_a_whole_sign_in_through_the_browser(tmp_path):
    async def scenario():
        async with Rig(tmp_path) as rig:
            user = rig.person("walker@mail.example", way="github")
            rig.walk(3600)
            assert rig.snapshot()["state"] == "signed_out"
            assert rig.account.sign_in("github") == OK
            assert rig.snapshot()["state"] == "signing_in"  # at once: the route does not wait
            await rig.idle()

            # The one address Walka opens in the browser, and the page the person ends on.
            (opened,) = rig.opened
            assert opened.startswith(rig.fake.origin + "/auth/v1/authorize?")
            assert dict(parse_qsl(urlsplit(opened).query))["provider"] == "github"
            ((status, headers, text),) = rig.pages
            assert status == 200 and signin.PAGE_SIGNED_IN in text and headers["Cache-Control"] == "no-store"

            shown = rig.snapshot()
            assert shown["state"] == "signed_in" and shown["problem"] == "" and shown["notice"] == ""
            assert (shown["email"], shown["way"], shown["can_send"]) == ("walker@mail.example", "github", True)
            assert shown["store"] == "lasting" and shown["host"] == rig.fake.origin.split("//")[1]
            # Signing in never switches sending on, and sends nothing.
            assert shown["sending"] is False and shown["chosen"] == "" and shown["counts"]["waiting"] == 1
            assert rig.pushes() == []
            # Straight afterwards one question, and no other: has this Walka account taken its first step?
            asked = [(seen.method, seen.path, seen.query) for seen in rig.asked()]
            assert asked == [
                ("POST", "/auth/v1/token", [("grant_type", "pkce")]),
                ("GET", "/rest/v1/profiles", [("select", "adult_confirmed_at")]),
            ]
            assert shown["first_step"] == "taken"

            # The refresh token is in the store and nowhere else; the file says who, and that sending is off.
            (stored,) = rig.keystore.secrets()
            assert rig.keystore.calls == [("write", rig.entry)]
            assert rig.entry == "Walka account - " + rig.fake.origin
            place = rig.book()["addresses"][rig.origin.text]
            assert place["signed_in"] == user and place["notice"] is None
            assert place["accounts"][user] == {
                "way": "github",
                "email": "walker@mail.example",
                "sending": False,
                "chosen": None,
                "walks": {},
            }
            assert stored not in rig.files_text()
            # The stored key is the one the accounts will take.
            assert (await calls.refresh(rig.account.client, rig.fake.key, refresh_token=stored)).user_id == user

    run(scenario)


@pytest.mark.parametrize("way", [None, 7, "", "email", "GitHub", "github ", ["github"], {"way": "github"}, b"github"])
def test_only_one_of_the_three_ways_starts_a_sign_in_and_anything_else_does_nothing(tmp_path, way):
    async def scenario():
        async with Rig(tmp_path) as rig:
            rig.person()
            assert rig.account.sign_in(way) == (400, {"ok": False, "error": "not a way to sign in"})
            await rig.idle()
            assert rig.opened == [] and rig.fake.requests == [] and rig.keystore.calls == []
            assert rig.snapshot()["state"] == "signed_out" and rig.wait.limits() == []

    run(scenario)


@pytest.mark.parametrize("way", ["discord", "github", "google"])
def test_each_of_the_three_ways_signs_in(tmp_path, way):
    async def scenario():
        async with Rig(tmp_path) as rig:
            rig.person(way=way)
            await rig.sign_in(way=way)
            assert rig.snapshot()["state"] == "signed_in" and rig.snapshot()["way"] == way

    run(scenario)


def test_while_somebody_is_signed_in_a_sign_in_is_refused(tmp_path):
    async def scenario():
        async with Rig(tmp_path) as rig:
            rig.person()
            await rig.sign_in()
            assert rig.account.sign_in("google") == (409, {"ok": False, "error": "signed in already"})
            await rig.idle()
            assert len(rig.opened) == 1 and rig.snapshot()["way"] == "github"

    run(scenario)


def test_starting_another_sign_in_closes_the_first(tmp_path):
    async def scenario():
        async with Rig(tmp_path) as rig:
            rig.person()
            rig.browser_comes = False
            assert rig.account.sign_in("github") == OK
            await rig.idle()
            assert rig.account.sign_in("google") == OK
            await rig.idle()
            first, second = rig.opened
            assert rig.snapshot()["state"] == "signing_in"
            # The first listener is gone: its address leads nowhere.
            back = dict(parse_qsl(urlsplit(first).query))["redirect_to"]
            with pytest.raises(aiohttp.ClientConnectionError):
                await rig.fake.visit(back + "?code=anything")
            # The second one works.
            status, _headers, text = await rig.fake.browser(second)
            await rig.idle()
            assert status == 200 and signin.PAGE_SIGNED_IN in text
            assert rig.snapshot()["state"] == "signed_in" and rig.snapshot()["way"] == "google"

    run(scenario)


def test_the_person_says_no_and_nothing_is_signed_in(tmp_path):
    async def scenario():
        async with Rig(tmp_path) as rig:
            rig.person()
            rig.fake.next_browser_user(None)
            assert rig.account.sign_in("github") == OK
            await rig.idle()
            assert signin.PAGE_NOTHING in rig.pages[0][2]
            shown = rig.snapshot()
            assert shown["state"] == "signed_out" and shown["problem"] == "refused"
            assert rig.keystore.calls == [] and rig.asked() == []
            assert not (rig.dir / ledger.FILE_NAME).exists()
            # The word goes when the next sign-in begins.
            rig.browser_comes = False
            assert rig.account.sign_in("github") == OK
            assert rig.snapshot()["problem"] == ""

    run(scenario)


@pytest.mark.parametrize("word", ["failed", "timeout", "refused", "unavailable"])
def test_a_store_that_will_not_keep_the_key_means_no_sign_in_and_never_a_file(tmp_path, word):
    async def scenario():
        async with Rig(tmp_path) as rig:
            rig.person()
            rig.keystore.refuse["write"] = word
            assert rig.account.sign_in("github") == OK
            await rig.idle()
            assert signin.PAGE_FAILED in rig.pages[0][2]  # the browser is told what is true
            shown = rig.snapshot()
            assert shown["state"] == "signed_out" and shown["problem"] == "no_store" and shown["email"] == ""
            assert rig.keystore.secrets() == []
            assert rig.account.access_token() is None  # nothing of it is kept
            assert not (rig.dir / ledger.FILE_NAME).exists()
            assert rig.asked("/rest/v1/") == []

    run(scenario)


def test_a_sign_in_nobody_comes_back_from_ends_by_the_made_up_clock(tmp_path):
    async def scenario():
        async with Rig(tmp_path) as rig:
            rig.person()
            rig.browser_comes = False
            assert rig.account.sign_in("github") == OK
            await rig.idle()
            assert rig.wait.limits() == [signin.LISTEN_FOR_S]
            await rig.advance(signin.LISTEN_FOR_S - 1)
            assert rig.snapshot()["state"] == "signing_in"
            await rig.advance(2)
            shown = rig.snapshot()
            assert shown["state"] == "signed_out" and shown["problem"] == "timed_out"
            assert rig.fake.requests == [] and rig.keystore.calls == []

    run(scenario)


def test_a_sign_in_cut_off_while_it_is_being_kept_leaves_no_key_behind(tmp_path):
    async def scenario():
        async with Rig(tmp_path) as rig:
            rig.person()
            gate = threading.Event()
            rig.keystore.block["write"] = gate  # the store sits on the write, as a Mac's keychain can
            assert rig.account.sign_in("github") == OK
            while ("write", rig.entry) not in rig.keystore.calls:
                await asyncio.sleep(0.005)
            rig.browser_comes = False
            assert rig.account.sign_in("google") == OK  # the first sign-in is cut off in the middle
            gate.set()
            del rig.keystore.block["write"]
            await rig.idle()
            assert rig.keystore.secrets() == []  # what the store was given is taken back
            shown = rig.snapshot()
            assert shown["state"] == "signing_in" and shown["email"] == ""
            book = rig.book()
            assert ledger.signed_in(book, rig.origin) is None

    run(scenario)


def test_a_sign_in_without_an_e_mail_address_can_never_switch_sending_on(tmp_path):
    async def scenario():
        async with Rig(tmp_path) as rig:
            rig.people[rig.fake.add_user("")] = "walker@mail.example"  # (no address to look for in the log)
            rig.walk(3600)
            await rig.sign_in()
            shown = rig.snapshot()
            assert shown["state"] == "signed_in" and shown["email"] == "" and shown["can_send"] is False
            before = len(rig.fake.requests)
            assert rig.account.prepare_question() == (409, {"ok": False, "error": "no e-mail address"})
            await rig.idle()
            assert rig.snapshot()["question"] is None and len(rig.fake.requests) == before
            assert rig.account.switch_on("anything", "all") == NOT_AN_ANSWER

    run(scenario)


# ---- signing out -------------------------------------------------------------------------------


def test_signing_out_forgets_first_says_so_at_once_and_then_tells_the_accounts(tmp_path):
    async def scenario():
        async with Rig(tmp_path) as rig:
            user = rig.person()
            await rig.sign_in()
            await rig.switch_on("all")
            (stored,) = rig.keystore.secrets()
            token = rig.account.access_token()
            before = len(rig.fake.requests)

            assert rig.account.sign_out() == OK
            # At once, before anything was written or asked:
            shown = rig.snapshot()
            assert (shown["state"], shown["email"], shown["sending"]) == ("signed_out", "", False)
            assert rig.account.access_token() is None
            assert len(rig.fake.requests) == before
            await rig.idle()

            assert rig.keystore.secrets() == []
            place = rig.book()["addresses"][rig.origin.text]
            assert place["signed_in"] is None and place["notice"] is None
            assert place["accounts"][user]["sending"] is False
            # The courtesy: this sign-in only, with the token that was taken into a variable.
            (logout,) = rig.fake.requests[before:]
            assert (logout.method, logout.path, logout.query) == ("POST", "/auth/v1/logout", [("scope", "local")])
            assert bearer(logout) == token
            with pytest.raises(calls.CallError) as refused:
                await calls.refresh(rig.account.client, rig.fake.key, refresh_token=stored)
            assert refused.value.kind == "sign_in_over"
            assert token not in strings_in(rig.account)

    run(scenario)


def test_signing_out_with_a_token_that_has_run_out_asks_nobody(tmp_path):
    async def scenario():
        async with Rig(tmp_path) as rig:
            rig.person()
            await rig.sign_in()
            rig.clock.advance(3700)
            before = len(rig.fake.requests)
            assert rig.account.sign_out() == OK
            await rig.idle()
            assert len(rig.fake.requests) == before  # no courtesy, and never a refresh in order to sign out
            assert rig.keystore.secrets() == [] and rig.keystore.calls[-1] == ("delete", rig.entry)
            assert rig.snapshot()["state"] == "signed_out"

    run(scenario)


def test_signing_out_never_fails_whatever_the_accounts_answer(tmp_path):
    async def scenario():
        async with Rig(tmp_path) as rig:
            rig.person()
            await rig.sign_in()
            rig.fake.drop_next("/auth/v1/logout")
            assert rig.account.sign_out() == OK
            await rig.idle()
            assert rig.snapshot()["state"] == "signed_out" and rig.snapshot()["last"]["word"] == ""
            assert rig.keystore.secrets() == []

    run(scenario)


def test_signing_out_reaches_the_file_though_the_store_sits_on_its_answer(tmp_path):
    async def scenario():
        async with Rig(tmp_path) as rig:
            user = rig.person()
            await rig.sign_in()
            await rig.switch_on("all")
            gate = threading.Event()
            rig.keystore.block["delete"] = gate  # the store sits on the delete, as a Mac's keychain can
            assert rig.account.sign_out() == OK
            for _ in range(400):
                if ledger.signed_in(rig.book(), rig.origin) is None:
                    break
                await asyncio.sleep(0.005)
            # "Signed out, sending off" is in the file already - so it holds at the next start,
            # whenever Walka is closed - while the store has not answered yet.
            place = rig.book()["addresses"][rig.origin.text]
            assert place["signed_in"] is None and place["accounts"][user]["sending"] is False
            assert len(rig.keystore.secrets()) == 1 and ("delete", rig.entry) in rig.keystore.calls
            gate.set()
            del rig.keystore.block["delete"]
            await rig.idle()
            assert rig.keystore.secrets() == []

    run(scenario)


def test_signing_out_when_nobody_is_signed_in_touches_nothing(tmp_path):
    async def scenario():
        async with Rig(tmp_path) as rig:
            assert rig.account.sign_out() == OK
            await rig.idle()
            assert rig.keystore.calls == [] and rig.fake.requests == []
            assert not (rig.dir / ledger.FILE_NAME).exists()
            # ... but it does end a sign-in that waits for the browser.
            rig.person()
            rig.browser_comes = False
            assert rig.account.sign_in("github") == OK
            await rig.idle()
            assert rig.account.sign_out() == OK
            await rig.idle()
            assert rig.snapshot()["state"] == "signed_out" and rig.snapshot()["problem"] == ""
            assert rig.keystore.calls == []

    run(scenario)


def test_sign_out_while_a_refresh_answer_is_held_leaves_the_store_empty(tmp_path):
    async def scenario():
        async with Rig(tmp_path, start=False) as rig:
            user = rig.person()
            await rig.sign_in()
            await rig.switch_on("all")
            rig.walk(3600)
            rig.clock.advance(3700)
            gate = rig.fake.hold("/auth/v1/token", after=True)  # the keys are changed over there; the answer waits
            going = asyncio.ensure_future(rig.account.sender.one_pass())
            await gate.arrived.wait()
            assert rig.account.sign_out() == OK
            await rig.idle()
            gate.release()
            assert await going == "dropped"
            await rig.idle()
            assert rig.keystore.secrets() == []  # the new key was not written after the sign-out
            assert rig.keystore.calls.count(("write", rig.entry)) == 1  # (the one of the sign-in)
            assert rig.pushes() == [] and rig.fake.walks(user) == []
            assert rig.account.access_token() is None
            assert rig.snapshot()["state"] == "signed_out"

    run(scenario)


def test_sign_out_and_sign_in_as_another_account_sends_nothing_until_the_question_is_answered_again(tmp_path):
    async def scenario():
        async with Rig(tmp_path) as rig:
            first = rig.person("first@mail.example")
            second = rig.person("second@mail.example", way="google")
            old = rig.walk(7200)
            await rig.sign_in(first)
            await rig.switch_on("all")
            assert len(rig.fake.walks(first)) == 1

            assert rig.account.sign_out() == OK
            await rig.idle()
            new = rig.walk(3600)
            await rig.saved()
            await rig.sign_in(second, way="google")
            await rig.saved()
            await rig.advance(7200)
            shown = rig.snapshot()
            assert (shown["email"], shown["sending"]) == ("second@mail.example", False)
            # Not one walk has gone to the second account, and the first got nothing new.
            assert rig.fake.walks(second) == [] and len(rig.fake.walks(first)) == 1
            assert shown["counts"]["waiting"] == 2 and shown["counts"]["sent"] == 0
            assert rig.account.in_account(old.id, old.start) is False  # not in THIS account

            # Only the question, answered again for THIS account, sends - and it says how many would go.
            question = await rig.question()
            assert (question["email"], question["way"], question["waiting"]) == ("second@mail.example", "google", 2)
            assert rig.account.switch_on(question["id"], "all") == OK
            await rig.idle()
            assert {walk["client_id"] for walk in rig.fake.walks(second)} == {old.id, new.id}
            assert len(rig.fake.walks(first)) == 1
            # Two accounts never get the same row id.
            assert not {walk["id"] for walk in rig.fake.walks(first)} & {walk["id"] for walk in rig.fake.walks(second)}

    run(scenario)


def test_signing_in_again_as_the_same_account_asks_the_question_again(tmp_path):
    async def scenario():
        async with Rig(tmp_path) as rig:
            user = rig.person()
            await rig.sign_in()
            await rig.switch_on("all")
            assert rig.account.sign_out() == OK
            await rig.idle()
            await rig.sign_in()
            rig.walk(3600)
            await rig.saved()
            assert rig.snapshot()["sending"] is False and rig.account_in_book(user)["sending"] is False
            assert rig.account_in_book(user)["chosen"] is None
            assert rig.pushes() == []
            await rig.switch_on("all")
            assert len(rig.fake.walks(user)) == 1

    run(scenario)


def test_a_book_carried_in_with_sending_on_is_off_after_any_sign_in(tmp_path):
    async def scenario():
        async with Rig(tmp_path, start=False) as rig:
            user = rig.person()
            other = "0b6f0a52-6d6f-4a8e-9d1c-3a1f0e5b7c21"
            chosen = {"what": "all", "at": 5.0}
            book = {
                "version": 1,
                "addresses": {
                    rig.origin.text: {
                        "signed_in": other,
                        "notice": None,
                        "accounts": {
                            user: {"way": "github", "email": "x@y.example", "sending": True, "chosen": chosen},
                            other: {"way": "google", "email": "z@y.example", "sending": True, "chosen": chosen},
                        },
                    }
                },
            }
            (rig.dir / ledger.FILE_NAME).write_text(json.dumps(book), "utf-8")
            rig.walk(3600)
            await rig.restart(start=False)
            # The file says somebody is signed in and sending is on - but the store holds no such sign-in.
            assert rig.snapshot()["sending"] is True
            assert await a_pass(rig) == "over"
            assert rig.fake.requests == []
            shown = rig.snapshot()
            assert (shown["state"], shown["problem"], shown["notice"]) == ("signed_out", "no_stored_sign_in", "")

            await rig.sign_in(user)
            assert await a_pass(rig) == "nothing"
            assert rig.snapshot()["sending"] is False and rig.pushes() == []
            accounts = rig.book()["addresses"][rig.origin.text]["accounts"]
            assert [accounts[who]["sending"] for who in (user, other)] == [False, False]
            assert [accounts[who]["chosen"] for who in (user, other)] == [None, None]

    run(scenario)


# ---- the stored sign-in ------------------------------------------------------------------------


def test_a_sign_in_that_is_simply_not_in_the_store_is_quietly_not_signed_in(tmp_path):
    async def scenario():
        async with Rig(tmp_path, start=False) as rig:
            user = rig.person()
            await rig.sign_in()
            await rig.switch_on("all")
            rig.walk(3600)
            files = rig.walk_files()
            rig.keystore._entries.clear()  # the data folder came from another computer, say
            await rig.restart(start=False)
            rig.keystore.calls.clear()
            before = len(rig.fake.requests)
            assert rig.snapshot()["state"] == "signed_in"  # the file says so; nothing is asked to find out
            assert await a_pass(rig) == "over"
            assert rig.keystore.calls == [("read", rig.entry)] and len(rig.fake.requests) == before
            shown = rig.snapshot()
            # Not decision 11's sentence: nobody signed out, and no Walka account was deleted.
            assert (shown["state"], shown["notice"], shown["problem"]) == ("signed_out", "", "no_stored_sign_in")
            assert shown["sending"] is False and shown["email"] == ""
            place = rig.book()["addresses"][rig.origin.text]
            assert place["signed_in"] is None and place["notice"] is None
            assert place["accounts"][user]["sending"] is False
            assert rig.walk_files() == files

    run(scenario)


@pytest.mark.parametrize(
    ("word", "outcome", "state", "problem"),
    [
        ("refused", "over", "signed_out", "store_refused"),
        ("unavailable", "over", "signed_out", "store_refused"),
        ("timeout", "stopped", "signed_in", ""),
        ("failed", "stopped", "signed_in", ""),
    ],
)
def test_a_store_that_refuses_the_read_signs_out_quietly_and_one_that_only_fails_does_not(
    tmp_path, word, outcome, state, problem
):
    async def scenario():
        async with Rig(tmp_path, start=False) as rig:
            user = rig.person()
            await rig.sign_in()
            await rig.switch_on("all")
            rig.walk(3600)
            rig.clock.advance(3700)
            rig.keystore.refuse["read"] = word
            before = len(rig.fake.requests)
            assert await a_pass(rig) == outcome
            assert len(rig.fake.requests) == before and rig.fake.walks(user) == []
            shown = rig.snapshot()
            assert (shown["state"], shown["problem"], shown["notice"]) == (state, problem, "")
            assert len(rig.keystore.secrets()) == 1  # what is in the store is left alone
            assert shown["last"]["word"] in LAST_WORDS and shown["problem"] in PROBLEMS

    run(scenario)


def test_a_new_key_that_cannot_be_stored_is_not_kept_in_memory_instead(tmp_path):
    async def scenario():
        async with Rig(tmp_path, start=False) as rig:
            user = rig.person()
            await rig.sign_in()
            await rig.switch_on("all")
            rig.walk(3600)
            rig.clock.advance(3700)
            rig.keystore.refuse["write"] = "failed"
            assert await a_pass(rig) == "over"
            assert rig.pushes() == [] and rig.fake.walks(user) == []
            assert rig.account.access_token() is None
            shown = rig.snapshot()
            assert (shown["state"], shown["problem"], shown["notice"]) == ("signed_out", "no_stored_sign_in", "")
            assert rig.keystore.secrets() == []

    run(scenario)


def test_a_stored_sign_in_of_another_data_folder_is_not_used_and_not_deleted(tmp_path):
    async def scenario():
        async with Rig(tmp_path, start=False) as rig:
            mine = rig.person("mine@mail.example")
            theirs = rig.person("theirs@mail.example")
            await rig.sign_in(mine)
            await rig.switch_on("all")
            rig.walk(3600)
            # Another data folder signed in at the same address: the one entry in the store is its key now.
            other_key = rig.fake.new_sign_in(theirs)["refresh_token"]
            rig.keystore._entries[rig.entry] = other_key
            rig.keys_seen.add(other_key)
            rig.clock.advance(3700)

            assert await a_pass(rig) == "over"
            # The refresh answered another Walka account: its access token is dropped unused.
            assert rig.pushes() == [] and rig.fake.walks(mine) == [] and rig.fake.walks(theirs) == []
            assert rig.asked("/rest/v1/") == rig.asked("/rest/v1/profiles")  # (only the questions of the sign-in)
            assert rig.account.access_token() is None
            shown = rig.snapshot()
            assert (shown["state"], shown["notice"], shown["problem"]) == ("signed_out", "", "no_stored_sign_in")
            assert shown["email"] == ""  # and never the other account's address
            # The entry is theirs: it is still there, and it still works - renewed, as every refresh renews it.
            (kept,) = rig.keystore.secrets()
            assert kept != other_key
            assert ("delete", rig.entry) not in rig.keystore.calls
            assert (await calls.refresh(rig.account.client, rig.fake.key, refresh_token=kept)).user_id == theirs

    run(scenario)


def test_the_new_refresh_token_is_in_the_store_before_the_new_access_token_is_used(tmp_path):
    async def scenario():
        async with Rig(tmp_path, start=False) as rig:
            rig.person()
            await rig.sign_in()
            await rig.switch_on("all")
            rig.walk(3600)
            (old_key,) = rig.keystore.secrets()
            old_access = rig.account.access_token()
            rig.clock.advance(3700)
            rig.keystore.calls.clear()
            first = len(rig.fake.requests)

            assert await a_pass(rig) == "sent"

            asked = rig.fake.requests[first:]
            assert [seen.path for seen in asked] == ["/auth/v1/token", "/rest/v1/rpc/schema_version", "/rest/v1/walks"]
            assert asked[0].json == {"refresh_token": old_key} and asked[0].header("Authorization") is None
            new_access = bearer(asked[1])
            assert new_access not in ("", old_access) and bearer(asked[2]) == new_access
            # Read immediately before the refresh, written at once after it - and only then a request with the new token.
            assert rig.keystore.calls == [("read", rig.entry), ("write", rig.entry)]
            new_key, seen_by_then = rig.keystore.writes[-1]
            assert new_key != old_key and rig.keystore.secrets() == [new_key]
            assert seen_by_then == first + 1  # the refresh had been answered; nothing else had been asked yet

            # With a lasting store no refresh token stays in the part's memory.
            held = strings_in(rig.account, {id(rig.keystore)})  # everything but the store itself
            assert new_access in held  # (the search does find what IS kept: the access token)
            assert new_key in strings_in(rig.account)  # (... and the store, when it is not left out)
            for key in (old_key, new_key):
                assert not [text for text in held if key in text]

    run(scenario)


def test_a_token_that_other_work_made_anew_meanwhile_is_used_and_no_second_one_is_made(tmp_path):
    async def scenario():
        async with Rig(tmp_path) as rig:
            user = rig.person()
            await rig.sign_in()
            await rig.switch_on("all")
            rig.walk(7200)
            await rig.saved()
            rig.walk(3600)
            rig.fake.expire_access_tokens()  # the accounts say so; by Walka's own clock the token is still good
            gate = rig.fake.hold("/rest/v1/walks", method="POST")
            rig.account.walk_saved()
            await gate.arrived.wait()  # the walk is on its way, with the old token
            # Meanwhile a button asks the accounts, is told that the token ran out, and makes a new one.
            assert rig.account.look_again() == OK
            while refreshes(rig) < 1 or rig.account._asking:  # (until that button's work is done)
                await asyncio.sleep(0.005)
            assert rig.account.access_token() not in (None, bearer(rig.pushes_seen()[-1]))
            gate.release()
            await rig.idle()
            # The walk was refused with the old token, and went with the one that was there already:
            # this run is not stopped for it, and the token was made anew once, not twice.
            assert len(rig.fake.walks(user)) == 2 and rig.account.latch == ""
            assert rig.snapshot()["last"]["word"] == "sent"
            assert refreshes(rig) == 1
            sent_with = [bearer(seen) for seen in rig.pushes_seen()]
            assert len(sent_with) == 3 and sent_with[1] != sent_with[2] == rig.account.access_token()

    run(scenario)


def test_a_new_key_that_cannot_be_stored_after_sending_was_switched_off_signs_nobody_out(tmp_path):
    async def scenario():
        async with Rig(tmp_path, start=False) as rig:
            rig.person()
            await rig.sign_in()
            await rig.switch_on("all")
            rig.walk(3600)
            rig.clock.advance(3700)
            gate = threading.Event()
            rig.keystore.block["write"] = gate  # the store sits on the write of the new key
            going = asyncio.ensure_future(rig.account.sender.one_pass())
            while ("write", rig.entry) not in rig.keystore.calls[1:]:
                await asyncio.sleep(0.005)
            assert rig.account.switch_off() == OK  # meanwhile sending is switched off
            rig.keystore.refuse["write"] = "failed"  # ... and then the store says no
            gate.set()
            assert await going == "dropped"  # that piece of work is over: nobody's sign-in is
            del rig.keystore.block["write"], rig.keystore.refuse["write"]
            await rig.idle()
            shown = rig.snapshot()
            assert (shown["state"], shown["sending"], shown["problem"], shown["notice"]) == ("signed_in", False, "", "")
            assert rig.pushes() == [] and len(rig.keystore.secrets()) == 1
            # The key in the store is the one before the last: the accounts still take it, once more.
            assert rig.account.look_again() == OK
            await rig.idle()
            assert rig.snapshot()["first_step"] == "taken" and rig.snapshot()["state"] == "signed_in"

    run(scenario)


def test_the_other_walka_signs_in_as_another_account_and_nothing_goes_until_the_question_is_answered_here(tmp_path):
    async def scenario():
        async with Rig(tmp_path, start=False) as rig:
            mine = rig.person("mine@mail.example")
            theirs = rig.person("theirs@mail.example", way="google")
            old = rig.walk(7200)
            await rig.sign_in(mine)
            await rig.switch_on("all")
            assert await a_pass(rig) == "sent"
            my_token = rig.account.access_token()
            assert my_token is not None
            new = rig.walk(3600)

            # The other Walka on this data folder and this store: signed out, signed in as another
            # Walka account, and sending switched on there.
            other_key = rig.fake.new_sign_in(theirs)["refresh_token"]
            rig.keystore._entries[rig.entry] = other_key
            rig.keys_seen.add(other_key)

            def theirs_now(book: dict) -> None:
                ledger.sign_out(book, rig.origin)
                ledger.sign_in(book, rig.origin, theirs, "google", "theirs@mail.example")
                ledger.set_sending(book, rig.origin, theirs, True, {"what": "all", "at": 1.0})

            ledger.Ledger(rig.dir).change(theirs_now)
            before = len(rig.fake.requests)

            assert await a_pass(rig) == "dropped"
            shown = rig.snapshot()
            assert (shown["state"], shown["email"], shown["way"]) == ("signed_in", "theirs@mail.example", "google")
            # In THIS Walka nobody answered a question for that account: sending is off here.
            assert shown["sending"] is False and shown["question"] is None
            assert rig.account.access_token() is None  # the first account's key is forgotten at once
            assert await a_pass(rig) == "nothing"
            assert len(rig.fake.requests) == before
            assert rig.account.in_account(old.id, old.start) is False  # not in the account that is signed in now

            # Only the question, answered here, sends - to the account that is signed in now, with its own key.
            question = await rig.question()
            assert (question["email"], question["waiting"]) == ("theirs@mail.example", 2)
            assert rig.account.switch_on(question["id"], "all") == OK
            await rig.idle()
            assert await a_pass(rig) == "sent"
            assert {walk["client_id"] for walk in rig.fake.walks(theirs)} == {old.id, new.id}
            assert [walk["client_id"] for walk in rig.fake.walks(mine)] == [old.id]
            assert my_token not in [bearer(seen) for seen in rig.fake.requests[before:]]

    run(scenario)


def test_a_button_and_the_sender_that_both_need_a_new_token_make_one_between_them(tmp_path):
    async def scenario():
        async with Rig(tmp_path) as rig:
            user = rig.person()
            await rig.sign_in()
            await rig.switch_on("all")
            await rig.advance(3700)  # the access token has run out
            rig.walk(3600)
            gate = rig.fake.hold("/auth/v1/token")
            rig.account.walk_saved()
            await gate.arrived.wait()  # the sender is making a new one ...
            assert rig.account.look_again() == OK  # ... and a button needs one too
            for _ in range(20):
                await asyncio.sleep(0.005)
            gate.release()
            await rig.idle()
            # One refresh: the stored key is spent by it, and a second one with the same key could end the sign-in.
            assert refreshes(rig) == 1
            assert len(rig.fake.walks(user)) == 1 and rig.snapshot()["first_step"] == "taken"
            assert rig.keystore.calls.count(("read", rig.entry)) == 1

    run(scenario)


def test_two_walkas_on_one_folder_and_one_store_take_turns_with_the_key(tmp_path):
    async def scenario():
        async with Rig(tmp_path, start=False) as rig:
            user = rig.person()
            await rig.sign_in()
            await rig.switch_on("all")
            one = rig.account
            two = rig.build()  # the second Walka: it reads the same file and the same store
            assert two.snapshot()["sending"] is True

            rig.walk(9000)
            assert await one.sender.one_pass() == "sent"
            for turn, walka in enumerate((two, one, two)):
                rig.clock.advance(3700)  # each time the access token has run out
                rig.walk(3600 - 60 * turn)
                assert await walka.sender.one_pass() == "sent"
            assert refreshes(rig) == 3 and len(rig.fake.walks(user)) == 4
            for walka in (one, two):
                assert walka.snapshot()["state"] == "signed_in" and walka.snapshot()["notice"] == ""
            # What the other one sent is not sent again.
            assert sum(len(rows) for rows in rig.pushes()) == 4

            # One switches off: the other stops at its next look at the file, before any request.
            assert one.switch_off() == OK
            await one.idle()
            rig.walk(60)
            before = len(rig.fake.requests)
            assert await two.sender.one_pass() == "dropped"
            assert len(rig.fake.requests) == before and two.snapshot()["sending"] is False

    run(scenario)


def test_without_a_lasting_store_the_sign_in_lasts_until_walka_is_closed(tmp_path):
    async def scenario():
        async with Rig(tmp_path, lasting=False) as rig:
            user = rig.person()
            await rig.sign_in()
            await rig.switch_on("all")
            rig.walk(3600)
            await rig.saved()
            assert rig.snapshot()["store"] == "until_closed" and len(rig.fake.walks(user)) == 1
            # The key lives in memory, in the store: a refresh still works while Walka runs.
            await rig.advance(3700)
            rig.walk(60)
            await rig.saved()
            assert len(rig.fake.walks(user)) == 2 and refreshes(rig) == 1

            rig.walk(30)
            before = len(rig.fake.requests)
            await rig.restart()
            # After a start nobody is signed in - said quietly, without a request and without a notice.
            shown = rig.snapshot()
            assert (shown["state"], shown["notice"], shown["problem"]) == ("signed_out", "", "")
            assert len(rig.fake.requests) == before and rig.keystore.calls[-1][0] != "read"
            place = rig.book()["addresses"][rig.origin.text]
            assert place["signed_in"] is None and place["accounts"][user]["sending"] is False

    run(scenario)


# ---- the question ------------------------------------------------------------------------------


def test_the_question_says_who_how_many_and_whether_the_page_is_public(tmp_path):
    async def scenario():
        async with Rig(tmp_path) as rig:
            user = rig.person("walker@mail.example", way="discord", public=True, handle="walker", show_days=True)
            unnamed, _held = rig.walk(9000), rig.walk(7200, steps=500_000)
            rig.walk(3600), rig.walk(1800)
            rig.rewrite(unnamed, drop=("treadmill",))
            await rig.sign_in(way="discord")
            before = len(rig.fake.requests)
            assert rig.account.prepare_question() == OK
            assert rig.snapshot()["question"] is None  # it is not there until everything it says was asked
            await rig.idle()
            shown = rig.snapshot()
            question = shown["question"]
            assert set(question) == QUESTION_KEYS
            assert {name: question[name] for name in QUESTION_KEYS - {"id"}} == {
                "email": "walker@mail.example",
                "way": "discord",
                "waiting": 2,
                "unnamed": 1,
                "held": 1,
                "public": True,
                "all_shows": True,
            }
            assert len(question["id"]) >= 16
            # Asked again before the question: the first step, then the page. Nothing else, and nothing sent.
            asked = [(seen.path, dict(seen.query).get("select")) for seen in rig.fake.requests[before:]]
            assert asked == [
                ("/rest/v1/profiles", "adult_confirmed_at"),
                ("/rest/v1/profiles", "handle,is_public,show_days,show_walks"),
            ]
            assert "walker" not in json.dumps(shown).replace("walker@mail.example", "")  # the handle stays out
            assert rig.account_in_book(user)["sending"] is False

            # A page that is public but lists neither days nor walks; and one that is not public.
            rig.fake.set_page(user, show_days=False)
            question = await rig.question()
            assert (question["public"], question["all_shows"]) == (True, False)
            rig.fake.set_page(user, is_public=False, show_walks=True)
            question = await rig.question()
            assert (question["public"], question["all_shows"]) == (False, False)

    run(scenario)


def test_a_question_is_answered_once_and_only_with_its_own_id_and_a_choice_spelled_out(tmp_path):
    async def scenario():
        async with Rig(tmp_path) as rig:
            user = rig.person()
            rig.walk(3600)
            await rig.sign_in()
            assert rig.account.switch_on("anything", "all") == NOT_AN_ANSWER  # there is no question yet
            question = await rig.question()
            junk = [
                (None, None),  # an empty body
                (question["id"], None),
                (question["id"], ""),
                (question["id"], "ALL"),
                (question["id"], "from"),
                (question["id"], True),
                (question["id"], ["all"]),
                (None, "all"),
                ("", "all"),
                (question["id"] + "x", "all"),
                (question["id"][:-1], "all"),
                (question["id"].encode(), "all"),
                (True, "all"),
                ({"id": question["id"]}, "all"),
                ("é" * 22, "all"),
                ("\ud800", "all"),  # half a character, as a request's JSON can hold one
                (question["id"] + "\udcff", "all"),
            ]
            for asked, send in junk:
                assert rig.account.switch_on(asked, send) == NOT_AN_ANSWER
            await rig.idle()
            await rig.saved()
            assert rig.snapshot()["sending"] is False and rig.account_in_book(user)["sending"] is False
            assert rig.pushes() == []
            assert rig.snapshot()["question"] == question  # junk does not use the question up

            assert rig.account.switch_on(question["id"], "all") == OK
            assert rig.snapshot()["question"] is None
            assert rig.account.switch_on(question["id"], "all") == NOT_AN_ANSWER  # a double press
            await rig.idle()
            assert rig.snapshot()["sending"] is True and len(rig.fake.walks(user)) == 1
            assert rig.account.switch_on(question["id"], "from_now_on") == NOT_AN_ANSWER
            await rig.idle()
            assert rig.account_in_book(user)["chosen"]["what"] == "all"

    run(scenario)


def test_a_question_runs_out_by_the_made_up_clock(tmp_path):
    async def scenario():
        async with Rig(tmp_path) as rig:
            user = rig.person()
            await rig.sign_in()
            question = await rig.question()
            before = len(rig.fake.requests)
            await rig.advance(QUESTION_GOOD_FOR_S - 1)
            assert rig.snapshot()["question"] == question
            await rig.advance(1)
            assert rig.account.switch_on(question["id"], "all") == NOT_AN_ANSWER  # too late, by a second
            assert rig.snapshot()["question"] is None
            await rig.idle()
            assert rig.account_in_book(user)["sending"] is False
            assert len(rig.fake.requests) == before  # and nobody makes a new one by itself

            # The wall clock set back or ahead does not bring it back, or end one early.
            rig.clock.jump_wall(-3 * 86400)
            question = await rig.question()
            assert question is not None
            rig.clock.jump_wall(4 * 86400)
            assert rig.snapshot()["question"] == question
            assert rig.account.switch_on(question["id"], "all") == OK

    run(scenario)


def test_a_question_is_for_one_account_and_one_sign_in(tmp_path):
    async def scenario():
        async with Rig(tmp_path) as rig:
            first = rig.person("first@mail.example")
            second = rig.person("second@mail.example")
            rig.walk(3600)
            await rig.sign_in(first)
            question = await rig.question()
            assert rig.account.sign_out() == OK
            assert rig.account.switch_on(question["id"], "all") == NOT_AN_ANSWER
            await rig.idle()
            await rig.sign_in(second)
            assert rig.snapshot()["question"] is None
            assert rig.account.switch_on(question["id"], "all") == NOT_AN_ANSWER
            await rig.idle()
            assert rig.snapshot()["sending"] is False and rig.pushes() == []

            # Signed out and in again as the same account: the old question is no answer either.
            question = await rig.question()
            assert rig.account.sign_out() == OK
            await rig.idle()
            await rig.sign_in(second)
            assert rig.account.switch_on(question["id"], "all") == NOT_AN_ANSWER
            # Nor after sending was switched off in between.
            question = await rig.question()
            assert rig.account.switch_off() == OK
            assert rig.account.switch_on(question["id"], "all") == NOT_AN_ANSWER
            await rig.idle()
            assert rig.pushes() == []

    run(scenario)


def test_without_the_first_step_there_is_no_question_and_sending_cannot_be_switched_on(tmp_path):
    async def scenario():
        async with Rig(tmp_path) as rig:
            user = rig.person(adult=False)  # signed in from Walka, never been to the website: no row at all
            rig.walk(3600)
            await rig.sign_in()
            shown = rig.snapshot()
            assert shown["state"] == "signed_in" and shown["first_step"] == "not_taken"
            # No row: so the accounts were asked who is there, before "no Walka account yet" is said.
            assert [seen.path for seen in rig.asked()][1:] == ["/rest/v1/profiles", "/auth/v1/user"]
            assert await rig.question() is None
            assert rig.snapshot()["first_step"] == "not_taken"
            assert rig.account.switch_on("", "all") == NOT_AN_ANSWER
            assert len(rig.opened) == 1  # the browser is never opened by itself, whatever was answered

            # The step is taken on the website. Walka does not poll: nothing is asked until a button is pressed.
            rig.fake.take_first_step(user)
            before = len(rig.fake.requests)
            await rig.advance(600)
            await rig.saved()
            assert len(rig.fake.requests) == before and rig.snapshot()["first_step"] == "not_taken"
            assert rig.account.look_again() == OK
            await rig.idle()
            assert rig.snapshot()["first_step"] == "taken"
            assert [seen.path for seen in rig.fake.requests[before:]] == ["/rest/v1/profiles"]
            # Nothing Walka did ticked anything.
            assert all(seen.method == "GET" for seen in rig.asked("/rest/v1/profiles"))
            await rig.switch_on("all")
            assert len(rig.fake.walks(user)) == 1

    run(scenario)


def test_an_empty_first_step_is_not_taken_and_needs_no_second_question(tmp_path):
    async def scenario():
        async with Rig(tmp_path) as rig:
            rig.person(adult=False, profile_row=True)  # a row, and the value empty
            await rig.sign_in()
            assert rig.snapshot()["first_step"] == "not_taken"
            assert rig.asked("/auth/v1/user") == []
            assert await rig.question() is None

    run(scenario)


def test_a_deleted_account_is_not_told_that_it_has_no_walka_account_yet(tmp_path):
    async def scenario():
        async with Rig(tmp_path) as rig:
            user = rig.person()
            rig.walk(3600)
            await rig.sign_in()
            files = rig.walk_files()
            rig.fake.delete_user(user)  # profiles answers "no row" for it, as for a new sign-in
            assert rig.account.look_again() == OK
            await rig.idle()
            shown = rig.snapshot()
            assert (shown["state"], shown["notice"], shown["first_step"]) == ("signed_out", "ended", "unknown")
            assert rig.keystore.secrets() == [] and rig.walk_files() == files
            assert len(rig.opened) == 1

    run(scenario)


def test_a_sign_in_that_was_ended_elsewhere_ends_here_when_the_accounts_say_so(tmp_path):
    async def scenario():
        async with Rig(tmp_path) as rig:
            user = rig.person(adult=False)
            await rig.sign_in()
            rig.fake.sign_out_everywhere(user)
            assert rig.account.look_again() == OK
            await rig.idle()
            assert (rig.snapshot()["state"], rig.snapshot()["notice"]) == ("signed_out", "ended")

    run(scenario)


@pytest.mark.parametrize(
    ("status", "body", "word"),
    [(500, None, "no_answer"), (403, {"code": "42501", "message": "permission denied"}, "stopped")],
)
def test_a_question_that_cannot_be_asked_is_no_question(tmp_path, status, body, word):
    async def scenario():
        async with Rig(tmp_path) as rig:
            user = rig.person()
            rig.walk(3600)
            await rig.sign_in()
            mark = rig.wait.mark()
            rig.fake.fail_next("/rest/v1/profiles", status, body)
            assert await rig.question() is None
            shown = rig.snapshot()
            assert shown["last"]["word"] == word and shown["busy"] == "" and shown["state"] == "signed_in"
            # The page comes after the first step: when IT cannot be asked there is no question either.
            first_step = [{"adult_confirmed_at": "2026-11-20T10:00:00+00:00"}]
            rig.fake.fail_next("/rest/v1/profiles", 200, first_step)
            rig.fake.fail_next("/rest/v1/profiles", status, body)
            before = len(rig.fake.requests)
            assert await rig.question() is None
            assert len(rig.fake.requests) == before + 2
            # A button's failure does not stop the run, and waits on no clock: the next press asks again.
            assert rig.wait.limits(mark) == []
            await rig.switch_on("all")
            assert len(rig.fake.walks(user)) == 1

    run(scenario)


def test_an_account_whose_page_cannot_be_read_gets_no_question(tmp_path):
    async def scenario():
        async with Rig(tmp_path) as rig:
            user = rig.person()
            rig.walk(3600)
            await rig.sign_in()
            # The first step is taken - and then the accounts have no row to say what the page shows.
            rig.fake.fail_next("/rest/v1/profiles", 200, [{"adult_confirmed_at": "2026-11-20T10:00:00+00:00"}])
            rig.fake.fail_next("/rest/v1/profiles", 200, [])
            assert await rig.question() is None  # everything the question says must have been asked
            assert rig.snapshot()["last"]["word"] == "stopped" and rig.account.latch == ""
            assert rig.account_in_book(user)["sending"] is False

    run(scenario)


# ---- when the sign-in ends (decision 11) -------------------------------------------------------


def test_the_notice_that_the_sign_in_ended_stays_until_ok_and_then_is_gone_for_good(tmp_path):
    async def scenario():
        async with Rig(tmp_path) as rig:
            user = rig.person()
            await rig.sign_in()
            await rig.switch_on("all")
            rig.walk(7200), rig.walk(3600)
            files = rig.walk_files()
            rig.fake.sign_out_everywhere(user)
            await rig.advance(3700)
            await rig.saved()
            assert (rig.snapshot()["state"], rig.snapshot()["notice"]) == ("signed_out", "ended")
            assert rig.walk_files() == files

            # It is said in the card, and is still there after a start - with nothing asked of anybody.
            before = len(rig.fake.requests)
            rig.keystore.calls.clear()
            await rig.restart()
            assert (rig.snapshot()["state"], rig.snapshot()["notice"]) == ("signed_out", "ended")
            assert rig.snapshot()["sending"] is False
            assert len(rig.fake.requests) == before and rig.keystore.calls == []

            assert rig.account.notice_ok() == OK
            assert rig.snapshot()["notice"] == ""  # at once
            await rig.idle()
            await rig.restart()
            assert rig.snapshot()["notice"] == ""
            assert rig.book()["addresses"][rig.origin.text]["notice"] is None
            assert rig.walk_files() == files  # byte for byte, through all of it
            assert len(rig.fake.requests) == before and len(rig.opened) == 1

            # Pressed again, with nothing to read: nothing happens, and no file is touched.
            written = (rig.dir / ledger.FILE_NAME).read_bytes()
            assert rig.account.notice_ok() == OK
            await rig.idle()
            assert (rig.dir / ledger.FILE_NAME).read_bytes() == written

    run(scenario)


def test_a_sign_in_that_ended_is_in_the_file_though_the_store_sits_on_its_answer(tmp_path):
    async def scenario():
        async with Rig(tmp_path, start=False) as rig:
            user = rig.person()
            await rig.sign_in()
            await rig.switch_on("all")
            rig.walk(3600)
            rig.fake.sign_out_everywhere(user)
            rig.clock.advance(3700)
            gate = threading.Event()
            rig.keystore.block["delete"] = gate
            going = asyncio.ensure_future(rig.account.sender.one_pass())
            for _ in range(400):
                if rig.book()["addresses"][rig.origin.text]["notice"] == "ended":
                    break
                await asyncio.sleep(0.005)
            place = rig.book()["addresses"][rig.origin.text]
            assert (place["signed_in"], place["notice"]) == (None, "ended")
            assert place["accounts"][user]["sending"] is False and not going.done()
            gate.set()
            del rig.keystore.block["delete"]
            assert await going == "over"
            assert rig.keystore.secrets() == []

    run(scenario)


def test_a_new_sign_in_takes_the_notice_away(tmp_path):
    async def scenario():
        async with Rig(tmp_path) as rig:
            user = rig.person()
            await rig.sign_in()
            rig.fake.delete_user(user)
            assert rig.account.look_again() == OK
            await rig.idle()
            assert rig.snapshot()["notice"] == "ended"
            rig.person("new@mail.example")
            await rig.sign_in()
            shown = rig.snapshot()
            assert (shown["state"], shown["notice"], shown["email"]) == ("signed_in", "", "new@mail.example")
            assert shown["sending"] is False

    run(scenario)


# ---- closing -----------------------------------------------------------------------------------


def our_tasks() -> list[str]:
    mine = asyncio.current_task()
    return [task.get_name() for task in asyncio.all_tasks() if task is not mine and "walka" in task.get_name()]


def test_closing_while_a_request_is_held_takes_no_time_leaves_no_task_and_sends_nothing(tmp_path):
    async def scenario():
        async with Rig(tmp_path) as rig:
            user = rig.person()
            await rig.sign_in()
            await rig.switch_on("all")
            rig.walk(3600)
            gate = rig.fake.hold("/rest/v1/walks", method="POST")
            rig.account.walk_saved()
            await gate.arrived.wait()
            assert rig.snapshot()["busy"] == "sending"
            loop = asyncio.get_running_loop()
            began = loop.time()

            rig.account.cancel()  # before the walk is saved: synchronous
            await rig.account.close()  # after it

            assert loop.time() - began < 1.0
            assert our_tasks() == [] and rig.account._tasks == set()
            await asyncio.wait_for(gate.left.wait(), 5)  # whoever asked has gone away
            gate.release()
            assert rig.fake.walks(user) == []

            # After stopping, every action refuses and a saved walk wakes nobody.
            before = len(rig.fake.requests)
            rig.account._spawn(rig.account._clear_notice)  # (whatever would still begin a task: it does not)
            assert our_tasks() == []
            assert rig.account.sign_in("github") == CLOSING
            assert rig.account.sign_out() == CLOSING
            assert rig.account.look_again() == CLOSING
            assert rig.account.prepare_question() == CLOSING
            assert rig.account.switch_on("x", "all") == CLOSING
            assert rig.account.switch_off() == CLOSING
            assert rig.account.notice_ok() == CLOSING
            rig.walk(60)
            rig.account.walk_saved()
            rig.account.start()
            await asyncio.sleep(0.05)
            assert our_tasks() == [] and len(rig.fake.requests) == before
            assert len(rig.keystore.secrets()) == 1  # still signed in at the next start
            await rig.account.close()  # a second time: nothing to do, and no error

            # The walk saved while closing goes at the next start.
            await rig.restart()
            assert len(rig.fake.walks(user)) == 2

    run(scenario)


def test_closing_in_the_middle_of_a_sign_in_takes_the_listener_down_and_keeps_nothing(tmp_path):
    async def scenario():
        async with Rig(tmp_path) as rig:
            rig.person()
            gate = rig.fake.hold("/auth/v1/token")  # the browser is back; the exchange waits for its answer
            assert rig.account.sign_in("github") == OK
            await gate.arrived.wait()
            loop = asyncio.get_running_loop()
            began = loop.time()
            rig.account.cancel()
            # cancel() alone - synchronous, before the walk is saved - ends the sign-in: the browser is
            # answered at once, and truly, though nothing was waited for and close() has not been called.
            await asyncio.wait_for(asyncio.gather(*rig._browsers), 5)
            assert signin.PAGE_FAILED in rig.pages[0][2]
            await rig.account.close()
            assert loop.time() - began < 1.0
            assert our_tasks() == [] and rig.account._tasks == set()
            assert rig.keystore.calls == [] and not (rig.dir / ledger.FILE_NAME).exists()
            back = dict(parse_qsl(urlsplit(rig.opened[0]).query))["redirect_to"]
            with pytest.raises(aiohttp.ClientConnectionError):
                await rig.fake.visit(back)
            gate.release()

    run(scenario)


def test_closing_waits_for_a_listener_that_was_just_being_taken_down(tmp_path, monkeypatch):
    async def scenario():
        async with Rig(tmp_path) as rig:
            rig.person()
            entered, go, gone = asyncio.Event(), asyncio.Event(), []
            real_close = signin.SignIn.close

            async def slow_close(self):
                entered.set()
                await go.wait()
                await real_close(self)
                gone.append(self)

            monkeypatch.setattr(signin.SignIn, "close", slow_close)
            assert rig.account.sign_in("github") == OK
            await entered.wait()  # the sign-in is over, and its listener is being closed ...
            rig.account.cancel()  # ... when Walka closes, and cuts that work off
            asyncio.get_running_loop().call_soon(go.set)
            await rig.account.close()
            assert len(gone) == 1 and our_tasks() == []  # close() saw to it that the listener is gone
            monkeypatch.undo()

    run(scenario)


def test_closing_right_after_sign_out_still_takes_the_key_out_of_the_store(tmp_path):
    async def scenario():
        async with Rig(tmp_path) as rig:
            rig.person()
            await rig.sign_in()
            assert rig.account.sign_out() == OK
            rig.account.cancel()  # in the same breath: the task that forgets has not run yet
            await rig.account.close()
            assert rig.keystore.secrets() == []
            assert ledger.signed_in(rig.book(), rig.origin) is None
            assert our_tasks() == []
            # The courtesy to the accounts is left out: closing never waits for the network.
            await asyncio.sleep(0.05)
            assert rig.asked("/auth/v1/logout") == []

    run(scenario)


def test_switching_off_and_closing_in_the_same_breath_still_writes_off(tmp_path):
    async def scenario():
        async with Rig(tmp_path) as rig:
            user = rig.person()
            await rig.sign_in()
            await rig.switch_on("all")
            assert rig.account_in_book(user)["sending"] is True
            assert rig.account.switch_off() == OK
            rig.account.cancel()  # in the same breath: the task that writes "off" has not run yet
            await rig.account.close()
            assert rig.account_in_book(user)["sending"] is False
            assert our_tasks() == []
            # So at the next start nothing is sent, and nobody is asked.
            rig.walk(3600)
            before = len(rig.fake.requests)
            await rig.restart()
            assert rig.snapshot()["sending"] is False and rig.snapshot()["state"] == "signed_in"
            assert len(rig.fake.requests) == before and rig.fake.walks(user) == []

    run(scenario)


def test_ok_and_closing_in_the_same_breath_and_the_notice_is_not_said_again(tmp_path):
    async def scenario():
        async with Rig(tmp_path) as rig:
            user = rig.person()
            await rig.sign_in()
            rig.fake.delete_user(user)
            assert rig.account.look_again() == OK
            await rig.idle()
            assert rig.snapshot()["notice"] == "ended"
            assert rig.account.notice_ok() == OK
            rig.account.cancel()
            await rig.account.close()
            assert our_tasks() == []
            await rig.restart()
            assert rig.snapshot()["notice"] == ""

    run(scenario)


def test_every_action_answers_at_once(tmp_path):
    async def scenario():
        async with Rig(tmp_path) as rig:
            rig.person()
            rig.walk(3600)
            names = ("sign_in", "sign_out", "look_again", "prepare_question", "switch_on", "switch_off", "notice_ok")
            for name in names + ("walk_saved", "cancel", "snapshot", "in_account", "start", "attach"):
                assert not inspect.iscoroutinefunction(getattr(WalkaAccount, name)), name
            assert inspect.iscoroutinefunction(WalkaAccount.close)

            await rig.sign_in()
            for press in (
                lambda: rig.account.look_again(),
                lambda: rig.account.prepare_question(),
                lambda: rig.account.switch_on(rig.snapshot()["question"]["id"], "all"),
                lambda: rig.account.switch_off(),
                lambda: rig.account.sign_out(),
                lambda: rig.account.sign_in("github"),
            ):
                before, stored, opened = len(rig.fake.requests), list(rig.keystore.calls), len(rig.opened)
                assert press() == OK
                # Back already, and nothing was asked of the accounts, the store or the browser: the work is a task's.
                assert len(rig.fake.requests) == before and rig.keystore.calls == stored
                assert len(rig.opened) == opened
                await rig.idle()
            assert rig.snapshot()["state"] == "signed_in" and len(rig.opened) == 2

    run(scenario)


def test_look_again_and_the_question_are_for_somebody_who_is_signed_in(tmp_path):
    async def scenario():
        async with Rig(tmp_path) as rig:
            assert rig.account.look_again() == NOT_SIGNED_IN
            assert rig.account.prepare_question() == NOT_SIGNED_IN
            assert rig.account.switch_off() == NOT_SIGNED_IN
            assert rig.account.switch_on("x", "all") == NOT_AN_ANSWER
            assert rig.account.notice_ok() == OK
            await rig.idle()
            assert rig.fake.requests == [] and rig.keystore.calls == []
            assert not (rig.dir / ledger.FILE_NAME).exists()

    run(scenario)


def test_a_token_made_anew_for_a_buttons_question_and_refused_at_once_is_not_made_anew_again(tmp_path):
    async def scenario():
        async with Rig(tmp_path) as rig:
            rig.person()
            await rig.sign_in()
            ran_out = {"code": "PGRST303", "details": None, "hint": None, "message": "JWT expired"}

            # Run out by Walka's own clock: the question begins with a new token - which is refused at once.
            await rig.advance(3700)
            rig.fake.fail_next("/rest/v1/profiles", 401, ran_out)
            assert rig.account.look_again() == OK
            await rig.idle()
            assert refreshes(rig) == 1  # never twice for one request
            shown = rig.snapshot()
            assert shown["last"]["word"] == "stopped" and shown["state"] == "signed_in"
            assert rig.account.latch == ""  # a button's failure stops no run

            # Refused while Walka took the token for good: made anew once, and asked once more.
            rig.fake.expire_access_tokens()
            assert rig.account.look_again() == OK
            await rig.idle()
            assert refreshes(rig) == 2 and len(rig.asked("/rest/v1/profiles")) == 4
            # ... and when that one is refused too, that is the end of it.
            rig.fake.fail_next("/rest/v1/profiles", 401, ran_out, times=2)
            assert rig.account.look_again() == OK
            await rig.idle()
            assert refreshes(rig) == 3 and len(rig.asked("/rest/v1/profiles")) == 6

    run(scenario)


def test_a_second_press_while_the_accounts_are_being_asked_asks_nothing_twice(tmp_path):
    async def scenario():
        async with Rig(tmp_path) as rig:
            rig.person()
            await rig.sign_in()
            gate = rig.fake.hold("/rest/v1/profiles")
            before = len(rig.fake.requests)
            assert rig.account.look_again() == OK
            await gate.arrived.wait()
            assert rig.snapshot()["busy"] == "asking"
            busy = (409, {"ok": False, "error": "busy"})
            assert rig.account.look_again() == busy and rig.account.prepare_question() == busy
            gate.release()
            await rig.idle()
            assert len(rig.fake.requests) == before + 1 and rig.snapshot()["busy"] == ""

    run(scenario)


# ---- the snapshot ------------------------------------------------------------------------------


def test_the_snapshots_shape_is_exactly_this_and_it_never_holds_a_key_or_the_accounts_id(tmp_path):
    async def scenario():
        async with Rig(tmp_path) as rig:
            user = rig.person(public=True)

            def look() -> dict:
                shown = rig.snapshot()
                assert set(shown) == SNAPSHOT_KEYS
                assert set(shown["counts"]) == {"waiting", "sent", "aside", "held", "unnamed", "unreadable"}
                assert set(shown["last"]) == {"word", "at", "added"}
                assert shown["question"] is None or set(shown["question"]) == QUESTION_KEYS
                assert shown["state"] in ("unavailable", "signed_out", "signing_in", "signed_in")
                assert shown["store"] in ("lasting", "until_closed")
                assert shown["problem"] in PROBLEMS and shown["last"]["word"] in LAST_WORDS
                assert shown["notice"] in ("", "ended") and shown["chosen"] in ("", "all", "from")
                assert shown["first_step"] in ("unknown", "taken", "not_taken")
                assert shown["busy"] in ("", "asking", "sending")
                assert type(shown["can_send"]) is bool and type(shown["sending"]) is bool
                assert type(shown["rev"]) is int
                text = json.dumps(shown, allow_nan=False)  # ready-made words and numbers: it can be sent as it is
                assert user not in text and rig.fake.key not in text
                for key in rig.needles() | {rig.account.access_token() or "no token"}:
                    assert key not in text
                return shown

            first = look()
            assert first == {
                "state": "signed_out",
                "host": rig.fake.origin.split("//")[1],
                "store": "lasting",
                "problem": "",
                "notice": "",
                "email": "",
                "way": "",
                "first_step": "unknown",
                "can_send": False,
                "sending": False,
                "chosen": "",
                "from": None,
                "busy": "",
                "counts": {"waiting": 0, "sent": 0, "aside": 0, "held": 0, "unnamed": 0, "unreadable": 0},
                "last": {"word": "", "at": 0.0, "added": 0},
                "aside": [],
                "held": [],
                "question": None,
                "rev": first["rev"],
            }
            assert rig.snapshot() is first  # cheap: made anew only when something changed
            record = rig.walk(3600)
            assert rig.snapshot() is not first  # ... a saved walk is such a change
            rig.walk(1800, steps=500_000)
            assert rig.account.sign_in("github") == OK
            look()
            await rig.idle()
            signed_in = look()
            assert signed_in["rev"] > first["rev"]
            await rig.question()
            look()
            await rig.switch_on("from_now_on")
            assert look()["from"] == rig.clock.wall()
            await rig.switch_on("all")
            sent = look()
            assert sent["rev"] > signed_in["rev"]  # the page's list of walks asks anew when this moves
            assert rig.account.in_account(record.id, record.start) is True
            rig.fake.sign_out_everywhere(user)
            await rig.advance(3700)
            rig.walk(60)
            await rig.saved()
            assert look()["notice"] == "ended"

    run(scenario)


def test_in_account_says_whether_a_copy_is_in_the_account_that_is_signed_in(tmp_path):
    async def scenario():
        async with Rig(tmp_path) as rig:
            rig.person()
            record = rig.walk(3600)
            assert rig.account.in_account(record.id, record.start) is False
            await rig.sign_in()
            await rig.switch_on("all")
            assert rig.account.in_account(record.id, record.start) is True
            assert rig.account.in_account(record.id, int(record.start)) is (record.start == int(record.start))
            assert rig.account.in_account(record.id, record.start + 1) is False  # the same id, another walk
            for junk in ((None, None), (record.id, "x"), (7, 7.0), (record.id, float("nan")), (record.id, 10**400)):
                assert rig.account.in_account(*junk) is False
            # An entry stays when its walk is deleted on the computer - and is found again after a start.
            rig.store.delete(record.id)
            await rig.restart()
            assert rig.account.in_account(record.id, record.start) is True
            assert rig.snapshot()["counts"]["sent"] == 0  # (it counts the walks that are still here)
            assert rig.account.sign_out() == OK
            assert rig.account.in_account(record.id, record.start) is False

    run(scenario)


# ---- the file ----------------------------------------------------------------------------------


def test_a_file_of_a_newer_walka_is_left_alone_and_the_part_stays_off(tmp_path):
    async def scenario():
        async with Rig(tmp_path, start=False) as rig:
            rig.person()
            newer = json.dumps({"version": 2, "addresses": {}, "something": "new"}).encode()
            (rig.dir / ledger.FILE_NAME).write_bytes(newer)
            rig.walk(3600)
            await rig.restart()
            assert rig.account.usable is False and rig.snapshot()["state"] == "unavailable"
            assert rig.account.sign_in("github") == (409, {"ok": False, "error": "not available"})
            await rig.saved()
            assert rig.opened == [] and rig.fake.requests == [] and rig.keystore.calls == []
            assert (rig.dir / ledger.FILE_NAME).read_bytes() == newer

    run(scenario)


def test_switching_off_when_the_file_cannot_be_written_takes_the_sign_in_away_instead(tmp_path, monkeypatch):
    async def scenario():
        async with Rig(tmp_path) as rig:
            user = rig.person()
            await rig.sign_in()
            await rig.switch_on("all")

            def full_disk(self, fn):
                raise ledger.LedgerError("cannot_write")

            monkeypatch.setattr(ledger.Ledger, "change", full_disk)
            assert rig.account.switch_off() == OK
            assert rig.snapshot()["sending"] is False
            await rig.idle()
            monkeypatch.undo()
            # The file still says "on". So the key itself is gone: nothing can be sent, now or after a start.
            assert rig.account_in_book(user)["sending"] is True
            assert rig.keystore.secrets() == [] and rig.account.access_token() is None
            rig.walk(3600)
            await rig.saved()
            before = len(rig.fake.requests)
            await rig.restart()
            assert rig.fake.walks(user) == [] and len(rig.fake.requests) == before
            assert rig.snapshot()["state"] == "signed_out" and rig.snapshot()["sending"] is False

    run(scenario)


def test_the_words_are_from_fixed_lists(tmp_path):
    async def scenario():
        async with Rig(tmp_path) as rig:
            rig.account.say("a word nobody chose: " + rig.fake.key)
            assert rig.snapshot()["last"]["word"] == "stopped"
            rig.account.stop_run("https://somewhere.example/?code=1")
            assert rig.snapshot()["last"]["word"] == "stopped" and rig.account.latch == "stopped"
            rig.account._problem = "an error's own text"
            rig.account._touch()
            assert rig.snapshot()["problem"] == ""

    run(scenario)


# ---- the house rules, looked for in the source ---------------------------------------------------


@pytest.mark.parametrize("module", [sender, service])
def test_nothing_is_logged_with_an_errors_own_words_and_no_clock_is_read_behind_the_rig(module):
    source = inspect.getsource(module)
    code = "\n".join(line.split("#")[0] for line in source.splitlines())
    for never in ("log.exception", "exc_info", "%r", "str(error)", "repr(", "log_event", "subprocess", "os.system"):
        assert never not in code, never
    for never in ("os.environ", "getenv", "webbrowser", "print("):
        assert never not in code, never
    # Every time comes from the clocks that were handed in: these two stand only as the defaults of parameters.
    assert code.count("time.time") == code.count("= time.time,") and code.count("time.time") <= 1
    assert code.count("time.monotonic") == code.count("= time.monotonic,") and code.count("time.monotonic") <= 1
    assert "asyncio.sleep" not in code and "call_later" not in code and "call_at" not in code
    # Every log line is a fixed sentence with, at most, the kind of an error or a word of Walka's own.
    for line in code.splitlines():
        if "log." in line and "(" in line and "%s" in line:
            assert "type(error).__name__" in source[source.index(line.strip()) :][:400] or "error.code" in line, line


def test_the_sender_waits_only_through_the_wait_it_was_handed():
    code = "\n".join(line.split("#")[0] for line in inspect.getsource(sender).splitlines())
    assert "wait_for" not in code and ".wait()" not in code and "asyncio.wait" not in code
    assert code.count("await service.wait(") == 1


def test_the_other_walka_signs_out_while_this_one_renews_the_key_and_no_key_is_left_behind(tmp_path):
    async def scenario():
        async with Rig(tmp_path, start=False) as rig:
            user = rig.person()
            await rig.sign_in()
            await rig.switch_on("all")
            rig.walk(3600)
            rig.clock.advance(3700)
            gate = rig.fake.hold("/auth/v1/token", after=True)
            going = asyncio.ensure_future(rig.account.sender.one_pass())
            await gate.arrived.wait()
            # The other Walka on this data folder signs out: the file says so, and the entry leaves the store.
            ledger.Ledger(rig.dir).change(lambda book: ledger.sign_out(book, rig.origin))
            rig.keystore.delete(rig.entry)
            gate.release()
            assert await going == "dropped"
            await rig.idle()
            # This Walka had just written the renewed key: it takes it out again.
            assert rig.keystore.secrets() == []
            assert rig.pushes() == [] and rig.fake.walks(user) == []
            assert rig.snapshot()["state"] == "signed_out" and rig.account.access_token() is None

    run(scenario)


def test_a_file_that_cannot_be_written_means_no_sign_in_and_the_key_leaves_the_store_again(tmp_path, monkeypatch):
    async def scenario():
        async with Rig(tmp_path) as rig:
            rig.person()

            def full_disk(self, fn):
                raise ledger.LedgerError("cannot_write")

            monkeypatch.setattr(ledger.Ledger, "change", full_disk)
            assert rig.account.sign_in("github") == OK
            await rig.idle()
            monkeypatch.undo()
            assert signin.PAGE_FAILED in rig.pages[0][2]
            shown = rig.snapshot()
            assert (shown["state"], shown["problem"]) == ("signed_out", "no_store")
            assert rig.keystore.secrets() == [] and rig.account.access_token() is None
            assert [what for what, _name in rig.keystore.calls] == ["write", "delete"]

    run(scenario)


def test_a_file_that_says_sending_is_on_for_an_account_without_an_e_mail_address_is_not_believed(tmp_path):
    async def scenario():
        async with Rig(tmp_path, start=False) as rig:
            user = rig.fake.add_user("")
            rig.people[user] = "walker@mail.example"  # (no address to look for in the log)
            await rig.sign_in()
            rig.walk(3600)
            chosen = {"what": "all", "at": 5.0}
            ledger.Ledger(rig.dir).change(lambda book: ledger.set_sending(book, rig.origin, user, True, chosen))
            await rig.restart(start=False)
            before = len(rig.fake.requests)
            shown = rig.snapshot()
            assert shown["state"] == "signed_in" and shown["sending"] is False and shown["can_send"] is False
            assert await a_pass(rig) == "nothing"
            assert len(rig.fake.requests) == before and rig.fake.walks(user) == []

    run(scenario)
