"""Sending walks to a Walka account (walka_account.sender), tried against the stand-in for the accounts.

Every answer of the brief's 5.3 through a whole pass; the three quiet
cases, where not one request may be made, the system's store may not be
looked into and nothing may wait on a clock; and what is never sent.

After EVERY test the rig's fixture looks again: nothing outside the list
of what Walka may ask, no key of a sign-in in any file, in the snapshot
or in the log, no task left with an error nobody read.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import uuid
from datetime import UTC, datetime

import pytest

import walka_account_rig
from fake_accounts import ADULTS, DAYS_TO_COME, PREFER
from walka import __version__
from walka.treadmill import simulated
from walka.walka_account import calls, ledger, sender
from walka.walka_account import row as row_part
from walka.walka_account.https import NetError
from walka.walka_account.row import ROW_KEYS
from walka.walka_account.sender import BATCH, MADE_UP, PAUSES_S, read_walks
from walka_account_rig import OK, PAD, Rig, run


@pytest.fixture(autouse=True)
def nothing_outside_the_list_and_nothing_told(caplog):
    walka_account_rig.before_test(caplog)
    yield
    walka_account_rig.after_test(caplog)


def refusal(code: str, message: str, details: str | None = None) -> dict:
    """A refusal in the database's own shape."""
    return {"code": code, "details": details, "hint": None, "message": message}


def utc_text(start: float) -> str:
    return datetime.fromtimestamp(start, UTC).isoformat()


async def sending(rig: Rig, send: str = "all", **person) -> str:
    """Somebody signs in and switches sending on. Gives the Walka account's id."""
    user = rig.person(**person)
    await rig.sign_in()
    await rig.switch_on(send)
    assert rig.snapshot()["sending"] is True
    return user


async def a_pass(rig: Rig) -> str:
    """One pass, made by hand (the rig was not started): what it came to."""
    outcome = await rig.account.sender.one_pass()
    await rig.idle()
    return outcome


def refreshes(rig: Rig) -> int:
    return sum(1 for seen in rig.asked("/auth/v1/token") if ("grant_type", "refresh_token") in seen.query)


# ---- a walk goes -------------------------------------------------------------------------------


def test_walks_are_sent_and_what_became_of_them_is_written_down(tmp_path):
    async def scenario():
        async with Rig(tmp_path, start=False) as rig:
            user = await sending(rig)
            first, second = rig.walk(7200), rig.walk(3600)
            before = len(rig.fake.requests)
            assert await a_pass(rig) == "sent"

            asked = [(seen.method, seen.path) for seen in rig.fake.requests[before:]]
            # The database's version first, once; then the walks in one request. Nothing else.
            assert asked == [("POST", "/rest/v1/rpc/schema_version"), ("POST", "/rest/v1/walks")]
            push = rig.asked("/rest/v1/walks", method="POST")[0]
            assert push.header("Prefer") == PREFER
            assert push.query == [("on_conflict", "user_id,start_key"), ("select", "id")]

            there = {walk["client_id"]: walk for walk in rig.fake.walks(user)}
            assert set(there) == {first.id, second.id}
            for record in (first, second):
                entry = rig.entry_of(user, record)
                assert entry == {"state": "added", "row": there[record.id]["id"], "at": rig.clock.wall()}
                assert rig.account.in_account(record.id, record.start) is True
            shown = rig.snapshot()
            assert shown["last"] == {"word": "sent", "at": rig.clock.wall(), "added": 2}
            assert shown["counts"] == {"waiting": 0, "sent": 2, "aside": 0, "held": 0, "unnamed": 0, "unreadable": 0}
            assert shown["busy"] == ""

            # Nothing waits any more: a second pass asks nobody, and the version is not asked twice in a run.
            before = len(rig.fake.requests)
            assert await a_pass(rig) == "nothing"
            rig.walk(1800)
            assert await a_pass(rig) == "sent"
            assert [seen.path for seen in rig.fake.requests[before:]] == ["/rest/v1/walks"]

    run(scenario)


def test_a_row_is_exactly_the_sixteen_keys_and_nothing_of_what_stays_here(tmp_path):
    async def scenario():
        async with Rig(tmp_path, start=False) as rig:
            await sending(rig)
            record = rig.walk(3600, avg_hr=131.5, max_hr=158, source="wow", game_session=7, character="Tester")
            assert sorted(json.loads(rig.file_of(record).read_text("utf-8"))) == sorted(
                "id start end seconds distance_m steps kcal kcal_extra max_kmh avg_hr max_hr source game_session "
                "character steps_source distance_source calories_source treadmill samples".split()
            )  # the nineteen keys of a walk's file
            assert await a_pass(rig) == "sent"

            push = rig.asked("/rest/v1/walks", method="POST")[0]
            (sent,) = push.json
            assert tuple(sent) == ROW_KEYS and len(ROW_KEYS) == 16
            assert uuid.UUID(sent["id"]).version == 4  # random: never worked out from the walk
            assert sent["client_id"] == record.id and sent["origin"] == "walka"
            assert sent["started_at"] == utc_text(record.start)[:19] + ".000Z"
            assert sent["session"] == "wow" and type(sent["steps"]) is int
            # (Looked for in the row without its id: that one is random, and one in two hundred holds "158".
            # The row is the whole request - see "(sent,) = push.json" - and its keys are exactly the sixteen.)
            body = json.dumps({name: value for name, value in sent.items() if name != "id"})
            # The treadmill's name, the heart rate, the top speed, the samples and the character stay on the computer.
            for stays in (PAD, "treadmill", "samples", "max_kmh", "avg_hr", "max_hr", "131.5", "158", "game_session", "Tester"):
                assert stays not in body

    run(scenario)


def test_batches_of_200_and_201(tmp_path):
    async def scenario():
        async with Rig(tmp_path, start=False) as rig:
            user = await sending(rig)
            for number in range(BATCH):
                rig.walk(1000 + 100 * number, seconds=30.0)
            assert await a_pass(rig) == "sent"
            assert [len(rows) for rows in rig.pushes()] == [200]

            for number in range(BATCH + 1):
                rig.walk(100_000 + 100 * number, seconds=30.0)
            assert await a_pass(rig) == "sent"
            assert [len(rows) for rows in rig.pushes()] == [200, 200, 1]
            assert len(rig.fake.walks(user)) == 401
            assert rig.snapshot()["last"]["added"] == 201
            ids = [sent["id"] for rows in rig.pushes() for sent in rows]
            assert len(set(ids)) == 401  # an id of its own for every row

    run(scenario)


# ---- every answer of 5.3 -----------------------------------------------------------------------


def test_a_token_that_runs_out_mid_send_is_made_anew_once_and_the_walk_goes(tmp_path):
    async def scenario():
        async with Rig(tmp_path, start=False) as rig:
            user = await sending(rig)
            rig.walk(7200)
            assert await a_pass(rig) == "sent"
            rig.fake.expire_access_tokens()  # the accounts say so; by Walka's own clock the token is still good
            rig.walk(3600)
            assert await a_pass(rig) == "sent"
            assert refreshes(rig) == 1
            assert len(rig.pushes()) == 3  # the second walk went twice: refused, then with the new token
            assert len(rig.fake.walks(user)) == 2

    run(scenario)


def test_a_token_refused_twice_running_stops_the_run_and_sets_nothing_aside(tmp_path):
    async def scenario():
        async with Rig(tmp_path, start=False) as rig:
            user = await sending(rig)
            rig.walk(3600)
            ran_out = refusal("PGRST303", "JWT expired")
            rig.fake.fail_next("/rest/v1/walks", 401, ran_out, times=2, method="POST")
            assert await a_pass(rig) == "stopped"
            assert refreshes(rig) == 1  # never twice for one request
            assert rig.account_in_book(user)["walks"] == {}
            assert rig.snapshot()["last"]["word"] == "stopped"
            assert rig.snapshot()["state"] == "signed_in" and rig.snapshot()["sending"] is True

    run(scenario)


@pytest.mark.parametrize("code", ["refresh_token_not_found", "refresh_token_already_used"])
def test_a_refresh_that_is_refused_ends_the_sign_in_and_touches_no_walk(tmp_path, code):
    async def scenario():
        async with Rig(tmp_path, start=False) as rig:
            user = await sending(rig)
            rig.walk(3600)
            if code == "refresh_token_not_found":
                rig.fake.sign_out_everywhere(user)
            else:
                # The stored key is used twice elsewhere: it is two changes old when Walka comes with it.
                rig.fake.reuse_window_s = 0
                (stored,) = rig.keystore.secrets()
                once = await calls.refresh(rig.account.client, rig.fake.key, refresh_token=stored)
                await calls.refresh(rig.account.client, rig.fake.key, refresh_token=once.refresh_token)
            asked_before = refreshes(rig)
            rig.clock.advance(3700)  # the access token has run out by Walka's own clock
            files = rig.walk_files()

            assert await a_pass(rig) == "over"

            assert refreshes(rig) == asked_before + 1
            assert rig.pushes() == []
            shown = rig.snapshot()
            assert (shown["state"], shown["notice"], shown["sending"], shown["problem"]) == (
                "signed_out",
                "ended",
                False,
                "",
            )
            assert shown["email"] == ""
            assert rig.keystore.secrets() == []  # the key is forgotten
            place = rig.book()["addresses"][rig.origin.text]
            assert place["signed_in"] is None and place["notice"] == "ended"
            assert place["accounts"][user]["sending"] is False
            assert rig.walk_files() == files  # everything on the computer is untouched
            assert rig.opened == rig.opened[:1]  # and the browser is not opened by itself
            # Nothing more is asked, whatever is saved.
            before = len(rig.fake.requests)
            rig.walk(60)
            assert await a_pass(rig) == "nothing"
            assert len(rig.fake.requests) == before

    run(scenario)


