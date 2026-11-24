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
  - **new** - new: the owner has not seen it.
- Where a sentence took the place of another, the old words stand beside it.
- If time is short: part 3, "Ten to look at first", then part 4, "Questions
  about words".

## 1. The dashboard

### 1.1 Words that were on the page before this change

- The rail's foot, on every view. Old: "Runs on this computer. Nothing is
  uploaded." (was `index.html:62`). Now (`index.html:62`): the same, word
  for word, unless sending is on. While sending is on: "Runs on this
  computer. Your walks are sent to your Walka account."
  (`walkaaccount.js:58`) **drafted**
- The foot of Settings. Old: "Everything on this page stays on this
  computer." (was `index.html:488`). Now (`index.html:499`): the same, word
  for word, unless sending is on. While sending is on: "Your walks are sent
  to your Walka account. Everything else on this page stays on this
  computer." (`walkaaccount.js:61`) **drafted**
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
(`walkaaccount.js:310`). **new**

**Nobody is signed in**

- "Optional. Walka works in full without it." (`walkaaccount.js:418`)
  **drafted**
- Buttons "Sign in with Discord", "Sign in with GitHub", "Sign in with
  Google" (`walkaaccount.js:432`). **drafted**
- "Signing in makes a Walka account if you have none: terms
  (walka.dev/terms/), privacy (walka.dev/privacy/). Nothing is sent until
  you switch that on. Your web browser opens for this, and Walka asks
  `<host>`." (`walkaaccount.js:439`) The two addresses are links.
  **drafted** (its first two sentences are the brief's)

**While signing in**

- "Waiting for your web browser. Sign in there, then come back."
  (`walkaaccount.js:301`) **drafted**
- Button "Stop waiting" (`walkaaccount.js:427`). **new** (without it the
  card waits five minutes for a browser nobody is looking at)

**A sign-in that did not work, or a stored sign-in that is gone**

- "Nothing was signed in." (`walkaaccount.js:69`) **drafted**
- "Walka could not finish signing in. Try again." (`walkaaccount.js:70`)
  **drafted**
- "Nobody came back from the browser. Try again." (`walkaaccount.js:71`)
  **drafted**
- "Walka could not keep the sign-in on this computer, so nothing was signed
  in." (`walkaaccount.js:72`) **drafted**
- "Walka could not check that it is talking to your Walka account's server,
  so it asked nothing." (`walkaaccount.js:73`) **new** - changed in this
  round. Old: "Walka could not check that it is talking to your account's
  server, so it asked nothing." (**drafted**)
- "There was no answer. Try again later." (`walkaaccount.js:74`) **drafted**
- "The sign-in is not on this computer any more. Nothing is sent."
  (`walkaaccount.js:75`) **drafted**
- "macOS did not let this version of Walka read the stored sign-in. Sign in
  again." (`walkaaccount.js:189`) **drafted**
- "Windows did not let Walka read the stored sign-in. Sign in again."
  (`walkaaccount.js:190`) **new**
- "The computer that runs Walka did not let it read the stored sign-in. Sign
  in again." (`walkaaccount.js:191`) **new** (for a page that is open on
  another device, which cannot tell which system Walka runs on)

**Signed out, and the sign-in may still be in the system's password store**
(all **new**, written in this round: before, the card said nothing)

- On Windows: "Walka could not make sure that your sign-in was taken out of
  Windows' Credential Manager. Try again, or delete the entry there by hand:
  its name begins "Walka account"." (`walkaaccount.js:203`)
- On a Mac: "Walka could not make sure that your sign-in was taken out of
  your Mac's keychain. Try again, or delete the entry by hand in Keychain
  Access: its name begins "Walka account"." (`walkaaccount.js:205`)
- On a page that is open on another device: "Walka could not make sure that
  your sign-in was taken out of the password store of the computer that runs
  Walka. Try again, or delete the entry there by hand: its name begins
  "Walka account"." (`walkaaccount.js:206`)
- Button "Try again" (`walkaaccount.js:411`).

**Signed in**

- "Signed in as `<e-mail>`, with `<way>`." (`walkaaccount.js:451`) The
  e-mail address whole, in bold. **drafted**
