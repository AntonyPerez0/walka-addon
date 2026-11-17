# Safety

Walka changes the speed of a motorised belt you are standing on, based on
what a video game character is doing. That deserves some care. This page
explains what the software will and will not do, so that nothing it does is
a surprise.

**Bluetooth is not an emergency stop.** Keep the treadmill's remote within
reach and use the safety key if your pad has one. If anything feels wrong,
stop the belt at the treadmill itself: Walka treats that as final and will
not restart it.

Ways to stop, fastest first:

1. the treadmill's remote, stop button or safety key;
2. **Ctrl+Alt+X** - a system-wide hotkey the companion registers on
   Windows; it works while the game has focus and the key press is not
   passed on to the game (change it under *Settings -> Safety limits*);
3. the STOP button on the dashboard;
4. *Stop* on the in-game HUD, or `/walka stop`.

## The rules the companion enforces

These live in one small state machine (`companion/src/walka/governor.py`)
with no I/O, so every rule below is covered by automated tests.

1. **Hands off unless a session is active.** Until you press *Start* (in the
   game HUD or the dashboard) Walka never sends the treadmill a command. You
   can use the pad with its remote as if Walka were not there (it will still
   log the walk).

2. **Your speed cap always wins.** The companion has a hard maximum speed
   (3.0 mph out of the box; change it in the dashboard under *Settings →
   Safety limits*). No game state, addon setting or corrupted frame can make the
   companion command more than that, nor more than the range the treadmill
   itself advertises. (On a pad that counts in mph the cap is the nearest
   0.1 mph step at or below it; the number sent over Bluetooth is that
   step rounded up to the next 0.1 km/h, which the pad cuts back down.) If you lower the cap during a walk, the belt starts
   coming down to it at once - at the gentle rate of rule 3, not in one
   jump.

3. **Changes are gentle.** Speed commands go out at most once a second and
   move by at most 0.2 km/h per second up and 0.4 km/h per second down - on
   a US pad that is one 0.1 mph step per second up, two down. (Research
   treadmills that change speed under a walker limit themselves to about
   twice that.) Speed-ups are measured from what the belt *reports*, not
   from what Walka asked for: no command is ever more than one step above
   the speed the belt is really doing, so a command that got lost cannot
   turn into a jump later. The addon also smooths the game's instant speed
   changes before they ever reach the companion, and holds your pace
   through short stops (looting, talking) so the belt does not surge and
   sag.

4. **Losing the game signal slows the belt, never speeds it up.** The signal
   is lost whenever the companion cannot see the strip in the corner of the
   game window: a loading screen, the game minimised, another window on top
   of that corner, the game frozen or closed. Counted from the last picture
   the companion saw:
   * after 1 second the belt may still slow down but is no longer sped up
     (and is not started);
   * until 5 seconds it holds its speed;
   * then it slows to the *safe speed* (0.8 mph by default) - never faster
     than it was already going, and never faster than the belt reports;
   * after 2 minutes of that, it pauses;
   * if the game window disappears altogether, the session ends and the belt
     stops - also if the treadmill was out of Bluetooth reach at that
     moment: the stop is delivered when it is back;
   * if you start the belt with the remote while the game is out of view,
     that walk is yours: Walka leaves the belt alone until it can see the
     game again. After a quarter of an hour without the game, with the belt
     off, Walka lets go of the session altogether.

5. **A stop at the treadmill is final.** If the belt is stopped or paused
   with the pad's own button, its remote or its safety key - or if it simply
   stops moving, or is found stopped when the Bluetooth link comes back -
   Walka will not start it again by itself. To carry on,
   pause and resume the session yourself (HUD or dashboard), start a new
   session, or press start on the remote. The addon's *automatic* pause
   when you go AFK does not count as you resuming: stop the belt at the
   pad, walk away, come back and touch a key, and it stays stopped.

