# Report: Walka can send a person's walks to their Walka account

24 November 2026, for the owner. Branch
`claude/walka-account-signin-sync-2xtpeb` of `walka-addon`. This is the
report that section 10 of the brief asks for. Like `ACCOUNT-WORK.md` and
`ACCOUNT-WORDS.md` it is a working file: it is removed before the branch
is merged. The live project's address and key stand nowhere in it, and
neither does anybody's e-mail address.

**Read this first: the release.** Later on 24 November the owner was
asked what "publish" meant, was told what a release would mean, and
answered "Release it now". So the brief's last step was done, by the
owner's word and against the order of its section 8. What follows in
this report was written before that and is left as it stood; where it
says that the address and key stand nowhere, that a download has no
part, or that nothing is released, section 8 at the end says what is
true now.

Three words used throughout. "The part" is the new piece of Walka's
desktop program that signs in to a Walka account and sends walks. "The
stand-in" is a home-made copy of the Walka accounts' server that runs
on the test computer only. "CI" is GitHub's build machines, which test
and pack Walka after every push.

## 0. In short

- Walka's desktop program can now sign a person in to their Walka
account (on walka.dev) through their own browser and, only after one
question, send each walk to it. Nothing comes down; neither side
deletes anything on the other.
- Never signed in, which is how Walka comes: no request to anybody, no
look into the password store, no reminder.
- It stops where the brief says: the live address and key stand
nowhere; a packed download makes no such part at all; no version file
touched, no tag, no release, no merge, no pull request. Apart from the
owner's one click on 21 November nothing touched the live project.
- 4,448 tests pass. CI: see the end of section 3.
- The last review confirmed twenty findings, one of them serious. All
are dealt with.
- On 24 November the owner answered the open questions with "Do
everything and then push and publish". What was done about each is in
section 7: among it, the gap of a backup put back is closed, five
sentences now say "Walka account", and "walks are sent" stays in sight
in a narrow window.
- "Publish" was not read as leave to release Walka: section 7 says why.
Still the owner's: what "publish" meant; Windows or a Mac for the
later trial.

## 1. What was built

53 files changed against the commit the work began from (`25cc69c`):
about 36,500 lines added, most of them tests. No new library was added;
`pyproject.toml` and the lock files are as they were.

### The part itself - `companion/src/walka/walka_account/` (all new)

- `__init__.py` - says what the part is and when it may ask another
machine at all.
- `threads.py` - runs slow work (the password store, files, looking up
a name) beside the loop that records the walk, on a thread that can
never hold Walka back when it closes.
- `row.py` - turns one walk's file into the sixteen things that are
sent, exactly as the website's own reader does, and says which walks
the Walka account would refuse (longer than two days, say).
- `https.py` - the one maker of requests. One address and no other; the
other side's certificate is always checked; no redirect is followed,
no proxy used, no cookie kept; a request says `Walka/<version>` and
nothing about the computer.
- `calls.py` - the only place that names what Walka may ask: nine kinds
of request, and the one address the browser is opened at, each
written out once, with what each answer means. No request that
changes or deletes anything in a Walka account exists here.
- `keystore.py` - where the sign-in is kept between two runs: Windows'
Credential Manager, the Mac's keychain, or - where there is neither -
memory. Reached with what Windows and macOS bring themselves.
- `ledger.py` - the new file `walka-account.json` in the data folder:
reading it, and changing one thing at a time so that two Walkas on
one folder cannot undo each other.
- `signin.py` - one sign-in through the browser: a listener of its own
on 127.0.0.1 for a few minutes, and three plain pages for the browser
("Walka is signed in. You can close this tab." and two more).
- `sender.py` - one pass over the walks that wait, and the task that
makes a pass at a start and after each walk is saved.
- `service.py` - the object that is handed to Walka: who is signed in,
whether sending is on, what the card's buttons do, and the ready-made
words and numbers for the dashboard.

### How it is handed to Walka (changed files)

- `companion/src/walka/cli.py` - decides whether there is a part at
all: only when Walka runs from its source code, both variables
(`WALKA_ACCOUNT_URL`, `WALKA_ACCOUNT_KEY`) are set, the address is one
Walka takes, the key is not a secret one, and the treadmill is not
the simulated one. Also: opening the browser for a sign-in, the
hidden command `httpscheck` (below), and the list of modules a packed
program must be able to load.
- `companion/src/walka/app.py` - takes the part (absent by default);
tells it "a walk was saved", "Walka has started", "Walka is closing";
adds its words to what the dashboard is told. Recording and closing
never wait for it.
- `companion/src/walka/web.py` - seven new addresses for the card's
buttons (sign in, sign out, look again, prepare the question, switch
on, switch off, "OK"). Each must be pressed, not merely opened;
without the part each answers "not there". The list of walks now
says of each walk whether a copy was sent to the Walka account that
is signed in, or to another one.
- `companion/src/walka/session.py` - the recorder, the one change to how
walks are recorded: while a walk is going, its Walka holds a lock on
the unfinished walk (an empty file, `active-session.lock`), so that a
second Walka opened on the same data folder does not take that walk
for one left behind by a crash (finding C9 below). Where a folder has
no locks, everything is as it was.

### The dashboard - `companion/src/walka/static/`

- `walkaaccount.js` (new) - the card "Walka account", last in Settings;
the question before sending; the two feet that follow the state; the
words of the question before a sent walk is deleted.
- `index.html` - the card's empty frame (hidden unless the part is
there), names on the two feet so that they can change, and the Share
view's sentence.
- `settings.js`, `app.js` - call the new script.
- `history.js` - the question before deleting a walk that was sent.
- `app.css` - how the card looks.

### The documents

- `README.md` - a new section "Your Walka account (on walka.dev)", and
the sentences that said nothing is uploaded.
- `SECURITY.md` - what now counts as a security problem, and what a
program on the same computer can reach.
- `docs/SAFETY.md` - the list of what the dashboard's buttons do.
- `THIRD-PARTY-NOTICES.md` and `Walka/THIRD-PARTY-NOTICES.md` - one
line each: the web library now also makes the requests to a Walka
account.
- `ACCOUNT-WORK.md` (new) - the hand-over file the brief asks for: the
owner's answers, the plan, what is built. `ACCOUNT-WORDS.md` (new) -
every sentence a person reads, old words and new, for the owner's yes.
`ACCOUNT-REPORT.md` (new) - this report. All three are removed before
a merge.

