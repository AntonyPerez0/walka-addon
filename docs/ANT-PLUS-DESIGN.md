# Design note: ANT+ USB sticks (not built)

A note for later. Nothing of this is in Walka.

## Why

Some heart-rate straps and watches send ANT+ only (older Garmin watches;
Garmin's list of Bluetooth broadcasters leaves them out), and Wahoo
recommends ANT+ for its TICKR on Windows PCs. A PC has no ANT+ radio: it
takes a USB stick (Garmin USB-m / USB2, Suunto movestick mini, and
unbranded ANT+ sticks, almost all built on Dynastream's nRF24AP2/AP1
chips, USB IDs `0fcf:1008` and `0fcf:1009`).

## How it would fit

* **A heart-rate source, not a treadmill reader.** It would feed
  `hr.HeartRateMonitor` the way `hr_network.py` does
  (`feed_network` would become a general `feed_external(bpm, source)`).
  ANT+ treadmills exist (the Fitness Equipment profile), but no walking
  pad Walka targets uses it.
* **Listen only.** ANT+ heart-rate monitors broadcast; a receiver opens a
  channel with the device type (120, heart rate), the ANT+ network key,
  period 8070 and frequency 57, and receives. Nothing is sent to the
  sensor - in line with Walka only listening. (Opening the channel is a
  conversation with the stick, not with the sensor.)
* **Pairing:** search with device number 0 (wildcard), then keep the
  device number found - as `heart_rate.address` keeps a Bluetooth sensor.
* **Data page 0-4/7:** byte 7 of every page is the computed heart rate;
  bytes 4-5 (beat time) and 6 (beat count) allow detecting repeats.

## What it would cost

* **A dependency.** Talking to the stick needs USB access: `pyusb` (and
  libusb on Windows, through a driver such as WinUSB installed with
  Zadig), or the `openant` library on top of it. That is a new entry in
  `pyproject.toml` and both lock files, and a driver step for users on
  Windows - the largest cost, and the reason this is a note.
* **The ANT+ network key** is licensed by Garmin/Dynastream: free for
  ANT+ adopters, but it has to be obtained under their terms, not copied.
* **Packaging:** libusb inside the Windows and Mac builds, and tests with
  a fake stick (replaying captured broadcast messages, as the Bluetooth
  readers do with `recording.py`).

## If it is built

1. Ask first (new dependency, driver step).
2. `walka/hr_ant.py`, off by default, a source like `hr_network.py`.
3. Settings: *Heart-rate sensor → ANT+ stick*, with what the stick and
   sensor are, and the Windows driver step explained.
4. Mark it untested on hardware until a user confirms it.