def test_an_account_that_is_not_open_is_asked_who_is_there_and_the_run_stops(tmp_path):
    async def scenario():
        async with Rig(tmp_path, start=False) as rig:
            user = await sending(rig)
            rig.fake._users[user].profile["adult_confirmed_at"] = None  # the first step is taken back
            rig.walk(3600)
            before = len(rig.fake.requests)
            assert await a_pass(rig) == "stopped"
            paths = [seen.path for seen in rig.fake.requests[before:]]
            # Refused whole with "Walka is for adults.": not sent row by row, but one question - who is there?
            assert paths == ["/rest/v1/rpc/schema_version", "/rest/v1/walks", "/auth/v1/user"]
            shown = rig.snapshot()
            assert shown["last"]["word"] == "not_open" and shown["first_step"] == "not_taken"
            assert shown["state"] == "signed_in" and shown["sending"] is True and shown["notice"] == ""
            assert rig.account_in_book(user)["walks"] == {}
            # This run is stopped: a walk saved later asks nobody.
            rig.walk(60)
            before = len(rig.fake.requests)
            assert await a_pass(rig) == "stopped"
            assert len(rig.fake.requests) == before

    run(scenario)


def test_an_account_that_is_gone_is_told_from_one_that_is_new_and_the_sign_in_ends(tmp_path):
    async def scenario():
        async with Rig(tmp_path, start=False) as rig:
            user = await sending(rig)
            rig.walk(3600)
            files = rig.walk_files()
            rig.fake.delete_user(user)  # its access token still opens the database until it runs out
            assert await a_pass(rig) == "over"
            assert rig.asked("/rest/v1/walks", method="POST")[0].json  # it was refused: "Walka is for adults."
            assert len(rig.asked("/auth/v1/user")) == 1
            shown = rig.snapshot()
            assert (shown["state"], shown["notice"], shown["sending"]) == ("signed_out", "ended", False)
            assert shown["first_step"] == "unknown"  # never "no Walka account yet" for one that was deleted
            assert rig.keystore.secrets() == []
            assert rig.walk_files() == files

    run(scenario)


def test_walka_is_for_adults_from_a_sign_in_that_was_ended_elsewhere_ends_the_sign_in_here(tmp_path):
    async def scenario():
        async with Rig(tmp_path, start=False) as rig:
            user = await sending(rig)
            rig.walk(3600)
            files = rig.walk_files()
            rig.fake._users[user].profile["adult_confirmed_at"] = None  # the first step is taken back ...
            rig.fake.sign_out_everywhere(user)  # ... and the sign-in was ended: the access token runs on for a while
            assert await a_pass(rig) == "over"
            assert rig.asked("/rest/v1/walks", method="POST")[0].json  # it was refused: "Walka is for adults."
            assert len(rig.asked("/auth/v1/user")) == 1  # who is there? Nobody: this sign-in is over
            shown = rig.snapshot()
            assert (shown["state"], shown["notice"], shown["sending"]) == ("signed_out", "ended", False)
            assert shown["first_step"] == "unknown"  # not "no Walka account yet": nobody is signed in
            assert shown["last"]["word"] != "not_open"
            assert rig.keystore.secrets() == []
            assert rig.walk_files() == files

    run(scenario)


def test_a_full_account_stops_the_run(tmp_path):
    async def scenario():
        async with Rig(tmp_path, start=False) as rig:
            user = await sending(rig)
            rig.fake.full_at = 1
            rig.walk(7200), rig.walk(3600)
            assert await a_pass(rig) == "stopped"
            assert rig.snapshot()["last"]["word"] == "full"
            assert len(rig.pushes()) == 1  # not row by row
            assert rig.fake.walks(user) == [] and rig.account_in_book(user)["walks"] == {}
            before = len(rig.fake.requests)
            rig.walk(60)
            assert await a_pass(rig) == "stopped"
            assert len(rig.fake.requests) == before

    run(scenario)


def test_one_bad_row_in_a_batch_is_found_one_by_one_and_set_aside(tmp_path, monkeypatch):
    async def scenario():
        # A rule of the database that this Walka does not know of: its own look at the row finds nothing.
        monkeypatch.setattr(row_part, "outside", lambda row, now_ms: None)
        async with Rig(tmp_path, start=False) as rig:
            user = await sending(rig)
            good, bad, fine = rig.walk(10800), rig.walk(7200, distance_m=250_000.0), rig.walk(3600)
            assert await a_pass(rig) == "sent"

            assert [len(rows) for rows in rig.pushes()] == [3, 1, 1, 1]  # refused whole, then one by one
            assert {walk["client_id"] for walk in rig.fake.walks(user)} == {good.id, fine.id}
            entry = rig.entry_of(user, bad)
            assert entry == {
                "state": "aside",
                "code": "23514",
                "message": 'new row for relation "walks" violates check constraint "walks_distance_m_check"',
                "version": __version__,
            }
            assert rig.entry_of(user, good)["state"] == "added" and rig.entry_of(user, fine)["state"] == "added"
            shown = rig.snapshot()
            assert shown["counts"] == {"waiting": 0, "sent": 2, "aside": 1, "held": 0, "unnamed": 0, "unreadable": 0}
            assert shown["aside"] == [{"id": bad.id, "start": bad.start, "code": "23514", "message": entry["message"]}]
            assert shown["last"] == {"word": "sent", "at": rig.clock.wall(), "added": 2}
            assert rig.account.in_account(bad.id, bad.start) is False
            # The database showed the whole row, the Walka account's id among it: none of that is kept.
            assert user not in rig.files_text().replace(f'"{user}":', "").replace(f'"signed_in":"{user}"', "")
            # The walk itself is not cut down to fit.
            assert json.loads(rig.file_of(bad).read_text("utf-8"))["distance_m"] == 250_000.0

            # This version of Walka does not send it again: not now, and not after a start.
            before = len(rig.fake.requests)
            assert await a_pass(rig) == "nothing"
            await rig.restart(start=False)
            assert await a_pass(rig) == "nothing"
            assert len(rig.fake.requests) == before
            assert rig.snapshot()["counts"]["aside"] == 1

            # Another version of Walka tries it again - and sets it aside again. Nothing went: not "sent".
            await rig.restart(start=False, version="9.9.9")
            assert rig.snapshot()["counts"]["waiting"] == 1
            assert await a_pass(rig) == "nothing"
            assert [len(rows) for rows in rig.pushes()][4:] == [1]  # alone, so it is not asked twice
            assert rig.entry_of(user, bad)["version"] == "9.9.9"
            assert rig.snapshot()["last"]["word"] == "nothing"

    run(scenario)


def test_a_pass_that_only_sets_a_walk_aside_does_not_say_sent(tmp_path, monkeypatch):
    async def scenario():
        monkeypatch.setattr(row_part, "outside", lambda row, now_ms: None)
        async with Rig(tmp_path) as rig:
            user = await sending(rig)
            # The ordinary case: one walk is saved, and the Walka account does not take it.
            bad = rig.walk(3600, distance_m=250_000.0)
            await rig.saved()
            assert [len(rows) for rows in rig.pushes()] == [1]
            assert rig.entry_of(user, bad)["state"] == "aside" and rig.fake.walks(user) == []
            shown = rig.snapshot()
            # Nothing is in the Walka account: the card must not say "Sent." above "0 sent".
            assert shown["last"]["word"] != "sent" and shown["last"] == {"word": "nothing", "at": rig.clock.wall(), "added": 0}
            assert (shown["counts"]["sent"], shown["counts"]["aside"], shown["counts"]["waiting"]) == (0, 1, 0)
            assert [told["id"] for told in shown["aside"]] == [bad.id]

            # A walk that is there already did go, as far as the person is concerned: that is "sent", though none was added.
            there = rig.walk(1800)
            rig.fake.add_walk(user, utc_text(there.start))
            await rig.saved()
            assert rig.snapshot()["last"] == {"word": "sent", "at": rig.clock.wall(), "added": 0}

    run(scenario)


# ---- one by one: what each single request can be answered ------------------------------------------


def test_one_by_one_a_walk_that_is_there_already_is_written_as_there_and_looked_up_once(tmp_path, monkeypatch):
    async def scenario():
        monkeypatch.setattr(row_part, "outside", lambda row, now_ms: None)
        async with Rig(tmp_path, start=False) as rig:
            user = await sending(rig)
            there, bad, fine = rig.walk(10800), rig.walk(7200, distance_m=250_000.0), rig.walk(3600)
            row_there = rig.fake.add_walk(user, utc_text(there.start))  # from the website, say
            assert await a_pass(rig) == "sent"
            assert [len(rows) for rows in rig.pushes()] == [3, 1, 1, 1]  # refused whole, then one by one
            assert rig.entry_of(user, there) == {"state": "there", "row": row_there, "deleted": False}
            assert rig.entry_of(user, bad)["state"] == "aside"
            assert rig.entry_of(user, fine)["state"] == "added"
            assert len(rig.asked("/rest/v1/walks", method="GET")) == 1  # one look-up, for the one that did not come back
            shown = rig.snapshot()
            assert shown["counts"]["aside"] == 1 and shown["counts"]["sent"] == 2
            assert shown["last"]["added"] == 1

    run(scenario)


def test_one_by_one_a_walk_that_is_there_already_is_not_shown_as_set_aside_when_the_look_up_fails(
    tmp_path, monkeypatch
):
    async def scenario():
        monkeypatch.setattr(row_part, "outside", lambda row, now_ms: None)
        async with Rig(tmp_path, start=False) as rig:
            user = await sending(rig)
            there, bad, _fine = rig.walk(10800), rig.walk(7200, distance_m=250_000.0), rig.walk(3600)
            rig.fake.add_walk(user, utc_text(there.start))
            rig.fake.fail_next("/rest/v1/walks", 500, None, method="GET")
            assert await a_pass(rig) == "sent"
            # What the single request said stands by itself: "there", its row unknown - never "the account did not take it".
            assert rig.entry_of(user, there) == {"state": "there", "row": None, "deleted": False}
            shown = rig.snapshot()
            assert shown["counts"]["aside"] == 1
            assert [told["id"] for told in shown["aside"]] == [bad.id]

    run(scenario)


