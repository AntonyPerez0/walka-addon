# The words of this change: Walka sends walks to a Walka account

Working notes, like `ACCOUNT-WORK.md`: this file belongs to the branch
`claude/walka-account-signin-sync-2xtpeb` and is removed before the branch
is merged. The live project's address and key stand nowhere in it, and
neither does anybody's e-mail address.

It is for the owner, whose words these are, and its last part is for the
website's maintainer. It holds every sentence of this change that a person
reads, as it stands now: the dashboard first, then the documents. It takes
the place of the list "The words, as drafted" that stood in
`ACCOUNT-WORK.md` and of two scratch lists that were never on the branch. A
yes to the words is a yes to this list.

## How to read it

- After each sentence stands where it is: the file and the line. A script
  looked every sentence and every line number up in the files as they are on
  24 November 2026; none is written from memory.
- `<e-mail>` is the e-mail address of the Walka account, `<way>` is Discord,
  GitHub or Google, `<host>` is the address Walka asks, `<n>` is a number,
  `<day and time>` is a day and a time.
- "The brief" is the written order for this work
  (`docs/brief-walka-sends-walks.md` in the website's repository), and
  "decision 5" is the fifth of its twelve decisions.
- Each sentence carries one of three marks:
  - **brief** - the brief's own words, word for word.
  - **drafted** - drafted, shown to the owner on 21 November (it stood in
    the list "The words, as drafted", which was in `ACCOUNT-WORK.md` until
    this file took its place).
  - **new** - not among the words shown on 21 November. It stood in this
    list when the owner answered on 24 November - unless it says that it was
    written, added or changed on 24 November: those the owner has not seen.
- Where a sentence took the place of another, the old words stand beside it.
- "In this round" means the round of fixes after the last review, before the
  owner's answer of 24 November. What came after that answer says "24
  November".
- On 24 November 2026 the owner answered the questions of part 4 with "Do
  everything" - read as yes to the words, and yes to each recommendation.
  What was changed on that answer says so where it stands. A sentence that
  was written after it is marked **new**: the owner has said what it should
  say, and has not seen how it says it.
- If time is short: part 3, "Ten to look at first", which now says what is
  settled and what is still to read.

## 1. The dashboard

### 1.1 Words that were on the page before this change

- The rail's foot, on every view. Old: "Runs on this computer. Nothing is
  uploaded." (was `index.html:62`). Now (`index.html:62`): the same, word
  for word, unless sending is on. While sending is on: "Runs on this
  computer. Your walks are sent to your Walka account."
  (`walkaaccount.js:58`) **drafted**. In a window narrower than 1180 pixels
  the rail has no room for its foot, and the page puts it away. That is
  still so while sending is off. While sending is on, the foot's second
  sentence stays in sight there, by itself: "Your walks are sent to your
  Walka account." (`walkaaccount.js:61`) - in small letters at the end of
  the narrow rail, under the four lamps, and in a window narrower than 720
  pixels in one line under the four lamps at the top. No new sentence: it is
  the foot's own. Built on the owner's answer of 24 November: question 6 in
  part 4.
- The foot of Settings. Old: "Everything on this page stays on this
  computer." (was `index.html:488`). Now (`index.html:499`): the same, word
  for word, unless sending is on. While sending is on: "Your walks are sent
  to your Walka account. Everything else on this page stays on this
  computer." (`walkaaccount.js:64`) **drafted**
- The Share view. Old: "The picture is made on this computer. Nothing is
  uploaded: you choose where to post it." (was `index.html:351`). Now, for
  everybody, with or without a Walka account: "The picture is made on this
  computer. It is not uploaded: you choose where to post it."
  (`index.html:351`) **drafted**
- History, the question before a walk is deleted: "Delete this walk? It
  disappears from your totals here and from the addon's history. This cannot
  be undone." (`history.js:166`) with the button "Delete permanently"
  (`history.js:161`) and afterwards "Walk deleted." (`history.js:186`) Not
  changed for a walk of which no copy was sent. For the others see 1.6.

Nothing else outside the card changed: no pill, no line in "Getting
started", no word on the Live view. The head of Settings still reads "Units,
your profile and your devices."

### 1.2 The card "Walka account", last in Settings

There is no card at all when Walka has no such part (every download today),
or while the simulated treadmill is in use.

Heading: "Walka account" (`index.html:492`). **drafted**

In the head, only while somebody is signed in: "Sending on" / "Sending off"
(`walkaaccount.js:318`). **new**

**Nobody is signed in**

- "Optional. Walka works in full without it." (`walkaaccount.js:427`)
  **drafted**
- Buttons "Sign in with Discord", "Sign in with GitHub", "Sign in with
  Google" (`walkaaccount.js:441`). **drafted**
- "Signing in makes a Walka account if you have none: terms
  (walka.dev/terms/), privacy (walka.dev/privacy/). Nothing is sent until
  you switch sending on. Your web browser opens for this, and Walka asks
  `<host>`." (`walkaaccount.js:448`) The two addresses are links.
  **drafted** (its first sentence is the brief's), but for its second
  sentence, which is **new** - changed on the owner's answer of 24 November.
  Old: "Nothing is sent until you switch that on." (the brief's own words).
  Why: "that" pointed at nothing on the card.

**While signing in**

- "Waiting for your web browser. Sign in there, then come back."
  (`walkaaccount.js:309`) **drafted**
- Button "Stop waiting" (`walkaaccount.js:436`). **new** (without it the
  card waits five minutes for a browser nobody is looking at)

**A sign-in that did not work, or a stored sign-in that is gone**

- "Nothing was signed in." (`walkaaccount.js:72`) **drafted**
- "Walka could not finish signing in. Try again." (`walkaaccount.js:73`)
  **drafted**
- "Nobody came back from the browser. Try again." (`walkaaccount.js:74`)
  **drafted**
- "Walka could not keep the sign-in on this computer, so nothing was signed
  in." (`walkaaccount.js:75`) **drafted**
- "Walka could not check that it is talking to your Walka account's server,
  so it asked nothing." (`walkaaccount.js:76`) **new** - changed in this
  round. Old: "Walka could not check that it is talking to your account's
  server, so it asked nothing." (**drafted**)
- "There was no answer. Try again later." (`walkaaccount.js:77`) **drafted**
- "The sign-in is not on this computer any more. Nothing is sent."
  (`walkaaccount.js:78`) **drafted**
- "macOS did not let this version of Walka read the stored sign-in. Sign in
  again." (`walkaaccount.js:195`) **drafted**
- "Windows did not let Walka read the stored sign-in. Sign in again."
  (`walkaaccount.js:196`) **new**
- "The computer that runs Walka did not let it read the stored sign-in. Sign
  in again." (`walkaaccount.js:197`) **new** (for a page that is open on
  another device, which cannot tell which system Walka runs on)

**Signed out, and the sign-in may still be in the system's password store**
(all **new**, written in this round: before, the card said nothing)

- On Windows: "Walka could not make sure that your sign-in was taken out of
  Windows' Credential Manager. Try again, or delete the entry there by hand:
  its name begins "Walka account". There may be two such entries: delete
  both." (`walkaaccount.js:211`)
- On a Mac: "Walka could not make sure that your sign-in was taken out of
  your Mac's keychain. Try again, or delete the entry by hand in Keychain
  Access: its name begins "Walka account". There may be two such entries:
  delete both." (`walkaaccount.js:213`)
- On a page that is open on another device: "Walka could not make sure that
  your sign-in was taken out of the password store of the computer that runs
  Walka. Try again, or delete the entry there by hand: its name begins
  "Walka account". There may be two such entries: delete both."
  (`walkaaccount.js:214`)
- Button "Try again" (`walkaaccount.js:420`).
- The last sentence of each, "There may be two such entries: delete both."
  (`walkaaccount.js:209`), is **new**: added on 24 November, after the
  owner's answer, and seen by nobody but its writer. Old: each of the three
  ended at "... its name begins "Walka account"." Why: while sending is on
  Walka now keeps a second entry in that password store, a mark (2.1, "Where
  it is kept"), and its name begins "Walka account" too. Signing out takes
  it out with the sign-in, and so does "Try again". If the store lets go of
  neither, two entries are left there - and a person who is told of "the
  entry" and deletes one of the two by hand may leave the sign-in where it
  is. The sentence is true of one entry and of two, and the README says the
  same (2.1). See question 5 in part 4.

**Signed in**

- "Signed in as `<e-mail>`, with `<way>`." (`walkaaccount.js:460`) The
  e-mail address whole, in bold. **drafted**
- "Signed in as `<e-mail>`." / "Signed in with `<way>`." / "Signed in."
  (`walkaaccount.js:461`) **new** (when the way or the e-mail address is not
  known)
- Button "Sign out" (`walkaaccount.js:462`). **drafted**
- ""Sign out" on walka.dev does not sign this computer out: sign out here."
  (`walkaaccount.js:467`) **drafted**
- Straight after it: "Signing out here may ask your Walka account one last
  time, to tell it so." (the same line) **new** (a button that makes Walka
  ask says so)
- And one of:
  - "The sign-in is kept in Windows' Credential Manager."
    (`walkaaccount.js:188`) **drafted**
  - "The sign-in is kept in your Mac's keychain." (`walkaaccount.js:189`)
    **drafted**
  - "The sign-in is kept in memory, until Walka is closed."
    (`walkaaccount.js:187`) **drafted**
  - "The sign-in is kept in the password store of the computer that runs
    Walka." (`walkaaccount.js:190`) **new** (for a page that is open on
    another device)

**The first step is not taken** (decision 4)

- "This sign-in has no Walka account yet. To open one - or to delete this
  sign-in - go to walka.dev/account/ and sign in there the same way (with
  `<way>`). If you already have a Walka account, it is under another e-mail
  address: sign out here and sign in the way that uses that address."
  (`walkaaccount.js:491`) **drafted**, with "a Walka account" for "an
  account" in its last sentence by the owner's answer of 24 November
  (question 2 in part 4) - and NOT the brief's words at one place, which
  stays so by the same answer: question 3 in part 4.
- A link that looks like a button: "Open walka.dev/account/"
  (`walkaaccount.js:493`). **drafted**
- Button "Look again (asks your Walka account)" (`walkaaccount.js:478`)
  **drafted**
- Under it the card now says only "Nothing is sent." (`walkaaccount.js:170`)
  **new** - changed in this round: before, it counted walks here as well ("5
  walks on this computer are not in your Walka account"), beside "no Walka
  account yet". Nor does it list walks in this state (the lines of "Walks
  that did not go", below): "your Walka account did not take ..." would
  speak of a Walka account too.

**No e-mail address is known**

- "The sign-in gave Walka no e-mail address, so nobody could tell which
  Walka account your walks would go to. Sending cannot be switched on."
  (`walkaaccount.js:502`) **new**

**Sending is off**

- "Nothing is sent. Walka has not sent `<n>` walks on this computer to your
  Walka account." (`walkaaccount.js:165`) and for one walk "Nothing is sent.
  Walka has not sent 1 walk on this computer to your Walka account."
  (`walkaaccount.js:164`) **new** - changed in this round. Old: "Nothing is
  sent. `<n>` walks on this computer are not in your Walka account."
  (**drafted**). Why: Walka does not know what is in the Walka account.
  Walks that were put in on the website are there, and Walka has asked
  nobody. And why "to your Walka account": after signing out and signing in
  to another Walka account, the walks that went to the first one are counted
  here again - Walka has sent them, but not to this one.
- When none waits: "Nothing is sent. `<n>` walks on this computer were sent
  before, or were in your Walka account already." (`walkaaccount.js:169`)
  and for one walk "Nothing is sent. 1 walk on this computer was sent
  before, or was in your Walka account already." (`walkaaccount.js:167`)
  **new** - changed in this round. Old: "Nothing is sent. `<n>` walks on
  this computer are in your Walka account already." (never shown to the
  owner). Why: a walk that was deleted in the Walka account since is counted
  here too.
- When there is no walk at all: "Nothing is sent." **new**
- Button "Send my walks to my Walka account..." (`walkaaccount.js:521`) and
  beside it "Asks your Walka account first." (`walkaaccount.js:536`)
  **drafted**
- "Asking your Walka account..." (`walkaaccount.js:352`) **new**
- "Switching sending on..." (`walkaaccount.js:351`) **new**
- "The question ran out after ten minutes. Nothing was switched on."
  (`walkaaccount.js:353`) **new**
- "That did not work just now. Try again later." (`walkaaccount.js:98`)
  **new** (a button's work ended early: the answer could not be used, or the
  switch could not be written down - or, since 24 November, the password
  store would not keep the mark that goes with the switch: 2.1, "Where it is
  kept")
- "There was no answer. Try again later." (`walkaaccount.js:97`) and "Walka
  could not check that it is talking to your Walka account's server, so it
  asked nothing." (`walkaaccount.js:99`) - the same two sentences as under
  "A sign-in that did not work".

**Sending is on**

- "Sending is on. Each walk goes to your Walka account when it is saved."
  (`walkaaccount.js:511`) **drafted**
- "`<n>` sent, `<n>` waiting." (`walkaaccount.js:507`) **drafted**
- After "Only walks from now on", on the same line: "Only walks from `<day
  and time>` on are sent." (`walkaaccount.js:508`) **new**
- Button "Stop sending" (`walkaaccount.js:509`). **drafted**
- "Sending now..." (`walkaaccount.js:337`) **new**
- After a try:
  - "Sent." (`walkaaccount.js:84`) **drafted**
  - "No answer - Walka tries again later." (`walkaaccount.js:85`)
    **drafted**
  - "There was no answer. Try again later." (`walkaaccount.js:86`)
    **drafted** (here for a button that asked)
  - "This Walka is too old for your Walka account as it is now: get the
    newest Walka." (`walkaaccount.js:87`) **new** - changed in this round.
    Old: "This Walka is too old for the account as it is now: get the newest
    Walka." (**drafted**)
  - "Your Walka account is full: it has room for 20,000 walks."
    (`walkaaccount.js:88`) **new** - changed in this round. Old: "The
    account is full: it has room for 20,000 walks." (**drafted**)
  - "Walka stopped for now and tries again when it is next started."
    (`walkaaccount.js:89`) **drafted**
  - "Walka could not check that it is talking to your Walka account's
    server, so it sent nothing." (`walkaaccount.js:91`) **new** - changed in
    this round. Old: "Walka could not check that it is talking to your
    account's server, so it sent nothing." (**drafted**)

**Walks that did not go** (under the card, in both states)

- "Your Walka account did not take `<n>` walks. They stay here."
  (`walkaaccount.js:154`) and "Your Walka account did not take 1 walk. It
  stays here." (`walkaaccount.js:153`) **new** - changed in this round. Old:
  "The account did not take `<n>` walks. They stay here." (**drafted**).
  Under it each walk's day and time, and what the Walka account itself said
  of it, as it came: for example `new row for relation "walks" violates
  check constraint "walks_distance_m_check"`. That text is not Walka's.
- "`<n>` walks are outside what your Walka account takes, and stay here."
  (`walkaaccount.js:150`) and "1 walk is outside what your Walka account
  takes, and stays here." (`walkaaccount.js:149`) **new** - changed in this
  round. Old: "`<n>` walks are outside what the account takes, and stay
  here." (**drafted**). Under it each walk's day and time, and why. The
  reasons are the website's own, for example "There are more than 400,000
  steps." or "It lasts longer than two days."
- After twenty walks: "and `<n>` more" (`walkaaccount.js:648`). **new**
- "`<n>` walks do not say which treadmill made them, and are not sent."
  (`walkaaccount.js:146`) **drafted**; for one walk "1 walk does not say
  which treadmill made it, and is not sent." (`walkaaccount.js:145`) **new**
- "`<n>` walks could not be read, and are not sent." (`walkaaccount.js:156`)
  and "1 walk could not be read, and is not sent." (`walkaaccount.js:156`)
  **new**

**When the sign-in ended** (decision 11)

- "Walka is no longer signed in to your Walka account, and has stopped
  sending. You signed out elsewhere, or the Walka account was deleted. Your
  walks on this computer are untouched." (`walkaaccount.js:68`) - the
  brief's words, with "Walka account" for "account" by the owner's answer of
  24 November (question 2 in part 4). Old: "You signed out elsewhere, or the
  account was deleted."
- Button "OK" (`walkaaccount.js:413`). **drafted**

**When Walka does not do what a button asked** (shown as the page's usual
red notice; all **new**)

- "Walka is still asking your Walka account. Try again in a moment."
  (`walkaaccount.js:109`)
- "Walka is closing." (`walkaaccount.js:110`)
- "The Walka account is not available in this Walka."
  (`walkaaccount.js:111`)
- "Walka is not signed in to a Walka account." (`walkaaccount.js:112`)
- "Walka is signed in already." (`walkaaccount.js:113`)
- "The sign-in gave Walka no e-mail address, so sending cannot be switched
  on." (`walkaaccount.js:114`)
- "The question ran out. Nothing was switched on." (`walkaaccount.js:115`)
- "That did not work. Try again." (`walkaaccount.js:116`)

### 1.3 The question before sending (decision 5)

A panel in the card. Every switching-on passes it.

- "Send my walks to my Walka account?" (`walkaaccount.js:121`) **drafted**
- "To `<e-mail>`, signed in with `<way>`." (`walkaaccount.js:548`)
  **drafted**
- "What goes, of each walk: when it started and ended, the day, and how far
  this computer's clock was from world time; how long the belt moved;
  distance, steps and calories (estimates), and how many of the calories
  were on top of sitting; how each number was come by (for the calories:
  from speed or from heart rate - the word, not the heart rate); whether a
  walking session was running in the game; and the walk's name on this
  computer, which is its start time. Each walk goes under a random number
  made for it, and says that it came from Walka." (`walkaaccount.js:122`)
  **drafted**, but for its last sentence, which is **new** - added on 24
  November. Why: a question that says what goes must say all of it, and
  sixteen things go with each walk. The first sentence left two of them out:
  the random number the walk is kept under, and the word that says it came
  from Walka. The added sentence is the README's own, word for word (2.1).
- "What stays here: the samples, the heart rate, the top speed, the
  treadmill's name, the character, your weight, height, age and sex."
  (`walkaaccount.js:124`) **drafted**
- "Where: your Walka account, in the database the site's owner runs
  (`<host>`). walka.dev/privacy/" (`walkaaccount.js:125`) The address is a
  link. **drafted**
- "`<n>` walks would go now." **drafted** / "1 walk would go now." / "No
  walk would go now." **new** (`walkaaccount.js:142`)
- The choice, neither chosen at first: "All of them" (`walkaaccount.js:126`)
  / "Only walks from now on" (`walkaaccount.js:127`) **drafted**. Its name
  for screen readers: "Which walks to send" (`walkaaccount.js:566`) **new**
- "From then on each walk goes when it is saved, without asking again."
  (`walkaaccount.js:128`) **drafted**
- Where it applies, the two lines of 1.2 about walks that do not say which
  treadmill made them and walks outside what your Walka account takes.
- Only if the page is public: "Your page on walka.dev is public. Walks you
  send count on it as soon as they are sent, so anyone who reloads it can
  tell when you were on the pad." (`walkaaccount.js:130`) **drafted** (up to
  "as soon as they are sent" it is the brief's)
- Straight after it, in the same lines, only if that public page lists days
  or walks: "With "All of them" your whole history shows there at once."
  (`walkaaccount.js:133`) **drafted**. That it is said only of such a page
  is **new**, of 24 November: before, it was said of every public page. Why:
  a public page that lists neither days nor walks gives out sums and nothing
  else - how many walks, how long, how far. There the walks count at once,
  as the sentence before says, but no history shows.
- "Every walk recorded on this computer goes to this Walka account, whoever
  walked." (`walkaaccount.js:134`) - the brief's words, with "Walka account"
  for "account" by the owner's answer of 24 November (question 2 in part 4).
  Old: "... goes to this account, whoever walked."
- "To stop: switch it off here, or sign out. Stopping deletes nothing in
  your Walka account. There a walk is deleted one at a time:
  walka.dev/account/walks/" (`walkaaccount.js:135`) The address is a link.
  **drafted**; "stopping deletes nothing in your Walka account" is the
  brief's words, with "Walka account" for "account" by the owner's answer of
  24 November (question 2 in part 4). Old: "Stopping deletes nothing in the
  account."
- Buttons "Send my walks" (`walkaaccount.js:136`) and "Not now"
  (`walkaaccount.js:137`) **drafted**; beside them until a choice is made
  "Choose which walks first." (`walkaaccount.js:553`) **new**

### 1.4 The two feet while sending is on

The two sentences of 1.1. In a window narrower than 1180 pixels the rail's
foot is not shown while sending is off (it was not before this change
either). While sending is on, "Your walks are sent to your Walka account."
stays in sight there, on every view: see 1.1, and question 6 in part 4. The
foot of Settings says its sentence at every width, as before.

### 1.5 The Share view

See 1.1. One sentence, changed for everybody.

### 1.6 History: the question before a walk is deleted

- A walk of which no copy was sent: the words there always were (1.1).
- A walk that is in the Walka account that is signed in: "Delete this walk
  from this computer? It disappears from your totals here and from the
  addon's history. This cannot be undone. It stays in your Walka account,
  and on your public page if you have one, until you delete it there:
  walka.dev/account/walks/" (`walkaaccount.js:817`) The address is a link.
  **drafted**; from "It stays in your Walka account" on it is the brief's
  words, with "Walka account" for "account" by the owner's answer of 24
  November (question 2 in part 4). Old: "It stays in your account, and on
  your public page if you have one, until you delete it there: ..."
- A walk of which a copy was sent to a Walka account that is not signed in
  now - after signing out, say: "Delete this walk from this computer? It
  disappears from your totals here and from the addon's history. This cannot
  be undone. A copy of this walk was sent to a Walka account from this
  computer. Deleting it here does not delete that copy: that is done on
  walka.dev/account/walks/, signed in to that Walka account."
  (`walkaaccount.js:834`) **new**, written in this round: before, the
  question was the plain one again after signing out, though the copy stays.
- A walk with a copy in both: the words for the Walka account that is signed
  in, and after the address: ". Another copy was sent to a different Walka
  account from this computer. Deleting the walk here does not delete that
  copy either." (`walkaaccount.js:822`) **new**, written in this round.
- For all three the button reads "Delete from this computer"
  (`walkaaccount.js:824`) and afterwards the page says "Walk deleted from
  this computer." **drafted**

### 1.7 The three pages the web browser shows after signing in

Plain pages of Walka's own, one sentence each, under the title "Walka":

- "Walka is signed in. You can close this tab."
  (`walka_account/signin.py:67`) **brief**
- "Nothing was signed in. You can close this tab."
  (`walka_account/signin.py:68`) **brief**
- "Walka could not finish signing in. Go back to Walka and try again."
  (`walka_account/signin.py:69`) **brief**

### 1.8 The addresses the page links to

Four, all plain links that open in the web browser: walka.dev/terms/,
walka.dev/privacy/, walka.dev/account/, walka.dev/account/walks/. The page
never opens the address for signing in: Walka itself does.

## 2. The documents

Every sentence here is **new** - not among the words shown on 21 November -
unless it says otherwise. Those that were written on 24 November, after the
owner's answer, say so. The README is written for the day the feature ships.
`DISCLAIMER.md`, `docs/PROTOCOL.md`, `docs/HARDWARE.md` and the addon are
not touched.

### 2.1 README.md

**The sixth of "What you get"** (`README.md:135`)

- Old: "Stays on your computer. Nothing is uploaded. Walka never presses
  keys, moves the mouse or reads the game's memory - it looks at the
  top-left corner of the game window (what it looks at, exactly)." (was
  `README.md:135`)
- New: "Stays on your computer. Nothing is uploaded unless you sign in to
  your Walka account (on walka.dev) and switch sending on yourself. Walka
  never presses keys, moves the mouse or reads the game's memory - it looks
  at the top-left corner of the game window (what it looks at, exactly)."
  (`README.md:135`)

**The list of links under "The details"**

- A link "Your Walka account", after "Garmin Connect, Strava and others"
  (`README.md:163`).

**On a Mac**

- Old: "Nothing leaves your computer." (was `README.md:396`) New: "No
  picture of your screen leaves your computer." (`README.md:399`) (Not
  "nothing": with sending on, whether a walking session was running is read
  off the screen, and does go.)
- Added after "Your settings and walks are in ~/Library/Application
  Support/Walka.": "A sign-in to your Walka account is not in that folder:
  it is kept in your Mac's keychain." (`README.md:408`)

**Walka's window, "Your settings and walks"** (added after the sentence
about `walka.log`)

- "There is also active-session.lock, an empty file: through it one Walka
  tells another that it is recording a walk." (`README.md:447`) Written in
  this round: the file itself is new.
- "Once you have signed in to your Walka account there is also
  walka-account.json (with an empty walka-account.lock beside it)."
  (`README.md:448`)
- "It holds which Walka account is signed in on this computer (the address
  of the database it is in, its e-mail address, its id there, and whether
  you signed in with Discord, GitHub or Google), whether sending is switched
  on and what you chose when you switched it on, and what became of each
  walk: sent, there already, or not taken, with the reason that was given."
  (`README.md:451`)
- "While sending is on it holds a mark beside that switch as well: a random
  text that is a key to nothing, which Walka keeps a second time where the
  sign-in is kept." (`README.md:457`) Written on 24 November, after the
  owner's answer, and seen by nobody but its writer. Why: on that answer the
  gap of a backup put back was closed (see "Where it is kept", below), and
  the file holds one thing more for it.
- "It also notes that a sign-in ended (until you press OK), that a sign-in
  may still be in Windows' Credential Manager or in your Mac's keychain
  after you signed out (until it is taken out), and that a Walka account was
  found to be deleted." (`README.md:459`) Written in this round.
- "It stays when you sign out, and so does the e-mail address in it."
  (`README.md:463`)
- "The sign-in itself is not in that folder, nor in any file of Walka's: it
  is kept in Windows' Credential Manager or in your Mac's keychain."
  (`README.md:464`)
- "Neither the sign-in nor that e-mail address is ever written to
  walka.log." (`README.md:466`)

**"Numbers: estimated now, measured later"**

- Old: "(It is only reachable from the PC itself: it shows your walks,
  weight and height and has no password, so it is not offered to other
  devices on your network.)" (was `README.md:497`)
- New: "(It is only reachable from the PC itself: it shows your walks,
  weight and height - and the e-mail address of your Walka account, while
  Walka is signed in to one - and has no password, so it is not offered to
  other devices on your network.)" (`README.md:523`)

**A new section, "Your Walka account (on walka.dev)"** (`README.md:602`),
after "Garmin Connect, Strava and others"

- "Optional. Walka works in full without it, and if you never sign in it
  sends nothing and asks nobody." (`README.md:604`)
- "On Walka's website, https://walka.dev/, you can have a Walka account."
  (`README.md:607`)
- "It holds your walks - and, if you put them there, body numbers and a food
  diary - and its page is private until you make it public."
  (`README.md:607`)
- "Walka can send your walks to it." (`README.md:609`)
- "They are then kept in the database the website's owner runs; the
  website's privacy page says who else has a hand in that."
  (`README.md:610`)
- "Signing in. Dashboard → Settings → Walka account → Sign in with Discord,
  GitHub or Google." (`README.md:614`)
- "Your web browser opens: sign in there, then come back." (`README.md:615`)
- "Signing in makes a Walka account if you have none (terms, privacy)."
  (`README.md:616`) (the first seven words are the brief's)
- "A Walka account's first step - I am 18 or older, and I have read the
  terms and the privacy page - is taken on walka.dev/account/: Walka does
  not tick it for you, and until it is taken sending cannot be switched on."
  (`README.md:618`)
- "Nothing is sent until you switch that on. Signing in does not do it."
  (`README.md:623`) (the first sentence is the brief's. On the dashboard it
  reads "... until you switch sending on" since 24 November: 1.2. Here it
  was left: it follows "sending cannot be switched on", so "that" points at
  something.)
- "Send my walks to my Walka account... asks first: it names the Walka
  account, says what goes and how many walks, and lets you choose - all of
  them, or only walks from now on." (`README.md:624`)
- "From then on every walk recorded on this computer goes to that Walka
  account, whoever walked." (`README.md:626`)
- "What goes, of each walk: when it started and ended, the day, and how far
  your computer's clock was from world time; how long the belt moved;
  distance, steps and calories (estimates), and how many of the calories
  were on top of sitting; how each number was come by (for the calories:
  from speed or from heart rate - the word, not the heart rate); whether a
  walking session was running in the game; and the walk's name on your
  computer, which is its start time." (`README.md:628`) (The dashboard's
  question, with "this computer" made "your computer".)
- "Each walk goes under a random number made for it, and says that it came
  from Walka." (`README.md:634`) (Since 24 November the dashboard's question
  says this too, in the same words: 1.3.)
- "Every request names Walka and its version and says nothing else about
  your computer - but, like anything asked over the internet, it arrives
  with your IP address." (`README.md:636`)
- "What stays on your computer: the samples (speed and heart rate every five
  seconds), the heart rate, the top speed, the treadmill's name, your
  character, its name, its realm and its road, and your weight, height, age
  and sex." (`README.md:639`)
- "A walk made with the simulated treadmill (as with --demo) is never sent,
  and while Walka runs with that treadmill Walka account is not in Settings
  at all." (`README.md:642`)
- "A walk your Walka account does not take - one that lasts longer than two
  days, say - is not sent either: the dashboard says which."
  (`README.md:644`)
- "When. When Walka is started, and after each walk is saved, without asking
  again." (`README.md:647`)
- "(A walk that is saved as Walka closes goes at the next start. When there
  is no answer, Walka tries again later.)" (`README.md:648`)
- "If your page on walka.dev is public, a walk counts on it as soon as it is
  sent." (`README.md:649`)
- "To stop: Stop sending in the same place, or Sign out." (`README.md:651`)
- "Either takes effect at once, and neither deletes anything in your Walka
  account." (`README.md:651`)
- "Deleting on one side deletes nothing on the other. A walk you delete on
  the dashboard stays in your Walka account until you delete it there
  (walka.dev/account/walks/)." (`README.md:654`)
- "A walk you delete there stays on your computer and is not sent again."
  (`README.md:656`)
- "Nothing your Walka account does changes or deletes a walk on your
  computer." (`README.md:658`)
- "The sign-in Walka keeps is not a key for walks. It is the whole Walka
  account: whoever has it can read the body numbers and the food diary
  there, make your page public and delete the Walka account."
  (`README.md:661`)
- "Where it is kept: in Windows' Credential Manager, or in your Mac's
  keychain - never in a file of Walka's." (`README.md:665`)
- "On a system that has neither (Linux, from source) it is kept in memory,
  and you are signed in until Walka is closed." (`README.md:666`)
- "While sending is switched on, Walka keeps a mark beside it, as a second
  entry: a random text that is a key to nothing." (`README.md:668`) This
  sentence and the two after it were written on 24 November, after the
  owner's answer, and seen by nobody but their writer. Why: before, an older
  copy of `walka-account.json` put back from a backup while the sign-in was
  still stored switched sending on again, for that same Walka account,
  without the question. The owner's answer closed that gap, and the README
  says what Walka keeps for it.
- "The same mark is in walka-account.json, and a walk goes only while the
  two are the same - so an older copy of that file, put back from a backup,
  does not by itself switch sending on again." (`README.md:670`) ("By
  itself": a backup that puts back the password store together with the file
  brings back both marks.) What a person then sees is the card as it is
  after "Stop sending": nothing is sent, and the button "Send my walks to my
  Walka account..." asks again.
- "Stop sending and signing out take the mark out." (`README.md:672`)
- "Signing out in Walka removes it." (`README.md:674`)
- "If Windows or your Mac does not let Walka take it out, Walka says so
  under Walka account, with a button that tries again: until that works, the
  sign-in may still be there." (`README.md:674`) Written in this round.
- "Deleting Walka.exe (or Walka on a Mac) does not remove it: sign out
  first." (`README.md:677`) Changed in this round. Old: "Deleting Walka.exe
  (or Walka on a Mac) does not: sign out first." (was `README.md:661`)
- "(If Walka is gone already, or cannot take it out, the entry can be
  deleted by hand, in Windows' Credential Manager or in Keychain Access on a
  Mac: its name begins Walka account. There may be two such entries, the
  sign-in and the mark: delete both.)" (`README.md:678`) Changed in this
  round. Old: "(If Walka is gone already, the entry can be deleted by hand,
  in Windows' Credential Manager or in Keychain Access on a Mac: its name
  begins Walka account.)" (was `README.md:662`) Its last sentence, "There
  may be two such entries, the sign-in and the mark: delete both.", was
  written on 24 November, after the owner's answer, and seen by nobody but
  its writer.
- ""Sign out" on walka.dev does not sign this computer out. A computer stays
  signed in until somebody signs out on it, in Walka, or the Walka account
  is deleted." (`README.md:682`)
- "A proxy set for your system is not used. Walka makes its requests
  directly, and asks nobody whose certificate it cannot check."
  (`README.md:685`)
- "Where the internet is reached only through a proxy, Walka cannot sign in
  or send." (`README.md:686`)

**"Private by default", of the Share picture**

- Old: "Nothing is uploaded and nothing is posted for you." (was
  `README.md:682`) New: "It is not uploaded, and nothing is posted for you."
  (`README.md:797`)

**"Is this allowed?"** (added after "... which the addon does not put on the
strip.")

- "With sending to your Walka account switched on, one thing read off the
  game goes with each walk: whether a walking session was running."
  (`README.md:986`)
- "Not your character, its name, its realm or its road." (`README.md:989`)

**Development, the list of modules**

- Added: "walka_account/ (signing in to a Walka account and sending walks to
  it - the only part that asks anything over the internet),"
  (`README.md:1078`)

### 2.2 SECURITY.md

**"What counts", the second point**

- Old: "Anything that lets someone or something other than the person at the
  computer read what Walka stores (walks, weight and height, heart rate),
  change its settings or delete walks - for example another web page open in
  the same browser, or another device on the network - or make Walka send
  anything anywhere." (was `SECURITY.md:34`)
- New: "Anything that lets someone or something other than the person at the
  computer read what Walka stores (walks, weight and height, heart rate, the
  e-mail address of a Walka account that is signed in), change its settings
  or delete walks, sign Walka in to a Walka account or out of one, or switch
  the sending of walks on - for example another web page open in the same
  browser, or another device on the network." (`SECURITY.md:34`)

**A new point** (it takes the place of "or make Walka send anything
anywhere")

- "Anything that makes Walka send what its user did not switch on, or send
  anything anywhere but to the Walka account its user signed in to."
  (`SECURITY.md:40`)
- "(Your Walka account in the README says what is sent, and when."
  (`SECURITY.md:42`)
- "One command is not meant by this: Walka.exe httpscheck, which the build
  runs to see that the program checks certificates." (`SECURITY.md:43`)
- "It fetches the front page of walka.dev - one page, tried up to three
  times if nothing answers - and sends nothing of its user's with it."
  (`SECURITY.md:45`)
- "It runs only if somebody types it: no button and no setting does.)"
  (`SECURITY.md:47`)

**A new point**

- "The keys of a sign-in to a Walka account turning up where they can be
  read: in a file of Walka's, in walka.log, on the dashboard's page or in
  anything its API answers, on a command line or in an environment
  variable." (`SECURITY.md:49`)
- "They belong in Walka's memory, and the one that lasts in Windows'
  Credential Manager or the Mac's keychain." (`SECURITY.md:52`)

**"What does not", the first point** (added after "... Walka cannot defend
against it.")

- "Now that Walka can be signed in to a Walka account, that reaches further,
  so it is said plainly: the dashboard has no password, and it is shut off
  from other web pages, not from programs." (`SECURITY.md:69`)
- "Such a program - or another user of the same computer - can read the
  e-mail address of the Walka account that is signed in, sign Walka out,
  start a sign-in, and switch sending on: then the walks on that computer go
  to that Walka account, and count on its page at once if the page is
  public." (`SECURITY.md:72`)
- "On Windows, a program that runs as the same user can also read the stored
  sign-in in Credential Manager, and that is the whole Walka account:
  whoever has it can read the body numbers and the food diary there, make
  the page public and delete the Walka account." (`SECURITY.md:76`)
- "On Linux, where Walka runs from source, the address of a sign-in is on a
  command line while the web browser starts, and other users of the computer
  can read it there: with it, another user can sign Walka in to a Walka
  account of their own - so look at the e-mail address under Walka account
  before you switch sending on." (`SECURITY.md:79`) Written in this round.
  What it rests on: on Linux, opening a page in the web browser means
  starting a program with the address as its argument, and every user of the
  computer can read the arguments of every program. On a Mac the address is
  not on a command line. On Windows only programs of the same user can read
  it, and those are said in the sentence before.

### 2.3 docs/SAFETY.md

- Old: "Nothing the dashboard does reaches the treadmill either. Its buttons
  connect and disconnect the companion, change settings, install the addon
  and delete walks." (was `docs/SAFETY.md:59`)
- New: "Nothing the dashboard does reaches the treadmill either. Its buttons
  connect and disconnect the companion, change settings, install the addon,
  delete walks, sign Walka in to a Walka account and out of it, and switch
  the sending of walks to it on and off." (`docs/SAFETY.md:59`) The first
  sentence is as it was.

### 2.4 THIRD-PARTY-NOTICES.md and Walka/THIRD-PARTY-NOTICES.md

The same change in both copies (`THIRD-PARTY-NOTICES.md:87` and
`Walka/THIRD-PARTY-NOTICES.md:87`):

- Old: "License 2.0, MIT, BSD, PSF) - the dashboard's web server" (was
  `THIRD-PARTY-NOTICES.md:87`)
- New: "License 2.0, MIT, BSD, PSF) - the dashboard's web server, and the
  requests Walka makes to a Walka account" (`THIRD-PARTY-NOTICES.md:87`)

The copy in `Walka/` goes into the game's AddOns folder with the addon:
there it now names a Walka account (the owner's answer of 21 November: both
copies).

## 3. Ten to look at first

The ten sentences a reviewer picked as most worth the owner's own eye. The
owner's answer of 24 November - "Do everything" - was read as yes to the
words, so each of the ten says here whether it is settled, and what of it is
still worth a look. After them: what was written after that answer, and has
been seen by nobody but its writer.

1. The sentence under the sign-in buttons (1.2, "Nobody is signed in").
   Settled in one point: "until you switch that on", where "that" pointed at
   nothing on the card, now reads "until you switch sending on". Still worth
   a look: on the live project `<host>` will read as a string of letters and
   not as walka.dev; and a minute later the same card can say "This sign-in
   has no Walka account yet".
2. The first-step sentence (1.2). Settled: "it is under another e-mail
   address" stays where the brief has "you made it another way" (question
   3), and it now says "If you already have a Walka account" (question 2).
3. "What goes, of each walk: ..." (1.3). Settled: it now names all sixteen
   things that are sent. The two that were left out - a random number made
   for each walk, and that it came from Walka - are said in one more
   sentence, the README's own. Still worth a look: that new sentence, and
   that the whole is now 103 words long.
4. "Your page on walka.dev is public. ..." (1.3). Settled: its last
   sentence, "With "All of them" your whole history shows there at once.",
   is now said only of a public page that lists days or walks. Still worth a
   look: on such a page the website shows the last twelve weeks day by day,
   or the twenty newest walks - the sums are of the whole history, the lists
   are not. The sentence is the brief's and was left as it is.
5. "Nothing is sent. Walka has not sent `<n>` walks on this computer to your
   Walka account." and its partner for walks that were sent (1.2, "Sending
   is off"). Settled by the owner's yes to the words.
6. The question before a walk that was sent is deleted (1.6). Settled: it
   now says "It stays in your Walka account ..." (question 2), and the two
   questions beside it, for a copy in a Walka account that is not signed in,
   stay as built (question 7).
7. The two feet while sending is on (1.1). Settled by the owner's yes to the
   words. New with it: in a narrow window the rail's foot now keeps its
   second sentence in sight while sending is on (question 6). The website
   quotes the rail's foot: part 5.
8. "Your Walka account did not take `<n>` walks. They stay here." (1.2),
   with the Walka account's own message under it, as it came. Settled by the
   owner's yes to the words.
9. The README's sixth feature, and in its new section: "The sign-in Walka
   keeps is not a key for walks. It is the whole Walka account: ..." with
   "Signing out in Walka removes it." and the sentence after it. Settled by
   the owner's yes to the words.
10. SECURITY.md: "the dashboard has no password, and it is shut off from
    other web pages, not from programs", what such a program can do, and the
    exception made for `Walka.exe httpscheck`. Settled by the owner's yes to
    the words.

Written on 24 November, after the owner's answer, and seen by nobody but
their writer:

- The card: "Nothing is sent until you switch sending on." (1.2).
- The question: "Each walk goes under a random number made for it, and says
  that it came from Walka." - the README's sentence, now on the dashboard
  too (1.3).
- The question: "With "All of them" your whole history shows there at once."
  is left out for a public page that lists neither days nor walks (1.3).
- "Walka account" for "account" in five sentences (question 2): "or the
  Walka account was deleted", "goes to this Walka account, whoever walked",
  "Stopping deletes nothing in your Walka account", "If you already have a
  Walka account", "It stays in your Walka account". None of them names the
  Walka account twice in one sentence. Two do it in two sentences that
  follow each other: "Walka is no longer signed in to your Walka account,
  and has stopped sending. You signed out elsewhere, or the Walka account
  was deleted." and "This sign-in has no Walka account yet. ... If you
  already have a Walka account, ...". Both were left so: the plainest true
  form.
- A narrow window: where the sentence "Your walks are sent to your Walka
  account." stands while sending is on (1.1, and the pictures of part 6).
- README, five sentences about the mark that Walka now keeps while sending
  is on, so that an older copy of its file put back from a backup does not
  by itself switch sending on again: one where the README says what
  `walka-account.json` holds, three under "Where it is kept", and "There may
  be two such entries, the sign-in and the mark: delete both." (2.1). On the
  dashboard one sentence was written for it: "There may be two such entries:
  delete both.", at the end of each of the three sentences "Walka could not
  make sure that your sign-in was taken out of ..." (1.2).

Written in the round before, and settled by the owner's yes to the words:

- The card: the three sentences "Walka could not make sure that your sign-in
  was taken out of ..." with the button "Try again" (1.2) - but for their
  last sentence, which is of 24 November: see the list above.
- The card: both "Nothing is sent. ..." sentences with a count (1.2), and no
  count and no list of walks at all while the first step is not taken.
- The card: "your Walka account" in seven places that said "the account" or
  "your account's server" (1.2: could not check the server, three times; too
  old; full; did not take; outside what it takes).
- History: the question for a copy in a Walka account that is not signed in,
  and the sentence for a copy in both (1.6).
- README: the sentence about `active-session.lock`; "It also notes that a
  sign-in ended ..."; "If Windows or your Mac does not let Walka take it out
  ..."; the two changed sentences after it (2.1).
- SECURITY.md: the sentence about Linux and the command line (2.2).

## 4. Questions about words

Each had a recommendation. On 24 November 2026 the owner answered all of
them with "Do everything" - read as yes to each recommendation. Under each
question stands that answer, and what was done on it.

1. **"No Walka account yet", and "your Walka account" beside it.** While the
   first step is not taken the card says "This sign-in has no Walka account
   yet" and, in the same card, the button "Look again (asks your Walka
   account)", while it looks "Asking your Walka account...", and under "Sign
   out" the line "Signing out here may ask your Walka account one last time,
   to tell it so." (The count of walks "not in your Walka account" that
   stood there too is gone: 1.2.) Keep the three, or change them for this
   state? Recommended: keep them. A button that makes Walka ask has to say
   that it asks, and the only other name the card has for what is asked is
   `<host>`, which will be a string of letters. If they are to change: "Look
   again (asks `<host>`)", "Asking `<host>`...", "Signing out here may ask
   `<host>` one last time, to tell it so." - all three or none.

   **Answered.** The owner's answer of 24 November: Do everything - read as
   yes to the recommendation. The three stay as they are. Nothing was
   changed.
2. **"Account" alone, in five sentences that come from the brief.** Decision
   2 says never "account" alone, because elsewhere in Walka that is the
   player's game account. Everything else written for this change now says
   "Walka account". Five places still say "account" alone, as the brief's
   own sentences do:
   - decision 11, in the card: "You signed out elsewhere, or the account was
     deleted."
   - decision 5, in the question: "Every walk recorded on this computer goes
     to this account, whoever walked."
   - decision 5, in the question: "Stopping deletes nothing in the account."
   - decision 4, in the card: "If you already have an account, ..."
   - decision 8, on History: "It stays in your account, and on your public
     page if you have one, until you delete it there: ..."

   Keep them, or say "Walka account"? Recommended: change at least the last
   one, to "It stays in your Walka account, and on your public page if you
   have one, until you delete it there: ...". It is the only one that is
   shown outside the card, where nothing has named the Walka account first.
   The other four stand under the heading "Walka account" or in the question
   "Send my walks to my Walka account?"; changing them too ("or the Walka
   account was deleted", "goes to this Walka account", "deletes nothing in
   your Walka account", "If you already have a Walka account") would make
   the page say one thing everywhere.

   **Answered.** The owner's answer of 24 November: Do everything - read as
   yes to the recommendation. All five now say "Walka account", and nothing
   else in them was changed: "You signed out elsewhere, or the Walka account
   was deleted."; "Every walk recorded on this computer goes to this Walka
   account, whoever walked."; "Stopping deletes nothing in your Walka
   account."; "If you already have a Walka account, ..."; "It stays in your
   Walka account, and on your public page if you have one, until you delete
   it there: ...". They are no longer the brief's words to the letter, and
   say so where they stand (1.2, 1.3, 1.6). No sentence on the dashboard
   says "account" alone any more, and a test now allows no exception.
3. **Decision 4's sentence is not the brief's.** The brief: "if you already
   have an account, you made it another way: sign out here and sign in that
   way". The card: "If you already have an account, it is under another
   e-mail address: sign out here and sign in the way that uses that
   address." Why: on the website, Discord and GitHub with the same e-mail
   address come to ONE Walka account, so "another way" is wrong whenever the
   addresses match. A second, empty sign-in comes from another e-mail
   address. This stood in the words shown on 21 November, but it was never
   said there that the owner's own words were replaced. Keep the new words
   (recommended), or go back to the brief's?

   **Answered.** The owner's answer of 24 November: Do everything - read as
   yes to the recommendation. The new words stay. (By question 2 the
   sentence now begins "If you already have a Walka account".)
4. **README, "Only listens."** The first of the six features is headed "Only
   listens." without "to your treadmill" (`README.md:123`), four points
   above the changed sixth, which now says walks can be sent. The treadmill
   is named in the very next words, so it is not false. The brief says both:
   change nothing about the belt, and keep "to your treadmill" attached to
   "only listens" wherever sending is described nearby. Left as it is. Leave
   it (recommended), or make the head "Only listens to your treadmill."? The
   website quotes these six.

   **Answered.** The owner's answer of 24 November: Do everything - read as
   yes to the recommendation. Left as it is.
5. **How the entry is named.** The card and the README say of the stored
   sign-in "its name begins "Walka account"", and not its whole name: the
   rest of the name is the address of the database. How Credential Manager
   and Keychain Access show it was looked at on no real Windows or Mac. Good
   enough until the owner has looked (recommended), or name it another way?

   **Answered.** The owner's answer of 24 November: Do everything - read as
   yes to the recommendation. Left as it is, until the owner has looked at a
   real Windows or Mac. One thing is different since that answer: while
   sending is on, Walka keeps a second entry in the same store, a mark
   (2.1), and its name begins "Walka account" as well. The README now says
   that there may be two such entries, and to delete both - and so do the
   card's three sentences (1.2), each with one more sentence at its end:
   "There may be two such entries: delete both." Those words are new, and
   seen by nobody but their writer.
6. **A narrow window.** In a window narrower than 1180 pixels the rail's
   foot is not shown (Walka's own window opens 1200 wide; it was so before
   this change). Before, the hidden words were a comfort. Now, with sending
   on, they are what tells that walks are sent - on Live, History and Share
   nothing else does. In Settings the card's "Sending on" and the foot say
   it at every width. Should the sentence stay in sight in a narrow window,
   for instance in the bar at the top? Not built; it needs the owner's word.

   **Answered.** The owner's answer of 24 November: Do everything - read as
   yes, the sentence stays in sight. Built. While sending is on - and only
   then - "Your walks are sent to your Walka account." is in sight at every
   width, on Live, History, Share and Settings. It is the rail's foot
   itself, kept where a narrow window used to put it away, and cut down to
   its second sentence: from 720 to 1179 pixels it stands in small letters
   at the end of the narrow rail, under the four lamps; below 720 pixels it
   is one line under the four lamps at the top of the page, and goes up with
   them when the page is scrolled - so there, further down a long page, it
   is out of sight until the page is scrolled back up, as the lamps are. No
   new sentence was written. While sending is off, or nobody is signed in,
   or Walka has no such part, the page is at every width what it was:
   fifty-four pictures of Live, History and Share from before and after are
   the same, dot for dot, and so are two hundred more that a second look
   made at five widths (part 6). One thing was left as the page always had
   it: in a window too low for the rail (lower than about 630 pixels with
   the narrow rail, about 520 with the wide one; Walka's window opens 720
   high) the rail scrolls, and its foot is at its end.
7. **After signing out, History speaks of a Walka account.** The rule is
   that nothing outside the card says a word of a Walka account to a person
   who is not signed in. The new question before deleting (1.6) breaks it on
   purpose, for walks of which a copy was sent from this computer, and only
   for those: the copy stays, and decision 8 wants the person told. Somebody
   who never signed in sees the words there always were.

   **Answered.** The owner's answer of 24 November: Do everything - read as
   yes to the recommendation. Yes, as built.
8. **Three sentences in the README that the brief did not ask for**, each
   added because the rest would otherwise say more than is true or leave a
   person stuck. Strike what is not wanted: "It stays when you sign out, and
   so does the e-mail address in it."; "... but, like anything asked over
   the internet, it arrives with your IP address."; "(If Walka is gone
   already, or cannot take it out, the entry can be deleted by hand ...)".

   **Answered.** The owner's answer of 24 November: Do everything - read as
   yes to the recommendation. The three stay.
9. **Not written in the README, though true:** after an update a Mac may
   refuse the new Walka the stored sign-in (the card then says "Sign in
   again"); a Walka account has room for 20,000 walks; on the website a walk
   is deleted one at a time; Discord, GitHub or Google learn of each sign-in
   made from Walka (the website's privacy page is the place for that).

   Nothing was asked here, and nothing was changed on the owner's answer.

## 5. For the website's maintainer

**Quotes of Walka, word for word, on the website that are different now**

1. The README's sixth feature. The home page
   (`src/lib/components/home/Features.svelte:46-47`, "The six of Walka's
   README, in its words") has: "Stays on your computer." / "Nothing is
   uploaded. Walka never presses keys, moves the mouse or reads the game's
   memory - it looks at the top-left corner of the game window." The README
   now has: "Nothing is uploaded unless you sign in to your Walka account
   (on walka.dev) and switch sending on yourself. Walka never presses keys,
   moves the mouse or reads the game's memory - it looks at the top-left
   corner of the game window". The other five are as they were.
2. The dashboard's rail foot. The privacy page
   (`src/routes/privacy/+page.svelte:286-287`, "In its own words") has:
   "Runs on this computer. Nothing is uploaded." That is still what the foot
   says while sending is off. While sending is on it says: "Runs on this
   computer. Your walks are sent to your Walka account." The quote is no
   longer the only thing the foot can say. In a window narrower than 1180
   pixels the foot is not shown while sending is off, as it never was; while
   sending is on its second sentence is shown there by itself: "Your walks
   are sent to your Walka account."

**Sentences the website repeats in its own words, and that changed here**

- The privacy page, `:282`: "it shows your walks, weight and height and has
  no password". The README now adds: "and the e-mail address of your Walka
  account, while Walka is signed in to one".
- The downloads page, `:231`: "No picture is saved or sent anywhere. Nothing
  leaves your computer." The README now says: "No picture of your screen
  leaves your computer."
- The Share picture (home page, privacy page, how-it-works, FAQ): "Nothing
  is uploaded and nothing is posted for you." The README now says: "It is
  not uploaded, and nothing is posted for you." The dashboard: "The picture
  is made on this computer. It is not uploaded: you choose where to post
  it."
- The safety page, `:260`, and the privacy page, `:383`: "... or make Walka
  send anything anywhere". SECURITY.md now says: "send what its user did not
  switch on, or send anything anywhere but to the Walka account its user
  signed in to".
- "Nothing is uploaded" stands in other places of the website's own
  (how-it-works `:257`, FAQ `:265`): true of every download until the
  feature ships, and false the day it does.

**Sentences of the dashboard that changed on the owner's answer of 24
November** (the website quotes none of them word for word; the brief does)

- Five sentences say "Walka account" where the brief has "account": "You
  signed out elsewhere, or the Walka account was deleted."; "Every walk
  recorded on this computer goes to this Walka account, whoever walked.";
  "Stopping deletes nothing in your Walka account."; "If you already have a
  Walka account, ..."; "It stays in your Walka account, and on your public
  page if you have one, until you delete it there: ...".
- Under the sign-in buttons: "Nothing is sent until you switch sending on."
  (the brief: "... until you switch that on.")
- The question before sending now also says: "Each walk goes under a random
  number made for it, and says that it came from Walka."
- The question says "With "All of them" your whole history shows there at
  once." only when the public page lists days or walks (`show_days` or
  `show_walks`), not for a page that shows sums alone.

**The four addresses** Walka now links to must stay: walka.dev/terms/,
walka.dev/privacy/, walka.dev/account/, walka.dev/account/walks/.

**The new file `walka-account.json`**, in the data folder beside the walks
(with an empty `walka-account.lock`). No key of a sign-in is ever in it.
Under the address of the database, and then under the Walka account's id, it
holds:

- how the person signed in (Discord, GitHub or Google) and the e-mail
  address the sign-in answered - so that the card can say which Walka
  account without asking anybody;
- which Walka account is signed in, if any; whether sending is on; what was
  chosen when it was switched on (all, or from which moment); and, while
  sending is on, a mark - a random text that is made anew each time the
  question before sending is answered, and that is a key to nothing;
- for each walk dealt with, under its name and its start: added (the row's
  id, when), there already (the row's id if it could be read back, and
  whether it is deleted there), or not taken (the code, the message, Walka's
  version);
- three notes: that a sign-in ended (until "OK" is pressed); that a sign-in
  may still be in the system's password store after signing out (until it is
  taken out); that the accounts said a Walka account was deleted.

It stays when the person signs out, with the e-mail address and the id of
every Walka account that was ever signed in on that computer. Deleting it
loses nothing in a Walka account.

The sign-in itself is kept in Windows' Credential Manager or the Mac's
keychain, and on other systems in memory until Walka is closed. While
sending is on, a second entry is kept beside it: the same mark that is in
the file. A walk goes only while the two are the same, so that an older copy
of the file, put back from a backup, does not by itself switch sending on
again. "Stop sending" and signing out take the mark out. (New on 24
November. The README says it in its section "Your Walka account (on
walka.dev)", under "Where it is kept".)

**Walka reads walks back.** After a send, for the walks that did not come
back as added, Walka makes one more request to tell "there already" from
"deleted there", with fifty starts at most in one request:

```
GET /rest/v1/walks?start_key=in.(...)&select=id,start_key,deleted_at
```

It reads the row's id, its start and whether it is deleted; nothing else,
and no other table. If that request fails, the walk is written down as there
already and is not asked about again.

**Also worth knowing**

- Two walks that start in the same second are one walk to the database: the
  second is written down as there already, with the first walk's row.
- A walk that was deleted in the Walka account stays on the computer, is not
  sent again, and counts among "sent before, or were in your Walka account
  already" on the card.
- A Walka account that is nearly full takes none of a batch, though some of
  its walks would fit.
- A computer stays signed in until somebody signs out on it, in Walka, or
  the Walka account is deleted. "Sign out" on the website does not sign it
  out.

## 6. The pictures

Pictures of every state of the card, of the question and of the History view
were made again on 24 November 2026, after the owner's answer, from the page
as it is now, in a window 1200, 900 and 480 pixels wide, light and dark. New
among them:

- Live, History and Share at each of the three widths, light and dark, while
  sending is on: the sentence "Your walks are sent to your Walka account."
  is in sight in every one (`08-sending-on-live-...`, `-history-...`,
  `-share-...`).
- The same views while nobody is signed in, while somebody is signed in and
  sending is off, and after sending was stopped (`01-signed-out-...`,
  `05-sending-off-...`, `10b-stopped-sending-...`): no word of a Walka
  account in any of them. These fifty-four pictures were also made from the
  page as it was before this round, and each pair is the same, dot for dot.
- The question for a public page that lists neither days nor walks, without
  the sentence about the whole history
  (`07a-question-public-sums-only-...`).
- Four pictures of the Live view in a low window while sending is on
  (`08-sending-on-live-1200x420-...` and three more): there the rail scrolls
  and its foot is at its end, as it always was.

With them is `notes.txt`: what the card said in each state, word for word as
the browser showed it, and for each of the new pictures what of the rail's
foot could be read and where it stood - also at the widths where the page
changes its layout (1180, 1179, 720, 719) and down to 320 pixels. Nothing
was wider than the window, and nothing lay on something else. And
`same-as-before.txt`: which pictures are the same, dot for dot, as those
made from the page before this round.

All of these pictures were made once more at the end of that day, from the
files as they are after the last change to the program (the mark that goes
with the switch for sending: 2.1) and to the card (the sentence "There may
be two such entries: delete both.": 1.2, pictures
`15-sign-in-may-still-be-in-the-store-...`). Every other word the browser
showed, in every state, was the same as in the look before.

A second look, by a reviewer, is in the folder `review`: Live, History,
Share and Settings in a window 1180, 1179, 900, 480 and 360 pixels wide,
light and dark, in six states - Walka has no such part, nobody ever signed
in, signed in with sending off, sending on, sending stopped, signed out
again. The same pictures were made from the page of the last commit before
the owner's answer. In the five states without sending, all two hundred
pictures of the window are the same as before, dot for dot. The pictures of
the whole Settings page differ in two places only: under the sign-in buttons
the changed sentence ("... until you switch sending on."), and the name of a
folder that the look itself made up. With sending on, the sentence is in
sight in all forty pictures, lies on nothing, and nothing is wider than the
window. Its `notes.txt` says where it stood in each, and what became of it
when the page was scrolled to its end: from 720 pixels up it stays where it
is, below that it goes up with the page.

The pictures are not on the branch: they go to the owner with the report.
They were made on Linux with a browser that says it is on Windows, so they
show the words for Windows.