Not touched: `DISCLAIMER.md`, `docs/PROTOCOL.md`, `docs/HARDWARE.md`,
the game addon (no `.lua`, no `.toc`), the three version files.

### The build checks

- `tools/check_https.py` (new) - starts a server with a home-made
certificate, runs the packed program's hidden `httpscheck` command,
and wants three things: the program fetched walka.dev's front page
over a checked connection, it did come to the home-made server, and
it turned back at the certificate before asking anything.
- `tools/check_no_account.py` (new) - starts the packed program, not in
demo mode, with the two variables set, and wants: no card's words in
what the dashboard is told, "not there" from all seven addresses, and
no `walka-account.json` written. The proof that a download has no
part, taken from the download itself.
- `.github/workflows/ci.yml` - both checks as steps in the Windows build
and in the Mac builds.

### The tests - `companion/tests/`

- `fake_accounts.py`, `test_fake_accounts.py` (new) - the stand-in,
written from the brief and the website's record of calls, importing
nothing of the part; and its own tests. It fails any test in which
Walka asks something outside the brief's list.
- `walka_account_rig.py` (new) - puts a whole part together for a test:
a made-up clock, a password store in memory, the stand-in, and a
"browser" that is the stand-in's own.
- One file for each module (new): `test_walka_account_row.py`,
`_https.py`, `_calls.py`, `_keystore.py`, `_ledger.py`, `_signin.py`,
`_sender.py`, `_service.py`, `_threads.py`.
- Walka as a whole (new): `test_walka_account_wiring.py` (the part
handed in or absent, the buttons' addresses, the command line),
`_quiet.py` (the three cases in which nobody may be asked, over many
hours), `_program.py` (closing, starting again, made-up walks),
`_nokeys.py` (one long story, then a search of every file, the log
and the dashboard for every key that was in play), `_two_walkas.py`
(two Walkas on one data folder, the second a real second process).
- `test_walka_account_page.py` (new) - the card's script, as text and
run under Node. `test_walka_account_httpscheck.py` and
`test_walka_account_build.py` (new) - the hidden command and the two
build tools.
- `conftest.py` (changed) - a guard: any ordinary test that opens the
real password store or the real browser fails. `test_app_ftms.py`
(one test added) - nothing the new buttons do reaches the treadmill.

## 2. What a person now can do, and what Walka does

This is also the first item of the list for the website's maintainer.

**Where it is.** One card, "Walka account", last in Settings. It is not
there at all in a Walka that has no part (every download today), nor
while the simulated treadmill is in use. Until somebody signs in it is
three buttons and three sentences, and nothing else on the dashboard
says a word about it.

**Signing in.** The person presses "Sign in with Discord", "GitHub" or
"Google". Walka opens their own web browser - never its own window -
and waits on this computer (127.0.0.1) for the browser to come back,
five minutes at most. Signing in makes a sign-in record at walka.dev if
the person has none; the card says so before the browser opens. Signing
in never switches sending on.

**The first step.** "I am 18 or older, and I have read the terms and
the privacy page" is ticked on walka.dev/account/ and nowhere else.
Walka asks whether it was taken, never ticks it, and until it is taken
sending cannot be switched on.

**Switching sending on** passes one question, every time. It names the
Walka account (the e-mail address, whole, and the way of signing in),
says what goes and what stays, where it goes, how many walks would go
now, lets the person choose "All of them" or "Only walks from now on"
(neither is chosen for them), says what a public page shows, that every
walk recorded on this computer goes whoever walked, and how to stop.
The question is good for ten minutes. An empty press switches nothing.

**What is sent, of each walk - sixteen things and no more:**

1. a random number made by Walka for this walk;
2. the word that says it came from Walka;
3. the walk's name on the computer, which is its local start time
 (such as `20261119-213000`);
4. when it started, and 5. when it ended (world time);
6. the calendar day where the walk was;
7. how many minutes the computer's clock was ahead of world time;
8. how long the belt moved;
9. distance, 10. steps, 11. calories (estimates);
12. how many of the calories were on top of sitting;
13. whether a walking session was running in the game;
14 to 16. how steps, distance and calories were come by (measured by
 the treadmill or estimated; for the calories "from speed" or "from
 heart rate" - the word, not the heart rate).

Every request also says `Walka/<version>` and, like anything asked over
the internet, arrives with the computer's IP address.

**Never sent:** the samples (speed and heart rate every five seconds),
the heart rate, the top speed, the treadmill's name, the character, its
name, realm or road, a picture, weight, height, age or sex. Also not
sent: the walk that is still going; any walk made with the simulated
treadmill; a walk whose file does not say which treadmill made it; a
walk the Walka account would refuse (shown in the card with the reason,
never cut down to fit). A real walk whose treadmill name is empty IS
sent (the owner's answer of 21 November, as it was read).

**When.** When Walka is started, and after each walk is saved. A walk
saved while Walka closes goes at the next start. At most 200 walks in
one request. When nothing answers, Walka tries again after 1, 2, 5, 15,
30 and then 60 minutes - only while a walk waits and sending is on. The
sender has no other timer.

**What is kept on the computer, and where.**

- `walka-account.json` (with an empty `walka-account.lock` beside it;
and, new with this work, an empty `active-session.lock` for the walk
that is still going),
in the data folder beside the walks. It holds: the address of the
database; the Walka account's id there and its e-mail address; the
way of signing in; whether sending is on and what was chosen (all, or
from which moment); for each walk dealt with: sent (under which
number, and when), there already, or not taken (with the reason
given); until "OK" is pressed, that a sign-in ended; that a sign-in may
still be in the password store after signing out (until it is taken
out); and that a Walka account was found to be deleted.
No key is ever in it. It stays after signing out, the e-mail address
with it.
- The sign-in itself (the one lasting key): in Windows' Credential
Manager (of the kind that stays on this computer), or in the Mac's
keychain; the entry's name begins "Walka account". On a system with
neither (Linux) it is in memory and lasts until Walka is closed.
Never a file.
- The short-lived key (good for about an hour): in memory only.
- The e-mail address is in that file and on the dashboard's card. It is
never in `config.json` or `walka.log`. No key is in any file, the
log, the dashboard's page or a command line.

**How a person stops it.** "Stop sending", or "Sign out", in the same
card. Either takes effect at once; walks that were waiting for another
try are dropped; nothing is deleted in the Walka account. To send again
the question is asked afresh, and it counts the walks made meanwhile:
they are never sent silently.

**Signing out** never waits for the network: the key is taken out of
the store and out of memory, sending is switched off, and the card says
so at once. Then, as a courtesy with a three-second limit, Walka tells
the sign-in service - only if its short-lived key has not run out. If
the password store does not confirm that the key is gone, the card says
"Walka could not make sure that your sign-in was taken out of ..." and
names where, with a button "Try again"; Walka's file remembers it until
the store confirms. Nothing tries by itself at a later start.
"Sign out" on walka.dev does not sign a computer out; the card says so.

**Deleting.** Deleting a walk on the dashboard deletes it on the
computer only; the question before it says that the copy stays in the
Walka account until it is deleted there (walka.dev/account/walks/). For
a walk that went to a Walka account that is not the one signed in now,
the question has a wording of its own: "A copy of this walk was sent to
a Walka account from this computer. Deleting it here does not delete
that copy: that is done on walka.dev/account/walks/, signed in to that
Walka account." - shown also after signing out, and only for walks of
which a copy was sent (section 7, answer 3). A walk
deleted in the Walka account stays on the computer and is not sent
again. Walka has no request that changes or deletes anything in a Walka
account, and nothing a Walka account does changes a file on the
computer.

**When the sign-in ends** (the renewal is refused, or the Walka account
is gone): the keys are forgotten, sending goes off, everything on the
computer stays, and the card says once, until "OK" is pressed, the
brief's sentence for it, word for word: no longer signed in, sending
stopped, the walks on this computer untouched. It does not ask the
person to sign in again.

**When the stored sign-in is simply not there** (a data folder copied
from another computer, say): the card says quietly that the sign-in is
not on this computer any more and that nothing is sent. A Mac that
refuses the read: "Sign in again."

**The simulated treadmill** (`--demo`, `--simulate`, or "simulated" in
`config.json`): nothing. No card, no request, and its walks are never
counted or sent - also when they lie among real ones.

## 3. What was tried, and how

**The tests.** 4,448 passed and 9 skipped here (Linux, Python 3.12);
the style check (`ruff`) is clean. Before the work there were 2,826: so
about 1,620 are new. All of them run against the stand-in on the test
computer, a password store in memory and a made-up clock. No test opens
a real browser or asks anybody on the internet.

Every test the brief asks for (section 8, step 4) exists: the three
records of the website's own test with their rows letter for letter;
every answer of the brief's 5.3; a key that runs out in the middle of a
send; a renewal that fails; no network; closing in the middle of a send
(no walk lost, nothing left running); the three quiet cases over many
hours, with a tripwire on every way out of the computer and on the
password store; signing out and signing in as somebody else; a walk
deleted while its send waits; made-up walks by all three ways; the
traffic being the brief's list and nothing else; and the search for
keys in every file, the log and the dashboard.

**Breaking the code on purpose** (to see whether a test notices). In
the last review: 48 changes that each break one of the brief's rules -
44 noticed, the other 4 each a second guard behind one that is tested.
82 changes against the tests as a whole - 72 noticed, 3 second guards,
7 real gaps (findings C17 and C18 and note N19 below). 55 changes
against the whole-program tests alone - 38 noticed there, the other 17
only by the tests of the single modules (note N20). Before that, the
builder and the reviewer of each module made several hundred such
changes (130 and 66 for the requests, 116 and about 155 for the sender
and the service, 153 for the stand-in, for instance).

**Random runs.** 1,750 random sequences of button presses, stalls and
kills on Python 3.12 and 1,550 on 3.10: never a walk in a Walka account
that was not switched on, never a key left after signing out, nothing
left running. A storm of 40 runs with lost answers, restarts and
deletions: in the end every walk was in the stand-in once.

**The row.** Compared with the website's own reader (run under Node, in
a scratch folder) on 360,000 made-up files: every walk that would be
sent was the same row to the letter. Nothing in the repository runs
that again; its tests hold what was learned.

**Attacks, in a scratch copy.** 80 odd spellings of an address (none
got through wrongly); hostile requests at the sign-in listener; the
bytes that leave, captured on the wire (the sixteen things and a short
fixed list of headers, nothing else); a search of every file and of the
whole history of the branch for the live address and key (none).

**The page in a real browser.** The real dashboard, served by the real
program against the stand-in, driven through 19 views in Chromium at
three widths (1200, 900 and 480 pixels), light and dark, and again
after the last fixes and after the owner's answers: 179 pictures (and
240 more of the narrow window, compared dot for dot with the page as
it was), kept in the scratch folder, not in
the repository.

**Python 3.10.** The part's tests were run by hand on 3.10.20 and pass.

**CI at `21afb24`, before the last review: green in every job.** The
tests on Ubuntu, Windows
and macOS (Python 3.12). The packed Windows program and both Mac
programs (Apple silicon, Intel), each with the step "HTTPS works in the
program, and it checks certificates": the packed program fetched
walka.dev's front page once over HTTPS and refused a home-made
certificate. One real round trip through Windows' Credential Manager
and one through the Mac's keychain ran on CI's own machines, under a
made-up name. Two earlier failures on CI's Windows machine were fixed
in `76930e7`: a test that counted a task of Windows' own as Walka's,
and a real fault in `tools/check_https.py`.

**CI after the last review's fixes.** Green in every job at `e5506a1`,
the commit before the one that added this paragraph (which changes this
file only). The tests on Ubuntu, Windows (4,376 passed there; the tests
with two Walkas and a real second process were among them, not skipped)
and macOS. The packed Windows program and both Mac programs, each with
both build checks: HTTPS as before, and the new step "The program has no
Walka account's part" - so the proof that a download has no part now
comes from the three downloads themselves. The HTTPS step on the Macs
took 4 seconds and 1 second, where it had taken 37: the half minute was
the check's own home-made server asking the computer for its name, not
the program and not the network.

**After the owner's answers of 24 November** the same was done again
for what they changed: 4,448 tests here; the backup gap tried against
306 combinations of files and states of the password store, and two
Walkas stepped against each other in 600 random orders; the page
pictured at five widths. What CI showed for it is in the commit that
follows this one - see `ACCOUNT-WORK.md`, "Where it stands".

**The proof of the brief's 5.2.** Done by the owner on 21 November
2026: the live project lets a sign-in come back to 127.0.0.1. Nothing
was signed in by it.

## What was NOT tried

- Anything against the live project: no real sign-in with Discord,
GitHub or Google, no real send. A whole sign-in there has still never
been made by anybody.
- The local copy of the database (Docker was not running on the build
computer). Everything ran against the stand-in, which was written
from the brief's 5.4 and the website's `docs/calls-tried.md`.
- Anything on a real Windows PC or a real Mac outside CI. That holds
for the last fixes too: the recorder's new lock ran on Windows and
macOS on CI's machines only (there with a real second process), and
the new build check `tools/check_no_account.py` likewise.
- The Mac's keychain after an update of Walka. From source the entry
belongs to Python, not to Walka, so this cannot even be seen until a
packed Walka has the part.
- How Walka's own window (pywebview) hands outside links to the web
browser: taken from its setting and an existing test, not watched.
- Python 3.10 and 3.11 on CI (CI runs 3.12 only).
- A password store on Linux: there is none here, and the sign-in is in
memory, by the owner's answer.
- On Windows, that the whole process ends at once when the password
store or the network hangs (the test for it needs something Windows
lacks; a hand check is in section 6).
- A newly set-up Windows PC that lacks a root certificate; a network
that is reached only through a proxy (Walka then cannot sign in, and
the README says so); an answer that is Cloudflare's own page rather
than the database's (Walka would stop that run and set nothing aside
- shown against the stand-in only).