- "Signed in as `<e-mail>`." / "Signed in with `<way>`." / "Signed in."
  (`walkaaccount.js:452`) **new** (when the way or the e-mail address is not
  known)
- Button "Sign out" (`walkaaccount.js:453`). **drafted**
- ""Sign out" on walka.dev does not sign this computer out: sign out here."
  (`walkaaccount.js:458`) **drafted**
- Straight after it: "Signing out here may ask your Walka account one last
  time, to tell it so." (the same line) **new** (a button that makes Walka
  ask says so)
- And one of:
  - "The sign-in is kept in Windows' Credential Manager."
    (`walkaaccount.js:182`) **drafted**
  - "The sign-in is kept in your Mac's keychain." (`walkaaccount.js:183`)
    **drafted**
  - "The sign-in is kept in memory, until Walka is closed."
    (`walkaaccount.js:181`) **drafted**
  - "The sign-in is kept in the password store of the computer that runs
    Walka." (`walkaaccount.js:184`) **new** (for a page that is open on
    another device)

**The first step is not taken** (decision 4)

- "This sign-in has no Walka account yet. To open one - or to delete this
  sign-in - go to walka.dev/account/ and sign in there the same way (with
  `<way>`). If you already have an account, it is under another e-mail
  address: sign out here and sign in the way that uses that address."
  (`walkaaccount.js:482`) **drafted** - and NOT the brief's words at one
  place: see question 3 in part 4.
- A link that looks like a button: "Open walka.dev/account/"
  (`walkaaccount.js:484`). **drafted**
- Button "Look again (asks your Walka account)" (`walkaaccount.js:469`)
  **drafted**
- Under it the card now says only "Nothing is sent." (`walkaaccount.js:164`)
  **new** - changed in this round: before, it counted walks here as well ("5
  walks on this computer are not in your Walka account"), beside "no Walka
  account yet". Nor does it list walks in this state (the lines of "Walks
  that did not go", below): "your Walka account did not take ..." would
  speak of a Walka account too.

**No e-mail address is known**

- "The sign-in gave Walka no e-mail address, so nobody could tell which
  Walka account your walks would go to. Sending cannot be switched on."
  (`walkaaccount.js:493`) **new**

**Sending is off**

- "Nothing is sent. Walka has not sent `<n>` walks on this computer to your
  Walka account." (`walkaaccount.js:159`) and for one walk "Nothing is sent.
  Walka has not sent 1 walk on this computer to your Walka account."
  (`walkaaccount.js:158`) **new** - changed in this round. Old: "Nothing is
  sent. `<n>` walks on this computer are not in your Walka account."
  (**drafted**). Why: Walka does not know what is in the Walka account.
  Walks that were put in on the website are there, and Walka has asked
  nobody. And why "to your Walka account": after signing out and signing in
  to another Walka account, the walks that went to the first one are counted
  here again - Walka has sent them, but not to this one.
- When none waits: "Nothing is sent. `<n>` walks on this computer were sent
  before, or were in your Walka account already." (`walkaaccount.js:163`)
  and for one walk "Nothing is sent. 1 walk on this computer was sent
  before, or was in your Walka account already." (`walkaaccount.js:161`)
  **new** - changed in this round. Old: "Nothing is sent. `<n>` walks on
  this computer are in your Walka account already." (never shown to the
  owner). Why: a walk that was deleted in the Walka account since is counted
  here too.
- When there is no walk at all: "Nothing is sent." **new**
- Button "Send my walks to my Walka account..." (`walkaaccount.js:512`) and
  beside it "Asks your Walka account first." (`walkaaccount.js:527`)
  **drafted**
- "Asking your Walka account..." (`walkaaccount.js:344`) **new**
- "Switching sending on..." (`walkaaccount.js:343`) **new**
- "The question ran out after ten minutes. Nothing was switched on."
  (`walkaaccount.js:345`) **new**
- "That did not work just now. Try again later." (`walkaaccount.js:95`)
  **new** (a button's work ended early: the answer could not be used, or the
  switch could not be written down)
