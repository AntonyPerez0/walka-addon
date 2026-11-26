# Installing Walka

[← Back to the README](../README.md) · [Disclaimer](../DISCLAIMER.md) · [Safety](SAFETY.md)

## Get started


> [!NOTE]
> **Walka only listens to your treadmill.** It reads the speed, distance
> and steps the treadmill reports, and writes your walks down. It cannot
> start or stop the belt or change its speed - nothing in it can. You set
> the speed at the treadmill, with its own remote and buttons.
> (Versions up to 0.6 did drive the belt. They have been withdrawn.)

> [!WARNING]
> **Early version - checked on one walking pad so far:** a UREVO Strol 2E
> (URTM030) with Windows 11, in November 2026. Every other treadmill has
> not been tried yet. Read [Before your first walk](#before-your-first-walk).

> [!CAUTION]
> **Walking while you play is at your own risk.** Your eyes are on a game,
> not on your feet. Walka comes with no warranty, and it is not a safety
> device or a medical device. If you have a health condition, are pregnant
> or are in any doubt, ask a doctor first - and stop if you feel dizzy or
> unwell. The whole of it: [DISCLAIMER.md](../DISCLAIMER.md).

*These steps are for Windows. On a Mac, steps 1 and 2 are different - they
are under [On a Mac](#on-a-mac) - and the rest is the same.*

1. **Download `Walka.exe`** with the button above. It goes to your
   *Downloads* folder. If the browser says it "isn't commonly downloaded",
   choose **Keep** (in Edge: **...** → **Keep** → **Show more** → **Keep
   anyway**).
2. **Double-click it.** Walka opens in a window of its own, with its
   *dashboard* in it. **Leave it open while you walk** (minimised is
   fine) - closing the window closes Walka: the walk is saved and the
   recording ends (the belt is not touched).

   The first time, Windows may say *"Windows protected your PC"*. Click
   **More info**, then **Run anyway**. (Walka is not signed by a company,
   so Windows does not recognise it yet. It does not mean that anything
   was found: see [Is the download safe?](#is-the-download-safe))
3. **Follow the setup guide.** The first time Walka opens, it takes you
   through setting up one step at a time (units, the treadmill, your weight
   and height, the game addon, and - optional - a heart-rate sensor).
   Afterwards "Getting started" on the dashboard ticks off what is left:
   * **Connect the treadmill:** switch it on and close its own phone app
     (UREVO, WELLFIT Fitness, ...). Don't pair it in Windows Bluetooth
     settings - Walka finds it by itself.
   * **Install the game addon:** click *Install* on the dashboard, then
     restart the game and tick *Walka* in the AddOns list. (By hand
     instead: unzip the [addon download](https://github.com/AntonyPerez0/walka-addon/releases/latest/download/Walka-addon.zip)
     into `World of Warcraft\_forever_\Interface\AddOns`.)
   * **Enter your weight and height:** for the calorie count.
4. **Walk.** Log in to the game: the addon starts a walking session for
   your character by itself - there is nothing to type or press. Step onto
   the pad and start the belt **with the treadmill's own remote**, at the
   speed you like. When you log out (or type `/reload`), the game saves
   the session, and Walka files the walk under your character.

**The belt is yours.** Start it, stop it and change its speed at the
treadmill - its remote, its buttons, its safety key. Walka has no button
for any of that.

## What you need

* Windows 11 (Windows 10 generally works) with Bluetooth 4.0 / "LE" or
  newer - or a Mac, with an Apple chip or an Intel processor and a recent
  macOS (see [On a Mac](#on-a-mac)).
* The walking pad or treadmill, switched on, **not** paired in the
  computer's Bluetooth settings, and with its own phone app **closed**
  (most accept one connection at a time).
* WoW: Forever, with the Walka addon. Any display mode will do,
  fullscreen included. (Walka's floating window can only be shown on top
  of the game in *Windowed* or *Windowed (Fullscreen)* mode, but you do not
  need it.)

## Install, step by step

1. **Get the companion.** Download
   [`Walka.exe`](https://github.com/AntonyPerez0/walka-addon/releases/latest/download/Walka.exe)
   (every version is on the
   [Releases page](https://github.com/AntonyPerez0/walka-addon/releases)),
   or run it from source (Python 3.14 or newer):

   ```powershell
   cd companion
   py -m pip install -e .
   py -m walka
   ```

   On a Mac: [the zip for your Mac](#on-a-mac), or from source with
   `python3` in place of `py`.

2. **Run it.** Walka's window opens, with the dashboard in it. (The
   dashboard is a page served on your own computer, at
   <http://127.0.0.1:8787>: `--browser` shows it in your web browser
   instead, and `--no-window` shows it nowhere. See
   [Walka's window](#walkas-window).)
3. **Install the addon.** With the game closed, on the dashboard:
   *Settings → Game addon → Install automatically*. Or by hand: download
   [`Walka-addon.zip`](https://github.com/AntonyPerez0/walka-addon/releases/latest/download/Walka-addon.zip)
   and unzip it into `World of Warcraft\_forever_\Interface\AddOns\` (on a
   Mac: `/Applications/World of Warcraft/_forever_/Interface/AddOns/`), so
   that there is a `Walka` folder in it. Start the game and tick *Walka* in
   the AddOns list.
4. **Connect the treadmill.** Switch it on: Walka looks for it by itself.
   To pick one yourself: dashboard → *Treadmill → Scan*, then *Connect*
   next to yours (a UREVO Strol 2E shows as `URTM030`, a WELLFIT as
   `WELLFIT TM Linker`).
5. **Tell it about you.** *Settings → Body profile*: weight and height
   (used for calories and steps).

## On a Mac

> [!WARNING]
> **The Mac version is new.** GitHub's Macs build it, run its tests and
> check that the finished program starts, records a walk on a simulated
> pad and saves it when it is closed. Nobody has yet tried it with the game
> and a walking pad on a real Mac. Do everything under
> [Before your first walk](#before-your-first-walk) - and if you do,
> [say how it went](https://github.com/AntonyPerez0/walka-addon/issues).

1. **Download the zip for your Mac.** *Apple menu → About This Mac* says
   which: *Chip: Apple M...* →
   [`Walka-mac-apple-silicon.zip`](https://github.com/AntonyPerez0/walka-addon/releases/latest/download/Walka-mac-apple-silicon.zip);
   *Processor: ... Intel ...* →
   [`Walka-mac-intel.zip`](https://github.com/AntonyPerez0/walka-addon/releases/latest/download/Walka-mac-intel.zip).
2. **Open the zip** (double-click it in *Downloads*). Inside is the
   application, `Walka`. It runs from where it is; you can also drag it
   into *Applications*.
3. **Double-click `Walka`.** The first time, macOS refuses: *"Apple could
   not verify Walka is free of malware"*. Click **Done**, open **System
   Settings → Privacy & Security**, scroll down to *Security* and click
   **Open Anyway** next to Walka. (On macOS 14 and older: hold Control,
   click `Walka`, choose **Open**, then **Open**.) Walka is not signed with
   an Apple developer account, so macOS does not recognise it. It does not
   mean that anything was found: see
   [Is the download safe?](#is-the-download-safe)
4. **Walka's window opens**, with the dashboard in it. Leave it open while
   you walk (minimised is fine). Closing the window - or *Walka → Quit* -
   closes Walka: the walk is saved and the recording ends (the belt is not
   touched). From there it is step 3 of [Get started](#get-started).

macOS asks before a program may use Bluetooth, and it asks about **Walka**
by name:

* **Bluetooth** - to listen to the walking pad. Click **Allow**.

After you replace Walka with a newer version, macOS may ask again: Walka is
not signed with an Apple developer account, and to macOS a program like
that which has changed is a new program. It can be changed later under
**System Settings → Privacy & Security → Bluetooth**.

Walka does not need **Screen Recording**: it does not look at the screen,
only at the file the game saves for its addon (see
[What Walka looks at](HOW-IT-WORKS.md#what-walka-looks-at)). Versions up
to 0.10 did ask for it. If you allowed it then, you can switch Walka off
under **System Settings → Privacy & Security → Screen & System Audio
Recording**.

What is different from Windows:

* **The game** is looked for in `/Applications/World of Warcraft`. If
  yours is somewhere else, choose the addon folder yourself under
  *Settings → Game addon*.
* **Your settings and walks** are in
  `~/Library/Application Support/Walka`. A sign-in to
  [your Walka account](WALKA-ACCOUNT.md#your-walka-account-on-walkadev) is not in that
  folder: it is kept in your Mac's keychain.
* **Commands** like `probe` and `game` are typed in Terminal: drag the
  `Walka` application into a Terminal window (that types where it is),
  then - with no space in between - type `/Contents/MacOS/Walka probe` and
  press Return. Started this way Walka belongs to Terminal as far as macOS
  is concerned: it is **Terminal** that is asked about, and allowed,
  Bluetooth.
* **From source:** `cd companion`, `python3 -m pip install -e .`,
  `python3 -m walka` (Python 3.14 or newer).

## Is the download safe?

`Walka.exe` - like `Walka` for the Mac - is a program from the internet
that your computer does not recognise, so it is right to ask. This is what
there is to go on:

* **Every release is scanned for viruses before it is published.**
  Microsoft Defender - the antivirus that is part of Windows - scans
  `Walka.exe`, the Mac programs and the addon's files on one of GitHub's
  Windows machines, with the virus definitions of that day. If it finds anything, nothing is
  published. The badge under the download buttons is the result for the
  latest release; `virus-scan.json` on the
  [release page](https://github.com/AntonyPerez0/walka-addon/releases/latest)
  has the details. A scan that finds nothing is not proof that a file is
  harmless - no scanner can give that - which is why the rest of this
  list is here.
* **You can check it yourself.** Drop the file you downloaded on
  [virustotal.com](https://www.virustotal.com/gui/home/upload) - no
  account needed. It runs the file past the scanners of about 70
  antivirus makers.
* **It is built in the open.** Nobody makes `Walka.exe` or the Mac
  programs on their own computer:
  GitHub [builds it](https://github.com/AntonyPerez0/walka-addon/actions/workflows/ci.yml)
  from the code on this page, and all of that code is here to read.
* **What else is in it is fixed and listed.** The program also carries
  Python and a handful of libraries. The build takes exact versions of
  them, checked against checksums that are part of this repository
  (`companion/requirements-build.lock`), so a library that is tampered
  with tomorrow does not get into Walka by being the newest. `Walka.exe
  licenses` lists them, with their licences.
* **You can check that your download is the file that was built.**
  `SHA256SUMS.txt` on the release page has the checksum of every download
  (on Windows: `Get-FileHash Walka.exe` in PowerShell; on a Mac: `shasum
  -a 256` and the file). GitHub also keeps a signed, public record that
  these files were built by this repository's workflow from this version
  of the code; with GitHub's `gh` tool, `gh attestation verify Walka.exe
  --repo AntonyPerez0/walka-addon` checks a download against it. And the
  files of a published release are never replaced: a fix is a new version.

**If a scanner or two on VirusTotal flag it:** `Walka.exe` is a Python
program packed into one file, and it is not signed by a company. A few
scanners are wary of every file like that, so one or two flags with a
vague name (*Generic*, *Suspicious*, ...) are usually a false alarm. The
report shows which scanners flag it and what they call it: you decide. If
many of them flag it, do not open it, and
[open an issue](https://github.com/AntonyPerez0/walka-addon/issues).

**The *Windows protected your PC* message** is about something else:
Windows shows it for every program that is new and not signed by a
company. It does not mean that anything was found. The same goes for
*"Apple could not verify Walka is free of malware"* on a Mac: macOS says
that about every program that was not signed with an Apple developer
account and sent to Apple to be checked.

## Walka's window

Walka is a program with a window of its own: no console window to leave
open next to it, and no tab in your web browser. Closing the window closes
Walka - the walk being recorded is saved first, and the belt is not
touched.

* **What draws it.** The window shows the dashboard with what your
  system already has for showing pages inside programs: *Microsoft Edge
  WebView2* on Windows (part of Windows 11, and on nearly every
  Windows 10), WebKit on a Mac. Nothing is downloaded for it. The
  dashboard is still a page that Walka serves on your own computer, at
  `127.0.0.1:8787`, and still only there.
* **Without WebView2** (some Windows 10 PCs) Walka opens the dashboard in
  your web browser instead, and shows a small box that says *Walka is
  running*: leave it open while you walk, and press **OK** in it to close
  Walka.
* **In the browser on purpose:** start Walka with `--browser`, or set
  `"window": false` under `"web"` in `config.json`. `--no-window` shows
  the dashboard nowhere; it is still at its address.
* **Saving files.** *Download TCX* and *Download PNG* ask where to save
  the file. (In a browser they went to the downloads folder.)
* **Your settings and walks** are in `%APPDATA%\Walka` on Windows and in
  `~/Library/Application Support/Walka` on a Mac. Next to them is
  `walka.log`: what Walka used to write in its console window - what it
  connected to, what went wrong. It can hold your treadmill's name and
  Bluetooth address, and it stays on your computer unless you send it to
  somebody. Your settings are in `config.json`: if you edit it by hand
  and Walka cannot read it, Walka starts with its defaults and keeps your
  file as `config.json.unreadable`. `active-session.json` is the walk in
  progress, written every
  half minute. If a walk cannot be saved (a full disk), it stays in that
  file until it can, even across a restart. There is also
  `active-session.lock`, an empty file: through it one Walka tells
  another that it is recording a walk. `walka.lock`, empty too, is held
  for as long as Walka runs: a second Walka started on the same folder
  says that one is running already, and does not start. Once you have
  signed in to
  [your Walka account](WALKA-ACCOUNT.md#your-walka-account-on-walkadev) there is also
  `walka-account.json` (with an empty `walka-account.lock` beside it). It
  holds which Walka account is signed in on this computer (the address
  of the database it is in, its e-mail address, its id there, and whether
  you signed in with Discord, GitHub or Google), whether sending is
  switched on and what you chose when you switched it on, and what became
  of each walk: sent, there already, or not taken, with the reason that
  was given. Once you sign out, it no longer holds the e-mail address,
  how you signed in or what you chose: only the account's id and what
  became of each walk (so that signing in again sends none of them a
  second time). While sending is on it holds a mark beside that switch as
  well: a random text that is a key to nothing, which Walka keeps a
  second time where the sign-in is kept. It also notes that a sign-in
  ended (until you press *OK*), that a sign-in may still be in Windows'
  Credential Manager or in your Mac's keychain after you signed out
  (until it is taken out), and that a Walka account was found to be
  deleted. It stays when you sign out, and so does the e-mail address in
  it. The sign-in itself is not in that folder, nor in any file of
  Walka's: it is kept in Windows' Credential Manager or in your Mac's
  keychain. Neither the sign-in nor that e-mail address is ever written
  to `walka.log`.
* **Typing a command** (`Walka.exe probe`, `Walka.exe game`) on Windows:
  open the folder `Walka.exe` is in, click the address bar at the top of
  that window, type `cmd` and press Enter. A command prompt opens in that
  folder: type the command there. The prompt comes back at once, before
  the command is done - what the command prints follows below it.

## Before your first walk

So far Walka has been checked with the game on one pad: a UREVO Strol 2E
(URTM030), with Windows 11, in November 2026. On any other pad, or on a
Mac, it has not. Nothing here can move the belt, so what there is to check
is whether your walk is *counted right*:

1. **`Walka.exe probe`** (or `py -m walka probe`; for how to type a
   command see [Walka's window](#walkas-window), and on a Mac
   [On a Mac](#on-a-mac)). It lists what the pad
   exposes and listens while you operate it with the remote. It sends the
   treadmill no commands. Keep the report - it settles the open questions in
   [docs/HARDWARE.md](HARDWARE.md).
2. **Walk for a few minutes with the dashboard open** and compare it with
   the treadmill's own display: the speed, and after the walk the distance
   and the steps. If they disagree, please
   [open an issue](https://github.com/AntonyPerez0/walka-addon/issues)
   with the probe report.
3. **`Walka.exe game`** after you have played with the addon and logged
   out (or typed `/reload`) once: checks that Walka finds what the game
   saved for it - the walking sessions and your characters.
4. **In game**, the checks in
   [docs/IN-GAME-CHECKLIST.md](IN-GAME-CHECKLIST.md).

And for you, not for Walka: start slow, and know where the remote is.