## 3a. What the last review found, and what was done

Six reviewers read the whole change at `21afb24`; each serious finding
was then checked by two sceptics. Twenty were confirmed.

- **C0.** One test failed by chance about once in 200 runs (it looked
for the digits "158" in text that also held a random number).
Done: the test now looks where it should.
- **C1.** If the only waiting walk was deleted in the very instant a
pass began, Walka could still renew its key and look into the
password store - a connection with nothing to send.
Done: Walka now looks whether the walk is still there at the last moment
before the store is read, before the key is renewed and before each
request. What remains is a walk deleted while a request is already on
its way.
- **C2.** After signing out, the question before deleting a sent walk
no longer said that its copy stays in the Walka account.
Done, with C10: the question before deleting has a third wording, for a
walk of which a copy went to a Walka account that is not the one signed
in now - also after signing out. New words (section 7, answer 3). A walk in a Walka account that Walka learned was deleted
no longer counts.
- **C3.** When the password store would not delete the sign-in at
sign-out, the key stayed there, the card said "signed out", and
nothing said so or tried again.
Done, with C6: on signing out Walka first notes in its file that the
sign-in may still be in the password store, and strikes the note when
the store confirms. Otherwise the card says so, with a button "Try
again"; the README says the same. Nothing tries by itself at a later
start: a start touches no password store.
- **C4.** `ACCOUNT-WORK.md` still said "No code written".
Done.
- **C5.** If the password store sat on an earlier write for longer than
its time limit, a sign-out's delete was dropped, and the key was
written back after the person had signed out.
Done: a delete now waits its turn at the password store, however long,
and cannot be dropped; and it stands down behind a later sign-in, so
that it never takes out a newer key.
- **C6.** The same as C3, seen by the security reviewer: the README
says flatly that signing out removes the sign-in.
See C3.
- **C7.** An older `walka-account.json` put back from a backup (or
changed by hand) while the sign-in is still stored switched sending
on again, for that same Walka account, without the question.
Done after the owner's answer of 24 November: "sending is on" is now
tied to the stored sign-in. When the question is answered Walka makes
a random mark and keeps it twice: in its file, and as a second entry
in the password store. A walk goes only while the two are the same.
"Stop sending" and signing out take the mark out of the store, so an
older file's mark never matches again. Tried against 306 combinations
of older or changed files and states of the store: nothing was sent
and no request was made. What it does not close is in section 3b.
- **C8.** Two buttons pressed in the same instant (two tabs, or a
program on the computer) could leave the card saying "off" and the
file saying "on": at the next start the walks went.
Done: a picture of the file that is older than Walka's own last write is
read again.
- **C9 (the serious one).** A second Walka opened on the same data
folder while a walk was going took the unfinished walk for one left
by a crash, saved it cut short and sent it. The whole walk could then
never replace it in the Walka account.
Done, in the recorder: while a walk is going its Walka holds a lock on
the unfinished walk; a second Walka on the same folder leaves it alone.
After a crash the lock is free and the walk is recovered as before.
Tried with a real second process; on Windows and macOS by CI only.
- **C10.** The same as C2, also after a sign-in that ended.
See C2.
- **C11.** The card said "Sent." after a pass in which nothing went and
a walk was only set aside.
Done.
- **C12.** The card stated how many walks are, or are not, in the Walka
account - which Walka does not know (a person may have put them there
on the website).
Done: with sending off the card now says "Walka has not sent <n> walks
on this computer to your Walka account." or "<n> walks on this computer
were sent before, or were in your Walka account already."
- **C13.** With the first step not taken the card said that this
sign-in has no Walka account yet and, two lines on, how many walks
are not in "your Walka account".
Done in part: in that state the card now shows no count and no list of
walks. The three other places stay, by the owner's answer (section 7,
answer 4).
- **C14.** The word stood alone, without "Walka", in seven sentences
written for this change and in five taken word for word from the
brief.
Done for the seven: they say "Walka account", and a test now fails on
any new sentence that does not. The brief's own five were changed too,
after the owner's answer (section 7, answer 5).
- **C15.** The words the owner must say yes to were only in the scratch
folder.
Done: `ACCOUNT-WORDS.md`.
- **C16.** The HTTPS step takes 37 seconds on the Macs against 4 on
Windows, inside a limit of 60, and nothing in the log said where the
time goes.
Done: the check now prints how long each of its parts took, and its
home-made server no longer asks the computer for its own name. That
was the cause: the step now takes 4 seconds and 1 second on the Macs.
- **C17.** Sending one by one after a refused batch was tested for two
of its outcomes only.
Done: the tests are there, and each fails against the broken version.
- **C18.** No test for a `walka-account.json` that cannot be written
when the question is answered, or cannot be read before a request.
Done: the tests are there, and each fails against the broken version.
- **C19.** The download itself was never started with the two variables
set, to see that it really has no part.
Done: `tools/check_no_account.py`, as a step in the Windows build and
the Mac builds.