- "There was no answer. Try again later." (`walkaaccount.js:94`) and "Walka
  could not check that it is talking to your Walka account's server, so it
  asked nothing." (`walkaaccount.js:96`) - the same two sentences as under
  "A sign-in that did not work".

**Sending is on**

- "Sending is on. Each walk goes to your Walka account when it is saved."
  (`walkaaccount.js:502`) **drafted**
- "`<n>` sent, `<n>` waiting." (`walkaaccount.js:498`) **drafted**
- After "Only walks from now on", on the same line: "Only walks from `<day
  and time>` on are sent." (`walkaaccount.js:499`) **new**
- Button "Stop sending" (`walkaaccount.js:500`). **drafted**
- "Sending now..." (`walkaaccount.js:329`) **new**
- After a try:
  - "Sent." (`walkaaccount.js:81`) **drafted**
  - "No answer - Walka tries again later." (`walkaaccount.js:82`)
    **drafted**
  - "There was no answer. Try again later." (`walkaaccount.js:83`)
    **drafted** (here for a button that asked)
  - "This Walka is too old for your Walka account as it is now: get the
    newest Walka." (`walkaaccount.js:84`) **new** - changed in this round.
    Old: "This Walka is too old for the account as it is now: get the newest
    Walka." (**drafted**)
  - "Your Walka account is full: it has room for 20,000 walks."
    (`walkaaccount.js:85`) **new** - changed in this round. Old: "The
    account is full: it has room for 20,000 walks." (**drafted**)
  - "Walka stopped for now and tries again when it is next started."
    (`walkaaccount.js:86`) **drafted**
  - "Walka could not check that it is talking to your Walka account's
    server, so it sent nothing." (`walkaaccount.js:88`) **new** - changed in
    this round. Old: "Walka could not check that it is talking to your
    account's server, so it sent nothing." (**drafted**)

**Walks that did not go** (under the card, in both states)

- "Your Walka account did not take `<n>` walks. They stay here."
  (`walkaaccount.js:148`) and "Your Walka account did not take 1 walk. It
  stays here." (`walkaaccount.js:147`) **new** - changed in this round. Old:
  "The account did not take `<n>` walks. They stay here." (**drafted**).
  Under it each walk's day and time, and what the Walka account itself said
  of it, as it came: for example `new row for relation "walks" violates
  check constraint "walks_distance_m_check"`. That text is not Walka's.
- "`<n>` walks are outside what your Walka account takes, and stay here."
  (`walkaaccount.js:144`) and "1 walk is outside what your Walka account
  takes, and stays here." (`walkaaccount.js:143`) **new** - changed in this
  round. Old: "`<n>` walks are outside what the account takes, and stay
  here." (**drafted**). Under it each walk's day and time, and why. The
  reasons are the website's own, for example "There are more than 400,000
  steps." or "It lasts longer than two days."
- After twenty walks: "and `<n>` more" (`walkaaccount.js:639`). **new**
- "`<n>` walks do not say which treadmill made them, and are not sent."
  (`walkaaccount.js:140`) **drafted**; for one walk "1 walk does not say
  which treadmill made it, and is not sent." (`walkaaccount.js:139`) **new**
- "`<n>` walks could not be read, and are not sent." (`walkaaccount.js:150`)
  and "1 walk could not be read, and is not sent." (`walkaaccount.js:150`)
  **new**

**When the sign-in ended** (decision 11)

- "Walka is no longer signed in to your Walka account, and has stopped
  sending. You signed out elsewhere, or the account was deleted. Your walks
  on this computer are untouched." (`walkaaccount.js:65`) **brief**
- Button "OK" (`walkaaccount.js:404`). **drafted**

**When Walka does not do what a button asked** (shown as the page's usual
red notice; all **new**)

- "Walka is still asking your Walka account. Try again in a moment."
  (`walkaaccount.js:106`)
- "Walka is closing." (`walkaaccount.js:107`)
- "The Walka account is not available in this Walka."
  (`walkaaccount.js:108`)
- "Walka is not signed in to a Walka account." (`walkaaccount.js:109`)
- "Walka is signed in already." (`walkaaccount.js:110`)
- "The sign-in gave Walka no e-mail address, so sending cannot be switched
  on." (`walkaaccount.js:111`)
- "The question ran out. Nothing was switched on." (`walkaaccount.js:112`)
- "That did not work. Try again." (`walkaaccount.js:113`)

### 1.3 The question before sending (decision 5)

A panel in the card. Every switching-on passes it.

- "Send my walks to my Walka account?" (`walkaaccount.js:118`) **drafted**
- "To `<e-mail>`, signed in with `<way>`." (`walkaaccount.js:539`)
  **drafted**
- "What goes, of each walk: when it started and ended, the day, and how far
  this computer's clock was from world time; how long the belt moved;
  distance, steps and calories (estimates), and how many of the calories
  were on top of sitting; how each number was come by (for the calories:
  from speed or from heart rate - the word, not the heart rate); whether a
  walking session was running in the game; and the walk's name on this
  computer, which is its start time." (`walkaaccount.js:119`) **drafted**
- "What stays here: the samples, the heart rate, the top speed, the
  treadmill's name, the character, your weight, height, age and sex."
  (`walkaaccount.js:121`) **drafted**
- "Where: your Walka account, in the database the site's owner runs
  (`<host>`). walka.dev/privacy/" (`walkaaccount.js:122`) The address is a
  link. **drafted**
- "`<n>` walks would go now." **drafted** / "1 walk would go now." / "No
  walk would go now." **new** (`walkaaccount.js:136`)
- The choice, neither chosen at first: "All of them" (`walkaaccount.js:123`)
  / "Only walks from now on" (`walkaaccount.js:124`) **drafted**. Its name
  for screen readers: "Which walks to send" (`walkaaccount.js:557`) **new**
- "From then on each walk goes when it is saved, without asking again."
  (`walkaaccount.js:125`) **drafted**
- Where it applies, the two lines of 1.2 about walks that do not say which
  treadmill made them and walks outside what your Walka account takes.
- Only if the page is public: "Your page on walka.dev is public. Walks you
  send count on it as soon as they are sent, so anyone who reloads it can
  tell when you were on the pad. With "All of them" your whole history shows
  there at once." (`walkaaccount.js:127`) **drafted** (its first two
  sentences, up to "as soon as they are sent", are the brief's)
- "Every walk recorded on this computer goes to this account, whoever
  walked." (`walkaaccount.js:128`) **brief**
- "To stop: switch it off here, or sign out. Stopping deletes nothing in the
  account. There a walk is deleted one at a time: walka.dev/account/walks/"
  (`walkaaccount.js:129`) The address is a link. **drafted** ("stopping
  deletes nothing in the account" is the brief's)
- Buttons "Send my walks" (`walkaaccount.js:130`) and "Not now"
  (`walkaaccount.js:131`) **drafted**; beside them until a choice is made
  "Choose which walks first." (`walkaaccount.js:544`) **new**

### 1.4 The two feet while sending is on

The two sentences of 1.1. In a window narrower than 1180 pixels the rail's
foot is not on the page at all (it was not before this change either): see
question 6 in part 4.

### 1.5 The Share view

See 1.1. One sentence, changed for everybody.

### 1.6 History: the question before a walk is deleted

- A walk of which no copy was sent: the words there always were (1.1).
- A walk that is in the Walka account that is signed in: "Delete this walk
  from this computer? It disappears from your totals here and from the
  addon's history. This cannot be undone. It stays in your account, and on
  your public page if you have one, until you delete it there:
  walka.dev/account/walks/" (`walkaaccount.js:787`) The address is a link.
  **drafted**; from "It stays in your account" on it is the **brief**'s. See
  question 2 in part 4.
- A walk of which a copy was sent to a Walka account that is not signed in
  now - after signing out, say: "Delete this walk from this computer? It
  disappears from your totals here and from the addon's history. This cannot
  be undone. A copy of this walk was sent to a Walka account from this
  computer. Deleting it here does not delete that copy: that is done on
  walka.dev/account/walks/, signed in to that Walka account."
  (`walkaaccount.js:804`) **new**, written in this round: before, the
  question was the plain one again after signing out, though the copy stays.
- A walk with a copy in both: the words for the Walka account that is signed
  in, and after the address: ". Another copy was sent to a different Walka
  account from this computer. Deleting the walk here does not delete that
  copy either." (`walkaaccount.js:792`) **new**, written in this round.
- For all three the button reads "Delete from this computer"
  (`walkaaccount.js:794`) and afterwards the page says "Walk deleted from
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

Every sentence here is **new** - the owner has not seen it - unless it says
otherwise. The README is written for the day the feature ships.
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
- "It also notes that a sign-in ended (until you press OK), that a sign-in
  may still be in Windows' Credential Manager or in your Mac's keychain
  after you signed out (until it is taken out), and that a Walka account was
  found to be deleted." (`README.md:457`) Written in this round.
- "It stays when you sign out, and so does the e-mail address in it."
  (`README.md:460`)
- "The sign-in itself is not in that folder, nor in any file of Walka's: it
  is kept in Windows' Credential Manager or in your Mac's keychain."
  (`README.md:461`)
- "Neither the sign-in nor that e-mail address is ever written to
  walka.log." (`README.md:463`)

**"Numbers: estimated now, measured later"**

- Old: "(It is only reachable from the PC itself: it shows your walks,
  weight and height and has no password, so it is not offered to other
  devices on your network.)" (was `README.md:497`)
- New: "(It is only reachable from the PC itself: it shows your walks,
  weight and height - and the e-mail address of your Walka account, while
  Walka is signed in to one - and has no password, so it is not offered to
  other devices on your network.)" (`README.md:520`)

**A new section, "Your Walka account (on walka.dev)"** (`README.md:599`),
after "Garmin Connect, Strava and others"

- "Optional. Walka works in full without it, and if you never sign in it
  sends nothing and asks nobody." (`README.md:601`)
- "On Walka's website, https://walka.dev/, you can have a Walka account."
  (`README.md:604`)
- "It holds your walks - and, if you put them there, body numbers and a food
  diary - and its page is private until you make it public."
  (`README.md:604`)
- "Walka can send your walks to it." (`README.md:606`)
- "They are then kept in the database the website's owner runs; the
  website's privacy page says who else has a hand in that."
  (`README.md:607`)
- "Signing in. Dashboard → Settings → Walka account → Sign in with Discord,
  GitHub or Google." (`README.md:611`)
- "Your web browser opens: sign in there, then come back." (`README.md:612`)
- "Signing in makes a Walka account if you have none (terms, privacy)."
  (`README.md:613`) (the first seven words are the brief's)
- "A Walka account's first step - I am 18 or older, and I have read the
  terms and the privacy page - is taken on walka.dev/account/: Walka does
  not tick it for you, and until it is taken sending cannot be switched on."
  (`README.md:615`)
- "Nothing is sent until you switch that on. Signing in does not do it."
  (`README.md:620`) (the first sentence is the brief's)
- "Send my walks to my Walka account... asks first: it names the Walka
  account, says what goes and how many walks, and lets you choose - all of
  them, or only walks from now on." (`README.md:621`)
- "From then on every walk recorded on this computer goes to that Walka
  account, whoever walked." (`README.md:623`)
- "What goes, of each walk: when it started and ended, the day, and how far
  your computer's clock was from world time; how long the belt moved;
  distance, steps and calories (estimates), and how many of the calories
  were on top of sitting; how each number was come by (for the calories:
  from speed or from heart rate - the word, not the heart rate); whether a
  walking session was running in the game; and the walk's name on your
  computer, which is its start time." (`README.md:625`) (The dashboard's
  question, with "this computer" made "your computer".)
- "Each walk goes under a random number made for it, and says that it came
  from Walka." (`README.md:631`)
- "Every request names Walka and its version and says nothing else about
  your computer - but, like anything asked over the internet, it arrives
  with your IP address." (`README.md:633`)
- "What stays on your computer: the samples (speed and heart rate every five
  seconds), the heart rate, the top speed, the treadmill's name, your
  character, its name, its realm and its road, and your weight, height, age
  and sex." (`README.md:636`)
- "A walk made with the simulated treadmill (as with --demo) is never sent,
  and while Walka runs with that treadmill Walka account is not in Settings
  at all." (`README.md:639`)
- "A walk your Walka account does not take - one that lasts longer than two
  days, say - is not sent either: the dashboard says which."
  (`README.md:641`)
- "When. When Walka is started, and after each walk is saved, without asking
  again." (`README.md:644`)
- "(A walk that is saved as Walka closes goes at the next start. When there
  is no answer, Walka tries again later.)" (`README.md:645`)
- "If your page on walka.dev is public, a walk counts on it as soon as it is
  sent." (`README.md:646`)
- "To stop: Stop sending in the same place, or Sign out." (`README.md:648`)
- "Either takes effect at once, and neither deletes anything in your Walka
  account." (`README.md:648`)
- "Deleting on one side deletes nothing on the other. A walk you delete on
  the dashboard stays in your Walka account until you delete it there
  (walka.dev/account/walks/)." (`README.md:651`)
- "A walk you delete there stays on your computer and is not sent again."
  (`README.md:653`)
- "Nothing your Walka account does changes or deletes a walk on your
  computer." (`README.md:655`)
- "The sign-in Walka keeps is not a key for walks. It is the whole Walka
  account: whoever has it can read the body numbers and the food diary
  there, make your page public and delete the Walka account."
  (`README.md:658`)
- "Where it is kept: in Windows' Credential Manager, or in your Mac's
  keychain - never in a file of Walka's." (`README.md:662`)
- "On a system that has neither (Linux, from source) it is kept in memory,
  and you are signed in until Walka is closed." (`README.md:663`)
- "Signing out in Walka removes it." (`README.md:666`)
- "If Windows or your Mac does not let Walka take it out, Walka says so
  under Walka account, with a button that tries again: until that works, the
  sign-in may still be there." (`README.md:666`) Written in this round.
- "Deleting Walka.exe (or Walka on a Mac) does not remove it: sign out
  first." (`README.md:669`) Changed in this round. Old: "Deleting Walka.exe
  (or Walka on a Mac) does not: sign out first." (was `README.md:661`)
- "(If Walka is gone already, or cannot take it out, the entry can be
  deleted by hand, in Windows' Credential Manager or in Keychain Access on a
  Mac: its name begins Walka account.)" (`README.md:670`) Changed in this
  round. Old: "(If Walka is gone already, the entry can be deleted by hand,
  in Windows' Credential Manager or in Keychain Access on a Mac: its name
  begins Walka account.)" (was `README.md:662`)
- ""Sign out" on walka.dev does not sign this computer out. A computer stays
  signed in until somebody signs out on it, in Walka, or the Walka account
  is deleted." (`README.md:673`)
- "A proxy set for your system is not used. Walka makes its requests
  directly, and asks nobody whose certificate it cannot check."
  (`README.md:676`)
- "Where the internet is reached only through a proxy, Walka cannot sign in
  or send." (`README.md:677`)

**"Private by default", of the Share picture**

- Old: "Nothing is uploaded and nothing is posted for you." (was
  `README.md:682`) New: "It is not uploaded, and nothing is posted for you."
  (`README.md:788`)

**"Is this allowed?"** (added after "... which the addon does not put on the
strip.")

- "With sending to your Walka account switched on, one thing read off the
  game goes with each walk: whether a walking session was running."
  (`README.md:977`)
- "Not your character, its name, its realm or its road." (`README.md:980`)

**Development, the list of modules**

- Added: "walka_account/ (signing in to a Walka account and sending walks to
  it - the only part that asks anything over the internet),"
  (`README.md:1069`)

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

The ten sentences a reviewer picked as most worth the owner's own eye, as
they stand now, and after them what this round added.

1. The sentence under the sign-in buttons (1.2, "Nobody is signed in"). On
   the live project `<host>` will read as a string of letters and not as
   walka.dev; "that" in "until you switch that on" points at nothing on the
   card; and a minute later the same card can say "This sign-in has no Walka
   account yet".
2. The first-step sentence (1.2), with "it is under another e-mail address"
   where the brief has "you made it another way": question 3.
3. "What goes, of each walk: ..." (1.3). One sentence of ninety words. It
   leaves out two of the sixteen things that are sent: a random number made
   for each walk, and that it came from Walka. The README names both.
4. "Your page on walka.dev is public. ..." (1.3). It is said for every
   public page, also one that lists neither days nor walks.
5. "Nothing is sent. Walka has not sent `<n>` walks on this computer to your
   Walka account." and its partner for walks that were sent (1.2, "Sending
   is off"). New in this round.
6. The question before a walk that was sent is deleted, "It stays in your
   account ..." (1.6): question 2. And the two new questions beside it, for
   a copy in a Walka account that is not signed in.
7. The two feet while sending is on (1.1). The website quotes the rail's
   foot.
8. "Your Walka account did not take `<n>` walks. They stay here." (1.2),
   with the Walka account's own message under it, as it came.
9. The README's sixth feature, and in its new section: "The sign-in Walka
   keeps is not a key for walks. It is the whole Walka account: ..." with
   "Signing out in Walka removes it." and the sentence after it.
10. SECURITY.md: "the dashboard has no password, and it is shut off from
    other web pages, not from programs", what such a program can do, and the
    exception made for `Walka.exe httpscheck`.

New in this round, and seen by nobody but their writers:

- The card: the three sentences "Walka could not make sure that your sign-in
  was taken out of ..." with the button "Try again" (1.2).
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

Each with a recommendation. Nothing here is changed until the owner says so.

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
4. **README, "Only listens."** The first of the six features is headed "Only
   listens." without "to your treadmill" (`README.md:123`), four points
   above the changed sixth, which now says walks can be sent. The treadmill
   is named in the very next words, so it is not false. The brief says both:
   change nothing about the belt, and keep "to your treadmill" attached to
   "only listens" wherever sending is described nearby. Left as it is. Leave
   it (recommended), or make the head "Only listens to your treadmill."? The
   website quotes these six.
5. **How the entry is named.** The card and the README say of the stored
   sign-in "its name begins "Walka account"", and not its whole name: the
   rest of the name is the address of the database. How Credential Manager
   and Keychain Access show it was looked at on no real Windows or Mac. Good
   enough until the owner has looked (recommended), or name it another way?
6. **A narrow window.** In a window narrower than 1180 pixels the rail's
   foot is not shown (Walka's own window opens 1200 wide; it was so before
   this change). Before, the hidden words were a comfort. Now, with sending
   on, they are what tells that walks are sent - on Live, History and Share
   nothing else does. In Settings the card's "Sending on" and the foot say
   it at every width. Should the sentence stay in sight in a narrow window,
   for instance in the bar at the top? Not built; it needs the owner's word.
7. **After signing out, History speaks of a Walka account.** The rule is
   that nothing outside the card says a word of a Walka account to a person
   who is not signed in. The new question before deleting (1.6) breaks it on
   purpose, for walks of which a copy was sent from this computer, and only
   for those: the copy stays, and decision 8 wants the person told. Somebody
   who never signed in sees the words there always were.
8. **Three sentences in the README that the brief did not ask for**, each
   added because the rest would otherwise say more than is true or leave a
   person stuck. Strike what is not wanted: "It stays when you sign out, and
   so does the e-mail address in it."; "... but, like anything asked over
   the internet, it arrives with your IP address."; "(If Walka is gone
   already, or cannot take it out, the entry can be deleted by hand ...)".
9. **Not written in the README, though true:** after an update a Mac may
   refuse the new Walka the stored sign-in (the card then says "Sign in
   again"); a Walka account has room for 20,000 walks; on the website a walk
   is deleted one at a time; Discord, GitHub or Google learn of each sign-in
   made from Walka (the website's privacy page is the place for that).

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
   longer the only thing the foot can say.

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
  chosen when it was switched on (all, or from which moment);
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
keychain, and on other systems in memory until Walka is closed.

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
were made again on 24 November 2026 from the page as it is now, in a window
1200, 900 and 480 pixels wide, light and dark - the new states among them
(the sign-in that may still be in the password store; the question before
deleting a walk whose copy is in a Walka account that is not signed in; the
first step not taken, without a count). They are not on the branch: they go
to the owner with the report. They were made on Linux with a browser that
says it is on Windows, so they show the words for Windows.