@pytest.mark.parametrize(
    ("kind", "word", "limits"),
    [("500", "later", [PAUSES_S[0]]), ("no answer", "later", [PAUSES_S[0]]), ("a gateway's page", "stopped", [])],
)
def test_one_by_one_a_single_request_that_fails_ends_the_pass_and_sets_nothing_more_aside(
    tmp_path, monkeypatch, kind, word, limits
):
    async def scenario():
        monkeypatch.setattr(row_part, "outside", lambda row, now_ms: None)
        async with Rig(tmp_path) as rig:
            user = await sending(rig)
            bad, good, fine = rig.walk(10800, distance_m=250_000.0), rig.walk(7200), rig.walk(3600)
            mark = rig.wait.mark()
            # Each request is held when it arrives, so that the next one's answer can be set before it is asked.
            batch = rig.fake.hold("/rest/v1/walks", method="POST")
            rig.account.walk_saved()
            await batch.arrived.wait()  # the three together: refused whole
            first = rig.fake.hold("/rest/v1/walks", method="POST")
            batch.release()
            await first.arrived.wait()  # the bad one, alone: refused, set aside
            second = rig.fake.hold("/rest/v1/walks", method="POST")
            first.release()
            await second.arrived.wait()  # a good one, alone - and this request fails
            if kind == "500":
                rig.fake.fail_next("/rest/v1/walks", 500, refusal("XX000", "internal error"), method="POST")
            elif kind == "no answer":
                rig.fake.drop_next("/rest/v1/walks", method="POST")
            else:
                rig.fake.fail_next("/rest/v1/walks", 403, b"<html>the gateway's own page</html>", method="POST")
            second.release()
            await rig.idle()

            assert [len(rows) for rows in rig.pushes()] == [3, 1, 1]  # the pass ends there: the third is not asked
            assert rig.entry_of(user, bad)["state"] == "aside"
            assert rig.entry_of(user, good) is None and rig.entry_of(user, fine) is None  # they wait
            assert rig.fake.walks(user) == []
            shown = rig.snapshot()
            assert shown["last"]["word"] == word  # never "sent": nothing went
            assert (shown["counts"]["aside"], shown["counts"]["waiting"]) == (1, 2)
            assert rig.wait.limits(mark) == limits  # "later" waits for another try; "stopped" does not

    run(scenario)


def test_one_by_one_a_walk_deleted_meanwhile_is_not_sent_and_not_looked_up(tmp_path, monkeypatch):
    async def scenario():
        monkeypatch.setattr(row_part, "outside", lambda row, now_ms: None)
        async with Rig(tmp_path, start=False) as rig:
            user = await sending(rig)
            _bad, gone, fine = rig.walk(10800, distance_m=250_000.0), rig.walk(7200), rig.walk(3600)
            batch = rig.fake.hold("/rest/v1/walks", method="POST")
            going = asyncio.ensure_future(rig.account.sender.one_pass())
            await batch.arrived.wait()
            first = rig.fake.hold("/rest/v1/walks", method="POST")
            batch.release()
            await first.arrived.wait()  # the bad one, alone
            assert rig.store.delete(gone.id) is True  # on the dashboard, before its own turn comes
            first.release()
            assert await going == "sent"
            await rig.idle()
            assert [len(rows) for rows in rig.pushes()] == [3, 1, 1]  # no request for the deleted one
            assert rig.asked("/rest/v1/walks", method="GET") == []  # and the Walka account is not asked about it
            assert [walk["client_id"] for walk in rig.fake.walks(user)] == [fine.id]
            assert rig.entry_of(user, gone) is None

    run(scenario)


def test_an_id_that_is_taken_gets_a_new_one_and_one_more_try(tmp_path):
    async def scenario():
        async with Rig(tmp_path, start=False) as rig:
            user = await sending(rig)
            record = rig.walk(3600)
            taken = refusal("23505", 'duplicate key value violates unique constraint "walks_pkey"', "Key (id)=(x)")
            rig.fake.fail_next("/rest/v1/walks", 409, taken, method="POST")
            assert await a_pass(rig) == "sent"
            first, second = (rows[0]["id"] for rows in rig.pushes())
            assert first != second
            assert rig.entry_of(user, record) == {"state": "added", "row": second, "at": rig.clock.wall()}

            # Taken twice running: not for good, and not written down. The walk goes at a later start.
            other = rig.walk(1800)
            rig.fake.fail_next("/rest/v1/walks", 409, taken, times=2, method="POST")
            assert await a_pass(rig) == "nothing"
            assert len(rig.pushes()) == 4
            assert rig.entry_of(user, other) is None and rig.snapshot()["counts"]["aside"] == 0
            before = len(rig.fake.requests)
            assert await a_pass(rig) == "nothing"  # not again in this run
            assert len(rig.fake.requests) == before
            await rig.restart(start=False)
            assert await a_pass(rig) == "sent"
            assert rig.entry_of(user, other)["state"] == "added"

    run(scenario)


def test_a_walk_in_the_days_to_come_is_not_written_down_and_goes_in_a_later_run(tmp_path):
    async def scenario():
        # The accounts' clock is three days behind this computer's: to them, an hour ago is the day after tomorrow.
        async with Rig(tmp_path, start=False, accounts_behind_s=3 * 86400.0) as rig:
            user = await sending(rig)
            old, new = rig.walk(5 * 86400.0), rig.walk(3600)
            assert await a_pass(rig) == "sent"
            assert [len(rows) for rows in rig.pushes()] == [2, 1, 1]
            assert [walk["client_id"] for walk in rig.fake.walks(user)] == [old.id]
            assert rig.entry_of(user, new) is None  # not set aside, not written at all
            shown = rig.snapshot()
            assert shown["counts"]["aside"] == 0 and shown["counts"]["waiting"] == 1 and shown["aside"] == []
            before = len(rig.fake.requests)
            assert await a_pass(rig) == "nothing"  # not again in this run
            assert len(rig.fake.requests) == before

            rig.accounts_behind_s = 0.0
            await rig.restart(start=False)
            assert await a_pass(rig) == "sent"
            assert rig.entry_of(user, new)["state"] == "added"

    run(scenario)


def test_the_days_to_come_message_is_the_one_the_accounts_send():
    assert calls.DAYS_TO_COME == DAYS_TO_COME and calls.ADULTS == ADULTS


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (400, refusal("PGRST102", "Empty or invalid json")),
        (400, refusal("PGRST204", "Could not find the 'device' column of 'walks' in the schema cache")),
        (401, refusal("PGRST301", "No suitable key or wrong key type")),
        (403, refusal("42501", "permission denied for table walks")),
        (404, refusal("PGRST205", "Could not find the table 'public.walks' in the schema cache")),
        (403, b"<html><body><h1>403 Forbidden</h1>the gateway's own page</body></html>"),
        (200, []),
        (302, None),
    ],
)
def test_an_answer_that_says_nothing_about_a_walk_stops_the_run_and_sets_nothing_aside(tmp_path, status, body):
    async def scenario():
        async with Rig(tmp_path) as rig:
            user = await sending(rig)
            first = rig.walk(3600)
            rig.fake.fail_next("/rest/v1/walks", status, body, method="POST")
            await rig.saved()
            shown = rig.snapshot()
            assert shown["last"]["word"] == "stopped" and shown["counts"]["aside"] == 0
            assert shown["counts"]["waiting"] == 1
            assert rig.account_in_book(user)["walks"] == {}
            assert shown["state"] == "signed_in" and shown["sending"] is True

            # A walk saved later in the same run causes NO request - and nothing waits on a clock.
            before, mark = len(rig.fake.requests), rig.wait.mark()
            second = rig.walk(60)
            await rig.saved()
            await rig.advance(7200)
            await rig.saved()
            assert len(rig.fake.requests) == before
            assert rig.wait.limits(mark) == []

            # At the next start it is tried again.
            await rig.restart()
            assert {walk["client_id"] for walk in rig.fake.walks(user)} == {first.id, second.id}

    run(scenario)


def test_no_answer_means_later_with_growing_pauses_asked_of_the_one_wait(tmp_path):
    async def scenario():
        async with Rig(tmp_path) as rig:
            user = await sending(rig)
            record = rig.walk(3600)
            mark = rig.wait.mark()
            rig.fake.fail_next("/rest/v1/walks", 500, refusal("XX000", "internal error"), times=2, method="POST")
            await rig.saved()
            assert rig.snapshot()["last"]["word"] == "later"
            assert rig.wait.limits(mark) == [PAUSES_S[0]]
            rig.wait.time_is_up()
            await rig.idle()
            assert rig.wait.limits(mark) == [PAUSES_S[0], PAUSES_S[1]]
            rig.fake.drop_next("/rest/v1/walks", method="POST")  # no answer at all
            rig.wait.time_is_up()
            await rig.idle()
            assert rig.wait.limits(mark) == list(PAUSES_S[:3]) == [60.0, 120.0, 300.0]
            assert rig.account_in_book(user)["walks"] == {} and rig.snapshot()["counts"]["aside"] == 0

            rig.wait.time_is_up()
            await rig.idle()
            assert rig.entry_of(user, record)["state"] == "added"
            assert rig.snapshot()["last"]["word"] == "sent"
            # It went: no pause is kept, and the count of tries begins anew.
            assert rig.wait.limits(mark) == [60.0, 120.0, 300.0]
            assert [call.limit for call in rig.wait.waiting()] == [None]
            rig.walk(60)
            rig.fake.fail_next("/rest/v1/walks", 503, b"<html>busy</html>", method="POST")
            await rig.saved()
            assert rig.wait.limits(mark)[3:] == [60.0]

    run(scenario)


def test_the_pauses_grow_to_an_hour_and_no_further(tmp_path):
    async def scenario():
        async with Rig(tmp_path) as rig:
            await sending(rig)
            rig.walk(3600)
            mark = rig.wait.mark()
            rig.fake.fail_next("/rest/v1/walks", 500, None, times=8, method="POST")
            await rig.saved()
            for _ in range(7):
                rig.wait.time_is_up()
                await rig.idle()
            assert rig.wait.limits(mark) == [60.0, 120.0, 300.0, 900.0, 1800.0, 3600.0, 3600.0, 3600.0]

    run(scenario)