## 3b. Known and left

From the last review's notes (N0 to N24) and from `ACCOUNT-WORK.md`.
Those marked "fixed" were put right together with the findings above.

- **N0** Fixed: the README's list of what `walka-account.json` holds
now names the note that a sign-in ended.
- **N1** Narrowed, not closed: Walka now looks into its file just
before the key is stored. What remains: two Walkas on one folder: a key
renewal that comes
back later than a sign-out and a new sign-in in the other Walka can
write the old key over the new one. No walk goes to the wrong Walka
account; the person may be told that the sign-in ended when it did
not.
- **N2** A request the sign-in listener cannot read at all gets the web
library's own short refusal instead of Walka's. Nothing leaks and
nothing is used up. A browser that sends very long cookies for
127.0.0.1 could fail to finish a sign-in.
- **N3** Said, in SECURITY.md (a new sentence, for the owner's yes): on
Linux the address of a sign-in is on a command
line while the browser starts, where another user of the computer can
read it and use it to sign Walka in to a Walka account of their own.
No code can prevent that there.
- **N4** Left (no small, safe fix was found): a browser that comes back
in the very instant a
sign-in is cancelled may be left with a tab that spins.
- **N5** Fixed. It was: on Python 3.10 and 3.11 only, closing Walka in
one rare instant took one second longer and wrote a warning to the log.
- **N6** Fixed: a renewed key is now kept unless somebody signed out or
another Walka account signed in meanwhile. (Its short-lived twin is
still dropped then: one more renewal later.)
- **N7** Covered by C3 and C6 above.
- **N8** "Only walks from now on" is a moment of the computer's clock:
if the clock is put back afterwards, new walks are held back until
the clock passes that moment. The card shows the moment.
- **N9** One failed read of a walk's file (another program held it for
a moment) keeps that walk back until Walka is started again.
- **N10** A walk deleted in the Walka account is still counted as sent
in the card - the new words "were sent before, or were in your Walka
account already" are true of it; and when the
look-up of rows fails, Walka does not ask again later.
- **N11** Two walks that start in the same second: the second is
written down as "there already". That is the contract's rule.
- **N12** A Walka account that is nearly full takes none of a batch,
though some of its walks would fit. That is what the brief asks.
- **N13** One sentence of the brief's decision 4 was changed on
purpose, and stays (section 7, answer 6).
- **N14** Fixed. It was: after "Stop sending", a freshly opened
dashboard could say "That did not work just now" of a button nobody pressed.
- **N15** With no part, the page is not quite what it was: the Share
view's sentence changed for everybody (as the brief asks); the list
of walks in History now refreshes sooner after a change; and every
download's page holds the hidden, empty frame of the card.
- **N16** The rail's foot is hidden in a window narrower
than 1,180 pixels, as it was before - but it is now the place that
says walks are sent. Built since: section 7, answer 8. (The scratch pictures were made
again; none is a copy of another now.)
- **N17** README line 123: left as it is (section 7, answer 7).
- **N18** The ten sentences most worth the owner's own eye: listed in
`ACCOUNT-WORDS.md`, part 3.
- **N19** Fixed: the missing test (the first step not taken AND the
sign-in ended elsewhere) is there.
- **N20** Seventeen rules are proved only by the tests of single
modules, not by a test of Walka as a whole. Left.
- **N21** On Windows the test that Walka ends as a process
is skipped; the hand check is in section 6.
- **N22** Fixed: the few tests that allowed only half a second or one
second of real time now allow two or three.
- **N23** Fixed: if a build machine ever lacks Node, openssl or git, the
test run now stops and says so, where before three groups of tests
would have been skipped silently.
- **N24** Every build, and so every release, now needs walka.dev to
answer the three build machines. A red step "HTTPS works in the
program" that says "did not answer (3 tries)" means: run it again.

