# Disclaimer

**Walka does not move your treadmill. It only listens to it: the belt is
started, stopped and set to a speed by you, at the treadmill itself. You
walk on it while you look at a game, and you do that at your own risk.**

Please read this page before your first walk. It says what Walka is and is
not, what is yours to check, and what its authors do not promise.

## What Walka is, and is not

* **A notebook, not a remote control.** Walka reads what your treadmill
  reports over Bluetooth - speed, distance, on some pads steps - and writes
  your walks down. It has no way to start or stop a belt or to change its
  speed, and it sends the treadmill no commands
  ([docs/SAFETY.md](docs/SAFETY.md) says how that is made sure of). The
  one thing it sends some pads is a fixed "report your numbers" question:
  older WalkingPads and FitShow pads say nothing until asked.
  Versions up to 0.6 did drive the belt. They have been withdrawn: do not
  use one.
* **A free hobby project, and an early one.** It has been checked on one
  physical treadmill so far, a UREVO Strol 2E with Windows 11 (see
  [What has and has not been verified](docs/SAFETY.md#what-has-and-has-not-been-verified)).
* **Not a safety device.** Walka cannot stop a belt. The treadmill's own
  stop button, remote and safety key are what stops it - keep them within
  reach.
* **Not a medical device, and not medical advice.** Calories, steps, heart
  rate and everything worked out from them are estimates, for interest
  only. Do not use them to decide anything about your health, your diet,
  your medication or your training.
* **Not made, tested or approved by the maker of your treadmill.** Walka
  connects to the treadmill the way a fitness app does, to read its
  numbers. Most treadmills accept one such connection at a time, so the
  maker's own app cannot be connected while Walka is.
* **Not approved by Blizzard.** See
  [Is this allowed?](docs/HOW-IT-WORKS.md#is-this-allowed)

## Before you walk

* **Ask a doctor first** if you have a heart condition, high blood
  pressure, trouble with balance or dizziness, an injury, if you are
  pregnant, if you take medicine that affects your balance or your heart
  rate, if you have not exercised for a long time - or if you are in any
  doubt.
* **Stop at once** if you feel dizzy, faint, short of breath, have pain in
  your chest, or anything hurts. Stop the belt at the treadmill and step
  off.
* **Follow your treadmill's own manual:** its weight limit, its safety key
  or clip, the free space it asks for behind and beside the belt, the
  floor it stands on, what to wear on your feet.
* **Adults only.** Keep children and pets away from the treadmill, and
  stop the belt when you step off.
* **Your eyes are on a game.** Walking while you play takes more care than
  walking. Choose a slow speed, keep the treadmill's remote within reach,
  and do not do it when you are tired or have been drinking.

## It is your decision

By downloading, installing or using Walka you accept that you do so at your
own risk. Whether walking at a desk while playing is safe for you, for your
treadmill and for the room you do it in is for you to decide - and so is
whether to let anyone else do it on your equipment.

## No warranty

WALKA IS PROVIDED "AS IS" AND "AS AVAILABLE", WITHOUT WARRANTY OF ANY KIND,
EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE, SAFETY, ACCURACY AND
NONINFRINGEMENT. NOTHING IN WALKA, ITS DOCUMENTATION OR ITS DASHBOARD IS A
PROMISE THAT IT WILL WORK WITHOUT FAULT OR WITHOUT INTERRUPTION.

## Limit of liability

TO THE FULLEST EXTENT PERMITTED BY LAW, IN NO EVENT SHALL THE AUTHORS,
CONTRIBUTORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY - INCLUDING PERSONAL INJURY, DEATH, DAMAGE TO PROPERTY OR
EQUIPMENT, LOSS OF DATA, AND ACTION TAKEN AGAINST A GAME ACCOUNT - WHETHER
IN AN ACTION OF CONTRACT, TORT (INCLUDING NEGLIGENCE) OR OTHERWISE, ARISING
FROM, OUT OF OR IN CONNECTION WITH WALKA OR THE USE OF IT.

The law of some places does not allow all of this - for example, limits on
liability for injury caused by negligence, or on the rights of consumers.
Nothing on this page takes away a right that the law says cannot be taken
away. Where that is so, the two sections above apply as far as the law
allows.

## The code

Walka's code is published under the MIT licence: see [LICENSE](LICENSE),
which says the same "as is, no warranty" about the code. If you pass Walka
on or build on it, that licence and this page go with it.

## Names

Walka is a fan-made tool. It is not affiliated with or endorsed by Blizzard
Entertainment, UREVO, WELLFIT or any other maker of treadmills. World of
Warcraft is a trademark of Blizzard Entertainment, Inc.; the other names
belong to their owners. Code and ideas taken from other projects are listed
in [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md).

*Version 2 of this disclaimer: since 0.7 Walka only listens to the
treadmill.*