@pytest.mark.parametrize(("retry_after", "pause"), [("600", 600.0), ("5", 60.0), ("86400", 60.0), ("soon", 60.0)])
def test_a_429_is_later_and_the_accounts_can_only_make_the_pause_longer(tmp_path, retry_after, pause):
    async def scenario():
        async with Rig(tmp_path) as rig:
            await sending(rig)
            rig.walk(3600)
            mark = rig.wait.mark()
            rig.fake.fail_next("/rest/v1/walks", 429, None, headers={"Retry-After": retry_after}, method="POST")
            await rig.saved()
            assert rig.wait.limits(mark) == [pause]
            assert rig.snapshot()["last"]["word"] == "later"

    run(scenario)


def test_a_pause_is_dropped_when_sending_is_switched_off(tmp_path):
    async def scenario():
        async with Rig(tmp_path) as rig:
            user = await sending(rig)
            rig.walk(3600)
            rig.fake.fail_next("/rest/v1/walks", 500, None, method="POST")
            await rig.saved()
            assert [call.limit for call in rig.wait.waiting()] == [60.0]
            before, mark = len(rig.fake.requests), rig.wait.mark()

            assert rig.account.switch_off() == OK
            assert rig.snapshot()["sending"] is False  # at once
            await rig.idle()
            assert [call.limit for call in rig.wait.waiting()] == [None]  # the pause is gone
            await rig.advance(4000)
            await rig.saved()
            assert len(rig.fake.requests) == before and rig.wait.limits(mark) == []
            assert rig.fake.walks(user) == []
            assert rig.account_in_book(user)["sending"] is False

    run(scenario)


def test_a_pause_is_dropped_when_nothing_waits_any_more(tmp_path):
    async def scenario():
        async with Rig(tmp_path) as rig:
            user = await sending(rig)
            record = rig.walk(3600)
            rig.fake.fail_next("/rest/v1/walks", 500, None, method="POST")
            await rig.saved()
            before, mark = len(rig.fake.requests), rig.wait.mark()
            rig.store.delete(record.id)  # deleted on the dashboard while its send waits
            rig.wait.time_is_up(60.0)
            await rig.idle()
            await rig.advance(4000)
            assert len(rig.fake.requests) == before and rig.wait.limits(mark) == []
            assert rig.fake.walks(user) == []

    run(scenario)


def test_a_refresh_that_gets_no_answer_does_not_end_the_sign_in(tmp_path):
    async def scenario():
        async with Rig(tmp_path, start=False) as rig:
            user = await sending(rig)
            record = rig.walk(3600)
            rig.clock.advance(3700)
            rig.fake.fail_next("/auth/v1/token", 500, None)
            assert await a_pass(rig) == "later"
            shown = rig.snapshot()
            assert shown["state"] == "signed_in" and shown["sending"] is True and shown["notice"] == ""
            assert len(rig.keystore.secrets()) == 1
            assert await a_pass(rig) == "sent"
            assert rig.entry_of(user, record)["state"] == "added"

    run(scenario)


def test_a_database_of_a_newer_version_gets_nothing(tmp_path):
    async def scenario():
        async with Rig(tmp_path) as rig:
            user = await sending(rig)
            rig.fake.schema = 2
            rig.walk(3600)
            await rig.saved()
            assert rig.snapshot()["last"]["word"] == "too_old"
            assert rig.pushes() == [] and rig.account_in_book(user)["walks"] == {}
            before = len(rig.fake.requests)
            rig.walk(60)
            await rig.saved()
            assert len(rig.fake.requests) == before  # stopped until Walka is started again
            rig.fake.schema = 1
            await rig.restart()
            assert len(rig.fake.walks(user)) == 2

    run(scenario)


@pytest.mark.parametrize("answer", [1.5, "1", None, True, [1]])
def test_a_version_that_is_no_whole_number_stops_the_run(tmp_path, answer):
    async def scenario():
        async with Rig(tmp_path, start=False) as rig:
            await sending(rig)
            rig.walk(3600)
            rig.fake.fail_next("/rest/v1/rpc/schema_version", 200, answer)
            assert await a_pass(rig) == "stopped"
            assert rig.pushes() == []

    run(scenario)


def test_a_certificate_that_cannot_be_checked_means_no_request_and_a_plain_word(tmp_path):
    class NoCertificate:
        """A client whose every request ends where the certificate is checked."""

        asked = 0

        def __init__(self, origin):
            self.origin = origin

        async def request(self, *_args, **_how):
            NoCertificate.asked += 1
            raise NetError("certificate")

    async def scenario():
        async with Rig(tmp_path) as rig:
            user = await sending(rig)
            rig.walk(3600)
            before = len(rig.fake.requests)
            await rig.restart(client_factory=NoCertificate)
            shown = rig.snapshot()
            assert shown["last"]["word"] == "certificate"
            assert shown["state"] == "signed_in" and shown["notice"] == ""  # nobody is signed out for it
            assert len(rig.keystore.secrets()) == 1
            assert NoCertificate.asked == 1 and len(rig.fake.requests) == before
            assert rig.account_in_book(user)["walks"] == {}
            rig.walk(60)
            await rig.saved()
            assert NoCertificate.asked == 1  # this run is stopped

    run(scenario)


def test_walks_that_are_there_already_or_deleted_there_are_not_sent_again(tmp_path):
    async def scenario():
        async with Rig(tmp_path, start=False) as rig:
            user = await sending(rig)
            new, there, deleted = rig.walk(10800), rig.walk(7200), rig.walk(3600)
            row_there = rig.fake.add_walk(user, utc_text(there.start))  # put in on the website, say
            row_deleted = rig.fake.add_walk(user, utc_text(deleted.start), deleted=True)
            assert await a_pass(rig) == "sent"

            assert rig.entry_of(user, new)["state"] == "added"
            assert rig.entry_of(user, there) == {"state": "there", "row": row_there, "deleted": False}
            assert rig.entry_of(user, deleted) == {"state": "there", "row": row_deleted, "deleted": True}
            assert rig.account.in_account(there.id, there.start) is True
            assert rig.account.in_account(deleted.id, deleted.start) is False  # the copy there is gone
            assert rig.snapshot()["last"] == {"word": "sent", "at": rig.clock.wall(), "added": 1}
            assert rig.snapshot()["counts"]["sent"] == 3 and rig.snapshot()["counts"]["waiting"] == 0
            # The book was written BEFORE the look-up was asked: one look-up, after the walks.
            paths = [(seen.method, seen.path) for seen in rig.asked("/rest/v1/walks")]
            assert paths == [("POST", "/rest/v1/walks"), ("GET", "/rest/v1/walks")]
            # A walk deleted in the account stays on the computer, and none of the three goes again.
            assert rig.file_of(deleted).exists()
            before = len(rig.fake.requests)
            assert await a_pass(rig) == "nothing"
            assert len(rig.fake.requests) == before

    run(scenario)


def test_the_book_is_written_before_the_look_up_and_a_failed_look_up_is_not_asked_again(tmp_path):
    async def scenario():
        async with Rig(tmp_path, start=False) as rig:
            user = await sending(rig)
            record = rig.walk(3600)
            rig.fake.add_walk(user, utc_text(record.start))
            gate = rig.fake.hold("/rest/v1/walks", method="GET")
            rig.fake.fail_next("/rest/v1/walks", 500, None, method="GET")
            going = asyncio.ensure_future(rig.account.sender.one_pass())
            await gate.arrived.wait()
            # The look-up waits for its answer - and the walk is in the book already.
            assert rig.entry_of(user, record) == {"state": "there", "row": None, "deleted": False}
            gate.release()  # ... and the answer is no answer to the question
            assert await going == "sent"
            assert len(rig.asked("/rest/v1/walks", method="GET")) == 1
            assert rig.entry_of(user, record) == {"state": "there", "row": None, "deleted": False}
            before = len(rig.fake.requests)
            assert await a_pass(rig) == "nothing"
            await rig.restart(start=False)
            assert await a_pass(rig) == "nothing"
            assert len(rig.fake.requests) == before  # there is nothing to send: so nothing is asked, ever

    run(scenario)


def test_no_new_token_is_made_only_to_look_up_which_row_a_walk_has(tmp_path):
    async def scenario():
        async with Rig(tmp_path, start=False) as rig:
            user = await sending(rig)
            record = rig.walk(3600)
            rig.fake.add_walk(user, utc_text(record.start))
            gate = rig.fake.hold("/rest/v1/walks", method="POST", after=True)
            going = asyncio.ensure_future(rig.account.sender.one_pass())
            await gate.arrived.wait()
            rig.clock.advance(3700)  # the answer is slow, and the access token runs out meanwhile
            rig.keystore.calls.clear()
            gate.release()
            assert await going == "sent"
            # The walk is "there", its row unknown: that is not worth a look into the store and a new key.
            assert rig.entry_of(user, record) == {"state": "there", "row": None, "deleted": False}
            assert refreshes(rig) == 0 and rig.keystore.calls == []
            assert rig.asked("/rest/v1/walks", method="GET") == []

    run(scenario)


def test_the_card_names_twenty_walks_at_most_and_counts_them_all(tmp_path):
    async def scenario():
        async with Rig(tmp_path, start=False) as rig:
            await sending(rig)
            records = [rig.walk(1000 + 100 * number, steps=500_000) for number in range(25)]
            assert await a_pass(rig) == "nothing"
            shown = rig.snapshot()
            assert shown["counts"]["held"] == 25 and len(shown["held"]) == 20
            assert shown["held"][0]["id"] == records[0].id  # the newest first

    run(scenario)


