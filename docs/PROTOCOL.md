# How the addon and the app share data

How the addon and the companion app tell each other what they know.

A World of Warcraft addon runs in a sandbox: no files, no network, no
Bluetooth, and nothing can reach it while the game runs. But the game reads
and writes files for it, at fixed moments:

* **From the game to the app:** what the addon keeps in its *saved
  variables*, the game writes to disk when the player logs out, types
  `/reload`, or is disconnected. The app reads those files. They are the
  only way anything leaves the game.
* **From the app to the game:** the game loads the Lua files in the addon's
  own folder at login and on `/reload`. The app writes one of them,
  `CompanionData.lua`.

Nothing passes between the two while the game runs. Nothing is drawn on the
screen for the app, and the app does not look at the screen. (Until 0.10 the
addon drew a strip of black and white squares in the corner of the game
window, and the app read it with a screen capture. That is gone.)

Implementations: `Walka/Walka.lua`, `Walka/Tracker.lua`,
`Walka/Core/Journey.lua`, `Walka/Core/SessionLog.lua` and
`Walka/RoadRecorder.lua` (addon); `companion/src/walka/game_file.py`,
`journey.py`, `road.py`, `savedvars.py` and `addon_sync.py` (app). The
test-suite checks the two sides against each other.

## 1. The files

| File | Written by | When | Read by | When |
|------|-----------|------|---------|------|
| `WTF/Account/<account>/SavedVariables/Walka.lua` (`WalkaDB`) | the game, for the addon | logout, `/reload`, disconnect | the app | looked at every 5 seconds, read again only when it has changed |
| `WTF/Account/<account>/<realm>/<character>/SavedVariables/Walka.lua` (`WalkaCharDB`) | the game, for the addon | the same | the app | when the Share page asks for a road |
| `Interface/AddOns/Walka/CompanionData.lua` (`WalkaCompanionData`) | the app | after every walk, when walks are filed, at start-up | the game, for the addon | login, `/reload` |

All paths are under the game's folder (the one that holds `Interface` and
`WTF`). The app finds it where the addon is installed, and looks in every
other install it finds too.

Nothing can make the game write its files sooner, and if the game crashes,
what the addon kept since the last save is lost. The walk itself is not:
the app records it from the treadmill.

## 2. The account-wide file: `WalkaDB`

```lua
WalkaDB = {
    settings = { ... },          -- the addon's own; the app does not read them
    sessionCounter = 12,
    sessions = {                 -- walking sessions marked in the game, oldest first
        { id = 11, start = 1795000000, ["end"] = 1795003600,
          character = "Name-Realm", auto = true,
          seconds = 3600, distanceM = 0, steps = 0, kcal = 0, yards = 9200, maxKmh = 0 },
        ...
    },
    active = {                   -- the session still running when the game saved
        savedAt = 1795007200,
        session = { id = 12, start = 1795005000, character = "Name-Realm", ... },
    },
    journeys = {                 -- one record per character, under "Name-Realm"
        ["Name-Realm"] = { ... },   -- section 3
    },
}
```

### Sessions

A *walking session* is the time the addon counted for a character: from when
it started (by itself once the character is in the game, or with
`/walka start`) to when it stopped. The addon keeps the newest 400. The app
reads of each one:

* `start`, `end` - Unix time, in seconds;
* `id` - the addon's session counter, 1-255;
* `character` - the character's "Name-Realm". A session whose name the game
  hid when it began (it starts with `?`) belongs to nobody;
* `auto` - the addon started it by itself.

