# Work in progress: Walka sends walks to a Walka account

This file belongs to the branch `claude/walka-account-signin-sync-2xtpeb`
and is removed before the branch is merged. Whoever continues gets the
brief (`docs/brief-walka-sends-walks.md` in `AntonyPerez0/walka-website`,
branch `ccr-a2c5d66c-cbhqpm`) and this file.

The live project's address and key stand nowhere in this repository, and
not in this file either (decision 12). Neither does anybody's e-mail
address, nor the address used for the proof of 5.2.

## Where it stands

- 21 November 2026. Both repositories read (walka-addon `25cc69c`,
  walka-website `264e54b`). No code written.
- The plan and the questions below were put to the owner. **Waiting for
  the owner's yes and answers.** Nothing is built before that.
- Baseline on Linux, Python 3.12, from the hashed dev lock: `ruff check
  src tests` clean; `python -m pytest` 2826 passed, 7 skipped, 215 s.
- The Docker daemon does not run on the machine this was planned on: a
  local Supabase stack may stay "not tried".

## The owner's answers, in their own words

(none yet)

## Where the brief and the files disagree

The file wins unless said. Each of these was told to the owner.

1. **Walks without a treadmill name (decision 6).** Every version of
   Walka, from the first commit on, writes the `treadmill` key, and a
   made-up walk always carries "Simulated pad" (set when the simulated
   pad is made: `treadmill/simulated.py:59`). But a REAL walk is saved
   with `"treadmill": ""` when a game session is marked before the pad's
   first connection of that run: the name is copied once, when the
   recorder is made (`app.py:244`). So "empty name: not sent" holds back
   real walks, and new ones keep joining them. A file with no such key at
   all was written by no Walka. Until the owner answers, the brief's
   default stands (not sent, counted apart). Loading fills a missing key
   with `""`, so the two can only be told apart from the file itself.
2. **The contract against the brief**, where the brief is followed
   because rule 3a is not up for trade: `data-model.md` says a program
   reads `schema_version()` "when they start" and refreshes "shortly
   before the hour is up", and that the person is asked "to sign in
   again" when the sign-in is over. The brief says: before the first send
   of a run; only when a request is due and the token has run out; and
   "asking for nothing". Reported as places where the contract is wrong.
3. **Decision 4's words "you made it another way"** are wrong when the
   e-mail addresses match: `accounts-setup.md:447-449` - Discord and
   GitHub with the same address come to ONE account. A second, empty
   sign-in comes from another e-mail address.
4. **A deleted account reads like a new one** for up to an hour
   (`profiles` answers `200 []` for both: `calls-tried.md:91`, `:159`).
   So on "no row" ask `GET /auth/v1/user` before decision 4's words are
   shown; `user_not_found` is decision 11.
5. **Decision 11's sentence** names two causes; the contract knows a
   third (the keys were used twice, the account is there), and "signed
   out elsewhere" is nothing the website can do today (its sign-out ends
   that browser's sign-in only). A stored sign-in that is simply not in
   the store (a data folder copied from another computer) is not
   decision 11 at all: quiet "not signed in", words of its own.
6. **Any public page shows a sent walk at once** - the count, the time
   and the distance are given out whenever `is_public` is true
   (`public_profile()`), not only on pages that show days or walks.
7. **The `device` column.** The website's privacy page already says "A
   program that sends walks may add a random number that tells one
   installation from another". The brief never mentions it. Not sent,
   unless the owner says otherwise: a row has exactly the website's 16
   keys - the 15 of `walka_row.py` and `id`.
8. **`walka_row.py` is not the website's reader line for line** at three
   edges no Walka-written file reaches: an id with a line end after it
   (`$` in Python), `NaN` and `Infinity` in the file (Python's `json`
   takes them; the browser refuses the whole file), a number too large
   for `math.isfinite`. The copy here goes by the website's reader.
9. **Python 3.10** is stated (`pyproject.toml`, README) and checked
   nowhere: CI runs 3.12, the hashed locks cannot be installed on 3.10,
   `test_release.py` imports `tomllib`, and five tests fail there. Tried
   on 3.10.20: `datetime.fromisoformat` refuses a time with five
   decimals and any time that ends in `Z`.