def test_a_book_that_cannot_be_written_stops_the_run_instead_of_sending_again_and_again(tmp_path, monkeypatch):
    async def scenario():
        async with Rig(tmp_path, start=False) as rig:
            user = await sending(rig)
            rig.walk(3600)

            def full_disk(self, fn):
                raise ledger.LedgerError("cannot_write")

            monkeypatch.setattr(ledger.Ledger, "change", full_disk)
            assert await a_pass(rig) == "stopped"
            assert len(rig.fake.walks(user)) == 1
            before = len(rig.fake.requests)
            assert await a_pass(rig) == "stopped"
            assert len(rig.fake.requests) == before
            monkeypatch.undo()

    run(scenario)


def test_a_pass_never_raises_and_says_only_what_kind_of_error_it_was(tmp_path, monkeypatch, caplog):
    async def scenario():
        async with Rig(tmp_path, start=False) as rig:
            await sending(rig)
            rig.walk(3600)

            async def broken(*_args, **_how):
                raise RuntimeError("a-word-that-must-not-be-told")

            monkeypatch.setattr(calls, "push", broken)
            assert await a_pass(rig) == "stopped"
            assert rig.snapshot()["last"]["word"] == "stopped"
            monkeypatch.undo()

    run(scenario)
    assert "RuntimeError" in caplog.text and "a-word-that-must-not-be-told" not in caplog.text


# ---- the three quiet cases: no request, no look into the store, no timer ---------------------------


def quiet(rig: Rig):
    """Write down where things stand; give a check that nothing was asked of anybody since."""
    requests, mark = len(rig.fake.requests), rig.wait.mark()
    rig.keystore.calls.clear()
    opened = len(rig.opened)

    def check() -> None:
        assert rig.fake.requests[requests:] == []
        assert rig.keystore.calls == []
        assert rig.wait.limits(mark) == []
        assert len(rig.opened) == opened

    return check


async def a_long_run(rig: Rig) -> None:
    """Hours go by, walks are saved, the dashboard asks for the snapshot, the computer's clock is set a day ahead."""
    for hour in range(30):
        rig.snapshot()
        await rig.advance(3600)
        if hour % 5 == 0:
            rig.walk(120 + hour, seconds=60.0)
            await rig.saved()
        if hour == 12:
            rig.clock.jump_wall(86400)
            await rig.saved()
    for call in rig.wait.waiting():
        assert call.limit is None


def test_never_signed_in_not_one_request_no_look_into_the_store_no_timer(tmp_path):
    async def scenario():
        async with Rig(tmp_path, start=False) as rig:
            rig.walk(7200), rig.walk(3600)
            check = quiet(rig)
            rig.account.start()
            await rig.idle()
            await a_long_run(rig)
            check()
            assert rig.fake.requests == [] and rig.wait.limits() == []
            assert rig.snapshot()["state"] == "signed_out"
            assert not (rig.dir / ledger.FILE_NAME).exists()  # not even the file is made

    run(scenario)


def test_signed_in_with_sending_off_not_one_request_at_a_start_or_after(tmp_path):
    async def scenario():
        async with Rig(tmp_path) as rig:
            rig.person()
            await rig.sign_in()
            rig.walk(7200)
            await rig.restart(start=False)
            check = quiet(rig)
            rig.account.start()
            await rig.idle()
            await a_long_run(rig)
            check()
            shown = rig.snapshot()
            assert shown["state"] == "signed_in" and shown["sending"] is False
            # Walks made while sending is off are counted, and that is all.
            assert shown["counts"]["waiting"] == 7 and shown["counts"]["sent"] == 0

    run(scenario)


@pytest.mark.parametrize("send", ["all", "from_now_on"])
def test_sending_on_with_nothing_to_send_not_one_request_at_a_start_or_after(tmp_path, send):
    async def scenario():
        async with Rig(tmp_path) as rig:
            rig.walk(7200)
            await sending(rig, send)
            await rig.restart(start=False)
            check = quiet(rig)
            rig.account.start()
            await rig.idle()
            rig.snapshot()
            # Hours go by and the access token runs out: it is not made anew, for there is nothing to ask.
            await rig.advance(5 * 3600.0)
            rig.clock.jump_wall(86400)
            await rig.saved()
            await rig.advance(3600.0)
            check()
            assert rig.snapshot()["sending"] is True
            assert [call.limit for call in rig.wait.waiting()] == [None]

    run(scenario)


def test_a_walk_that_cannot_be_sent_is_no_reason_for_a_request(tmp_path):
    async def scenario():
        async with Rig(tmp_path) as rig:
            await sending(rig)
            await rig.restart(start=False)
            check = quiet(rig)
            unnamed, _held, torn = rig.walk(7200), rig.walk(3600, steps=500_000), rig.walk(1800)
            rig.rewrite(unnamed, drop=("treadmill",))
            rig.file_of(torn).write_text('{"id": "' + torn.id + '", "start": ', "utf-8")
            rig.account.start()
            await rig.idle()
            await rig.saved()
            # Something waited by Walka's list, nothing was left once the files were read: nobody was asked.
            check()
            counts = rig.snapshot()["counts"]
            assert (counts["unnamed"], counts["held"], counts["unreadable"], counts["waiting"]) == (1, 1, 1, 0)

    run(scenario)


# ---- what was chosen ---------------------------------------------------------------------------


def test_only_walks_from_now_on_is_kept_across_a_restart_until_all_of_them_is_chosen(tmp_path):
    async def scenario():
        async with Rig(tmp_path) as rig:
            old = rig.walk(7200)
            before = len(rig.fake.requests)
            user = await sending(rig, "from_now_on")
            moment = rig.clock.wall()
            chosen = rig.account_in_book(user)["chosen"]
            assert chosen == {"what": "from", "from": moment, "at": moment}
            assert rig.pushes() == []
            shown = rig.snapshot()
            assert (shown["chosen"], shown["from"], shown["counts"]["waiting"]) == ("from", moment, 0)
            assert not [seen for seen in rig.fake.requests[before:] if "schema_version" in seen.path]

            # A walk that began before that moment and is saved after it is never sent; one that began after is.
            await rig.advance(900)
            under_way = rig.walk(1000)
            await rig.saved()
            assert rig.pushes() == []
            new = rig.walk(600)
            await rig.saved()
            assert [walk["client_id"] for walk in rig.fake.walks(user)] == [new.id]

            # Not at a later start either.
            await rig.restart(start=False)
            check = quiet(rig)
            rig.account.start()
            await rig.idle()
            check()
            assert rig.snapshot()["chosen"] == "from" and rig.snapshot()["from"] == moment

            # Until "all of them" is chosen, in a later question - which counts the two that stayed.
            question = await rig.question()
            assert question["waiting"] == 2
            assert rig.account.switch_on(question["id"], "all") == OK
            await rig.idle()
            assert {walk["client_id"] for walk in rig.fake.walks(user)} == {old.id, under_way.id, new.id}
            assert rig.account_in_book(user)["chosen"]["what"] == "all" and rig.snapshot()["from"] is None

    run(scenario)


def test_a_walk_that_began_at_the_very_moment_of_from_now_on_goes_and_one_before_it_stays(tmp_path):
    async def scenario():
        async with Rig(tmp_path) as rig:
            user = await sending(rig, "from_now_on")
            before, at = rig.walk(0.1), rig.walk(0)  # a tenth of a second before the moment, and at it
            assert rig.account_in_book(user)["chosen"]["from"] == at.start
            assert rig.snapshot()["counts"]["waiting"] == 1
            await rig.saved()
            assert [walk["client_id"] for walk in rig.fake.walks(user)] == [at.id]
            assert rig.entry_of(user, before) is None

    run(scenario)


def test_walks_made_while_sending_was_off_are_counted_in_the_next_question_and_never_sent_silently(tmp_path):
    async def scenario():
        async with Rig(tmp_path) as rig:
            user = await sending(rig)
            assert rig.account.switch_off() == OK
            await rig.idle()
            rig.walk(3600), rig.walk(1800)
            await rig.saved()
            assert rig.pushes() == [] and rig.snapshot()["counts"]["waiting"] == 2
            question = await rig.question()
            assert question["waiting"] == 2
            assert rig.pushes() == []  # asking the question sends nothing
            assert rig.account.switch_on(question["id"], "from_now_on") == OK
            await rig.idle()
            assert rig.fake.walks(user) == []

    run(scenario)


# ---- what is never sent ------------------------------------------------------------------------


def test_the_name_of_the_simulated_treadmill_is_the_one_walka_gives_it():
    assert f'status.name = "{MADE_UP}"' in inspect.getsource(simulated)


def test_made_up_walks_are_never_counted_or_sent(tmp_path):
    async def scenario():
        async with Rig(tmp_path) as rig:
            real = rig.walk(7200)
            for ago in (5000, 4000, 3000):
                rig.walk(ago, treadmill=MADE_UP)  # by --demo, by --simulate, by the config: the file is the same
            assert rig.snapshot()["counts"]["waiting"] == 0  # (nobody is signed in: nothing is counted)
            user = rig.person()
            await rig.sign_in()
            assert rig.snapshot()["counts"]["waiting"] == 1
            question = await rig.question()
            assert (question["waiting"], question["unnamed"], question["held"]) == (1, 0, 0)
            assert rig.account.switch_on(question["id"], "all") == OK
            await rig.idle()
            rig.walk(60, treadmill=MADE_UP)
            await rig.saved()
            assert [walk["client_id"] for walk in rig.fake.walks(user)] == [real.id]
            assert sum(len(rows) for rows in rig.pushes()) == 1
            counts = rig.snapshot()["counts"]
            assert counts == {"waiting": 0, "sent": 1, "aside": 0, "held": 0, "unnamed": 0, "unreadable": 0}
            assert MADE_UP not in "".join(seen.body.decode("utf-8") for seen in rig.fake.requests)

    run(scenario)