From the closing of the backup gap (C7) - sending can still come on
without the question only in these ways:

- The password store refuses both to delete the mark and to write over
it when sending is stopped or somebody signs out. Then an older file
is still believed.
- Walka ends (a power cut, say) in the instant between writing "off"
into its file and taking the mark out of the store. The mark stays
until the next answer to the question, sign-in or sign-out.
- Two Walkas on one folder: sending stopped in one, and an older file
put back before the other has looked. That other one sends until it is
started again; it looks once in a run.
- A backup that puts back the password store and the file together.
- Somebody who can change the file while sending is on can keep its
mark and change the rest ("only from now on" into "all"). That is the
program on the same computer that SECURITY.md already speaks of.

And what an honest person may notice: two data FOLDERS that use the
same address share the mark, so switching sending on in one makes the
other ask its question again. After an older file was put back and
nothing waits to be sent, the card says "on" until the first walk, and
then, quietly, off: Walka may not look into the password store at a
start. The brief says the sign-in "alone" is kept in the password
store; the mark is a second thing there, a key to nothing.

The narrow window: between 720 and 1,179 pixels the sentence stands at
the foot of the rail. Below 720 it is one line under the four lamps at
the top and scrolls away with the page, as the lamps do: further down
a long page it is out of sight.

From the last fixes themselves:

- "Try again" (after a sign-out whose key the password store did not
let go): if the store refuses again, the sentence simply stays. If the
delete went through late by itself, the sentence still stays until
"Try again" is pressed once. If the file says that somebody has signed
in since, the press deletes nothing and the sentence goes.
- Two data FOLDERS that use the same address share one entry in the
password store: signing out in one - or "Try again" there - takes the
other's sign-in out too. That folder then says, quietly, that the
sign-in is not on this computer any more.
- The recorder's lock: two Walkas on one folder that BOTH record a walk
at the same time still write over each other's unfinished walk, as
before. An older Walka (without the lock) beside a new one: as
before. After a crash on Windows, if Windows has not yet let go of the
dead program's lock when Walka is started again, the unfinished walk
is recovered a little later (at the next walk's first half minute, or
at the next start), not at once. It is not lost.
- On Windows, the address of a sign-in is on the web browser's command
line while it starts, where programs of the same user can read it (on
Linux: other users too - SECURITY.md says so; on a Mac: nobody).
- On Python 3.10 two tests fail that have nothing to do with the part's
logic: an older test of Walka's start and stop, and the test that the
card's script is served as "text/javascript" (as its older twin for
the Share view fails there: Python 3.10 names that kind of file
differently). The part's own tests pass on 3.10.

From `ACCOUNT-WORK.md` and the builders' reports:

