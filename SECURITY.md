# Reporting a security or safety problem

Walka moves a treadmill belt. A hole in it is not only a matter of data:
so if you have found a way to make Walka do something to the belt that
its user did not ask for, please say so in private first.

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

Anything that lets someone or something other than the person at the
computer:

* start the belt, speed it up, or raise the speed cap - for example
  another web page open in the same browser, another device on the
  network, or a picture or video on the screen that is taken for the
  game's signal;
* keep STOP, the STOP hotkey or a stop at the treadmill from working, or
  restart a belt that was stopped;
* get past the disclaimer that has to be accepted before a belt is driven;
* read what Walka stores (walks, weight and height, heart rate) from
  outside the computer, or make Walka send anything anywhere;
* write files outside Walka's own folders and the game's addon folder;
* put something into a release that is not built from this repository's
  code (the build, its pinned dependencies, the release workflow).

The rules the belt is meant to follow are in [docs/SAFETY.md](docs/SAFETY.md),
and how the dashboard is shut off from other pages is at the top of
`companion/src/walka/web.py`. A case where Walka breaks one of its own
rules is welcome here too, even if no attacker is involved - if it could
hurt someone before it is fixed, report it this way rather than in public.

## What does not

* A program that already runs on the same computer as the user. It can
  press keys, read files and talk to the treadmill itself; Walka cannot
  defend against it.
* Someone with physical access to the computer or the treadmill.
* Other software on a treadmill's Bluetooth: a treadmill takes commands
  from whatever connects to it. That is the treadmill's design, not
  Walka's.
* Antivirus programs that flag `Walka.exe` without naming anything: see
  [Is the download safe?](README.md#is-the-download-safe)

Ordinary bugs - the belt does not follow, a number is wrong, a treadmill is
not found - go to the
[issue tracker](https://github.com/AntonyPerez0/walka-addon/issues).

## Which versions

Only the latest release is fixed. If you run an older one, update first
and try again.