def test_a_file_that_says_simulated_is_not_sent_whatever_walkas_list_says(tmp_path):
    async def scenario():
        async with Rig(tmp_path, start=False) as rig:
            user = await sending(rig)
            record = rig.walk(3600)
            rig.rewrite(record, treadmill=MADE_UP)
            before = len(rig.fake.requests)
            assert await a_pass(rig) == "nothing"
            assert len(rig.fake.requests) == before and rig.fake.walks(user) == []
            assert rig.snapshot()["counts"] == dict.fromkeys(
                ("waiting", "sent", "aside", "held", "unnamed", "unreadable"), 0
            )

    run(scenario)


def test_while_the_treadmill_is_the_simulated_one_the_part_does_nothing_at_all(tmp_path):
    async def scenario():
        async with Rig(tmp_path) as rig:
            user = await sending(rig)
            rig.walk(3600, treadmill=PAD)  # a real walk waits
            await rig.restart(start=False, kind="simulated")
            check = quiet(rig)
            rig.account.start()
            await rig.idle()
            assert rig.account.usable is False
            unavailable = (409, {"ok": False, "error": "not available"})
            assert rig.account.sign_in("github") == unavailable
            assert rig.account.sign_out() == unavailable
            assert rig.account.look_again() == unavailable
            assert rig.account.prepare_question() == unavailable
            assert rig.account.switch_on("x", "all") == unavailable
            assert rig.account.switch_off() == unavailable
            assert rig.account.notice_ok() == unavailable
            await rig.saved()
            await rig.advance(7200)
            check()
            shown = rig.snapshot()
            assert shown["state"] == "unavailable" and shown["email"] == "" and shown["sending"] is False
            assert shown["counts"]["waiting"] == 0
            assert rig.fake.walks(user) == []
            # With a real pad again, at the next start, it is as it was left: signed in, sending on.
            await rig.restart(kind="ftms")
            assert len(rig.fake.walks(user)) == 1

    run(scenario)


def test_a_file_without_a_treadmill_key_is_counted_apart_and_not_sent_but_an_empty_name_is_sent(tmp_path):
    async def scenario():
        async with Rig(tmp_path) as rig:
            user = rig.person()
            await rig.sign_in()
            unnamed, empty = rig.walk(7200), rig.walk(3600, treadmill="")
            rig.rewrite(unnamed, drop=("treadmill",))  # written by no Walka: every Walka writes the key
            assert json.loads(rig.file_of(empty).read_text("utf-8"))["treadmill"] == ""
            question = await rig.question()
            assert (question["waiting"], question["unnamed"]) == (1, 1)
            assert rig.account.switch_on(question["id"], "all") == OK
            await rig.idle()
            # A game session marked before the pad's first connection: a real walk with an empty name. It goes.
            assert [walk["client_id"] for walk in rig.fake.walks(user)] == [empty.id]
            assert rig.entry_of(user, unnamed) is None  # not written down: it waits for the owner's word
            counts = rig.snapshot()["counts"]
            assert (counts["unnamed"], counts["sent"], counts["waiting"]) == (1, 1, 0)

    run(scenario)


@pytest.mark.parametrize("name", [None, 7, ["UREVO"], {"name": "UREVO"}, True])
def test_a_file_whose_treadmill_is_no_name_at_all_is_counted_with_the_unnamed_and_not_sent(tmp_path, name):
    async def scenario():
        async with Rig(tmp_path, start=False) as rig:
            user = await sending(rig)
            record = rig.walk(3600)
            rig.rewrite(record, treadmill=name)  # no Walka writes that
            before = len(rig.fake.requests)
            assert await a_pass(rig) == "nothing"
            assert len(rig.fake.requests) == before and rig.fake.walks(user) == []
            assert rig.snapshot()["counts"]["unnamed"] == 1 and rig.snapshot()["counts"]["waiting"] == 0

    run(scenario)


@pytest.mark.parametrize("change", [{"id": "20200101-000000"}, {"start": 1.0}, {"start": "1795296400.0"}, {"id": 7}])
def test_a_file_whose_own_id_or_start_is_another_is_skipped(tmp_path, change):
    async def scenario():
        async with Rig(tmp_path, start=False) as rig:
            user = await sending(rig)
            record = rig.walk(3600)
            rig.rewrite(record, **change)
            before = len(rig.fake.requests)
            assert await a_pass(rig) == "nothing"
            assert len(rig.fake.requests) == before and rig.fake.walks(user) == []
            assert rig.snapshot()["counts"]["unreadable"] == 1

    run(scenario)


def test_a_walk_outside_what_the_account_takes_is_held_shown_and_not_sent(tmp_path):
    async def scenario():
        async with Rig(tmp_path, start=False) as rig:
            user = await sending(rig)
            long, far, fine = rig.walk(4 * 86400.0), rig.walk(7200, distance_m=250_000.0), rig.walk(3600)
            rig.rewrite(long, end=long.start + 3 * 86400.0)
            assert await a_pass(rig) == "sent"
            assert [walk["client_id"] for walk in rig.fake.walks(user)] == [fine.id]
            assert sum(len(rows) for rows in rig.pushes()) == 1
            shown = rig.snapshot()
            assert shown["counts"]["held"] == 2 and shown["counts"]["waiting"] == 0
            assert shown["held"] == [
                {"id": far.id, "start": far.start, "why": "The distance is more than 200 km (124 miles)."},
                {"id": long.id, "start": long.start, "why": "It lasts longer than two days."},
            ]
            # The verdict is not written down (one of the reasons depends on the day), and nothing is cut to fit.
            assert rig.entry_of(user, long) is None and rig.entry_of(user, far) is None
            assert "longer than two days" not in rig.files_text()
            before = len(rig.fake.requests)
            assert await a_pass(rig) == "nothing"
            assert len(rig.fake.requests) == before

    run(scenario)


def test_a_walk_deleted_while_its_send_waits_is_not_sent(tmp_path):
    async def scenario():
        async with Rig(tmp_path, start=False) as rig:
            user = await sending(rig)
            stays, goes = rig.walk(7200), rig.walk(3600)
            gate = rig.fake.hold("/rest/v1/rpc/schema_version")
            going = asyncio.ensure_future(rig.account.sender.one_pass())
            await gate.arrived.wait()
            assert rig.store.delete(goes.id) is True  # on the dashboard, while the pass waits for an answer
            gate.release()
            assert await going == "sent"
            assert [[sent["client_id"] for sent in rows] for rows in rig.pushes()] == [[stays.id]]
            assert [walk["client_id"] for walk in rig.fake.walks(user)] == [stays.id]

            # And when it was the only one, nothing is sent at all.
            only = rig.walk(60)
            rig.fake.expire_access_tokens()
            gate = rig.fake.hold("/auth/v1/token", after=True)
            going = asyncio.ensure_future(rig.account.sender.one_pass())
            await gate.arrived.wait()
            rig.store.delete(only.id)
            gate.release()
            assert await going == "nothing"
            assert len(rig.fake.walks(user)) == 1

    run(scenario)


def test_the_only_walk_deleted_while_the_file_is_read_again_means_no_new_key_and_no_request(tmp_path):
    async def scenario():
        async with Rig(tmp_path, start=False) as rig:
            user = await sending(rig)
            rig.walk(7200)
            assert await a_pass(rig) == "sent"  # (the version of the rules is known from here on)
            only = rig.walk(60)
            rig.clock.advance(3700)  # the access token has run out: the next request would need a new one
            real = rig.account.still

            async def still_and_deleted(epoch):
                await real(epoch)
                rig.store.delete(only.id)  # on the dashboard, while the file was read again

            rig.account.still = still_and_deleted
            before = len(rig.fake.requests)
            rig.keystore.calls.clear()
            assert await a_pass(rig) == "nothing"
            # No walk is left to send: so no request - not for a new key either - and no look into the store.
            assert rig.fake.requests[before:] == [] and rig.keystore.calls == []
            assert len(rig.fake.walks(user)) == 1

    run(scenario)


def test_the_only_walk_deleted_while_the_store_is_read_means_no_request_for_a_new_key(tmp_path):
    async def scenario():
        async with Rig(tmp_path, start=False) as rig:
            user = await sending(rig)
            rig.walk(7200)
            assert await a_pass(rig) == "sent"
            only = rig.walk(60)
            rig.clock.advance(3700)
            real = rig.account.keys.read

            async def read_and_deleted(name):
                stored = await real(name)
                rig.store.delete(only.id)  # on the dashboard, while the store for secrets was read
                return stored

            rig.account.keys.read = read_and_deleted
            before = len(rig.fake.requests)
            rig.keystore.calls.clear()
            assert await a_pass(rig) == "nothing"
            assert rig.fake.requests[before:] == []  # the look into the store was the last of it
            assert rig.keystore.calls == [("read", rig.entry)]
            assert len(rig.fake.walks(user)) == 1 and rig.snapshot()["state"] == "signed_in"

    run(scenario)


@pytest.mark.parametrize("token", ["still good", "run out"])
def test_the_only_walk_deleted_before_the_first_send_of_a_run_means_the_rules_are_not_asked(tmp_path, token):
    async def scenario():
        async with Rig(tmp_path, start=False) as rig:
            user = await sending(rig)
            only = rig.walk(60)
            if token == "run out":
                rig.clock.advance(3700)
            rig.keystore.calls.clear()
            real = rig.account.still

            async def still_and_deleted(epoch):
                await real(epoch)
                rig.store.delete(only.id)

            rig.account.still = still_and_deleted
            before = len(rig.fake.requests)
            assert await a_pass(rig) == "nothing"
            assert rig.fake.requests[before:] == []  # not even which version of its rules the database is at
            assert rig.keystore.calls == []  # and no key was made anew for that question
            assert rig.fake.walks(user) == [] and rig.account.schema_known is False

    run(scenario)