- The web library asks a read once more by itself when the line is cut:
a read (never a send of walks) can reach the Walka accounts twice.
- A key renewal refused with any other word than the two the brief
names stops the run and ends nothing.
- At a start nothing reads the password store (the brief's rule), so
the card shows the e-mail address from the file until the first
request finds the store empty.
- Where the sign-in is kept in memory (Linux), a second Walka on the
same folder signs the first one out in the file.
- Python 3.10 is stated in the README and checked nowhere on CI; five
older tests already failed there before this work.
- On Windows and Linux a person can add root certificates through two
system variables; checking itself cannot be switched off.
- Every download holds the part's code and the hidden command
`httpscheck`, which fetches walka.dev's front page if somebody types
it. No part is ever made from that code in a download. SECURITY.md
names the command.

## 4. Where the contract (`docs/data-model.md`) was wrong or silent

Line numbers are of the website's files as read (commit `264e54b`).

**Wrong, or against the brief's rules - Walka follows the brief:**

1. *When the version of the rules is read.* Contract, line 343: "read
 `schema_version()` when they start". The brief: before the first
 send of a run, never at a start. Walka: once in a run, just before
 the first walk goes.
2. *When the key is renewed.* Line 300: "Refresh in one place only,
 shortly before the hour is up". The brief: only when a request is
 due and the key has run out; and "one place" is not enough, because
 two Walkas can run on one folder. Walka: no timer; the lasting key
 is read from the store just before each renewal and the new one
 written at once.
3. *What to say when the sign-in is over.* Lines 305-306: "ask the
 person to sign in again". The brief forbids it (somebody who has
 just deleted their Walka account would make a new one). Walka: the
 sentence of section 2, asking for nothing.
4. *Which refusals go walk by walk.* Lines 204-213 list `PGRST102`,
 `PGRST204` and `428C9` among the refusals and say "For any other,
 send the rows one by one". Those three mean the program built the
 request wrongly and say nothing about a walk. Walka: only a refusal
 about one row goes walk by walk; those three stop the run and set
 nothing aside (as the website's own code does).
5. *The proof.* Lines 289-290: "Not tried: a whole sign-in against the
 live project. Do that first, with a script of twenty lines". It
 needed no script and no key: the owner opened one address. A whole
 sign-in is still untried.
6. *The reader in Python.* Lines 222-223 call `walka_row.py` "the same
 reader". At three edges that no file written by Walka reaches it is
 not (a line end after the name; "NaN" in the file; a number too
 large). Walka's copy goes by the website's browser code.

**Silent - what Walka does:**

7. *Telling a deleted Walka account from a new sign-in.* Lines 310-312
 say a send of walks cannot tell them apart. Nothing says that the
 read of the profile cannot either (both answer an empty list, for up
 to an hour). Walka asks who is signed in (`GET /auth/v1/user`)
 whenever the profile has no row, before it says that this sign-in
 has no Walka account yet.
8. *The look-up of walks that did not come back.* Line 155 asks for
 `select=id,deleted_at` and does not say how an answer is matched to
 a walk, in which order rows come, or how many starts fit in one
 request. Walka asks for `id,start_key,deleted_at`, matches by the
 start, and asks for 50 at a time.
9. *What comes back from a send.* Line 152: the rows added, "in the
 order sent". Walka asks for the ids only (`select=id`) and matches
 by id, not by order - so the answer does not carry the whole row.
10. *An id that is taken.* Lines 144-145 name `23505` with
  `walks_pkey`. The same number also means "this start is there
  already" when a request is built without its conflict rule. Walka
  makes a new id only when the answer names `walks_pkey`.
11. *Signing out with a key that has run out.* Lines 313-316 give the
  order (forget first, then tell the service) and not this case. The
  brief: the service refuses it; do not renew in order to sign out.
  Walka: no courtesy call then.
12. *A renewal refused with another word* than the two of lines
  306-307: nothing is said. Walka stops the run and ends nothing.
13. *The third way to the simulated treadmill.* Lines 255-259 name
  `--demo` and `--simulate`; `"kind": "simulated"` in `config.json`
  needs neither. And nothing is said of a real walk with an empty
  treadmill name (Walka writes one when a game session is marked
  before the pad first connects): Walka sends it.
14. *How long the short key lasts.* Line 278 gives `expires_in` and
  `expires_at`, and the brief says to go by the second. Walka counts
  the seconds of the first from the moment the answer came, on its
  own clock: the second is by the server's clock, and the computer's
  may be wrong.
15. *The `device` column.* Lines 243-245: "A program may fill it."
  Walka does not send it.
16. *`/auth/v1/settings`* is recorded nowhere. Walka never asks it.

**Beside the contract.** `docs/accounts-setup.md` says both that a
return to 127.0.0.1 is allowed (lines 142-149) and that it is not
(313-316); the owner's proof settled it. And the eighteen places where
the BRIEF and the files disagreed were told to the owner on 21 November
and stand in `ACCOUNT-WORK.md`; those that matter to the website are in
section 5.

## 5. For the website's maintainer

The list at the end of the brief's section 7, item by item.

### 5.1 Exactly what the program now does

Section 2 above, whole. Four addresses of the website are linked from
the dashboard and the README and must stay: `walka.dev/terms/`,
`walka.dev/privacy/`, `walka.dev/account/`, `walka.dev/account/walks/`.

### 5.2 Every changed sentence

Old words and new, with file and line: `ACCOUNT-WORDS.md` on the
branch (198 sentences and places, each looked up in the files by a
script). In kind:

- the dashboard: six places where words that were there changed or got
a second form (the two feet, the Share view, the question before a
walk is deleted, its button and the short notice after it), and the
new card with its question - about a hundred sentences, button names
and short states;
- `README.md`: four old sentences changed, about ten added to
paragraphs that were there, and one new section of about thirty-five;
- `SECURITY.md`: one point changed, two new points, three sentences
added to a fourth; `docs/SAFETY.md`: one sentence; both copies of the
notices: one line.

The two that the website quotes word for word:

1. **The rail's foot** (privacy page, `+page.svelte` lines 286-287, "In
 its own words"). Old, and still what it says while sending is off:
 "Runs on this computer. Nothing is uploaded." New, while sending is
 on: "Runs on this computer. Your walks are sent to your Walka
 account." The quote is no longer the only thing the foot can say.
2. **The sixth of the README's six features** (home page,
 `Features.svelte` lines 46-47). Old: "Stays on your computer.
 Nothing is uploaded. Walka never presses keys, moves the mouse or
 reads the game's memory - it looks at the top-left corner of the
 game window". New: "Stays on your computer. Nothing is uploaded
 unless you sign in to your Walka account (on walka.dev) and switch
 sending on yourself. Walka never presses keys, ..." (the rest as it
 was). The other five are unchanged.

