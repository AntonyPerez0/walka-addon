# Third-party notices

## Road to Sixty

Walka's road recorder and maps (`Walka/Core/Road.lua`,
`Walka/RoadRecorder.lua`, `Walka/UI/RoadMap.lua`, `Walka/UI/WorldMapRoad.lua`)
follow approaches worked
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

## OpenMap

When the addon OpenMap - <https://github.com/openmap-world/openmap> - is
installed, Walka draws the road on OpenMap's seamless world map
(`Walka/UI/WorldMapRoad.lua`). It does so by calling the interface OpenMap
offers other addons (`OpenMapAPI`, described in OpenMap's `docs/api.md`):
where a world position is on its map, how high its map art reaches, and a
switch in its menu. How a frame of another addon is to be placed on the
world map's canvas is taken from that description, and what the game does
not allow on the map in combat from OpenMap's own notes on it. No code,
data or art of OpenMap is in Walka, and Walka works the same without it.
OpenMap's code is published under the MIT licence (its map textures are
Blizzard's and its artists', and are not used here).

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
