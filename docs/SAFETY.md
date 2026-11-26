# Safety

**Walka does not move the belt.** It listens to what your treadmill reports
- its speed, its distance, on some pads its steps - and writes your walks
down. Starting the belt, stopping it and choosing its speed are yours, with
the treadmill's own remote, buttons and safety key, exactly as if Walka
were not there.

Versions up to 0.6 were different: the belt followed your character, and
the companion started, stopped and steered it. That has been taken out, not
switched off. A motorised belt under someone who is looking at a game was
not worth the risk of a program getting it wrong. Those versions have been
withdrawn; do not use one.

You still walk on a treadmill while you play, and that is at your own
risk: see the [disclaimer](../DISCLAIMER.md), and
[You, before the belt](#you-before-the-belt) below.

## What Walka does to a treadmill, all of it

When the companion connects to a treadmill it:

1. reads one value - its speed range (*Supported Speed Range*), which the
   dashboard shows;
2. switches notifications on for three things it listens to: *Treadmill
   Data* (speed, distance, time, steps where the pad sends them), *Fitness
   Machine Status* (the pad saying that it was started, stopped, paused or
   set to another speed) and *Training Status* (its countdown).

That is everything. Switching a notification on is the only thing Walka
ever writes to a standard (FTMS) treadmill, and it is what every app that
shows a treadmill's numbers has to do.

### One exception, for pads that only report when asked

Some walking pads do not speak the standard: they speak their maker's own
protocol, and send their numbers only in answer to a question. For those - and only those -
Walka sends **one fixed "report your numbers" query per protocol, and
nothing else**:

| Protocol | Written to | The bytes, always these | How often |
|---|---|---|---|
| WalkingPad (older KingSmith pads) | `0xFE02` | `F7 A2 00 00 A2 FD` ("your current status") | once a second |
| PitPat / SupeRun | - | nothing: these pads report by themselves | - |
| FitShow (`FS-...` machines only) | `0xFFF2` | `02 51 51 03` ("your status") | once a second |

These are the complete list. Walka cannot send any other bytes: the code
that sends them takes no bytes from anywhere - it looks up its protocol's
one entry in a fixed table - and a test fails if a byte of that table
changes, if anything else in Walka writes to a Bluetooth characteristic,
or if a query could reach the FTMS control point. None of these queries
starts, stops or changes the speed of a belt. The readers for these
protocols have not been tried on hardware with Walka.

The treadmill's *Fitness Machine Control Point* - the one channel through
which an app can start, stop or steer a belt - is never written to and
never listened to. Walka does not ask the treadmill for control.

## What reaches outside your computer

Walka asks GitHub, once a day, which version of Walka is the newest -
nothing about you is sent, and *Settings → Updates* switches it off. A new
version is downloaded from GitHub only when you press **Update now** (see
[USING.md](USING.md#updates)). Heart rate over the local network is off
until you switch it on in Settings: Walka then listens, on a port of its
own and with a pairing code, for a heart rate from a watch app on your home
network - see [HEART-RATE.md](HEART-RATE.md#heart-rate-over-the-local-network).
The dashboard itself answers this computer only.

## How that is made sure of

Not by a setting, and not by a rule that could be got round. The parts
that moved the belt are gone:

* **There is no command to send.** The code that builds treadmill commands
  (request control, start, stop, pause, set speed) has been deleted, with
  the rules that decided when to send them (the *governor*), the manual
  session, the STOP button and hotkey, and the second process that stopped
  the belt if the companion was killed (the *guard*).
* **There is no way to send one.** The companion reaches a treadmill
  through one small interface (`BleClient` in
  `companion/src/walka/treadmill/ftms_ble.py`), which can connect, read a
  value and switch notifications on. It has no write. The treadmill reader
  built on it (`Treadmill` in `treadmill/base.py`) can connect and
  disconnect, and nothing else. The one door that writes is the query
  link for the two pads above (`treadmill/query_link.py`): it takes no
  bytes, and writes only its protocol's query.
* **Nothing the game saves reaches the treadmill.** The addon's saved
  file, which the game writes at a logout or `/reload`, says one thing the
  companion uses for the walk: which walking session of which character it
  is filed under - after the walk, never while it runs. (Until 0.10 the
  addon drew a strip of squares on the screen for the companion; a field
  in it once asked for a belt speed. The strip is gone.)
* **Nothing the dashboard does reaches the treadmill either.** Its buttons
  connect and disconnect the companion, change settings, install the addon,
  delete walks, sign Walka in to a Walka account and out of it, and switch
  the sending of walks to it on and off.
  *Disconnect* and closing the companion let go of the treadmill and leave
  the belt exactly as it is.

The automated tests check each of these: that the reader and the Bluetooth
adapter have no method that could write; that no command builder exists;
that every reader kind, run against a stand-in for the Bluetooth library
that records every write, sends nothing but the two status queries above,
each to its own pad; that an emulated treadmill which records every byte
written to it has recorded none after long runs of everything the game, the dashboard and
the radio can do; and that a belt does the same, tick for tick, whether or
not a companion is listening to it and whatever the game saves meanwhile.

## What the addon does

The addon in the game does not move anything either - it never could. It
cannot reach the companion while the game runs at all: what it keeps, the
game writes to a file at a logout or `/reload`, and the companion reads
that.

* A walking session - which starts by itself at login, or with
  `/walka start` - says "the walks now are this character's". Once the
  game has saved it, the companion files the walk it recorded under that
  session. Clicking
  *Walka* in the game's AddOns menu pauses it.
* The addon has no windows and draws nothing on the screen. The numbers
  it keeps for the character's journey are estimates, counted from the
  pace you tell it (`/walka pace`): an addon cannot be told what the
  treadmill is really doing while the game runs. What the treadmill
  measured replaces them once the walk has been filed under the session
  and the game has loaded the companion's file, at a later login or
  `/reload`.
* The companion's floating window shows the walk on top of the game. It
  is a window like any other: it shows numbers, and has no button for the
  belt.
* Going AFK pauses the session - that is, the addon's count - if you leave
  that option on. The belt is not touched by it: stop the belt yourself
  when you step off.

## You, before the belt

Walka is not a medical device and nothing in it is medical advice. Its
calories, steps and heart rate are estimates, for interest only.

* Ask a doctor first if you have a heart condition, high blood pressure,
  trouble with balance or dizziness, an injury, if you are pregnant, if you
  take medicine that affects your balance or heart rate, if you have not
  exercised for a long time - or if you are in any doubt.
* Stop at once if you feel dizzy, faint, short of breath, have pain in your
  chest, or anything hurts: stop the belt at the treadmill and step off.
* Follow your treadmill's own manual - weight limit, safety key or clip,
  free space behind and beside the belt, footwear.
* Walking at a desk while you play is for adults. Do not do it when you are
  tired or have been drinking: your eyes are on a game, not on your feet.

## Sensible habits

* Start slow. Studies of treadmill desks find people settle at 1-2 mph,
  that typing is barely affected there, and that mouse work suffers most
  (expect your aim to be worse than seated).
* Keep the remote where your hand finds it without looking, and use the
  safety key or clip if your treadmill has one.
* Choose a speed and leave it. Changing the speed in the middle of a fight
  is one more thing to do with a hand you need.
* Do the first sessions out of combat, somewhere quiet.
* Leave the pad's buzzer on: its countdown is the warning that the belt is
  about to move, for you and for whoever else is in the room.
* Stop the belt when you step off. Walka will not do it for you - nothing
  will. Children and pets: a belt left running is a hazard.

## What has and has not been verified

Walka's logic is exercised by a large automated test-suite against a
simulated treadmill and against an emulated one at the Bluetooth level,
both modelled on the documented behaviour of UREVO firmware. The reader
listens to the same standard characteristics as open-source projects that
work with UREVO pads, including one that lists the Strol 2E (URTM030) as
verified on hardware. It has been run against **one physical treadmill**:
a UREVO Strol 2E (URTM030), from Windows 11, in November 2026 - the speed,
distance and steps on the dashboard matched the pad's own display, and
walks were filed under the character in the game. No other treadmill, and
no Mac, has been tried with it yet.

What that leaves open is whether your walks are *counted right* - whether
a particular treadmill is found, and whether its numbers are read as it
means them. It does not leave open whether the belt can be moved: there is
nothing in Walka that could. `walka probe` prints what a treadmill
exposes, for a treadmill whose numbers do not come out right; see
[HARDWARE.md](HARDWARE.md).