The website says "nothing is uploaded" or its like in 27 places of its
live pages (privacy 12, questions 6, home 3, the Walks page 2, one each
on how-it-works, downloads and safety, and the privacy page's part on
sessions, which is worded for browsers only). Its screenshots of the
dashboard show the rail's foot and the Share view's older sentence.

### 5.3 For the privacy page

- *A session for each computer where Walka is signed in.* The brief
states it: when it began, when it was last renewed, the IP address,
and the name - which is `Walka/<version>` and nothing else (seen on
the wire against the stand-in). Each renewal is noted. Walka renews
only when it is about to ask something and its short key has run out.
What the sign-in service really stores was not looked at in this
work: nothing touched the live project.
- *Through Cloudflare, as a browser's requests.* Walka asks the
project's address directly and uses no proxy. Taken from the privacy
page as it stands; not seen in this work.
- *Discord, GitHub or Google learn of each sign-in made from Walka.*
The sign-in goes through the person's browser and the other site, as
on the website. Walka throws the other site's own keys away unread.
- *Signing in from Walka makes a sign-in record for a person who has
none.* True, and the card says so before the browser opens. Until the
first step is taken on the website nothing can be sent. The card then
sends the person to walka.dev/account/ - where "No thanks: delete
this sign-in" acts at once, without a question.
- *Where a signed-in Walka keeps its key, and what the new file holds.*
Section 2, "What is kept on the computer". Note that the file keeps
the e-mail address and the id of every Walka account that was ever
signed in on that computer, also after signing out.
- *A public page shows each walk as soon as it is sent.* Any public
page: the number of walks, the time and the distance are given out
whenever the page is public, not only on pages that list days or
walks. The question says so.
- *"Sign out" on the website does not sign Walka out.* True; the card
and the README say so.

Also for that page:

- It already says "A program that sends walks may add a random number
that tells one installation from another". Walka does not send one.
- It lists three ways a walk "got here". A walk sent by Walka says
"walka", exactly as one read from Walka's file in the browser: the
website cannot tell the two apart.
- What reaches the database and should be named there: the sixteen
things of section 2 - among them the walk's name on the computer and
the calories on top of sitting.

### 5.4 For the terms

- A computer where Walka is signed in stays so until somebody signs out
ON it, the Walka account is deleted, or the sign-in ends. The website
has no "sign out everywhere" and no list of signed-in computers
(section 7, still open, 3).
- Walka sends every walk recorded on that computer, whoever walked. The
terms say "Put in your own walks": the question tells the person so
in one sentence, and Walka does not ask after each walk.

### 5.5 What the reviews found that the maintainer should know

