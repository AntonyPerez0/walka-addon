# Work in progress: Walka sends walks to a Walka account

This file belongs to the branch `claude/walka-account-signin-sync-2xtpeb`
and is removed before the branch is merged. Whoever continues gets the
brief (`docs/brief-walka-sends-walks.md` in `AntonyPerez0/walka-website`,
branch `ccr-a2c5d66c-cbhqpm`) and this file. Two more files of the same
kind lie beside it and are removed with it: `ACCOUNT-WORDS.md` (every
sentence a person reads, old words and new, for the owner's yes) and
`ACCOUNT-REPORT.md` (the report the brief's section 10 asks for).

Until 24 November the live project's address and key stood nowhere in
this repository (decision 12). Since the owner's "Release it now" of that
day they stand in one file of the program
(`companion/src/walka/walka_account/project.py`) and nowhere else: not in
this file either. Neither does anybody's e-mail
address, nor the address used for the proof of 5.2.

## Where it stands

- 21 November 2026. Both repositories read (walka-addon `25cc69c`,
  walka-website `264e54b`). The plan and the questions below were put to
  the owner, who answered "Do everything" the same day (see the next
  part). The build followed the order under "The plan".
- 24 November 2026. Everything of the brief's section 8 up to the stop
  of step 6 is built and pushed: the part, its place in Walka, the card
  and the question on the dashboard, the words of section 7 in README,
  SECURITY, SAFETY and the notices, the tests, the build checks. A last
  review (six reviewers, two sceptics for each serious finding)
  confirmed twenty findings; nineteen are fixed, one is put to the owner
  (a backup put back - see "Known and left"). What was done about each
  is in `ACCOUNT-REPORT.md`.
- It stops where the brief says. No live address or key anywhere; a
  packed program has no part; the version files are untouched; no tag,
  no release, no merge, no pull request.
- 24 November 2026, later. The owner answered "Do everything and then
  push and publish" (see the next part). Done since: the backup gap
  closed; five sentences say "Walka account"; the question names all
  sixteen things; with sending on, "walks are sent" stays in sight in a
  narrow window; the report published as a private page. 4,448 tests
  pass here. CI at `f4613af` (the commit with those changes): green in
  every job - the tests on Ubuntu, Windows and macOS, the packed Windows
  program and both Mac programs with both build checks. The job that
  publishes a release did not run: nothing was tagged.
- 24 November 2026, evening. The owner chose "Release it now" (see the
  next part). The release change is made, reviewed and pushed: the
  address and key in one file, the from-source rule lifted, version
  0.8.0-rc2. 4,496 tests pass here; CI at `bdf97a1` is green in every
  job, the build check on the packed Windows program and both Mac
  programs among them. **The tag is not pushed:** the session this work
  ran in may push to this one branch only, and a tag was refused. The
  release is published the moment somebody pushes the tag `v0.8.0-rc2`
  on a commit of this branch whose CI is green; nothing else is missing.
- **Still the owner's:** the tag (or the word to do it another way);
  which computer they have for the trial.

## The owner's answers, in their own words

- 21 November 2026, to the plan and its eighteen questions: "Do
  everything". Read by the assistant (its reading, not the owner's
  words) as: yes to the plan, and the proposed answer to every
  question - so: walks with an empty treadmill name are sent, a file
  with no such key is not; the brief over the contract where rule 3a
  speaks; SECURITY.md says what a program on the computer can reach;
  without a store for secrets the sign-in lasts until Walka is closed;
  on a Mac no box by surprise; no new library; the hidden `httpscheck`;
  written for Python 3.10 and reported as not checked there; walks go
  at once, public page or not, and the question says so; the sentence
  for a shared computer; no number for the installation; the e-mail
  address whole; the ended sign-in said in the card until "OK"; no line
  in DISCLAIMER.md; both copies of the notices; the words drafted here
  for the owner to correct. It is NOT read as leave to put in the live
  values, to touch the version files, to tag, release or merge.
- 21 November 2026, the proof of 5.2: "This is what happened when I
  click on the link", with a picture of their browser (on a phone) at an
  address on `127.0.0.1` under "This site can't be reached - 127.0.0.1
  refused to connect". That failure is the proof:
  **the live project lets a sign-in come back to 127.0.0.1.** No address
  was added in Supabase for it. (The one-time code of that try was never
  used and has run out.)
- 24 November 2026, to the report and its questions: "Do everything and
  then push and publish". Read by the assistant (its reading, not the
  owner's words) as: yes to the words in `ACCOUNT-WORDS.md`, and the
  recommended answer to each open question - so: the five sentences of
  the brief that said "account" alone now say "Walka account"; the three
  places beside "no Walka account yet" stay; decision 4's changed
  sentence stays; README "Only listens." stays; the notices' line stays
  in both copies; the question before deleting says of a copy in another
  Walka account what it says, also after signing out; with sending on,
  "walks are sent" stays in sight in a narrow window; and the gap of a
  backup put back is closed. "Push": done, to this branch. "Publish":
  NOT read as leave to release Walka, to tag, to merge, to put in the
  live address and key or to touch the version files - the brief puts
  the website's changes and the owner's own trial before that, and a
  published release is never replaced. The report was published as a
  private page for the owner to pass on, and the owner was asked what
  was meant.
- 24 November 2026, asked what "publish" meant, with three choices put
  before them (the report page is enough; open a pull request; release
  it now - the last with the words: this goes against the brief's order
  (website first, your trial first), nothing has been tried on a real
  Windows PC or Mac, and it cannot be undone): **"Release it now"**. So
  the brief's last step is done, by the owner's word and against the
  order of its section 8: the address and the publishable key are in the
  program, the from-source rule is lifted, and it goes out under a new
  version number. Chosen by the assistant, not by the owner: the number
  0.8.0-rc2 - a trial release, as 0.8.0-rc1 was, which is published but
  never becomes "the latest" download - because nobody has yet signed in
  or sent a walk for real; and a tag on this branch, with no merge into
  the default branch, so that the README people download Walka from
  does not speak of sending before the website does.
- 24 November 2026, told that the tag could not be pushed from the
  session and asked how it should be done (push the tag yourself; or the
  branch is merged into the default branch and the release started
  there, which changes the README people download Walka from, and for
  which these three notes are removed first; or stop for now): **"I want
  you to publish. I don't want to do anything you should be doing
  that."** Read as: the second way, since it is the only one the
  assistant can go by itself. So these notes are removed in the next
  commit, as the brief asks before a merge; what they held stays in the
  branch's history, and the report stands as the page that was
  published for the owner.
- Not answered yet: Windows or a Mac.

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
   all was written by no Walka. By the owner's answer of 21 November (as
   it was read) a walk with an empty name IS sent, and a file with no
   such key is not, and is counted apart. Loading fills a missing key
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

