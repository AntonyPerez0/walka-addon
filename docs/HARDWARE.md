# Hardware notes: UREVO Strol 2E (URTM030)

What is known about controlling this pad over Bluetooth, where it comes
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
| Pairing | None. **Do not pair it in Windows Bluetooth settings.** |
| Connections | One at a time. The UREVO phone app must be closed or the pad is invisible. |
| Which device Walka picks | The address you chose in the dashboard, if any. Otherwise a device whose Bluetooth name starts with `URTM`; some other Fitness Machine (a bike trainer, another treadmill) is only used if no such name is heard during the whole 15-second scan, and then the one with the strongest signal. |
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

## Sources

* UREVO URTM030 manual V3.0 (2023-11-20).
* [TechHQUSA/underfoot PROTOCOL.md](https://github.com/TechHQUSA/underfoot/blob/main/PROTOCOL.md) - measured protocol of the Strol 2E Pro (URTM059).
* [jcpsimmons/urevo-walkpad](https://github.com/jcpsimmons/urevo-walkpad) - SpaceWalk 5L (URTM054), including the mph rounding workaround.
* [Linemass/walking-pad](https://github.com/Linemass/walking-pad) - a Python + bleak Windows client for UREVO pads.
* [Web3Dave/ftms-js](https://github.com/Web3Dave/ftms-js), [VRTI supported devices](https://raphii.co/vrti/supported-devices/treadmills/), [qdomyos-zwift](https://github.com/cagnulein/qdomyos-zwift).
