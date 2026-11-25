<h1 align="center">
  <img src="docs/images/logo.svg" width="44" height="44" align="top" alt=""> Walka
</h1>

<p align="center">
  <b>Walk on your walking pad while you play World of Warcraft.</b><br>
  Walka counts your steps, miles and calories, and draws your character's road on the map.
</p>

<p align="center">
  <a href="https://github.com/AntonyPerez0/walka-addon/releases/latest/download/Walka.exe"><img src="docs/images/download-button.svg" width="340" alt="Download Walka for Windows"></a>
</p>

<p align="center">
  <a href="https://github.com/AntonyPerez0/walka-addon/releases/latest/download/Walka-mac-apple-silicon.zip"><img src="docs/images/download-mac-button.svg" width="340" alt="Download Walka for Mac"></a>
</p>

<p align="center">
  <a href="https://github.com/AntonyPerez0/walka-addon/releases/latest/download/Walka-addon.zip"><img src="docs/images/download-addon-button.svg" width="230" alt="Download the game addon"></a>
</p>

<p align="center">
  <sub><b>Walka only listens to your treadmill.</b> It cannot start, stop or change the speed of the belt: you do that at the treadmill itself.<br>
  <b>Early version:</b> checked on one walking pad so far - a UREVO Strol 2E (URTM030) with Windows 11, in November 2026. Every other treadmill, and the Mac version, are still untried. Walking while you play is at your own risk - read the <a href="DISCLAIMER.md">disclaimer</a> before you download.<br>
  <b>Free.</b> Nothing to install: on Windows it is one file, <code>Walka.exe</code>, and you just open it - it has a window of its own, like any program.<br>
  <b>Mac:</b> the button is for an Apple chip (M1 or later) - for an older Mac with an Intel processor, <a href="https://github.com/AntonyPerez0/walka-addon/releases/latest/download/Walka-mac-intel.zip">download this one</a>.
  The Mac version is new and has not been tried with the game on a real Mac yet: see <a href="docs/INSTALL.md#on-a-mac">On a Mac</a>.<br>
  <b>Game addon:</b> the Walka addon for World of Warcraft. Walka can also put it in the game for you (step 3).</sub>
</p>

<p align="center">
  <a href="docs/INSTALL.md#is-the-download-safe"><img src="docs/images/virus-scan.svg" width="300" alt="Scanned for viruses by Microsoft Defender, before every release."></a><br>
  <a href="https://github.com/AntonyPerez0/walka-addon/releases/latest"><img src="https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fgithub.com%2FAntonyPerez0%2Fwalka-addon%2Freleases%2Flatest%2Fdownload%2Fvirus-scan.json&amp;query=%24.badge&amp;label=latest%20scan&amp;color=1a7f37" alt="The virus scan of the latest release: what Microsoft Defender found, and when"></a>
</p>

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/images/dashboard-dark.png">
    <img src="docs/images/dashboard-light.png" width="880" alt="The Walka dashboard: the belt's speed as the treadmill reports it, and this walk's time, distance, steps, calories and heart rate (with an optional Bluetooth heart-rate sensor)">
  </picture>
</p>

**Walka** is a free program for Windows and Mac, plus an addon for World of
Warcraft: Forever. It listens to your walking pad over Bluetooth, writes
down each walk - steps, distance, calories - and files it under the
character you are playing. **You need:** a Windows 10 or 11 PC or a Mac
with Bluetooth, and a walking pad that shows its numbers to apps over
Bluetooth ([which treadmills work](docs/TREADMILLS.md)).

## Get started

1. **Download Walka** with a button above, and open it.
   * **Windows** may say *"Windows protected your PC"*: click **More info**,
     then **Run anyway**. A browser may say the file "isn't commonly
     downloaded": choose **Keep** (in Edge: **...** → **Keep** → **Show
     more** → **Keep anyway**). Walka is not signed by a company yet; this
     does not mean anything was found - see
     [Is the download safe?](docs/INSTALL.md#is-the-download-safe)
   * **Mac:** open the zip, then the **Walka** app in it. The first time,
     allow it under *System Settings → Privacy & Security → Open Anyway*.
     Details: [On a Mac](docs/INSTALL.md#on-a-mac).
2. **Follow the setup guide** that opens the first time. It connects the
   treadmill (switch it on, close its phone app, don't pair it in
   Bluetooth settings), installs the game addon, and asks your weight and
   height.
3. **Start the belt with the treadmill's own remote** and play. Walka
   records the walk by itself, with the game or without it.

> [!NOTE]
> **Walka only listens to your treadmill.** It cannot start, stop or change
> the speed of the belt - nothing in it can. You set the speed at the
> treadmill, with its own remote and buttons.

> [!CAUTION]
> **Walking while you play is at your own risk.** Your eyes are on a game,
> not on your feet. Walka comes with no warranty, and it is not a safety
> device or a medical device. If you have a health condition, are pregnant
> or are in any doubt, ask a doctor first - and stop if you feel dizzy or
> unwell. Read the [disclaimer](DISCLAIMER.md) before your first walk.

## Safety

* **Walka cannot move the belt.** There is no command in it to send, and
  nothing the game or the dashboard does reaches the treadmill. The tests
  check that an emulated treadmill has been sent nothing.
* **Stopping the belt is yours.** Walka is not a safety device: the
  treadmill's own stop button, remote and safety key are what stops it.
  Keep the remote within reach, start slow, stop the belt when you step
  off, and keep children and pets away from it.
* **Versions up to 0.6.1 could move the belt.** They have been withdrawn:
  replace one with the current download and delete the old `Walka.exe`.
* Found a way to make Walka send a treadmill anything at all? Report it in
  private: [SECURITY.md](SECURITY.md).

The whole of it: [docs/SAFETY.md](docs/SAFETY.md) and
[DISCLAIMER.md](DISCLAIMER.md).

## More

* [Installing Walka](docs/INSTALL.md) - step by step, on a Mac, Walka's window, [is the download safe?](docs/INSTALL.md#is-the-download-safe), [before your first walk](docs/INSTALL.md#before-your-first-walk)
* [Using Walka](docs/USING.md) - the dashboard, the floating window, your journey and a picture to share; [heart rate](docs/HEART-RATE.md)
* [Which treadmills work](docs/TREADMILLS.md) and [hardware notes](docs/HARDWARE.md)
* [How Walka works](docs/HOW-IT-WORKS.md) - what it looks at, why it only listens, [is this allowed?](docs/HOW-IT-WORKS.md#is-this-allowed)
* [Your Walka account](docs/WALKA-ACCOUNT.md) on walka.dev - optional
* [Troubleshooting](docs/TROUBLESHOOTING.md) and [the in-game checklist](docs/IN-GAME-CHECKLIST.md)
* [Developing Walka](docs/DEVELOPMENT.md) and [translating the dashboard](docs/TRANSLATING.md)

Walka has a website too: <https://walka.dev/>
