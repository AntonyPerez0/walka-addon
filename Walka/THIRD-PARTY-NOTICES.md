# Third-party notices

This page is about what Walka's own code and pictures owe to other
projects. The packaged program (`Walka.exe`, `Walka` on a Mac) also
*contains* other software - see the end of this page.

## Road to Sixty

Walka's road recorder (`Walka/Core/Road.lua`, `Walka/RoadRecorder.lua`)
follows approaches worked
out in Road to Sixty by Jordan Burch - <https://github.com/Jordanburch101/road-to-sixty> -
an addon that records a character's road from level 1 to 60 and replays it
on a map: which game calls say where the character is, how often a point of
the path is worth keeping, and how the game's own map art can be drawn
without shipping any. The code in Walka is written for Walka; where it
follows Road to Sixty closely the file says so. Road to Sixty is far more
than this (a replay, a journal of quests, kills and gear, fog of war) and
runs happily next to Walka.

Road to Sixty is published under the MIT licence:

```
MIT License

Copyright (c) 2026 Jordan Burch (https://github.com/Jordanburch101)

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## The continents on the Share picture

The low-poly maps behind the road on the Share picture
(`companion/src/walka/static/lands.js`) are drawings made for Walka by
`tools/gen_lands.py`. No map art of the game is used. To find where the
coast runs, that script was given the world positions of creatures and
objects as listed in the world database of the CMaNGOS project's
`classic-db` - <https://github.com/cmangos/classic-db>, published under the
GNU General Public License v3 - and drew a generalised outline around them:
seventy to ninety straight edges per continent, with eight bays picked by
hand. The database itself is not part of Walka: `lands.js` holds only that
outline, and the relief of its facets is invented. (The tests and the
demo's made-up road name a few dozen towns by their world coordinates,
which are facts about the game that any database of it lists alike.)
World of Warcraft, Kalimdor and the
Eastern Kingdoms are Blizzard Entertainment's; Walka is a fan-made tool and
is not affiliated with or endorsed by Blizzard.

## What is packed into Walka.exe (and Walka on a Mac)

The program you download is Walka's code together with Python and the
libraries it runs on, in one file:

* **Python** (Python Software Foundation License) - <https://www.python.org>
* **aiohttp** and the libraries it brings along - aiohappyeyeballs,
  aiosignal, attrs, frozenlist, idna, multidict, propcache, yarl (Apache
  License 2.0, MIT, BSD, PSF) - the dashboard's web server, and the
  requests Walka makes to a Walka account
* **bleak** (MIT) - Bluetooth; on Windows with the **PyWinRT** packages
  (`winrt-*`, MIT), on a Mac with **PyObjC** (MIT)
* the bootloader of **PyInstaller** (GPL 2.0 or later, with an exception
  that allows distributing programs built with it under any licence) - the
  small program that unpacks and starts the rest

Their licences ask that the copyright notice and the licence text go with
every copy. They do: the build collects them from the very packages it
packs (`companion/third_party.py`) and puts them into the program, where

    Walka.exe licenses

prints all of them, with the exact version of each. The versions
themselves are fixed in `companion/requirements-build.lock`. Run from
source, Walka packs nothing: the libraries are the ones you installed.

The game addon (`Walka-addon.zip`) contains no third-party code.
