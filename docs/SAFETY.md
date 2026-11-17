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
   passed on to the game (change it under *Settings -> Safety*);
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
   Safety*). No game state, addon setting or corrupted frame can make the
   companion command more than that, nor more than the range the treadmill
   itself advertises.

3. **Changes are gentle.** Speed commands go out at most once a second and
   move by at most 0.2 km/h per second up and 0.4 km/h per second down - on
   a US pad that is one 0.1 mph step per second up, two down. (Research
   treadmills that change speed under a walker limit themselves to about
   twice that.) The addon also smooths the game's instant speed changes
   before they ever reach the companion, and holds your pace through short
   stops (looting, talking) so the belt does not surge and sag.

4. **Losing the game signal slows the belt, never speeds it up.** The signal
   is lost whenever the companion cannot see the strip in the corner of the
   game window: a loading screen, the game minimised, another window on top
   of that corner, the game frozen or closed. Then:
   * for 5 seconds the belt holds its speed;
   * then it slows to the *safe speed* (0.8 mph by default) - never faster
     than it was already going;
   * after 2 minutes of that, it pauses;
   * if the game window disappears altogether, the session ends and the belt
     stops.

5. **A stop at the treadmill is final.** If the belt is stopped or paused
   with the pad's own button, its remote or its safety key - or if it simply
   stops moving - Walka will not start it again by itself. To carry on,
   pause and resume the session yourself (HUD or dashboard), start a new
   session, or press start on the remote. The addon's *automatic* pause
   when you go AFK does not count as you resuming: stop the belt at the
   pad, walk away, come back and touch a key, and it stays stopped.

6. **The belt only starts on a deliberate act.** Pressing *Start* starts it
   (after the treadmill's own 3-2-1 countdown, at its minimum speed). In
   every other situation where the belt is stopped but a session is still
   marked active - the companion was started mid-session, the treadmill
   reconnected, the belt was paused after a long signal loss - Walka waits
   until **your character starts moving** (you pressed a movement key) or
   you press *Resume*.

7. **Starts and speed changes are never repeated on a guess; stops are
   repeated until they work.** If a start or speed command gets no
   acknowledgement it may or may not have been applied, and Walka does not
   send it again blindly. A stop is the opposite case: after any pause or
   stop of its own, Walka watches the speed the belt reports and sends a
   stop every few seconds for as long as it is still moving and not slowing
   down.

8. **Closing the companion stops the belt** it was driving (Ctrl+C, or
   closing its window). A crash or a killed process cannot do that: the belt
   then keeps its current speed until you stop it at the treadmill.

9. **STOP latches.** The dashboard's STOP button and the hotkey stop the
   belt immediately, and nothing - not a session still running in the game,
   not the game coming back into view - restarts it until you resume.

10. **Speeds stay in walking territory.** The cap can be raised from its
    default 3.0 mph, but not above 6.5 km/h (4.0 mph), the top of a pad's
    desk mode. Walka changes speed by itself under someone who is looking at
    a game; running speeds are not for that.

11. **The pad is given time between a stop and a start.** A treadmill that
    has just been stopped is still closing its workout and would ignore a
    start; Walka waits about ten seconds (you will see "waiting for the
    treadmill to finish its previous workout").

## What the addon does

* *Start* in the HUD is the only thing that arms the system from the game
  side. *Stop* ends the session and stops the belt.
* Going AFK pauses the session (and the belt) automatically; it resumes when
  you come back. You can turn this off.
* By default the belt never stops while a session runs: when your character
  stands still it drops to the *standing* speed (1.0 mph). You can set the
  standing speed to "stop the belt", but every restart then costs the
  treadmill's countdown, and the belt starts when your character moves.
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
