# Which treadmills work

[← Back to the README](../README.md) · [Disclaimer](../DISCLAIMER.md) · [Safety](SAFETY.md)

## Which treadmills work

Walka reads **Bluetooth FTMS**, the standard that fitness apps use to show
a treadmill's speed and distance. If an app other than the maker's own can
*show the speed* of your treadmill, Walka very likely can too. The
treadmill does not have to be one that apps can control: Walka controls
nothing.

<!-- treadmill-table:start (made by tools/treadmill_table.py from treadmill/known.py - edit that) -->
| Treadmill | Bluetooth name | Read as | Evidence | Where from |
|---|---|---|---|---|
| **UREVO Strol 2E (URTM030)** | `URTM030...` | standard Bluetooth FTMS | **verified by a Walka user** (Windows 11, November 2026) | A Walka user: speed, distance and steps match the pad's own display, and walks are filed under the character in the game. Sends steps in its treadmill data. |
| UREVO pads (URTM054, URTM059, URTM041, ...) | `URTM...` | standard Bluetooth FTMS | reported by another project; untested on hardware with Walka | [qdomyos-zwift](https://github.com/cagnulein/qdomyos-zwift) reads every `URTM...` device as a standard FTMS treadmill; others measured sibling models. |
| WELLFIT TM029 Pro and other WELLFIT treadmills | `WELLFIT TM Linker...` | standard Bluetooth FTMS | reported by another project; untested on hardware with Walka | [qdomyos-zwift issue 2659](https://github.com/cagnulein/qdomyos-zwift/issues/2659): a WELLFIT owner and the project's author got it working over FTMS. |
| KingSmith WalkingPad A1, R1, C1 (older models) | `WalkingPad...` | WalkingPad protocol (asked once a second with one fixed status query) | reported by another project; untested on hardware with Walka | [ph4r05/ph4-walkingpad](https://github.com/ph4r05/ph4-walkingpad) (MIT), [qdomyos-zwift](https://github.com/cagnulein/qdomyos-zwift), [klassm/walkingpad_macos_client](https://github.com/klassm/walkingpad_macos_client): the WiLink protocol on service 0xFE00. |
| KingSmith WalkingPad A1 Pro | `KS-ST-A1P...` | WalkingPad protocol (asked once a second with one fixed status query) | reported by another project; untested on hardware with Walka | [ph4r05/ph4-walkingpad](https://github.com/ph4r05/ph4-walkingpad) (MIT), [qdomyos-zwift](https://github.com/cagnulein/qdomyos-zwift), [klassm/walkingpad_macos_client](https://github.com/klassm/walkingpad_macos_client): the WiLink protocol on service 0xFE00. |
| KingSmith WalkingPad C2 | `KS-BLC...` | WalkingPad protocol (asked once a second with one fixed status query) | reported by another project; untested on hardware with Walka | [ph4r05/ph4-walkingpad](https://github.com/ph4r05/ph4-walkingpad) (MIT), [qdomyos-zwift](https://github.com/cagnulein/qdomyos-zwift), [klassm/walkingpad_macos_client](https://github.com/klassm/walkingpad_macos_client): the WiLink protocol on service 0xFE00. |
| KingSmith WalkingPad R2 | `KS-BLR...` | WalkingPad protocol (asked once a second with one fixed status query) | reported by another project; untested on hardware with Walka | [ph4r05/ph4-walkingpad](https://github.com/ph4r05/ph4-walkingpad) (MIT), [qdomyos-zwift](https://github.com/cagnulein/qdomyos-zwift), [klassm/walkingpad_macos_client](https://github.com/klassm/walkingpad_macos_client): the WiLink protocol on service 0xFE00. |
| PitPat PitPat-T01 (also sold as SupeRun, DeerRun and others) | `PitPat-T...` | PitPat protocol | reported by another project; untested on hardware with Walka | [lapo-luchini/pitpat-exporter](https://github.com/lapo-luchini/pitpat-exporter) (ISC, with hardware captures), [KeiranY/PitPat-WebBT](https://github.com/KeiranY/PitPat-WebBT), [qdomyos-zwift](https://github.com/cagnulein/qdomyos-zwift) (`deerruntreadmill`): service 0xFBA0, or 0xFFFF on some boards. |
| FitShow treadmills and pads for the FitShow app | `FS-...` | FitShow protocol (asked with one fixed status query) | reported by another project; untested on hardware with Walka | the FitShow protocol document ([limdongkyu/fitshow-device-protocol](https://github.com/limdongkyu/fitshow-device-protocol)), [qdomyos-zwift](https://github.com/cagnulein/qdomyos-zwift) (`fitshowtreadmill`), [sstjohn/milltender](https://github.com/sstjohn/milltender), [Palaszontko/treadmillbar](https://github.com/Palaszontko/treadmillbar). Some also speak FTMS: then that is used. |
| **Any other FTMS treadmill** | (found by the service it advertises, after a 15-second scan - or pick it: *Treadmill → Scan*) | standard Bluetooth FTMS | depends on the treadmill | Check its numbers as in [Before your first walk](INSTALL.md#before-your-first-walk). |
<!-- treadmill-table:end -->

Only the UREVO Strol 2E (URTM030) has been tried with Walka on the real
machine; [Before your first walk](INSTALL.md#before-your-first-walk) is for
every other one.
Details and sources: [docs/HARDWARE.md](HARDWARE.md).

Things to know:

* **The speed is yours.** Walka sets none and limits none. How fast you
  walk is between you and the treadmill.
* **One connection at a time.** Most treadmills talk to one app. While
  Walka is connected, the maker's phone app cannot be - and the other way
  round.
* **Walka remembers your treadmill.** After the first connection it only
  reconnects to that one, so that a neighbour's walk is never counted as
  yours. *Forget this treadmill* on the dashboard lets you move to another.

**What will not work:** treadmills that only talk to their maker's app in
a private language Walka does not read (many NordicTrack / iFIT machines),
and bikes or rowers - Walka only reads treadmills. (The older KingSmith
WalkingPads have a private language too, but Walka reads that one: see
the table above. It is untried on a real pad.)

**Is yours not listed, or not behaving?** Press **Check my treadmill** on
the dashboard (*Live → Treadmill*) with the treadmill on, and work it with
its remote while it listens. (From a command prompt it is
`Walka.exe probe --record`; on a Mac [`Walka probe --record`](INSTALL.md#on-a-mac).) It only
listens - it sends the treadmill nothing at all - and saves a small text
report and a recording of what the treadmill sent.
[Fill in "My treadmill"](https://github.com/AntonyPerez0/walka-addon/issues/new?template=my-treadmill.yml)
with both files and the treadmill's make and model, and it can be added.

The saved report is made to be posted in public. It lists fitness machines
only - not the headphones, watches and televisions that are also in
Bluetooth range, yours or a neighbour's - with their addresses cut short,
and without the treadmill's serial number. (The window the probe runs in
shows everything: that is for your eyes. Post the file, not a screenshot
of the window.)

**Not in the list when you scan?** *Treadmill → Scan → Show all Bluetooth
devices* lists everything in range. *Check* next to a device connects to
it, looks at what it offers, and lets go: if it sends something Walka can
read, Walka connects to it as your treadmill; if not, it says what the
device does offer. Checking writes nothing to the device. (Once it is your
treadmill, an older WalkingPad or a FitShow pad gets the fixed status
question that pad needs - and only a device named as one: `WalkingPad` or
`KS-...`, `FS-...`.)

**A treadmill that is not FTMS, or a foot pod:** Walka also reads standard
*Running Speed and Cadence* sensors (untested on hardware so far) - see
[HARDWARE.md](HARDWARE.md#running-speed-and-cadence-sensors-foot-pods-treadmill-sensors).
