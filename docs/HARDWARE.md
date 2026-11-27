# Hardware notes: UREVO Strol 2E (URTM030), and other treadmills

Walka listens to a treadmill over standard Bluetooth FTMS. It only listens:
it never sends a treadmill a command (see [SAFETY.md](SAFETY.md)). Older
WalkingPads and FitShow pads, which report only when asked, get one fixed
status question once a second, and nothing else. It was
written around the UREVO Strol 2E, which most of this page is about; what
is known about other makes - the WELLFIT TM029 Pro first of all - is under
[Other treadmills](#other-treadmills).

What is known about reading the Strol 2E over Bluetooth, where it comes
from, and what still needs confirming on a real unit. Most of it is
collected from the manual and from open-source projects that work with
UREVO pads; what a Walka user saw on a real Strol 2E is under
[Verified on hardware](#verified-on-hardware). Run `walka probe` on your
pad to check the rest. Like the companion itself, the probe never sends a
command; the only thing it writes is the switch that turns notifications
on.

## Verified on hardware

| Model | Computer | When | What was checked |
|---|---|---|---|
| UREVO Strol 2E (`URTM030`) | Windows 11 | November 2026 | Found and read by Walka. Speed, distance and steps on the dashboard match the pad's own display. The *Live* screen captions under Steps and Distance say *treadmill counter*: the pad sends both itself. With the game running, walks were filed under the character (with version 0.10, which read a strip the addon drew on the screen; filing from the game's saved file, from 0.11, has not been checked on hardware yet). |

That is one pad, one owner and one computer. Not checked yet on it: the
items under [Still unknown](#still-unknown-for-the-urtm030-specifically),
and a heart-rate source (a Garmin watch was tried; how that went is not
written down yet). No other treadmill, and no Mac, has been tried with
Walka.

## Summary

| | |
|---|---|
| Bluetooth name | `URTM030` (UREVO pads advertise their model code) |
| Protocol | Standard Bluetooth FTMS (Fitness Machine Service `0x1826`). No vendor handshake needed - confirmed on a real Strol 2E (above). |
| Steps | Sent by the pad itself, in Treadmill Data - confirmed on a real Strol 2E. |
| Pairing | None. **Do not pair it in Windows (or macOS) Bluetooth settings.** |
| Connections | One at a time. The UREVO phone app must be closed or the pad is invisible - and while Walka is connected, the phone app cannot be. |
| Which device Walka picks | The address you chose in the dashboard, if any. Otherwise a device whose Bluetooth name is a known one (`URTM...`, `WELLFIT TM Linker`); some other Fitness Machine is only used if no such name is heard during the whole 15-second scan, and then the one with the strongest signal. A machine that says in its advertisement that it is not a treadmill (a bike trainer, a rower) is never used. |
| Speed range (US) | 0.6-4.0 mph with the handle folded (desk mode), 0.6-6.2 mph with it raised |
| Speed step | 0.2 mph on the console buttons |
| Start | 3-2-1 countdown, then the belt starts at its minimum speed |
| Max user weight | 265 lb / 120 kg |
| Belt | 15 x 40.2 in (38 x 102 cm) |

## Evidence that plain FTMS works

* [ftms-js](https://github.com/Web3Dave/ftms-js) lists "Urevo Strol 2E
  (`URTM030`)" as verified on hardware for connect and live data.
* [VRTI](https://raphii.co/vrti/supported-devices/treadmills/) lists
  "UREVO Strol 2E (URTM030) - Generic FTMS".
* The UREVO manual says the pad works with Zwift and Kinomap, which speak
  FTMS.
* [qdomyos-zwift](https://github.com/cagnulein/qdomyos-zwift) routes every
  device named `URTM...` to its generic FTMS treadmill driver.

How strong was that, before a Walka user tried it? Moderate. The ftms-js
project is one person's report from a repository that was a few days old
when this was written, and VRTI's list is community-reported. Everything else comes from sibling models
(URTM054, URTM059, URTM041), which share a firmware family but are not the
same pad. That is why the probe is there.

## What Walka reads

On a connection the companion reads one characteristic once and listens
to three. It writes to none of them (switching notifications on is a write
to the characteristic's own switch, as in every app that shows a
treadmill's numbers).

| Characteristic | How | What for |
|---|---|---|
| Supported Speed Range (`0x2AD4`) | read once | The range and step shown on the dashboard, and whether the firmware thinks in mph. |
| Treadmill Data (`0x2ACD`) | notifications | Speed, distance, time; steps where the pad appends them. This is the walk. |
| Fitness Machine Status (`0x2ADA`) | notifications, if there | "Started", "stopped", "paused", "set to this speed" - what you did at the pad. |
| Training Status (`0x2AD3`) | notifications, if there | The countdown before the belt moves. |
| Fitness Machine Control Point (`0x2AD9`) | **never touched** | The channel through which an app could start, stop or steer the belt. Walka neither writes to it nor listens to it. |

A treadmill needs Treadmill Data and nothing else. One that has no control
point at all - a treadmill that apps can watch and not drive - is all
Walka needs.

## Quirks Walka handles

* **Speeds are always km/h on the wire**, even on a pad that displays mph.
  US firmware reports its speed truncated to 0.01 km/h (2.0 mph arrives as
  3.21). A sibling pad reports its range as 0.96-6.43 km/h in steps of
  0.16 km/h, which is how Walka recognises mph firmware; you can force it
  in the settings. It only changes how the pad's range is shown.
* **The pad's counters reset** after a stop, or after a pause longer than
  10 minutes; its timer display wraps at 99:59 and its calorie counter stops
  at 6553. Walka therefore keeps its own totals and uses the pad's counters
  only as increments.
* **A belt running down** after a stop still reports a speed for a moment.
  That is shown as stopped (or paused), not as running again.
* **A pad that announces nothing.** Not every treadmill has the status
  characteristic. Then the data alone says whether the belt is turning.
* **A speed range with a minimum of zero**, or one that makes no sense.
  Walka shows its default minimum and keeps the advertised maximum.
* **Steps** are not part of standard treadmill data. Sibling models either
  append a 24-bit step counter to Treadmill Data (flagged by a reserved
  bit, `0x2000`) - Walka reads that - or only send steps on a vendor
  channel (`0xFFF1`), which Walka does not use. If your pad sends no steps,
  Walka estimates them from speed and your height and says so.
* **Calories** shown by the pad ignore your weight; Walka computes its own.
* **Data that stops arriving** while the link stays up: after five seconds
  Walka no longer counts the belt as moving. What is not known is not
  written down.

## Still unknown for the URTM030 specifically

Settled by a Walka user on a real pad (see
[Verified on hardware](#verified-on-hardware)): the URTM030 reports its
steps and its distance in Treadmill Data, and Walka reads both - steps
being the 24-bit counter flagged by bit `0x2000`, the only place Walka
reads steps from.

Still to be found by reading a probe report (none has been posted yet):

* the exact speed range and step it advertises, folded and unfolded;
* what its vendor channels (`0xFFF1` and the like) carry; `walka probe
  --all` switches those on. Walka does not need them for steps on this
  pad;
* how it reports what is done at the pad: whether a start, a pause and a
  stop with the remote each come as a status event, and whether a change of
  speed does;
* whether it switches itself off, and drops the connection, after a long
  pause (siblings do), and whether it keeps advertising while it is in
  standby. Walka listens for the pad it is tied to without connecting, and
  connects again when the pad is heard after at least 20 seconds of
  silence (switched off and on); also when you next start a session in the
  game, or when you press *Connect*. A pad that advertises all through
  standby is not connected to by that rule, so it is not woken.

## Other treadmills

The reader is not UREVO's: it is the Fitness Machine Service as the
Bluetooth specification describes it. Any treadmill that sends Treadmill
Data can be read - also one that cannot be driven by apps at all. What the
make changes is how the treadmill is found.

### How a treadmill is found

1. **By address**, once Walka has connected to a treadmill (it then only
   reconnects to that one, so that a neighbour's walk is never counted as
   yours) or you picked one from *Treadmill → Scan*.
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

* Treadmill Data (`0x2ACD`), with a speed in it while the belt moves.
  Without that characteristic the device is refused.
* Supported Speed Range (`0x2AD4`), if there is one, is read for the
  minimum, maximum and step that the dashboard shows. Without it Walka
  shows 1.0-6.0 km/h in steps of 0.1 (0.6-4.0 mph in steps of 0.1 if the
  firmware grid is set to mph).

What Walka does **not** do on any treadmill: start it, stop it, change its
speed or touch the incline. How fast a treadmill goes is its owner's
business; Walka has no speed limit of its own, because it sets no speed.

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
project's author got it working over FTMS, live data included.

That is one treadmill, one owner, and another program. Not known for a
WELLFIT, and found by a `walka probe` report or a first walk:

* whether it advertises the Fitness Machine Service and a machine type
  (if not, it is still found by its name);
* the speed range and step it reports. US units very likely report an mph
  grid (0.96 km/h minimum, 0.16 km/h steps), which Walka then recognises by
  itself;
* whether steps are in its data, and what it announces when it is started,
  paused and stopped at its console.

### Other makes

Nothing is known first-hand. A treadmill that an independent app can show
the speed of over FTMS should work as it is: let the scan run its 15
seconds, or pick it from *Treadmill → Scan*. qdomyos-zwift's source is the
best public list of which Bluetooth names are FTMS treadmills, but a name
on that list is not proof of plain FTMS - several of them need changes of
their own there.

To add a treadmill to the names Walka knows:

1. Run `walka probe --record` on it and keep the report and the recording
   next to it (`walka-probe-....txt` and `.json`): the report shows the
   advertised name, the services, the speed range and the feature bits;
   the recording holds every notification heard, with its time, so the
   walk can be played back - in Walka's tests, or on the dashboard with
   `walka run --replay walka-probe-....json`. Both are made to be posted:
   fitness machines only, addresses cut short, no serial number. It
   listens to standard treadmills, foot pods (RSC) and the WalkingPad and
   PitPat protocols. It sends no query, so a pad that reports only when
   asked (an older WalkingPad, FitShow) says little in it; for FitShow's
   generic 0xFFF0 channel use `walka probe --record --all`.
2. Walk on it with the companion running, and check the dashboard against
   the treadmill's own display: speed, distance, steps.
3. Add an entry to `KNOWN_TREADMILLS` in `treadmill/known.py` - the start
   of the Bluetooth name, as exactly as the make allows, the model, how
   much is known (`evidence`: *verified by a Walka user* only for a walk
   checked against the treadmill's display, with `checked` saying on what
   and when; otherwise *reported by another project*) and where that comes
   from - then run `python tools/treadmill_table.py`, which remakes the
   table in [TREADMILLS.md](TREADMILLS.md). And a paragraph here saying
   what was seen and on which model.

## Running Speed and Cadence sensors (foot pods, treadmill sensors)

Walka can also read a standard Bluetooth *Running Speed and Cadence*
sensor (service `0x1814`, RSC Measurement `0x2A53`): a foot pod on a shoe,
or a treadmill's own speed sensor. This reader
([`treadmill/rsc.py`](../companion/src/walka/treadmill/rsc.py)) is
**untested on hardware**: it is built from the Bluetooth specification and
tested against recordings put together from it.

* Pick the sensor under *Treadmill → Scan → Show all Bluetooth devices →
  Check*. If it sends RSC, Walka switches to reading it that way (and
  remembers it).
* **Speed** is the sensor's. RSC has no "stopped" or "paused" events: the
  belt counts as moving while the speed is above a crawl.
* **Steps** are counted from the cadence, so they are real steps, not an
  estimate. Some sensors report strides (one per two steps): a cadence
  under 75 a minute at walking speed is taken for strides and doubled. If
  your step count comes out half or double, please say so in an issue,
  with a `walka probe --record` recording.
* **Distance** is the sensor's own total if it sends one; otherwise Walka
  works it out from the speed.
* A foot pod measures your feet, not the belt: on a treadmill the two
  agree closely, but not exactly.

## Makers' own protocols (untested on hardware)

These readers are written from public open-source projects and tested
against recordings put together from them. None has been tried on a real
pad with Walka yet: if you have one, please post a report (*Check my
treadmill*, or `walka probe --record`) in a ["My treadmill"
issue](https://github.com/AntonyPerez0/walka-addon/issues/new?template=my-treadmill.yml).
Each may send one fixed status query, and nothing else - see
[SAFETY.md](SAFETY.md#what-walka-does-to-a-treadmill-all-of-it). Which
protocol a pad speaks is decided on connecting, by the services it has; a
pad that also speaks FTMS is read through FTMS.

### Older KingSmith / Xiaomi WalkingPads ("WiLink")

| | |
|---|---|
| Models | WalkingPad A1, A1 Pro, R1, C1, C2, R2 - not the newer KS-HD models (Z1 and others), which speak FTMS |
| Bluetooth names | `WalkingPad`, `KS-ST-A1P`, `KS-BLC`, `KS-BLR` (others are found by *Show all Bluetooth devices*) |
| Service | `0xFE00`: notifications on `0xFE01`, queries to `0xFE02` (write without response) |
| Query | `F7 A2 00 00 A2 FD`, once a second (the maker's app asks about every 0.75 s) |
| Answer | 20 bytes: `F8 A2`, belt state (0 stopped, 1 running, 5 standby, 7-9 counting down), speed in 0.1 km/h, mode, time (3 bytes, big-endian, s), distance (3 bytes, 10 m), steps (3 bytes), 4 bytes, checksum (sum of bytes 1-17), `FD` |
| Ignored | anything else: the "last record" answer (`F8 A7`), a packet with a wrong checksum |

Sources: [ph4r05/ph4-walkingpad](https://github.com/ph4r05/ph4-walkingpad)
(MIT; `ph4_walkingpad/pad.py` - the query, the checksum and the layout, and
82 captured packets that the layout and checksum were checked against),
[cagnulein/qdomyos-zwift](https://github.com/cagnulein/qdomyos-zwift)
(`src/devices/kingsmithr1protreadmill/`), [klassm/walkingpad_macos_client](https://github.com/klassm/walkingpad_macos_client)
(`0xFE02` takes only writes without response), [darnfish/walkingpad](https://github.com/darnfish/walkingpad),
[madmatah/hass-walkingpad](https://github.com/madmatah/hass-walkingpad) and
[mcdax/walkingpad-controller](https://github.com/mcdax/walkingpad-controller)
(belt states, names). Where they disagree, Walka follows the majority and
the captured data: speed in 0.1 km/h (one project scales it otherwise),
steps as 3 bytes (one reads 2). Other apps send set-up packets after
connecting (a mode, preferences); Walka sends none of them.

### PitPat / SupeRun / DeerRun pads ("PitPat" app)

| | |
|---|---|
| Bluetooth names | `PitPat-T01` (also on pads sold as SupeRun, DeerRun and others: they appear to share one maker's board) |
| Service | `0xFBA0`, notifications on `0xFBA2`; on some boards (an ESP32) `0xFFFF`, notifications on `0xFF02`, each frame wrapped in `4D 04 <sequence> <length>` |
| Query | **none**: these pads send their status by themselves, and Walka writes nothing to them. (Other apps send a keep-alive after each status; several working ones never do.) |
| Status | start byte `0x66`/`0x67`/`0x68`, its length, ..., XOR of bytes 1 to n-3, `0x43`. Big-endian: speed (bytes 3-4, km/h x 1000, also when the pad shows mph), distance (7-10, m), calories (18-19), time (20-23; ms from firmware 20 on), flags (26: `0x18` bits stopped / running / paused / starting) |
| Steps | 4 bytes at 14 by the maker's app - always 0 on the captured pads - else 2 bytes at 43 of the 47- and 50-byte frames, which matched a hand count (86 of 86) |

Sources: [lapo-luchini/pitpat-exporter](https://github.com/lapo-luchini/pitpat-exporter)
(ISC; `SPEC.md` and some 1,800 captured frames - the layout above was
checked against them), [KeiranY/PitPat-WebBT](https://github.com/KeiranY/PitPat-WebBT),
[azmke/pitpat-treadmill-control](https://github.com/azmke/pitpat-treadmill-control),
[moritzWa/pitpat-menubar](https://github.com/moritzWa/pitpat-menubar),
[Rick-Pro/stride-android](https://github.com/Rick-Pro/stride-android),
[omrilotan/walk](https://github.com/omrilotan/walk),
[sirfergy/HomeAssistantWalkingPad](https://github.com/sirfergy/HomeAssistantWalkingPad)
and [qdomyos-zwift](https://github.com/cagnulein/qdomyos-zwift)
(`src/devices/deeruntreadmill/`). Not known: what a pad does if it is never
sent a keep-alive (the captured ones kept talking), and a third transport
on `0xFFF0` that one project mentions - Walka does not read that one,
because FitShow machines use the same service.

### FitShow treadmills and pads ("FitShow" app)

| | |
|---|---|
| Bluetooth names | `FS-...` (for example `FS-3D6CD7`). Some FitShow machines speak FTMS as well: those are read through FTMS, with no query |
| Service | `0xFFF0`: notifications on `0xFFF1`, commands to `0xFFF2` (two other variants, on `0xFFE0` and `0xAE00`, are not read) |
| Query | `02 51 51 03` (SYS_STATUS), once a second - and only to a device named `FS-...`, because `0xFFF1`/`0xFFF2` are on many other devices too |
| Answer | `02 51 <state> ...`, the XOR of command and data, `03`; little-endian. State 0 idle, 1 ended, 2 counting down, 3 running, 4 slowing down, 5 error, 6 safety key out, 9 ready, 10 paused. With 1/3/4/10: speed (byte 3, 0.1 km/h - 0.1 mph on a machine set to miles), incline (4), time (5-6, s), steps (11-12) |
| Not read | distance (7-8) and calories (9-10): projects disagree about their units, so Walka works both out itself. Machine information and workout frames are ignored. |
| Miles | the status does not say. A machine that shows mph: *Settings → Treadmill → Advanced → Firmware speed grid → mph*. (Finding out by itself would take a second query, which Walka does not send.) |

Sources: the FitShow protocol document as published in
[limdongkyu/fitshow-device-protocol](https://github.com/limdongkyu/fitshow-device-protocol)
(framing, status codes), [qdomyos-zwift](https://github.com/cagnulein/qdomyos-zwift)
(`src/devices/fitshowtreadmill/`), and - on real machines -
[sstjohn/milltender](https://github.com/sstjohn/milltender) (a LifeSpan TX6,
`FS-3D6CD7`, which works with nothing but the status query) and
[Palaszontko/treadmillbar](https://github.com/Palaszontko/treadmillbar)
(MIT; an HS-750WP pad). Merach machines need an extra "unlock" packet in
other apps; Walka does not send it, so they may not work.

## Windows notes

* The Bluetooth library (bleak) documents Windows 11 as its supported
  version; Windows 10 generally works.
* If the pad has been paired in Windows *Bluetooth & devices*, remove it
  there: Windows then holds the connection and the pad stops advertising.
* Weak built-in Bluetooth (no external antenna) is a common cause of
  dropouts; a USB Bluetooth 5 adapter is the usual fix.
* The companion has a window of its own, drawn by Microsoft Edge WebView2
  (part of Windows 11; on Windows 10 it comes with Edge's updates and with
  Office). GitHub's Windows machines open that window in every build,
  load the dashboard in it and close it again. Walka has been used on one
  real Windows 11 PC (November 2026); on Windows 10 it has not been tried.
  A PC without WebView2 gets the dashboard in the
  browser, and a message box to close the companion with.
* The window makes its thread a single-threaded COM apartment, which the
  Windows Runtime's Bluetooth cannot be used from. So with a window, the
  Bluetooth - and everything else the companion does - runs on a second
  thread (`cli._run_in_window`).
* Started from a command prompt with a command (`Walka.exe probe`), the
  companion writes to that prompt's window, and what follows applies to
  it: clicking inside a classic Windows console starts a text selection,
  which freezes any program that then writes to it. The companion
  switches that ("QuickEdit") off while it runs and writes its log lines
  from a separate thread, so the console cannot hold up the recording. To
  copy text from it, use its menu (*Edit -> Mark*).
* Logging off or shutting Windows down: the companion keeps a hidden
  window to be told, and saves the walk as it does when its own window is
  closed. This has not been tried on a real PC yet. (A walk in progress is
  also saved every half minute, and picked up again at the next start.)

## Mac notes

The Mac version is new. GitHub's Macs build it and run the tests, and the
finished application is started there: it opens its window, loads the
dashboard in it, records a walk on the simulated pad and closes down in an
orderly way when it is told to end. It has not been tried with the game or
a walking pad yet - and nobody has clicked in its window.

* Bluetooth goes through macOS' CoreBluetooth (by way of bleak). macOS
  does not show a device's Bluetooth address: the "address" the companion
  stores for the pad is an identifier macOS makes up for it, different on
  every Mac. A settings file copied from a Windows PC will not find the
  pad by its address: clear *Treadmill -> address* (or press *Scan*).
* macOS asks before a program may use Bluetooth. Walka is an
  application, so it is asked for, and granted to, Walka (*System
  Settings -> Privacy & Security*) - or to Terminal, when a command is run
  from there. The application is signed "ad hoc", with no developer's
  name: macOS then knows it by its exact contents, and may ask again for a
  new version. Without the Bluetooth permission the scan fails. (Versions
  up to 0.10 also needed Screen Recording, to read a strip the addon drew
  on the screen. From 0.11 Walka does not look at the screen.)
* The game's saved files are looked for under
  `/Applications/World of Warcraft/_forever_/WTF/Account`, or wherever the
  addon is installed. `Walka game`, typed in Terminal as under
  [On a Mac](INSTALL.md#on-a-mac), says whether they were found.
* Closing the window and *Quit* reach the companion through the window,
  which is not allowed to close until the walk is saved; logging out and
  shutting down ask the application to quit the same way. Being told to
  end from outside (`kill`, or the Terminal window closing when a command
  was started there) arrives as a signal (SIGTERM, SIGHUP), and is
  answered the same way - that one GitHub's Macs try in every build; the
  window's own close button and *Quit* have not been clicked on a real
  Mac yet. A forced quit cannot save anything; the walk is then
  picked up from its last half-minute checkpoint at the next start.
* macOS does not recognise the program (it is not signed with an Apple
  developer account or notarised), so the first start has to be allowed
  under *System Settings -> Privacy & Security -> Open Anyway*.

## Sources

* UREVO URTM030 manual V3.0 (2023-11-20).
* [TechHQUSA/underfoot PROTOCOL.md](https://github.com/TechHQUSA/underfoot/blob/main/PROTOCOL.md) - measured protocol of the Strol 2E Pro (URTM059).
* [jcpsimmons/urevo-walkpad](https://github.com/jcpsimmons/urevo-walkpad) - SpaceWalk 5L (URTM054).
* [Linemass/walking-pad](https://github.com/Linemass/walking-pad) - a Python + bleak Windows client for UREVO pads.
* [Web3Dave/ftms-js](https://github.com/Web3Dave/ftms-js), [VRTI supported devices](https://raphii.co/vrti/supported-devices/treadmills/), [qdomyos-zwift](https://github.com/cagnulein/qdomyos-zwift).
* WELLFIT: [the maker's app page](https://wellfittreadmill.com/pages/app) (Bluetooth names, Kinomap and Zwift), qdomyos-zwift [issue 2659](https://github.com/cagnulein/qdomyos-zwift/issues/2659).
* Bluetooth SIG, Fitness Machine Service 1.0 - the advertisement's service data (machine type), features, Treadmill Data and status.