def test_a_walk_deleted_while_the_store_is_read_does_not_end_the_pass_for_the_walks_behind_it(tmp_path, monkeypatch):
    async def scenario():
        monkeypatch.setattr(row_part, "outside", lambda row, now_ms: None)
        async with Rig(tmp_path, start=False) as rig:
            user = await sending(rig)
            bad, gone, fine = rig.walk(10800, distance_m=250_000.0), rig.walk(7200), rig.walk(3600)
            batch = rig.fake.hold("/rest/v1/walks", method="POST")
            going = asyncio.ensure_future(rig.account.sender.one_pass())
            await batch.arrived.wait()
            first = rig.fake.hold("/rest/v1/walks", method="POST")
            batch.release()  # refused whole: one by one from here
            await first.arrived.wait()  # the bad one, alone
            # By this computer's clock the access token runs out before the next one's turn. (Not by
            # the accounts' clock, which is left where it was: the request that waits is still taken.)
            rig.clock.advance(3700)
            rig.accounts_behind_s = 3700.0
            real = rig.account.keys.read

            async def read_and_deleted(name):
                stored = await real(name)
                rig.store.delete(gone.id)  # ... and that one is deleted while the store is read for a new key
                return stored

            rig.account.keys.read = read_and_deleted
            first.release()
            assert await going == "sent"
            await rig.idle()
            # Nothing was asked for the deleted walk - and the one behind it went all the same.
            assert [[sent["client_id"] for sent in rows] for rows in rig.pushes()][1:] == [[bad.id], [fine.id]]
            # (One look into the store that ended there, and one for the key the last walk went with.)
            assert refreshes(rig) == 1 and rig.keystore.calls.count(("read", rig.entry)) == 2
            assert [walk["client_id"] for walk in rig.fake.walks(user)] == [fine.id]
            assert rig.entry_of(user, bad)["state"] == "aside" and rig.entry_of(user, gone) is None

    run(scenario)


def test_walks_deleted_before_a_later_batch_do_not_take_the_word_sent_from_the_ones_that_went(tmp_path):
    async def scenario():
        async with Rig(tmp_path, start=False) as rig:
            user = await sending(rig)
            walks = [rig.walk(86400.0 - number * 60) for number in range(BATCH + 1)]
            real = rig.account.keys.read

            async def read_and_deleted(name):
                stored = await real(name)
                rig.store.delete(walks[-1].id)  # the one walk of the second batch, while the store was read
                return stored

            rig.account.keys.read = read_and_deleted
            gate = rig.fake.hold("/rest/v1/walks", method="POST", after=True)
            going = asyncio.ensure_future(rig.account.sender.one_pass())
            await gate.arrived.wait()
            rig.clock.advance(3700)  # the answer is slow: the access token has run out when the second batch is due
            rig.keystore.calls.clear()
            gate.release()
            assert await going == "sent"
            assert [len(rows) for rows in rig.pushes()] == [BATCH]
            assert refreshes(rig) == 0 and rig.keystore.calls == [("read", rig.entry)]
            assert len(rig.fake.walks(user)) == BATCH
            assert rig.snapshot()["last"] == {"word": "sent", "at": rig.clock.wall(), "added": BATCH}

    run(scenario)


def test_the_task_ends_by_itself_when_walka_is_closing(tmp_path):
    async def scenario():
        async with Rig(tmp_path) as rig:
            await sending(rig)
            task = rig.account._sender_task
            assert task is not None and not task.done()
            # Closing, as far as the task can see - but nobody cancels it (before Python 3.12 a
            # cancellation could be lost on the way: then it must still not wait for ever).
            rig.account.stopping = True
            rig.account.wake.set()
            for _ in range(200):
                if task.done():
                    break
                await asyncio.sleep(0)
            assert task.done() and not task.cancelled() and task.exception() is None

    run(scenario)


def test_the_task_ends_by_itself_after_a_pass_that_walka_closed_under(tmp_path):
    async def scenario():
        async with Rig(tmp_path) as rig:
            user = await sending(rig)
            rig.walk(3600)
            task = rig.account._sender_task
            gate = rig.fake.hold("/rest/v1/rpc/schema_version")
            rig.account.walk_saved()
            await gate.arrived.wait()  # a pass is under way ...
            rig.account.stopping = True  # ... when Walka closes (and, again, nobody cancels the task)
            gate.release()
            await asyncio.wait({task}, timeout=10)
            assert task.done() and not task.cancelled() and task.exception() is None
            assert rig.pushes() == [] and rig.fake.walks(user) == []  # and nothing more was asked

    run(scenario)


def test_switching_off_while_a_batch_waits_sends_nothing_more(tmp_path):
    async def scenario():
        async with Rig(tmp_path, start=False) as rig:
            user = await sending(rig)
            for number in range(BATCH + 1):
                rig.walk(1000 + 100 * number, seconds=30.0)
            gate = rig.fake.hold("/rest/v1/walks", method="POST", after=True)  # the rows are in; the answer waits
            going = asyncio.ensure_future(rig.account.sender.one_pass())
            await gate.arrived.wait()
            assert rig.account.switch_off() == OK
            assert rig.snapshot()["sending"] is False
            gate.release()
            assert await going == "dropped"
            await rig.idle()
            assert [len(rows) for rows in rig.pushes()] == [200]  # the second batch never went
            assert len(rig.fake.walks(user)) == 200
            # What did go is written down all the same: it is in the account.
            walks = rig.account_in_book(user)["walks"]
            assert len(walks) == 200 and {entry["state"] for entry in walks.values()} == {"added"}
            assert rig.account_in_book(user)["sending"] is False
            before = len(rig.fake.requests)
            assert await a_pass(rig) == "nothing"
            assert len(rig.fake.requests) == before

    run(scenario)


def test_the_other_walka_on_this_folder_switches_off_and_this_one_stops_at_once(tmp_path):
    async def scenario():
        async with Rig(tmp_path, start=False) as rig:
            user = await sending(rig)
            rig.walk(3600)
            other = ledger.Ledger(rig.dir)
            other.change(lambda book: ledger.set_sending(book, rig.origin, user, False, None))
            before = len(rig.fake.requests)
            assert await a_pass(rig) == "dropped"  # this Walka still thought sending was on
            assert len(rig.fake.requests) == before
            assert rig.snapshot()["sending"] is False
            assert await a_pass(rig) == "nothing"

            # And nothing read from the file switches sending ON in a Walka that is running.
            chosen = {"what": "all", "at": 1.0}
            other.change(lambda book: ledger.set_sending(book, rig.origin, user, True, chosen, "a-mark-of-the-other"))
            assert rig.account.look_again() == OK
            await rig.idle()
            assert rig.snapshot()["sending"] is False
            assert await a_pass(rig) == "nothing"
            assert rig.fake.walks(user) == []

    run(scenario)


def test_the_look_up_is_not_asked_once_the_other_walka_has_switched_sending_off(tmp_path):
    async def scenario():
        async with Rig(tmp_path, start=False) as rig:
            user = await sending(rig)
            record = rig.walk(3600)
            rig.fake.add_walk(user, utc_text(record.start))  # there already: it will not come back
            gate = rig.fake.hold("/rest/v1/walks", method="POST", after=True)
            going = asyncio.ensure_future(rig.account.sender.one_pass())
            await gate.arrived.wait()
            # The other Walka on this data folder switches sending off while the answer is on its way.
            ledger.Ledger(rig.dir).change(lambda book: ledger.set_sending(book, rig.origin, user, False, None))
            gate.release()
            assert await going == "dropped"
            # What the answer said is written down - and nothing more is asked, not even which row it is.
            assert rig.entry_of(user, record) == {"state": "there", "row": None, "deleted": False}
            assert rig.asked("/rest/v1/walks", method="GET") == []
            assert rig.snapshot()["sending"] is False

    run(scenario)


def test_the_other_walka_signs_out_and_this_one_sends_nothing_more(tmp_path):
    async def scenario():
        async with Rig(tmp_path, start=False) as rig:
            user = await sending(rig)
            rig.walk(3600)
            ledger.Ledger(rig.dir).change(lambda book: ledger.sign_out(book, rig.origin))
            before = len(rig.fake.requests)
            assert await a_pass(rig) == "dropped"
            assert len(rig.fake.requests) == before and rig.fake.walks(user) == []
            shown = rig.snapshot()
            assert shown["state"] == "signed_out" and shown["sending"] is False and shown["email"] == ""

    run(scenario)


# ---- the files themselves ----------------------------------------------------------------------


def test_reading_the_files_never_raises_and_never_leaves_the_folder(tmp_path):
    folder = tmp_path / "sessions"
    folder.mkdir()
    outside_it = tmp_path / "secret.json"
    outside_it.write_text('{"id": "../secret", "start": 5.0, "treadmill": "x"}', "utf-8")
    (folder / "20261119-213000.json").write_bytes(b"\xff\xfe not text")
    (folder / "20261119-213001.json").write_text('{"id": "20261119-213001", "start": NaN, "treadmill": ""}', "utf-8")
    (folder / "20261119-213002.json").write_text("[]", "utf-8")
    (folder / "20261119-213003.json").mkdir()
    wanted = [
        ("../secret", 5.0, "a"),
        ("20261119-213000", 5.0, "b"),
        ("20261119-213001", 5.0, "c"),
        ("20261119-213002", 5.0, "d"),
        ("20261119-213003", 5.0, "e"),
        ("20261119-213004", 5.0, "f"),
        ("20261119-213005\n", 5.0, "g"),
    ]
    found = read_walks(folder, wanted, 1_795_300_000_000)
    assert [walk.kind for walk in found] == ["unreadable"] * 5 + ["gone", "unreadable"]
    assert all(walk.row is None for walk in found)


def test_a_file_too_large_to_be_a_walk_is_not_read(tmp_path, monkeypatch):
    async def scenario():
        async with Rig(tmp_path, start=False) as rig:
            record = rig.walk(3600)
            path = rig.file_of(record)
            path.write_text(path.read_text("utf-8") + " " * 2000, "utf-8")  # still a walk, only longer
            wanted, now_ms = [(record.id, record.start, "a")], int(rig.clock.wall() * 1000)
            (walk,) = read_walks(rig.store.dir, wanted, now_ms)
            assert walk.kind == "send"
            monkeypatch.setattr(sender, "MOST_FILE_BYTES", 1000)
            (walk,) = read_walks(rig.store.dir, wanted, now_ms)
            assert walk.kind == "unreadable" and walk.row is None
            monkeypatch.undo()

    run(scenario)