- Walka reads walks back: after a send, for the walks that did not come
back, one `GET` on the walks by their start, to tell "there already"
from "deleted there". It reads nothing else of the walks.
- It reads two things of the profile (whether the first step is taken;
the page's name and its switches for public, days and walks) and asks
who is signed in. It writes to the walks only: no change, no delete,
nothing to the profile, the body numbers, the foods or the diary,
ever.
- Two different walks that start in the same second: the second is
written down on the computer as "there already" and its numbers never
go. That is the contract's rule.
- A Walka account that is nearly full takes none of a batch of up to
200, though some would fit; the card then says it is full.
- The website deletes one walk at a time. "All of them" is undone walk
by walk, or by deleting the Walka account. The question says so.
- The `device` column is not sent.
- A public page shows a sent walk at once (5.3 above).
- A page the site's owner has taken down still reads as public to
Walka, so the question's sentence about a public page is shown
(reasoned from the database's rules, not tried).
- When the version of the database's rules goes above 1, every Walka in
use stops sending and says that it is too old, until people get a
newer one.

## 6. What is left before a release

In this order.

1. **The owner's yes to the words** - `ACCOUNT-WORDS.md`, and the
 questions of section 7.
2. **The website changes first**: the privacy page and the terms, with
 their dates - everything in section 5 - BEFORE any build that can
 reach the live database exists in public. The default branch's
 README is the page people download Walka from: this branch is not
 merged before then.
3. **The owner's trial, from source, against the live project.** In
 plain steps:
 - a Windows PC or a Mac with Python (CI uses 3.12) and Walka set up
   from its source code as the README says;
 - a real treadmill, or a data folder with real walks: the simulated
   treadmill does nothing here, by design;
 - the first step taken on walka.dev/account/ for the Walka account
   that is to be used;
 - in the window Walka is started from, the two variables
   `WALKA_ACCOUNT_URL` and `WALKA_ACCOUNT_KEY` set to the project's
   address and its public key, as they stand in the website's own
   pages - on that computer only, in no file of the repository, no
   issue and no message;
 - then: sign in; read the card and the question; choose "Only walks
   from now on"; walk a minute; look at walka.dev/account/walks/;
   "Stop sending"; "Sign out".
4. **What only a real Windows PC and a real Mac can show** - a hand
 check during that trial:
 - the browser comes back to "Walka is signed in. You can close this
   tab.", with each of Discord, GitHub and Google;
 - Credential Manager (Windows) or Keychain Access (Mac) shows one
   entry whose name begins "Walka account" (two while sending is
   on: the sign-in and the mark), and none is left after
   "Sign out";
 - Walka opened a second time while a walk is going: the walk is in
   History once, whole, after it ends;
 - a Mac shows no password box by surprise;
 - the four links to walka.dev open in the web browser, not in
   Walka's own window;
 - on Windows: signed in, sending on, a walk waiting, the network
   cable out - close the window: Walka is gone from Task Manager
   within four seconds and the walk is in the `sessions` folder
   (note N21);
 - in a window a little narrower than Walka opens, the sentence
   "Your walks are sent to your Walka account." is in sight;
 - later, once a packed Walka has the part: the Mac's keychain after
   an update of Walka.
5. **Then a change of a few lines**: the address and the key go into
 the program, and the from-source rule is lifted. The tests and the
 build check that prove "a download has no part" are changed with it,
 knowingly.
6. **The version number.** A tag `v0.8.0-rc1` already exists, at
 another commit, and a published release is never replaced: so a new
 number. The three version files: `Walka/Walka.toc`,
 `companion/pyproject.toml`, `companion/src/walka/__init__.py`.
 (GitHub will not tag a commit that itself changes the workflow file;
 the commit with the version files does not.)
7. **The pictures** `docs/images/dashboard-*.png`: they show the Live
 view with sending off, and those words are unchanged. New pictures
 only if the owner changes the foot's words.
8. **Remove** `ACCOUNT-WORK.md`, `ACCOUNT-WORDS.md` and this report's
 file `ACCOUNT-REPORT.md` before the merge.

## 7. The owner's answer of 24 November, and what is still open

On 24 November the owner answered the report and its questions: "Do
everything and then push and publish". It was read as yes to the
words and to each recommendation. That reading is the assistant's, not
the owner's words: say if any of it was not meant.

What was done about each question:

1. **The words** (`ACCOUNT-WORDS.md`): taken as yes. The sentences
 written after that answer are marked there as seen by nobody but
 their writer.
2. **A backup put back (C7)**: closed, as section 3a says, with the
 limits of section 3b.
3. **Deleting a walk of which a copy went to another Walka account**:
 the third wording stays, and a person who has signed out sees it too
 - only for walks of which a copy was sent.
4. **The first step not taken**: the three places that say "your Walka
 account" beside "no Walka account yet" stay as they are.
5. **Five sentences of the brief in which the word stood alone**: all
 five now say "Walka account" - "or the Walka account was deleted",
 "goes to this Walka account, whoever walked", "Stopping deletes
 nothing in your Walka account", "If you already have a Walka
 account", "It stays in your Walka account". A test now fails on any
 sentence of the card or of History that says the word alone.
6. **Decision 4's changed sentence** ("it is under another e-mail
 address"): stays.
7. **README line 123, "Only listens."**: left as it is.
8. **A narrow window**: built. While sending is on, and only then,
 "Your walks are sent to your Walka account." stays on every view
 below 1,180 pixels. Without sending, every picture of the page is dot
 for dot what it was. (Its one limit is in section 3b.)
9. **The notices in the game's folder**: the line stays in both
 copies.
10. **Three small things put right on the way**, each new and for the
  owner's eye: the question now names all sixteen things that go (it
  left out the random number and "it came from Walka"); "Nothing is
  sent until you switch sending on." (it said "that"); and "With "All
  of them" your whole history shows there at once." is now said only
  of a public page that lists days or walks, of which alone it is
  true. Where the password store did not let a sign-in go, the card
  now ends "There may be two such entries: delete both."

Still open:

1. **What "publish" meant.** It was NOT read as leave to release
 Walka, to tag, to merge, to put in the live address and key, or to
 touch the version files. The brief puts two things before a release
 that are not done: the website's privacy page and terms must change
 first, and the owner tries it once from source against the live
 project. A published release is never replaced, and a release now
 would be the first time anybody signs in or sends a walk for real.
 What was published is this report, as a private page the owner can
 pass on. If a release was meant, the owner says so in so many words.
2. **Windows or a Mac** for the trial.
3. **"Sign out everywhere" on the website** before the release? Today a
 computer that is lost, sold or shared stays signed in until somebody
 signs out on it or the Walka account is deleted. Recommended: yes,
 or at the least a plain sentence in the terms and on the privacy
 page. (The website maintainer's work.)
4. **The readings of "Do everything"**, of 21 and of 24 November: both
 lists are in `ACCOUNT-WORK.md`. Say if any was not meant.

(The first of these was answered the same day: section 8.)

## 8. The release, 24 November

**The owner's word.** Asked what "publish" meant, with three choices
(the report page is enough; open a pull request; release it now - the
last with the words that it goes against the brief's order, that nothing
has been tried on a real Windows PC or Mac, and that it cannot be
undone), the owner answered: "Release it now".

**What was changed for it.**

- The address of the Walka accounts and their publishable key now
stand in the program, in one file and nowhere else
(`companion/src/walka/walka_account/project.py`). Both are public by
design: the website's own pages carry the same two. They were taken
from the brief and then compared, letter for letter, with what the
public website itself serves: the same. It is never a secret key.
- The from-source rule is lifted. A download (Walka.exe, Walka on a
Mac) now has the part, with that address and no other: it does not
so much as read the two variables. From source the two variables
still name a developer's own database. With the simulated treadmill
there is still no part.
- Having the part asks nobody. A Walka in which nobody has signed in
makes no connection, reads no password store and shows nothing but
the quiet card last in Settings. That was proved again for the
default part, built as a download builds it.
- Now that there is something to go to, no test may go there: in every
test the built-in address reads as "none", and a guard fails any test
in which anything tries to reach another computer. Under a tripwire
of the reviewer's own, the whole suite - 4,495 tests, 109
processes - made no connection and no look-up of a name beyond this
computer.
- The build check that proved "a download has no part" now proves what
a download must be (`tools/check_account_part.py`): started with the
two variables set to something else, it has the part, nobody signed
in, sending off, the variables ignored, nothing written; and started
as the demo it has none. It presses "Stop sending" only - never "Sign
in", which would open a browser.
- The version is 0.8.0-rc2 in the three version files.
- The release's own page gained one paragraph, from words already
agreed: that a Walka account is optional, that Walka sends nothing
and asks nobody if you never sign in, and where the README says what
is sent, when, and how to stop.
- 4,496 tests pass here after it.

**What kind of release.** Chosen by the assistant, not by the owner:

- **A trial release, v0.8.0-rc2**, as v0.8.0-rc1 was. Walka's own
release job publishes a version with "-rc" as a trial: it is public,
and it never becomes "the latest" download that the README's buttons
give. Nobody has yet signed in or sent a walk for real, so the first
people to do so should be people who chose a trial. Making it 0.8.0
proper is one more step, after the owner has tried it.
- **From this branch, by a tag, with no merge.** The default branch's
README is the page people download Walka from; it is unchanged, so
it does not speak of sending before the website does.

**What this release cannot have behind it**, said plainly:

- No whole sign-in and no send has ever been made against the real
accounts, by anybody. This release is the first time.
- Nothing ran on a real Windows PC or a real Mac outside the build
machines. On a newly set-up Windows PC a root certificate may be
missing; the card then says that Walka could not check that it is
talking to your Walka account's server, and nothing is sent.
- The website's privacy page and terms do not yet say what Walka now
does (section 5 is the list for that). Its pages still say "nothing
is uploaded". The card and the question in Walka say what goes.
- If the trial shows a fault, the fix goes out as a new number: a
published release is never replaced.

**What the owner can do now.** Download v0.8.0-rc2 from the releases
page (it is marked as a pre-release), start it with a real treadmill
or real walks, and do the trial of section 6, point 3, with the
download itself: no source code and no variables are needed any more.
