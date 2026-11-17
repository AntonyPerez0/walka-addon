# Hardware notes: UREVO Strol 2E (URTM030)

What is known about controlling this pad over Bluetooth, where it comes
from, and what still needs confirming on a real unit. None of this was
measured by Walka's author; it is collected from the manual and from
open-source projects that control UREVO pads. Run `walka probe` on your pad
to check it (the probe only reads - it cannot move the belt).

## Summary

| | |
|---|---|
| Bluetooth name | `URTM030` (UREVO pads advertise their model code) |
| Protocol | Standard Bluetooth FTMS (Fitness Machine Service `0x1826`). No vendor handshake needed. |
| Pairing | None. **Do not pair it in Windows Bluetooth settings.** |
| Connections | One at a time. The UREVO phone app must be closed or the pad is invisible. |
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
repeats a motor command.

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
* **Steps** are not part of standard treadmill data. Sibling models either
  append a 24-bit step counter to Treadmill Data (flagged by a reserved
  bit, `0x2000`) - Walka reads that - or only send steps on a vendor
  channel (`0xFFF1`), which Walka does not use. If your pad sends no steps,
  Walka estimates them from speed and your height and says so.
* **Calories** shown by the pad ignore your weight; Walka computes its own.

## Still unknown for the URTM030 specifically

Found by reading the probe report:

* the exact speed range and step it advertises, folded and unfolded;
* whether targets above 4.0 mph are refused with the handle folded;
* where (or whether) it reports steps;
* how fast the belt accelerates;
* whether it stops by itself when nobody is walking.

## Sources

* UREVO URTM030 manual V3.0 (2023-11-20).
* [TechHQUSA/underfoot PROTOCOL.md](https://github.com/TechHQUSA/underfoot/blob/main/PROTOCOL.md) - measured protocol of the Strol 2E Pro (URTM059).
* [jcpsimmons/urevo-walkpad](https://github.com/jcpsimmons/urevo-walkpad) - SpaceWalk 5L (URTM054), including the mph rounding workaround.
* [Linemass/walking-pad](https://github.com/Linemass/walking-pad) - a Python + bleak Windows client for UREVO pads.
* [Web3Dave/ftms-js](https://github.com/Web3Dave/ftms-js), [VRTI supported devices](https://raphii.co/vrti/supported-devices/treadmills/), [qdomyos-zwift](https://github.com/cagnulein/qdomyos-zwift).