10. **A packed Mac program has no root certificates** unless it is given
    some: Python on a Mac only looks where the BUILD machine's Python
    kept them. And the check of 6.6 would be green all the same, because
    the machine that builds is the machine that checks. So on a Mac the
    client loads macOS's own list (`/etc/ssl/cert.pem`) by name, and the
    hidden check says where its roots came from and fails on none. On
    Windows, Python reads what is in the system's store already; a newly
    set-up PC may lack a root, and CI cannot show that.
11. **`asyncio.to_thread` holds the program at exit** until the call
    comes back (the loop's own threads are joined). So whatever can block
    without limit - the store for secrets, a look-up of a name - runs on
    daemon threads of the feature's own, as `cli.py:376`, `:501`, `:604`
    and `:686` do, not as 6.1 suggests.
12. **The dashboard's guard stops other web pages, not programs.** A
    request without an `Origin` header passes (`web.py:91-93`), and the
    page has no password. A program on the computer - or another user of
    it - could sign Walka out, start a sign-in or switch sending on. The
    id of the question stops a stale page and a double press, not them.
    SECURITY.md has to say what is meant. On Windows every program of the
    same user can read the stored sign-in.
13. **The brief against itself:** a packed program has no account feature
    (decision 12), yet its hidden command is to use "the same client"
    (6.6). So the client is a part of its own that takes ONE address, and
    every download holds a command that fetches walka.dev's front page
    once if somebody types it.
14. `Walka/THIRD-PARTY-NOTICES.md` is copied into the game's AddOns
    folder with the addon. A line about an account there sits next to
    rule 3h.
15. The rail's foot is hidden when the window is narrower than 1180
    pixels (`app.css:2280-2296`); Walka's window opens 1200 wide. The
    Walka-account card says itself whether sending is on.
16. On the website a walk is deleted one at a time (no bulk delete): "all
    of them" is undone walk by walk, or by deleting the account.
17. `accounts-setup.md` says both that a return to 127.0.0.1 is allowed
    (`:142-149`) and that it is not (`:313-316`). The proof comes first.
18. Small ones: the page reads the stream `/api/events`, not
    `/api/state` (both carry the snapshot); `App.log_event` writes to the
    Live view AND `walka.log`, so nothing about the account goes through
    it; the data folder also holds `walka.log.1` and `window/`; the
    snapshot's keys are pinned exactly (`test_app.py:492-507`) and the
    config's sections in four places; `history.js` has no native
    question, only a panel in the card; the ad hoc sentence is
    `docs/HARDWARE.md:246-248`; the README's list of modules is
    `README.md:957-966`.

## The plan

### The parts

A subpackage `companion/src/walka/walka_account/` (never plain
`account`):

- `row.py` - the website's `docs/walka_row.py`, taken over (item 8).
- `https.py` - the one maker of clients. Certificates: the system's store
  on Windows, macOS's own list on a Mac; checking is never off. One
  address and no other; no redirect followed; no cookie kept; no proxy
  read from the environment; `User-Agent: Walka/<version>` and nothing
  about the computer; `Accept-Encoding: identity`; an answer is read up
  to a limit; a resolver of its own on a daemon thread.
- `calls.py` - the only place that builds a request: one function for
  each line of 5.4, with method, path, query and `Prefer` fixed in the
  code. Every error of the network is caught here and turned into one of
  Walka's own, with nothing in it: an aiohttp error's `repr` carries the
  bearer. Of a refusal only `code` and `message` are kept (cut short),
  never `details`: it can hold the whole row.
- `keystore.py` - the system's store for secrets, with ctypes and no new
  library: Windows Credential Manager (`CredWriteW`, `CredReadW`,
  `CredDeleteW`; a credential that stays on this computer), the Mac's
  login keychain (the older `SecKeychain...` calls: the newer ones cannot
  be kept from showing a box there), and one in memory for tests. Every
  call on a daemon thread, under one lock, with a time limit; the result
  crosses to the loop in a box, never as an argument (asyncio would
  write it to the log). No file, ever. No command line.
- `ledger.py` - the new file of decision 9, `walka-account.json`:
  version 1; under the address, then the account's id: the way, the
  e-mail address, whether sending is on, what was chosen, and each walk
  dealt with under its id and its start. Read, change ONE thing, replace
  - under a lock file, so that two Walkas cannot undo each other. A file
  of a newer version is left alone and the feature stays off. Nothing
  read from it can switch sending on by itself: finishing ANY sign-in
  writes "off" for every account of that address.
- `signin.py` - 5.2 as written: the verifier, a listener of its own for
  one sign-in, the exchange before the answer, three plain pages, sign
  out by forgetting first.
- `sender.py` - the third task. One pass: (1) on the loop, which walks
  wait - none: stop here, no request, no read of the store; (2) off the
  loop, the files themselves, mapped; a walk the database would refuse
  is held back and shown; none left: stop here; (3) a token, if there is
  none or it has run out; (4) `schema_version`, once in a run; (5)
  batches of 200 at most; before each request the ledger is read again:
  still signed in, still on, the same account; (6) the answers of 5.3;
  the ledger is written before anything else is asked. "Stop this run"
  holds until Walka is started again. A later try is a whole new pass.
- `service.py` - what is handed to `App`: the state, the words for the
  snapshot (from a fixed list, never an error's own text), what the
  routes do, its own tasks (all of them ended by `App.stop()`).

Wiring: `App(..., walka_account=None)`. `cli` hands one in only when
Walka is not packed, both variables are set, the address passes, the key
is not a secret key, and the treadmill is not the simulated one. Else
there is no card, the routes answer 404 and the snapshot is as it was.
`App.stop()` only cancels before the walk is saved; whatever has to be
waited for (the listener's end) comes after, with a second's limit.

Routes, all POST, under the guard, answering at once (progress shows in
the snapshot): start a sign-in (`{"way": ...}`; anything else does
nothing), sign out, look again, prepare the question, switch on (the
question's id and the choice spelled out), switch off.

The dashboard: one card, "Walka account", last in Settings, not there at
all without the part, quiet until used. A panel in the card for the
question. Outside pages are plain links, as the disclaimer's are today;
only the sign-in's own address is opened from Python.

### Tests

All against a fake of the accounts (`companion/tests/fake_accounts.py`,
written from 5.4 and `calls-tried.md`, importing nothing from the
feature) that notes every request and fails the test - in a fixture, not
inside its own handler - on anything outside the list: method, path, the
query's values, `Prefer` whole, the names of the headers, the keys of the
body. Then: the row (the three records first, before the copy); every
answer of 5.3; the keys' order of events (the new refresh token is
written before the access token is used); the ledger (lost, damaged,
newer, two writers); the three quiet cases with a tripwire on every way
out (`loop.sock_connect`, `loop.create_connection`, `getaddrinfo`, the
opener, the store) and proof that no wait had a time limit; closing in
the middle; made-up walks by all three ways and among real ones; the
question (run out, answered twice, another account, empty body); the
listener; the routes with junk, with and without the part; no key in any
file, log (at debug level, after the failures too), snapshot or route;
the packed program; one real round trip for each store, skipped
elsewhere; the hidden `httpscheck` and its step in both build jobs.

### The order

The owner's yes - the proof of 5.2 - the words, as one list, to the
owner - the row - the fake, the client, the calls, `httpscheck` and its
CI steps - the ledger and the stores - signing in and out - the sender
and `App` - the routes and the snapshot - the dashboard - the words of
section 7 - the report. A push after each, and a look at CI after every
push. Then stop: no live values, no version files, no tag, no release, no
merge.

## Questions put to the owner on 21 November 2026

1. The proof of 5.2 (an address to open).
2. Walks with an empty treadmill name: send them (proposed), or not.
3. The brief over the contract in item 2 above (proposed: yes).
4. A program or another user on the computer can reach the switch
   (proposed: say so in SECURITY.md).
5. No store for secrets (Linux): signed in until Walka is closed
   (proposed), or no sending.
6. A Mac after an update: no box by surprise, "sign in again" in the card
   (proposed), or let macOS ask whenever.
7. Certificates: no library (proposed); `truststore` only if the owner's
   own trial shows the need.
8. The hidden `httpscheck` command in every download (proposed: yes).
9. Python 3.10: leave the sentence, write for 3.10, report it as not
   checked (proposed), or say 3.11.
10. A public page: send at once and say so (proposed), or hold back.
11. A shared computer: the sentence (proposed), or ask after each walk.
12. The random number for an installation: not sent (proposed).
13. The e-mail address: whole (proposed), or shortened.
14. When the sign-in ends: the sentence stands in the card until "OK"
    (proposed), or a pop-up as well.
15. DISCLAIMER.md: no line (proposed).
16. The notices' copy in the addon's folder: as the brief says, both
    (proposed), or the root copy only.
17. The words: drafted here as one list, corrected by the owner.
18. Which computer the owner has, Windows or a Mac.

## What comes next

The owner's answers go into this file, in their words, with the date.
Then the order above.
