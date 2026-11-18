# Hardware notes: UREVO Strol 2E (URTM030), and other treadmills

Walka drives a treadmill over standard Bluetooth FTMS. It was written
around the UREVO Strol 2E, which most of this page is about; what is known
about other makes - the WELLFIT TM029 Pro first of all - is under
[Other treadmills](#other-treadmills).

What is known about controlling the Strol 2E over Bluetooth, where it comes
from, and what still needs confirming on a real unit. None of this was
measured by Walka's author; it is collected from the manual and from
open-source projects that control UREVO pads. Run `walka probe` on your pad
to check it. The probe never sends a command, so it cannot move the belt;
the only thing it writes is the switch that turns notifications on.

## Summary

| | |
|---|---|
| Bluetooth name | `URTM030` (UREVO pads advertise their model code) |
| Protocol | Standard Bluetooth FTMS (Fitness Machine Service `0x1826`). No vendor handshake needed. |
| Pairing | None. **Do not pair it in Windows (or macOS) Bluetooth settings.** |
| Connections | One at a time. The UREVO phone app must be closed or the pad is invisible. |
| Which device Walka picks | The address you chose in the dashboard, if any. Otherwise a device whose Bluetooth name is a known one (`URTM...`, `WELLFIT TM Linker`); some other Fitness Machine is only used if no such name is heard during the whole 15-second scan, and then the one with the strongest signal. A machine that says in its advertisement that it is not a treadmill (a bike trainer, a rower) is never used. |
| Speed range (US) | 0.6-4.0 mph with the handle folded (desk mode), 0.6-6.2 mph with it raised |
| Speed step | 0.1 mph over Bluetooth on sibling models (the console buttons step 0.2 mph) |
| Start | 3-2-1 countdown, then the belt starts at its minimum speed |
| Max user weight | 265 lb / 120 kg |
| Belt | 15 x 40.2 in (38 x 102 cm) |

## Evidence that plain FTMS works

* [ftms-js](https://github.com/Web3Dave/ftms-js) lists "Urevo Strol 2E
  (`URTM030`)" as verified on hardware for connect, live data,
  start/stop/pause/resume and speed.
* [VRTI](https://raphii.co/vrti/supported-devices/treadmills/) lists
  "UREVO Strol 2E (URTM030) - Generic FTMS - Speed Control".
* The UREVO manual says the pad works with Zwift and Kinomap, which speak
  FTMS.
* [qdomyos-zwift](https://github.com/cagnulein/qdomyos-zwift) routes every
  device named `URTM...` to its generic FTMS treadmill driver.

How strong is that? Moderate. The ftms-js project is one person's report
from a repository that was a few days old when this was written, and VRTI's
list is community-reported. Everything else comes from sibling models
(URTM054, URTM059, URTM041), which share a firmware family but are not the
same pad. That is why the probe comes first.

## Command sequence

1. Subscribe to indications on the Control Point (`0x2AD9`) *before*
   writing to it.
2. Write `00` (Request Control) once per connection.
3. Write `07` (Start or Resume). The pad counts down 3-2-1 and starts the
   belt at minimum speed.
4. Wait until Treadmill Data (`0x2ACD`) reports a speed above zero. Speed
   writes are ignored while the belt is stopped, and zero is not a valid
   speed.
5. Write `02 lo hi` (Set Target Speed, unsigned 16-bit little-endian, units
   of 0.01 km/h).
6. `08 02` pauses (the pad keeps its counters); `08 01` stops (the pad's
   "END": counters reset).

Each command is answered by an indication `80 <opcode> <result>` (`01` =
success, `05` = control not permitted: request control again). A missing
answer does not mean the command was not applied, so Walka never blindly
repeats a start or a speed change. (A stop is different: Walka watches the
reported speed and asks again for as long as the belt keeps moving.) An
answer is only taken for the command it names: one that arrives late, for
an earlier command, is ignored.

**A pause or stop goes first.** The driver sends one command at a time and
normally waits up to three seconds for each answer. A pause or stop does
not wait behind that: it cuts the wait short, a start or speed change that
has not been written yet is dropped, and for a stop the driver does not
wait for the answer to Request Control either. What remains is the 0.4 s
the pad needs between two writes, so a stop is on its way within about
half a second. A Bluetooth write that does not complete within two seconds
(a dying link) is given up on rather than waited for.

Things other projects learnt the hard way, which the driver follows:

* **A start right after a stop is swallowed.** After `08 01` the Strol 2E
  spends a few seconds "post-workout" (it says so on the Training Status
  characteristic, `0x2AD3`). A start sent then is acknowledged with success
  and ignored. Walka waits ten seconds after any stop - longer while the pad
  still reports post-workout - before starting the belt again, and never
  takes the acknowledgement as proof: only a reported speed above zero
  counts. In-session halts use pause, not stop.
* **Requests must not arrive back to back.** Cheap Bluetooth modules drop
  them, so the driver leaves a gap between subscriptions and at least 0.4 s
  between control-point writes.
* **Request Control may be refused and still work.** Some pads answer it
  with a failure and then obey every command. The driver carries on and only
  asks again when a command comes back "control not permitted".
* **A speed write while the belt is stopped** is refused ("operation
  failed") or silently ignored, depending on the model.

## Quirks Walka handles

* **Speeds are always km/h on the wire**, even on a pad that displays mph.
* **US firmware rounds twice.** It truncates a target to 0.1 km/h, converts
  to mph and truncates again to 0.1 mph - so the natural 3.22 km/h for
  2.0 mph arrives as 1.9 mph. Walka sends the km/h value rounded *up* to the
  next 0.1 (3.3 km/h -> 2.05 mph -> 2.0 mph). A sibling pad reports its
  range as 0.96-6.43 km/h in steps of 0.16 km/h, which is how Walka
  recognises mph firmware; you can force it in the settings.
* **The pad's counters reset** after a stop, or after a pause longer than
  10 minutes; its timer display wraps at 99:59 and its calorie counter stops
  at 6553. Walka therefore keeps its own totals and uses the pad's counters
  only as increments.
* **Who stopped the belt?** The pad reports a stop or pause the same way
  whoever caused it ("stopped or paused by the user"). After a pause or stop
  of its own, Walka expects exactly one such report, saying the same thing
  it asked for (paused for a pause, stopped for a stop), within ten seconds
  - time for a pad that only reports once the belt has run down. Anything
  else is taken as somebody at the pad, and is final: a second report, a
  stop when Walka paused, anything from the safety key, however soon after
  Walka's own command it comes.
* **A speed range with a minimum of zero.** Some treadmills advertise
  0.00 km/h as their minimum. Walka then uses its default minimum but keeps
  the advertised maximum: it never commands more than the pad says it can do.
* **The top speed step may be unreachable.** For 4.0 mph the rounded-up
  value is 6.5 km/h, above the 6.43 km/h the pad advertises. If the pad
  refuses it, Walka sends 6.43 instead (which lands on 3.9 mph) and treats
  3.9 mph as the top from then on.
* **The desk-mode limit may not be enforced by the pad.** The manual ties
  the 4.0 mph limit to the remote ("to accelerate to 6.2 mph, press + on the
  console"), not to a sensor in the handle. A pad that treats Bluetooth like
  its console might accept more. Walka does not rely on the pad for this:
  its own speed cap is enforced first, and cannot be set above 6.5 km/h.
* **Steps** are not part of standard treadmill data. Sibling models either
  append a 24-bit step counter to Treadmill Data (flagged by a reserved
  bit, `0x2000`) - Walka reads that - or only send steps on a vendor
  channel (`0xFFF1`), which Walka does not use. If your pad sends no steps,
  Walka estimates them from speed and your height and says so.
* **Calories** shown by the pad ignore your weight; Walka computes its own.

## Still unknown for the URTM030 specifically

Found by reading the probe report:

* the exact speed range and step it advertises, folded and unfolded (the
  console buttons step 0.2 mph; siblings step 0.1 mph over Bluetooth);
* whether targets above 4.0 mph are refused with the handle folded;
* where (or whether) it reports steps - its manual and at least one review
  describe a display with speed, distance, time and calories only, while
  UREVO's product page mentions steps. By default the probe listens to the
  standard services only; `walka probe --all` also switches on the
  notifications of the vendor's own channels (`0xFFF1` and the like), which
  is where a step counter would show. What switching those on does on this
  firmware is not known - nothing, on the sibling models;
* how it reports a pause or stop that was sent over Bluetooth: at once or
  when the belt has stopped, and whether a pause is reported as "paused"
  (`02 02`) and a stop as "stopped" (`02 01`). The probe cannot show this -
  it sends no commands. If, after the belt has paused because your
  character stood still, the dashboard says "Belt stopped at the treadmill"
  although nobody touched the pad, this is why: please report it;
* whether it counts down before starting, and how fast the belt accelerates;
* whether it stops by itself when nobody is walking;
* whether it switches itself off, and drops the connection, after a long
  pause (siblings do; Walka then reconnects when you next start a session).

## Other treadmills

The driver is not UREVO's: it is the Fitness Machine Service as the
Bluetooth specification describes it, written carefully. Any treadmill
that implements that service *with a speed target* can be driven. What the
make changes is how the treadmill is found, and which firmware habits have
to be allowed for.

### How a treadmill is found

1. **By address**, once Walka has connected to a treadmill (it then only
   reconnects to that one) or you picked one from *Treadmill → Scan*.
2. **By name**, for the names in
   [`treadmill/known.py`](../companion/src/walka/treadmill/known.py):
   taken the moment it is heard.
3. **By what it advertises**: the Fitness Machine Service (`0x1826`). Such
   a device is only taken when the whole scan has turned up no known name,
   and then the one with the strongest signal. A fitness machine may add to
   its advertisement what kind of machine it is (service data: one flags
   byte, then a 16-bit type where bit 0 is "treadmill"). One that says it
   is something else is left out, here and in the dashboard's scan; one
   that does not say is taken to be a treadmill, and refused on connecting
   if it turns out to have no Treadmill Data characteristic.

A treadmill that advertises neither a known name nor the service does not
show up at all. If yours is like that but does have the service once
connected, its name can be added (below).

### What Walka expects of the treadmill

* Treadmill Data (`0x2ACD`) and the Control Point (`0x2AD9`). Without both
  it is refused.
* Start or Resume (`07`), Set Target Speed (`02`) and Stop (`08 01`). Pause
  (`08 02`) is used where the treadmill has it; a pause that is refused, or
  that does not stop the belt, is followed by a stop.
* A speed in Treadmill Data while the belt moves. Walka trusts nothing
  else: not an acknowledgement, not a status event.
* Supported Speed Range (`0x2AD4`), if there is one, is read for the
  minimum, maximum and step. Without it Walka assumes 1.0-6.0 km/h in steps
  of 0.1 (0.6-4.0 mph in steps of 0.1 if the firmware grid is set to mph).
* The feature bits (`0x2ACC`) are read. If they say "no speed target", the
  dashboard says so and Walka tries all the same - some firmware says no
  and obeys.

What Walka does **not** do on any treadmill: go above its own speed cap
(at most 6.5 km/h, 4.0 mph - a treadmill that reaches 16 km/h is still
walked on at walking pace), or touch the incline.

### WELLFIT (TM029 Pro, TM029, TM007 ...)

| | |
|---|---|
| Bluetooth names | `WELLFIT TM Linker` (the treadmill, for apps) and `WELLFIT TM Audio` (its loudspeaker). Walka knows the first; the second is not a treadmill. |
| Protocol | Standard FTMS. WELLFIT's own instructions for Kinomap say to choose the brand "FTMS"; it also connects to Zwift. |
| Phone app | WELLFIT Fitness. Close it before connecting. |
| Speed range | TM029 Pro: sold as up to 10 mph in the US, 1-16 km/h in Europe. What it advertises over Bluetooth is not known. |
| Start | Counts down, then starts at 0.6 mph (reported as 0.96 km/h) on a US unit. |

Where this comes from: a
[qdomyos-zwift issue](https://github.com/cagnulein/qdomyos-zwift/issues/2659)
in which the owner of a WELLFIT treadmill (model not named) and the
project's author got it working over FTMS. At the end the owner reports
that starting from the app counted down and went to 0.6 mph, that speed up
and down "worked perfectly", that pause held the belt until resumed, and
that stop switched it off. The
[change that did it](https://github.com/cagnulein/qdomyos-zwift/pull/2686):

* Request Control is written twice, half a second apart, when the
  connection is set up, and **not** again before each speed change (which
  that project does for other treadmills). Walka asks once per connection
  on every treadmill; for a WELLFIT it asks twice, 0.4 s apart, before the
  first command. The one exception is a stop as the very first command of
  a connection: that goes out after a single request rather than wait.
* The Control Point's indications are switched on before writing to it -
  which Walka does for every treadmill.

That is one treadmill, one owner, and another program's driver. It says
the command set works; it does not say Walka's timing does.

Not known for a WELLFIT, and found by a `walka probe` report or a first
careful session:

* whether it advertises the Fitness Machine Service and a machine type
  (if not, it is still found by its name);
* the speed range and step it reports. US units very likely report an mph
  grid (0.96 km/h minimum, 0.16 km/h steps), which Walka then recognises by
  itself; if the belt lands one step off the speed shown on the dashboard,
  set *Settings → Treadmill → Firmware speed grid* by hand;
* **how its firmware rounds a target speed.** Walka's rounding-up trick (see
  [Quirks](#quirks-walka-handles)) is right for UREVO's US firmware, which
  truncates twice. On firmware that rounds to the nearest step instead,
  some targets would land 0.1 mph high - including at the speed cap. Watch
  for this in the first manual session and report it;
* how it reports a pause or stop sent over Bluetooth, whether it stays
  "post-workout" after a stop, and whether steps are in its data;
* whether the safety key being out is reported as such, or only as a
  refused start.

### Other makes

Nothing is known first-hand. A treadmill that an independent app can
*drive* (not just read) over FTMS should work as it is: let the scan run
its 15 seconds, or pick it from *Treadmill → Scan*. qdomyos-zwift's source
is the best public list of which Bluetooth names are FTMS treadmills, but a
name on that list is not proof of plain FTMS - several of them need
changes of their own there.

To add a treadmill to the names Walka knows:

1. Run `walka probe` on it (read-only) and keep the report: it shows the
   advertised name, the services, the speed range and the feature bits.
2. Do the steps in the README's *Before your first walk*, standing beside
   the belt.
3. Add a line to `KNOWN_TREADMILLS` in `treadmill/known.py` - the start of
   the Bluetooth name, as exactly as the make allows - and a paragraph
   here saying what was seen and on which model.

## Windows notes

* The Bluetooth library (bleak) documents Windows 11 as its supported
  version; Windows 10 generally works.
* If the pad has been paired in Windows *Bluetooth & devices*, remove it
  there: Windows then holds the connection and the pad stops advertising.
* Weak built-in Bluetooth (no external antenna) is a common cause of
  dropouts; a USB Bluetooth 5 adapter is the usual fix.
* The companion's console window: clicking inside a classic Windows console
  starts a text selection, which freezes any program that then writes to
  it. The companion switches that ("QuickEdit") off for its own window
  while it runs and writes its log lines from a separate thread, so the
  window cannot hold up the belt control. To copy text from the window, use
  its menu (*Edit -> Mark*).
* Logging off or shutting Windows down: Windows does not tell a program
  like the companion through its console, so the companion keeps a hidden
  window to be told, and stops the belt as it does when its own window is
  closed. This has not been tried on a real PC yet - stop the belt yourself
  before you shut down.

## Mac notes

The Mac version is new. GitHub's Macs build it and run the tests, and the
finished program is started there: it drives the simulated belt, registers
the STOP hotkey and stops in an orderly way when its window is closed. It
has not been tried with the game, a real screen or a walking pad yet.

* Bluetooth goes through macOS' CoreBluetooth (by way of bleak). macOS
  does not show a device's Bluetooth address: the "address" the companion
  stores for the pad is an identifier macOS makes up for it, different on
  every Mac. A settings file copied from a Windows PC will not find the
  pad by its address: clear *Treadmill -> address* (or press *Scan*).
* macOS asks before a program may use Bluetooth or record the screen.
  Walka runs inside Terminal, so both are asked for, and granted to,
  Terminal (*System Settings -> Privacy & Security*). Without the
  Bluetooth permission the scan fails; without Screen Recording macOS hands
  over a picture with no game in it, which is why the companion asks
  macOS first and says so on the dashboard instead of *no game signal*.
* The screen is measured in points, and a Retina display has two pixels
  to the point. The companion asks for every pixel, so `Walka beacon`
  reports cell sizes in real pixels (`cell=4.00px` by default) and
  positions in points.
* macOS says where the game's window is, but not how tall its title bar
  is. The strip is looked for from the window's top edge down to 48
  points below it - a title bar is 28, and in full screen under a
  MacBook's camera notch the picture can begin 38 down - and nowhere
  else. If `Walka beacon` finds the window but no strip, please report
  the two positions it prints.
* The STOP hotkey is registered through Carbon (`RegisterEventHotKey`),
  which needs no permission. Its key presses arrive on the program's main
  thread, where the companion asks for them twenty times a second.
* Closing the Terminal window, logging out and shutting down all reach the
  companion as a signal (SIGHUP, SIGTERM): it stops the belt and saves the
  walk before it exits, as with Ctrl+C. A forced quit cannot do that.
* macOS does not recognise the program (it is not signed with an Apple
  developer account or notarised), so the first start has to be allowed
  under *System Settings -> Privacy & Security -> Open Anyway*.

## Sources

* UREVO URTM030 manual V3.0 (2023-11-20).
* [TechHQUSA/underfoot PROTOCOL.md](https://github.com/TechHQUSA/underfoot/blob/main/PROTOCOL.md) - measured protocol of the Strol 2E Pro (URTM059).
* [jcpsimmons/urevo-walkpad](https://github.com/jcpsimmons/urevo-walkpad) - SpaceWalk 5L (URTM054), including the mph rounding workaround.
* [Linemass/walking-pad](https://github.com/Linemass/walking-pad) - a Python + bleak Windows client for UREVO pads.
* [Web3Dave/ftms-js](https://github.com/Web3Dave/ftms-js), [VRTI supported devices](https://raphii.co/vrti/supported-devices/treadmills/), [qdomyos-zwift](https://github.com/cagnulein/qdomyos-zwift).
* WELLFIT: [the maker's app page](https://wellfittreadmill.com/pages/app) (Bluetooth names, Kinomap and Zwift), qdomyos-zwift [issue 2659](https://github.com/cagnulein/qdomyos-zwift/issues/2659) and [pull request 2686](https://github.com/cagnulein/qdomyos-zwift/pull/2686).
* Bluetooth SIG, Fitness Machine Service 1.0 - the advertisement's service data (machine type), features and control point.
