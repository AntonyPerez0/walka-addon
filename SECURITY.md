# Reporting a security or safety problem

Walka only listens to a treadmill: it has no way to start or stop a belt or
to change its speed. The worst thing that could be wrong with it is
therefore that this is not true. If you have found a way to make Walka send
a treadmill anything at all, or to get at what it stores, please say so in
private first.

## How to report

On GitHub, open this repository's **Security** tab and choose **Report a
vulnerability**
([direct link](https://github.com/AntonyPerez0/walka-addon/security/advisories/new)).
Only the maintainer sees the report. Please do not open a public issue for
it, and do not post the details anywhere else until there is a fix.

Say what you did, what happened, on which version (the dashboard's footer
and `Walka.exe --version` show it) and on which system. A way to repeat it
is worth more than anything else.

Walka is a hobby project with one maintainer: there is no bounty, and no
promised time to answer. A report that concerns the belt is looked at
before everything else. You will be told what was found and when a fixed
version is out, and named in its notes if you want to be.

## What counts

* **Walka writing to a treadmill.** Any way - through the dashboard, the
  game's signal, a setting, a file, a crafted Bluetooth device - to make
  the companion send a treadmill a command, or write to any of its
  characteristics beyond switching on the notifications it listens to.
  ([docs/SAFETY.md](docs/SAFETY.md) says what the companion does on a
  connection, and how the rest is kept out.)
* Anything that lets someone or something other than the person at the
  computer read what Walka stores (walks, weight and height, heart rate),
  change its settings or delete walks - for example another web page open
  in the same browser, or another device on the network - or make Walka
  send anything anywhere.
* A picture or video on the screen that is taken for the game's signal.
  (It can no longer move anything, but it could file a walk under the
  wrong character.)
* Writing files outside Walka's own folders and the game's addon folder.
* Putting something into a release that is not built from this
  repository's code (the build, its pinned dependencies, the release
  workflow).

How the dashboard is shut off from other pages is at the top of
`companion/src/walka/web.py`.

## What does not

* A program that already runs on the same computer as the user. It can
  press keys, read files and talk to the treadmill itself; Walka cannot
  defend against it.
* Someone with physical access to the computer or the treadmill.
* Other software on a treadmill's Bluetooth: a treadmill takes commands
  from whatever connects to it. That is the treadmill's design, not
  Walka's. (While Walka is connected, most treadmills accept no second
  connection.)
* Antivirus programs that flag `Walka.exe` without naming anything: see
  [Is the download safe?](README.md#is-the-download-safe)

Ordinary bugs - a walk is not recorded, a number is wrong, a treadmill is
not found - go to the
[issue tracker](https://github.com/AntonyPerez0/walka-addon/issues).

## Which versions

Only the latest release is fixed. Versions up to 0.6.1 could start, stop
and steer a belt; they have been withdrawn and are not supported. If you
still have one, replace it with the latest release.