# ---- waking, closing, and what the file is for -------------------------------------------------


def test_a_walk_saved_during_a_pass_goes_in_the_next_one_without_a_restart(tmp_path):
    async def scenario():
        async with Rig(tmp_path) as rig:
            user = await sending(rig)
            mark = rig.wait.mark()
            first = rig.walk(3600)
            gate = rig.fake.hold("/rest/v1/walks", method="POST")
            rig.account.walk_saved()
            await gate.arrived.wait()
            second = rig.walk(60)  # saved while the first is on its way
            rig.account.walk_saved()
            gate.release()
            await rig.idle()
            assert [[sent["client_id"] for sent in rows] for rows in rig.pushes()] == [[first.id], [second.id]]
            assert len(rig.fake.walks(user)) == 2
            assert rig.wait.limits(mark) == []  # woken by the event, never by a clock

    run(scenario)


def test_closing_in_the_middle_of_a_send_loses_no_walk_and_sends_none_twice(tmp_path):
    async def scenario():
        async with Rig(tmp_path) as rig:
            user = await sending(rig)
            for number in range(BATCH + 1):
                rig.walk(1000 + 100 * number, seconds=30.0)
            gate = rig.fake.hold("/rest/v1/walks", method="POST", after=True)  # the rows are in; the answer waits
            rig.account.walk_saved()
            await gate.arrived.wait()
            rig.account.cancel()
            await rig.account.close()
            gate.release()
            assert len(rig.fake.walks(user)) == 200 and rig.account_in_book(user)["walks"] == {}

            # At the next start everything that is not written down goes again; the account answers
            # "there already" for the two hundred it has, and takes the one it lacks.
            await rig.restart()
            assert len(rig.fake.walks(user)) == 201
            walks = rig.account_in_book(user)["walks"]
            assert len(walks) == 201
            assert sorted(entry["state"] for entry in walks.values()).count("there") == 200
            rows = {walk["start_key"]: walk["id"] for walk in rig.fake.walks(user)}
            assert {entry["row"] for entry in walks.values()} == set(rows.values())  # each walk knows its row
            assert rig.snapshot()["counts"]["sent"] == 201 and rig.snapshot()["counts"]["waiting"] == 0

    run(scenario)


def test_a_lost_file_means_sending_is_off_and_switched_on_again_everything_is_there_already(tmp_path):
    async def scenario():
        async with Rig(tmp_path) as rig:
            user = await sending(rig)
            rig.walk(7200), rig.walk(3600)
            await rig.saved()
            assert len(rig.fake.walks(user)) == 2
            (rig.dir / ledger.FILE_NAME).unlink()
            before = len(rig.fake.requests)
            await rig.restart()
            shown = rig.snapshot()
            assert (shown["state"], shown["sending"], shown["notice"]) == ("signed_out", False, "")
            assert len(rig.fake.requests) == before

            await rig.sign_in()
            await rig.switch_on("all")
            assert len(rig.fake.walks(user)) == 2  # nothing twice
            walks = rig.account_in_book(user)["walks"]
            assert [entry["state"] for entry in walks.values()] == ["there", "there"]
            assert {entry["row"] for entry in walks.values()} == {walk["id"] for walk in rig.fake.walks(user)}

    run(scenario)


def test_the_same_id_with_another_start_is_another_walk(tmp_path):
    async def scenario():
        async with Rig(tmp_path, start=False) as rig:
            user = await sending(rig)
            first = rig.walk(3600)
            assert await a_pass(rig) == "sent"
            rig.store.delete(first.id)  # no trace is left, and the id can be given out again
            second = rig.walk(1800, id=first.id)
            assert second.id == first.id and second.start != first.start
            assert await a_pass(rig) == "sent"
            assert len(rig.fake.walks(user)) == 2
            walks = rig.account_in_book(user)["walks"]
            assert len(walks) == 2  # the first one's entry stays when its walk is deleted
            assert rig.account.in_account(first.id, first.start) and rig.account.in_account(second.id, second.start)

    run(scenario)


def test_the_walk_still_going_is_never_sent(tmp_path):
    async def scenario():
        async with Rig(tmp_path, start=False) as rig:
            user = await sending(rig)
            done = rig.walk(3600)
            going = rig.walk(60, add=False)
            rig.store.checkpoint(going)  # active-session.json: the walk that is being recorded
            assert rig.store.active_path.exists()
            assert await a_pass(rig) == "sent"
            assert [walk["client_id"] for walk in rig.fake.walks(user)] == [done.id]
            assert rig.snapshot()["counts"]["waiting"] == 0

    run(scenario)


def test_a_token_made_anew_and_refused_at_once_is_not_made_anew_a_second_time(tmp_path):
    async def scenario():
        async with Rig(tmp_path, start=False) as rig:
            user = await sending(rig)
            rig.walk(7200)
            assert await a_pass(rig) == "sent"
            rig.walk(3600)
            rig.clock.advance(3700)  # run out by Walka's own clock: this request begins with a refresh
            rig.fake.fail_next("/rest/v1/walks", 401, refusal("PGRST303", "JWT expired"), method="POST")
            assert await a_pass(rig) == "stopped"
            assert refreshes(rig) == 1  # never twice for one request
            assert len(rig.fake.walks(user)) == 1 and rig.snapshot()["counts"]["aside"] == 0

    run(scenario)


def test_the_access_token_runs_out_by_the_monotonic_clock_not_by_the_computers_date(tmp_path):
    async def scenario():
        async with Rig(tmp_path, start=False) as rig:
            user = await sending(rig)
            # Somebody sets the computer's clock a day ahead. (The accounts' clock stays where it was.)
            rig.clock.jump_wall(86400)
            rig.accounts_behind_s = 86400.0
            rig.walk(3600)
            assert await a_pass(rig) == "sent"
            assert refreshes(rig) == 0  # the token is not taken for run out: no refresh, no look into the store
            # ... and a day back.
            rig.clock.jump_wall(-2 * 86400)
            rig.accounts_behind_s = -86400.0
            rig.walk(3600)
            assert await a_pass(rig) == "sent"
            assert refreshes(rig) == 0 and len(rig.fake.walks(user)) == 2
            # An hour by the monotonic clock, and it has run out.
            rig.clock.advance(3600 - 29)
            rig.walk(60)
            assert await a_pass(rig) == "sent"
            assert refreshes(rig) == 1

    run(scenario)


def test_no_answer_for_a_walk_that_was_deleted_meanwhile_is_no_reason_to_wait(tmp_path):
    async def scenario():
        async with Rig(tmp_path) as rig:
            user = await sending(rig)
            record = rig.walk(3600)
            mark = rig.wait.mark()
            gate = rig.fake.hold("/rest/v1/walks", method="POST")
            rig.fake.fail_next("/rest/v1/walks", 500, None, method="POST")
            rig.account.walk_saved()
            await gate.arrived.wait()
            rig.store.delete(record.id)
            gate.release()
            await rig.idle()
            # "Later" - but nothing waits any more: no pause is asked for.
            assert rig.wait.limits(mark) == [] and rig.fake.walks(user) == []
            assert [call.limit for call in rig.wait.waiting()] == [None]

    run(scenario)


def test_what_an_old_sign_in_is_answered_says_nothing_once_sending_is_off(tmp_path):
    async def scenario():
        async with Rig(tmp_path) as rig:
            await sending(rig)
            rig.walk(3600)
            mark = rig.wait.mark()
            gate = rig.fake.hold("/rest/v1/walks", method="POST")
            rig.fake.fail_next("/rest/v1/walks", 403, refusal("42501", "permission denied"), method="POST")
            rig.account.walk_saved()
            await gate.arrived.wait()
            assert rig.account.switch_off() == OK
            gate.release()
            await rig.idle()
            # The answer came after the switch: it stops no run and is not said.
            assert rig.account.latch == "" and rig.snapshot()["last"]["word"] != "stopped"
            assert rig.wait.limits(mark) == []
            # Switched on again in the same run, the walk goes.
            await rig.switch_on("all")
            assert rig.snapshot()["counts"]["sent"] == 1

    run(scenario)


def test_a_walk_that_walkas_list_calls_made_up_is_not_sent_whatever_its_file_says(tmp_path):
    async def scenario():
        async with Rig(tmp_path, start=False) as rig:
            user = await sending(rig)
            record = rig.walk(3600, treadmill=MADE_UP)
            rig.rewrite(record, treadmill=PAD)
            before = len(rig.fake.requests)
            assert await a_pass(rig) == "nothing"
            assert len(rig.fake.requests) == before and rig.fake.walks(user) == []

    run(scenario)


def test_a_stopped_run_goes_on_after_the_question_is_answered_again_or_somebody_signs_in(tmp_path):
    async def scenario():
        async with Rig(tmp_path) as rig:
            user = await sending(rig)
            rig.walk(3600)
            rig.fake.fail_next("/rest/v1/walks", 403, refusal("42501", "permission denied"), method="POST")
            await rig.saved()
            assert rig.account.latch == "stopped" and rig.fake.walks(user) == []
            await rig.saved()
            assert rig.fake.walks(user) == []  # stopped for this run ...

            await rig.switch_on("all")  # ... until the person switches sending on anew
            assert rig.account.latch == "" and len(rig.fake.walks(user)) == 1

            rig.walk(1800)
            rig.fake.fail_next("/rest/v1/walks", 403, refusal("42501", "permission denied"), method="POST")
            await rig.saved()
            assert rig.account.latch == "stopped"
            assert rig.account.sign_out() == OK
            await rig.idle()
            await rig.sign_in()  # ... or signs in anew
            assert rig.account.latch == "" and rig.snapshot()["last"]["word"] == ""
            await rig.switch_on("all")
            assert len(rig.fake.walks(user)) == 2

    run(scenario)