The rest of a record (the addon's own estimates) is for the addon.

`active` is the session that was still running when the game saved the
file. If the same character comes back within three minutes (a `/reload`,
a quick relog), the addon carries it on; otherwise it closes it at
`savedAt`. `WalkaDB` is shared by every character of the account, so a
different character logging in closes it too, at `savedAt`, and starts its
own. To the
app it is a session that ran from its `start` until at least `savedAt`; the
next file says how long it really lasted.

### How a walk is filed under a session

The app records a walk from the treadmill alone: a walk begins when the belt
moves and ends when the belt has stood still for 60 seconds. It is saved as
source `free` - walked, with no game session known yet.

Whenever the file has changed, the app compares every `free` walk with the
sessions in it. A walk belongs to the session it shares the most time with,
if that shared time is at least **half of the shorter of the two**. The walk
is then filed under that session: source `wow`, the session's number, and the
character's name (never the realm). A walk once filed stays filed, even when
the addon no longer lists the session.

The addon uses the same rule (`SessionLog.sameWalk`) to match the app's
walks in `CompanionData.lua` with its own sessions. One session can hold
several walks - the session the addon starts by itself lasts the whole
login, while a walk ends whenever the belt stands for a minute. Each such
walk is counted for the character, and the yards the character travelled
during the session are shared out among the walks by how much each one
overlaps it.

So a walk is filed under its game session only after the game has saved -
at a logout or `/reload`. Until then the dashboard lists it as a walk with no
game session.

## 3. A character's journey

Each record in `WalkaDB.journeys` is what the addon knows of one character
since it began tracking it (`Walka/Core/Journey.lua`):

| Field | Meaning |
|-------|---------|
| `since` | When tracking began: Unix time, in seconds. |
| `footYd` | Yards travelled on foot. |
| `travelYd` | Yards travelled by any means: mounts, flight paths, flying and swimming included. |
| `level`, `maxLevel`, `classId`, `faction` | As last read from the game; `0` = not known. Faction: `1` Alliance, `2` Horde. |
| `played` | The game's `/played` total, in seconds; `0` = not known yet. |
| `real` | The real-world walking that is settled: `{ walks, seconds, distanceM, steps, kcal, estimated }`. |
| `shared` | What the app reads, written at logout and on `/reload`: `{ savedAt, played, walks, seconds, distanceM, steps, kcal, estimated }`. |

"On foot" means moving while not mounted, not on a flight path, not flying
and not swimming; running as a ghost and jumping count.

`shared` holds the real-world totals as they stood when the game saved: the
walks counted for the character, the seconds on the moving belt, metres,
steps and kilocalories, all whole numbers. `estimated` is `true` when they
include walks the app has not yet confirmed with its measured numbers (the
addon counts those from the pace the walker tells it, `/walka pace`).
`savedAt` is when they were taken. A record from an addon that did not write
`shared` yet is read from `real`.

The app turns these into the numbers on the Share page (`journey.py`). It
checks every number against what can be true and leaves out one that
cannot be; it keeps only the name from "Name-Realm" and treats it as text
from a stranger. It tells characters apart by a hash of the whole
"Name-Realm" - djb2 over its UTF-8 bytes, kept to 32 bits (start with
`h = 5381`, then `h = (h × 33 + byte) mod 2^32` for every byte) - so the
realm itself is never passed on or kept. The latest numbers of every
character are remembered in the app's `journey.json`, so a picture can still
be made when the game's files are out of reach.

## 4. The per-character file: the road

The road a character travels (`Walka/Core/Road.lua`) is a megabyte by level
60, and only wanted for the picture on the Share page, so it is kept in the
per-character saved variable `WalkaCharDB`, which the game writes to

```
<game>\WTF\Account\<account>\<realm>\<character>\SavedVariables\Walka.lua
```

at the same moments as the account-wide file.

The table:

```lua
WalkaCharDB = {
    version = 1,
    guid = "Player-...",        -- a new character with an old one's name starts afresh
    key = "Name-Realm",         -- whose road this is
    yards = 12345, beltYards = 6789,
    maps = {                    -- per continent id: the game's map that shows that land
        [1] = { id = 1414, name = "Kalimdor" },
        [2600] = { id = 2500, name = "Zephras Isle" },  -- (whatever ids the game gives the isle)
    },
    segments = {                -- unbroken stretches travelled the same way
        { c = 1,                -- continent id (the game's, of the world position)
          m = "w",              -- on foot; "r" mounted, "t" flight path, "g" ghost
          b = 1,                -- a session the player started was running (absent otherwise;
                                -- never for the automatic one, and not read by the app)
          l = 17,               -- character level
          t = 1795000000,       -- when it began (epoch seconds)
          x = -600, y = 4200,   -- first point, world yards (x north, y west)
          n = 2,                -- points after the first
          d = "3,20,0;3,19,-4", -- those points: seconds, dx, dy from the one before
        },
        ...
    },
}
```

`maps` is how a land that is no continent of Azeroth is found again. The
game lists Kalimdor and the Eastern Kingdoms as the continents of its
world; the Skyborne's Zephras Isle lies in Skywall and is listed under
neither. So the first time the character stands on a land, the addon goes
up from the map the game shows it on to the largest map that still shows
that land alone (its continent's map, where it has one; never a map of a
whole world) and keeps that map's id and name. The addon's own road window
draws the land on that map; the app uses the name.

How the app reads it (`road.py`):

* **Whose road.** The app hashes each file's `key` the same way as a
  journey's "Name-Realm" (section 3) and takes the file whose hash matches
  the character asked for (the newest, if the character exists in two
  account folders). The folder names are not relied on, and neither they
  nor the realm are passed on or logged.
* **Only when asked.** The files are looked at when the Share page asks for
  a road (`GET /api/road?character=<id>`), at most every three seconds, and
  each is read again only when its size or date has changed. None of this
  runs on the loop that records the walk.
* **For the page** the road is thinned to at most 6,000 points
  (Ramer-Douglas-Peucker, the same tolerance everywhere) and grouped by
  continent and by how it was travelled (`walked` on the treadmill, `road`
  otherwise, `flight`), with its length measured before the thinning.
* **Walked on the treadmill.** The addon cannot see the belt, so the app
  does not take its `b` for it. Every point of the road has a time (`t`
  and the seconds in `d`), and the app knows when it measured the belt
  moving (the speed series of each walk it recorded, a sample every five
  seconds). The road travelled on foot or mounted while the belt was
  moving is `walked`; a stretch the belt started or stopped on is cut
  there. With no walk measured, none of the road is `walked`.
  With it go the places where the road begins and ends, where every tenth
  level was reached, and the passages from one continent to another (from
  where to where; the dozen made most often - the page draws an arc for up
  to three between the continents it shows). The page draws all this over
  its own low-poly map of each continent (`static/lands.js`), which is in
  the same coordinates: world yards, x to the north and y to the west.
* **A land without a map.** `lands.js` has Kalimdor and the Eastern
  Kingdoms. Any other continent id in the road - Zephras Isle - is given
  the name saved in `maps` (one line of plain text, at most 40 characters,
  else none) and a `land` of the same form as an entry of `lands.js`
  (`bounds`, `points`, `coast`, `faces`), made from the road itself
  (`sketch.py`): every triangle of a fixed honeycomb whose middle is within
  reach of where the character went on foot or mounted, plus whatever that
  encloses. The page draws it in place of a map, on a plate of its own.

## 5. Reading the game's files

Both files are read the same way (`savedvars.py`, `game_file.py`,
`road.py`):

* **Data, not a program.** A saved file is Lua source, but it is not run:
  the reader accepts assignments of tables, strings, numbers, booleans and
  nil, and refuses anything else - a call, an operator, a name used as a
  value. A file cut short (the game was writing it, or crashed while it
  did) can therefore never be mistaken for a whole one: its tables do not
  close.
* **Out of the game's way.** A file is read in one go and never held open,
  with every kind of sharing allowed on Windows, so the game can replace it
  at any moment. A file changed within the last two seconds is left for the
  next look; one that cannot be read is tried again only once it has
  changed, and what was read before it stands meanwhile.
* **Off the recording loop.** The files are read on a worker thread, never
  on the loop that records the walk, and what the loop asks of them is
  answered from the last look while the next one is under way. The addon's
  `CompanionData.lua` is written on a worker thread too. So a slow disk
  cannot hold up a walk.
* **Checked.** Every time and number is checked against what can be true
  (a session cannot begin before 2020 or last longer than a week), and a
  file, a session or a journey that does not pass is left out.
* **Private.** Nothing read from these files leaves the computer, and
  neither the account's folder name nor a character's realm is passed on or
  logged.

`walka game` (`Walka.exe game` on Windows) prints what the app finds: how
many accounts' files, when the game last wrote them, how many sessions, the
newest one and whose it was, and the characters.

## 6. The other way: `CompanionData.lua`

The app writes `Walka/CompanionData.lua` in the addon's folder: one global
table, `WalkaCompanionData`. The game loads it at login and on `/reload`,
like the data files written by other addon companion apps. The app rewrites
it after every walk, when walks are filed under game sessions, when a
setting changes, and at start-up; it replaces the file in one step, so the
game never loads half of one.

```lua
WalkaCompanionData = {
    version = 1,
    written = 1795007300,        -- when the app wrote it (Unix time)
    profile = { weightKg = 82, heightCm = 180, stepCalibration = 1.0 },
    treadmill = "URTM030",       -- the name of the treadmill in use, if known
    sessions = {                 -- the newest 300 walks, newest first
        { start = 1795000100, ["end"] = 1795003500,
          seconds = 3400, distanceM = 3100, steps = 4300, kcal = 210.5,
          maxKmh = 3.5, avgHr = 98, maxHr = 121,
          source = "wow",        -- "free", "wow", "manual", ...
          gameSession = 11,      -- the addon's session number, if filed under one
          stepsSource = "treadmill" },
        ...
    },
    older = {                    -- everything before those, added up
        sessions = 40, seconds = ..., distanceM = ..., steps = ..., kcal = ...,
        ["until"] = 1790000000,  -- every walk that began before this is in the sum
    },
}
```

(Until 0.10 the per-walk key was `beaconSession`.)

The addon matches these walks with its own sessions by the rule in
section 2 and shows the measured numbers in place of its estimates: in the
character's journey and in its session history. Until it has them - the
walk is filed only after the game has saved, and the addon reads the app's
file only at the next login or `/reload` - it counts with its own estimates
and marks the totals `estimated`.

Nothing in this file, or anywhere else, reaches the treadmill: the app only
listens to it ([SAFETY.md](SAFETY.md)).
