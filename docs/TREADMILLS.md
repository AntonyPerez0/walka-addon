# Which treadmills work

[← Back to the README](../README.md) · [Disclaimer](../DISCLAIMER.md) · [Safety](SAFETY.md)

## Which treadmills work

Walka reads **Bluetooth FTMS**, the standard that fitness apps use to show
a treadmill's speed and distance. If an app other than the maker's own can
*show the speed* of your treadmill, Walka very likely can too. The
treadmill does not have to be one that apps can control: Walka controls
nothing.

| Treadmill | How Walka finds it | What is known |
|---|---|---|
| **UREVO Strol 2E (URTM030)** | By name (`URTM...`), at once | **Verified by a Walka user** (Windows 11, November 2026): speed, distance and steps match the pad's own display, and walks are filed under the character in the game. |
| Other UREVO pads | By name (`URTM...`), at once | Other open-source projects read them the same way. Not tried with Walka yet. |
| **WELLFIT TM029 Pro** and other WELLFIT treadmills | By name (`WELLFIT TM Linker`), at once | Another open-source project works with a WELLFIT over FTMS. |
| **Any other FTMS treadmill** | By what it advertises, after a 15-second scan - or pick it yourself: dashboard → *Treadmill → Scan* | Depends on the treadmill. Check its numbers as in [Before your first walk](INSTALL.md#before-your-first-walk). |

Only the UREVO Strol 2E (URTM030) has been tried with Walka on the real
machine; the warning at the top of this page is for every other one.
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
a private language (many NordicTrack / iFIT machines, the older KingSmith
WalkingPads), and bikes or rowers - Walka only reads treadmills.

**Is yours not listed, or not behaving?** Press **Check my treadmill** on
the dashboard (*Live → Treadmill*) with the treadmill on, and work it with
its remote while it listens. (From a command prompt it is
`Walka.exe probe --record`; on a Mac [`Walka probe --record`](INSTALL.md#on-a-mac).) Like Walka itself it only
listens - it sends the treadmill no commands - and saves a small text
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
device does offer. Checking writes nothing to the device.