6. **The belt only starts on a deliberate act.** Pressing *Start* starts it
   (after the treadmill's own 3-2-1 countdown, at its minimum speed). In
   every other situation where the belt is stopped but a session is still
   marked active - the companion was started mid-session, the treadmill
   reconnected, the belt was paused after a long signal loss - Walka waits
   until **your character starts moving** or you press *Resume*. "Starts
   moving" means Walka saw the character stand still and then move, after
   any stretch in which it could not watch: a character that is already
   being carried along when the picture returns (a flight path, auto-run)
   pressed no key for you. A *Resume* that cannot be acted on - the game is
   out of view, the treadmill out of reach - counts for one minute; after
   that it has to be pressed again. Ending a dashboard (manual) session
   always stops the belt, even if a game session is running behind it; the
   game session then waits for the same deliberate act.

7. **Starts and speed changes are never repeated on a guess; stops are
   repeated until they work.** If a start or speed command gets no
   acknowledgement it may or may not have been applied, and Walka does not
   send it again blindly. A stop is the opposite case: after any pause or
   stop of its own, Walka watches the speed the belt reports and sends a
   stop every few seconds for as long as it is still moving and not slowing
   down. A stop that could not be delivered because the Bluetooth link
   dropped stays owed: Walka keeps trying to reconnect (for up to half an
   hour) and stops the belt as soon as it can reach it.

8. **Closing the companion stops the belt** it was driving (Ctrl+C, or
   closing its window) - before anything else is tidied up, and including a
   belt whose earlier stop had not been confirmed. *Disconnect* on the
   dashboard does the same: a belt in use is stopped first. A crash or a
   killed process cannot do that: the belt then keeps its current speed
   until you stop it at the treadmill.

9. **STOP latches.** The dashboard's STOP button and the hotkey stop the
   belt immediately, and nothing - not a session still running in the game,
   not the game coming back into view - restarts it until you resume.

10. **Speeds stay in walking territory.** The cap can be raised from its
    default 3.0 mph, but not above 6.5 km/h (4.0 mph), the top of a pad's
    desk mode. Walka changes speed by itself under someone who is looking at
    a game; running speeds are not for that.

11. **A speed you set at the treadmill is a ceiling.** If you slow the belt
    with the remote, Walka does not fight you: it keeps following your
    character below that speed and never goes above it. The dashboard says
    so ("held at the speed you set"). The ceiling is lifted by *Resume*, by
    pausing and resuming in game, by a new session - or by you raising the
    speed at the pad again. If you speed the belt up with the remote beyond
    what the game asks for, Walka walks it back down at the gentle rate.

12. **One treadmill.** The first treadmill Walka connects to is remembered,
    and from then on it only ever reconnects to that one, so a second pad
    in Bluetooth range cannot be driven by mistake. *Forget this treadmill*
    on the dashboard (refused while a belt is in use) lets you move to
    another.

13. **The pad is given time between a stop and a start.** A treadmill that
    has just been stopped is still closing its workout and would ignore a
    start; Walka waits about ten seconds (you will see "waiting for the
    treadmill to finish its previous workout").

## What the addon does

* *Start* in the HUD is the only thing that arms the system from the game
  side. *Stop* ends the session and stops the belt.
* Going AFK pauses the session (and the belt) automatically. Coming back
  does not restart it by itself: the session resumes when your character
  next *starts moving* - it must be seen standing still and then move - so
  typing a chat line does not start a belt, and neither does a character
  that is being carried along anyway (a flight path never counts; with
  auto-run or /follow, stop and go again, or press Resume). You can turn
  the automatic pause off.
* Inside dungeons, raids and battlegrounds the game hides its AFK flag from
  addons. There Walka keeps its own clock: five minutes without your
  character moving or fighting count as away, and the first movement
  after that as back.
* By default the belt never stops while a session runs: when your character
  stands still it drops to the *standing* pace (0.8 mph). Two options
  change that: *Keep walking* holds your walking pace instead, and *Stop
  the belt when my character stands still* pauses it - every restart then
  costs the treadmill's countdown, and the belt starts when your character
  moves.
* The option *In combat, never drop below running pace* only applies once
  your character has moved in that fight: something attacking a character
  that stands still never speeds the belt up, and never starts it.
* During combat the game hides the character's exact speed from addons.
  Walka then estimates it (moving or not, plus your last known run speed),
  so the belt keeps a sensible pace; it does not try to read the hidden
  value.

## Sensible habits

* Start slow. Studies of treadmill desks find people settle at 1-2 mph,
  that typing is barely affected there, and that mouse work suffers most
  (expect your aim to be worse than seated). The defaults sit in that range
  and the cap is 3.0 mph. Raise things gradually.
* Do the first sessions out of combat, somewhere quiet, until you know how
  the belt responds to your play style.
* Use the *Steady* feel preset if the speed changes distract you.
* Keep the top-left corner of the game window uncovered, and run the game
  windowed or borderless.
* Mute the pad's buzzer with its remote if speed-change beeps bother you.
* Do not leave a session running when you walk away: stop it (or let the
  AFK pause do it).
* Children and pets: a belt that starts when someone touches the keyboard
  is a hazard. Stop the session when you step off.

## What has and has not been verified

The logic above is exercised by a large automated test-suite against a
simulated treadmill that mimics the UREVO firmware's documented behaviour.
The Bluetooth driver follows the same command sequence as open-source
projects that control UREVO pads, including one that lists the Strol 2E
(URTM030) as verified on hardware. But this software has **not yet been run
against a physical treadmill** by its author. Before your first real
session, run `walka probe` (it only reads; it cannot move the belt) and
then try a manual session from the dashboard while standing beside the pad,
not on it.