## The words

The list that stood here on 21 November is replaced by
`ACCOUNT-WORDS.md`, which holds every sentence of this change that a
person reads, as it stands now, with file and line, the old words where
there were any, and a mark: the brief's own words, drafted and shown to
the owner on 21 November, or new and not yet seen by the owner. The
owner's yes is given to that file.

## What is built

The part, in `companion/src/walka/walka_account/`, each module with
tests in `companion/tests/test_walka_account_<name>.py`:

- `threads.py` - `off_loop`: work that may block, on a daemon thread.
- `row.py` - a walk's file to a row, the website's way (compared with
  the website's reader on 360,000 made-up files in a scratch run).
- `keystore.py` - the store for the refresh token: Windows Credential
  Manager, the Mac's login keychain, memory. The Windows and Mac calls
  have run nowhere but on CI (one real round trip each).
- `ledger.py` - the file `walka-account.json`.
- `https.py` - the address, the key's check, certificates, the client.
- `calls.py` - the requests of 5.4 and what each answer means.
- `signin.py` - one sign-in through the browser, with its listener.
- `sender.py` and `service.py` - the sending pass, and `WalkaAccount`,
  the object handed to `App`.

Its place in Walka:

- `cli.py` - `_build_walka_account`: since the release, every Walka has
  the part, made with the built-in address and key
  (`walka_account/project.py`), unless the treadmill is the simulated
  one. A packed program does not read the two variables; from source
  both together name a developer's own database, and one alone means no
  part. The hidden command `httpscheck`. The browser for a sign-in.
- `app.py` - `App(..., walka_account=None)`: started with Walka, told of
  each saved walk, cancelled before the walk is saved at closing and
  closed after it; the snapshot's key `walka_account` only with a part.
- `web.py` - seven POST routes under `/api/walka-account/` (404 without
  a part); on `/api/sessions` two flags for each walk: a copy is in the
  Walka account that is signed in, or in another one.
- `session.py` - while a walk is going, its Walka holds a lock
  (`active-session.lock`) on the checkpoint, so that a second Walka on
  the same data folder does not take the walk for one left by a crash.
  A store clears only a checkpoint it wrote itself; and a Walka that
  finds, with the lock free, a checkpoint another Walka left when it
  crashed makes a finished walk of it before writing its own. Where a
  folder has no locks everything is as it was. (The last review's one
  serious finding; it is the only change to how walks are recorded,
  with one line in `app.py`: the checkpoint is no longer cleared and
  written again every half minute, only written.)

The dashboard: `static/walkaaccount.js` (the card, the question, the
feet), and small changes in `index.html`, `settings.js`, `app.js`,
`history.js`, `app.css`.

The documents: `README.md`, `SECURITY.md`, `docs/SAFETY.md`, both copies
of `THIRD-PARTY-NOTICES.md`. Drafts until the owner's yes.

The build checks: `tools/check_https.py` and `tools/check_account_part.py`
(until the release: `check_no_account.py`) with one step each in the
Windows build and the Mac builds of `ci.yml`: the packed program makes
checked HTTPS requests and refuses a home-made certificate; and the
packed program, started with both variables set to something else, has
its Walka-account part with nobody signed in and the variables ignored,
and none when started as the demo.

The tests: `fake_accounts.py` (the stand-in for the accounts),
`walka_account_rig.py` (the rig), one test file for each module, and for
Walka as a whole `test_walka_account_wiring.py`, `_quiet.py`,
`_program.py`, `_nokeys.py`, `_two_walkas.py`, `_page.py`,
`_httpscheck.py`, `_build.py`.

Known and left (all of it is in `ACCOUNT-REPORT.md`, in plain words):

- **Closed on 24 November, with limits:** an older `walka-account.json`
  put back from a backup, or changed by hand, no longer switches sending
  on. "On" is tied to a random mark that is kept twice: in the file, and
  as a second entry in the system's store (`... - sending`). Sending can
  still come on without the question only when: the store refuses both
  to delete and to write over the mark at "Stop sending" or sign-out;
  Walka ends in the instant between writing "off" and taking the mark
  out; two Walkas run on one folder and an older file is put back before
  the second has looked (it looks once in a run); a backup puts back
  store and file together; or somebody who can write the file keeps its
  mark and changes the rest. Two data folders on one address share the
  mark. The brief (3d, decision 9) says the sign-in "alone" is in the
  store: the mark is a second thing there, a key to nothing.
- A sign-in that the system's store would not let go at sign-out is
  noted in the file and said in the card, with a button that tries
  again. Nothing tries by itself at a later start. Two data folders on
  one address share one entry in the store: a sign-out (or "Try again")
  in one takes the other's sign-in out too.
- aiohttp asks a GET once more by itself when the line is cut after
  sending: a read (never a send of walks) can reach the accounts twice.
- A refresh refused with any other code than the two the brief names
  stops the run and ends nothing.
- At a start nothing reads the store (rule 3a), so the card shows the
  e-mail address from the file until the first request finds the store
  empty.
- Two Walkas on one data folder: a refresh that comes back in the very
  instant the other Walka signs out and in again can still leave the
  wrong key in the store. No walk goes to the wrong Walka account.
- A browser that comes back in the very instant a sign-in is cancelled
  may be left with a tab that never finishes loading.
- Nothing ran on a real Windows PC or Mac outside CI; nothing touched
  the live project but the owner's one click.

## What comes next

1. The owner tries the trial release v0.8.0-rc2 on a real computer: the
   hand check in `ACCOUNT-REPORT.md`, section 6, with the download
   itself. Nobody has signed in or sent a walk for real before it.
2. The website's maintainer changes the privacy page and the terms
   (`ACCOUNT-REPORT.md`, section 5). They do not yet say what Walka now
   does.
3. Then, if the trial is good: 0.8.0 proper under its own number, and
   the merge into the default branch - with these three files removed
   and the README's words about sending going live with it.
