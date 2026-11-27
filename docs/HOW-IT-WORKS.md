# How Walka works

[← Back to the README](../README.md) · [Disclaimer](../DISCLAIMER.md) · [Safety](SAFETY.md)

## What Walka is

Walka is a walking log for people who walk on an under-desk pad while they
play **World of Warcraft: Forever**. You walk at the speed you set on the
pad; Walka counts your time, steps, distance and calories - for this walk
and every one before it - shows them on its dashboard and, if you like, in
a small floating window on top of the game, and keeps them per
character.

It is two pieces:

* **the addon** (`Walka/`) - marks your walking sessions and knows which
  character they belong to, and keeps your character's journey and road.
  It draws nothing on the screen: it is the game's end of the connection,
  and the app shows everything;
* **the companion** (`companion/`, the `Walka.exe` you download above, or
  `Walka` on a Mac) - a small app that listens to the treadmill over
  Bluetooth, records your walks, reads what the addon has saved, and shows
  a dashboard in a window of its own.

Neither of them moves the belt. Until version 0.6 the companion did: the
belt followed your character. That was taken out - a motorised belt under
someone who is looking at a game was not worth the risk of a program
getting it wrong - and those versions have been withdrawn. See
[Walka only listens](#walka-only-listens).

Built around the **UREVO Strol 2E (URTM030)**, and made to read any
treadmill that reports its data over standard Bluetooth FTMS - see
[which treadmills work](TREADMILLS.md#which-treadmills-work). The companion also works
without any game: a walk is recorded whenever the belt moves.

## How it works

```mermaid
flowchart TB
  addon["Walka addon<br/>in the game"] -- "its saved file, written by the game<br/>at logout and /reload" --> exe["Walka.exe (Walka on a Mac)<br/>on your computer"]
  pad["Walking pad"] -- "Bluetooth: speed, distance, steps<br/>(one way: Walka only listens)" --> exe
  exe -. "a file the game loads at login" .-> addon
  classDef box fill:#256abf,stroke:#256abf,color:#ffffff
  class addon,exe,pad box
```

An addon cannot use Bluetooth, files or the network. What it can do is keep
data in its *saved variables*, which the game writes to a file of its own
when you log out, type `/reload`, or are disconnected. The addon keeps its
walking sessions there - when each began and ended, and for which
character - and the companion reads that file. **Walka never sends input to
the game** - no key presses, no mouse input, no memory reading - and it
does not look at the screen.

The walk itself comes from the treadmill alone: the companion records it
while the belt moves, game or no game, and ends it when the belt has stood
still for a minute. Once the game has saved its file, a walk that overlaps
one of the addon's sessions (by at least half of the shorter of the two) is
filed under that session and the character's name. So a walk shows which
character it was for only after a logout or `/reload`; until then it is
listed as a walk with no game session. If the game crashes, the sessions
since its last save are lost - the walk itself is still recorded.

### What Walka looks at

* **Not the screen.** The companion takes no screenshots and does not
  capture the game window or anything else on screen. (Until version 0.10
  it read a strip of squares the addon drew in the corner of the game
  window. That is gone, and so is everything that came with it: the game no
  longer has to run in a window, and on a Mac no screen recording
  permission is asked for.)
* **Two files the game writes for the addon,** and only these: the addon's
  saved file in `WTF\Account\<account>\SavedVariables\Walka.lua`, which it
  looks at every few seconds and reads again only when the game has
  rewritten it, and the per-character `Walka.lua` files a few folders
  further down (`WTF\Account\<account>\<realm>\<character>\...`), read
  when the Share page asks for your character's road. It never writes to
  them.
* **What it takes from them:** the walking sessions the addon marked (when
  each began and ended, the character's name and realm), and each
  character's [journey](USING.md#your-journey-and-a-picture-to-share):
  level, time played, how far it has travelled, the real-world walking done
  while playing it, and its road. Not whether your character fought, died
  or rested - the addon does not keep that for the companion. The realm is
  used only to tell characters apart, and is never shown, sent or logged.

An addon also cannot receive live data, so the addon itself only keeps an
estimate (counted from the pace you tell it: see
[Using it](USING.md#using-it)) for your character's journey; the
companion's *measured* numbers are written to a file the game loads at
login or on `/reload`. The dashboard - and the floating window on top of
the game - always show the measured numbers live. What they do not show any
more is the game's state from moment to moment (whether a session is
running or paused, how your character is moving): the dashboard says when
the game last saved its file.

Details: [docs/PROTOCOL.md](PROTOCOL.md).

## Walka only listens

Walka does not start, stop, pause or steer the belt. Not from the game,
not from the dashboard, not when something goes wrong.

* **You start the belt** with the treadmill's remote or buttons, and **you
  stop it** the same way - or with its safety key. Keep the remote where
  your hand finds it without looking.
* **The speed is the one you set** at the treadmill. Walka reads it; it
  cannot change it.
* **Closing Walka, or pressing *Disconnect*,** ends the recording and lets
  go of the treadmill. The belt goes on doing what it was doing.
* **When you step off, stop the belt.** Nothing else will.

What Walka does to a treadmill is this, and no more: it connects, reads
the speed range the treadmill says it has, and switches on the
notifications through which the treadmill reports its speed, distance and
status. Older WalkingPads and FitShow pads report only when asked: to
those, and only those, Walka also sends one fixed "report your numbers"
question once a second ([docs/SAFETY.md](SAFETY.md#one-exception-for-pads-that-only-report-when-asked)). The channel through which an app could control the belt (the
*Fitness Machine Control Point*) is never written to, and the code that
once did has been deleted - there is no setting that brings it back. How
that is checked: [docs/SAFETY.md](SAFETY.md).

**If you have a version up to 0.6.1:** it could move the belt. Those
downloads have been removed; replace it with the current one and delete
the old `Walka.exe`. Your walks and settings are kept (the settings that
were about steering the belt are simply no longer read), and the addon is
updated with *Install* on the dashboard.

## Safety

Short version - the long one is [docs/SAFETY.md](SAFETY.md):

* **Walka cannot move the belt.** It only listens to the treadmill. There
  is no command in it to send, no interface in it to send one through, and
  nothing the game or the dashboard does reaches the treadmill. The one
  exception is a fixed status question to two kinds of older pads, which
  say nothing until asked. The tests check that an emulated treadmill has
  been sent nothing else after long runs of everything Walka can be made
  to do.
* **So the belt is entirely yours** - and so is stopping it. Walka is not a
  safety device and cannot stop a belt: the treadmill's own stop button,
  remote and safety key are what does.
* **You are walking while you look at a game.** Start slow, keep the remote
  within reach, stop the belt when you step off, and keep children and
  pets away from it.

Found a way to make Walka send a treadmill anything at all? Please report
it in private: [SECURITY.md](../SECURITY.md).

You use Walka at your own risk, with no warranty - see
[DISCLAIMER.md](../DISCLAIMER.md), which also says when to ask a doctor first.

## Is this allowed?

Honestly: **it is not approved by Blizzard, and you use it at your own
risk.**

What Walka does and does not do:

* The addon uses only the public addon API, is free and unobfuscated, and
  follows the addon policy.
* The companion never sends input to the game, never automates anything
  in the game, never reads the game's memory and does not look at the
  screen. Of the game's files it touches only Walka's own: the addon folder
  (it installs the addon and writes `CompanionData.lua` there) and the
  files the game saves for the addon, `WTF\Account\...\SavedVariables\Walka.lua`,
  which it only reads ([exactly what](#what-walka-looks-at)). What it
  learns from them is what the addon kept, after the fact: your walking
  sessions and your character's journey - not whether it fought, died or
  rested. With sending to
  [your Walka account](WALKA-ACCOUNT.md#your-walka-account-on-walkadev) switched on, one
  thing from the game goes with each walk: whether it had been filed under
  a walking session in the game when it was sent. Not your character, its
  name, its realm or its road.
* Walka is free, and stays free: no paid version, no advertising and no
  asking for money - in the game or outside it.
* When the game hides a value from addons (speed in combat), Walka does not
  try to get around it; the addon estimates instead.

Blizzard's enforcement statements target automation, input broadcasting,
client modification and tools that get around addon restrictions for an
advantage; Walka is none of those. But Blizzard's EULA is written broadly
(it prohibits unauthorised software that "reads" information from the
game), Blizzard does not pre-approve third-party tools, and no statement
says that an app reading its own addon's saved file, as Walka's does, is
fine. Many addons have companion apps that read their saved files - that is
not the same as permission.
