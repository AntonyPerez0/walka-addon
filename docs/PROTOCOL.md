# The Walka beacon protocol

How the addon talks to the companion app. Version 1.

A World of Warcraft addon runs in a sandbox: no files, no network, no
Bluetooth. The one thing it can do that the outside world can see is draw. So
the addon draws its state as a tiny strip of black and white squares in the
top-left corner of the game window, and the companion reads the strip with an
ordinary screen capture. Nothing is ever sent *to* the game.

Implementations: `Walka/Core/Protocol.lua` + `Walka/Beacon.lua` (addon) and
`companion/src/walka/protocol.py` + `beacon.py` (companion). The test-suite
checks them against each other.

## 1. The frame

Ten bytes: eight payload bytes followed by a CRC-16.

There are two kinds of frame, told apart by the frame type. This section
describes the *live frame*: the addon's state from moment to moment, and the
only thing the belt follows. The other kind, the *stat frame*, carries the
numbers of a character's journey and is described in
[section 4](#4-stat-frames).

Payload of the live frame, most-significant bit first:

| Bits   | Width | Field           | Meaning |
|--------|------:|-----------------|---------|
| 0–3    | 4     | version         | `1` |
| 4–5    | 2     | frame type      | `0` = live frame. (`1` = stat frame, section 4. `2` and `3` are not assigned.) |
| 6–13   | 8     | seq             | Heartbeat. +1 on every redraw (10 per second) of either kind of frame, wraps at 256. |
| 14–21  | 8     | session         | Walking-session counter, 1–255. `0` = no session. |
| 22–37  | 16    | flags           | See below. |
| 38–49  | 12    | target speed    | Belt speed the addon wants, in 0.01 km/h (0–40.95). |
| 50–61  | 12    | character speed | Character's speed in 0.02 yd/s (0–81.9). |
| 62–63  | 2     | reserved        | `0` |

CRC: CRC-16/CCITT-FALSE (polynomial `0x1021`, initial value `0xFFFF`, no
reflection, no final XOR) over the eight payload bytes, appended big-endian.
Check value: the CRC of the ASCII string `123456789` is `0x29B1`.

### Flags

Bit 0 is the least-significant bit of the 16-bit field.

| Value | Name      | Meaning |
|------:|-----------|---------|
| 1     | ACTIVE    | A walking session is running: the companion may drive the belt. |
| 2     | PAUSED    | The session is paused: the belt must be paused. |
| 4     | MOVING    | The character is moving. |
| 8     | MOUNTED   | |
| 16    | TAXI      | On a flight path. |
| 32    | SWIMMING  | |
| 64    | FLYING    | |
| 128   | DEAD      | Dead or ghost. |
| 256   | COMBAT    | |
| 512   | AFK       | |
| 1024  | FALLING   | |
| 2048  | RESTING   | In an inn or city. |
| 4096  | ESTIMATED | The character speed is an estimate (see below). |

ACTIVE, PAUSED, MOVING and AFK change what the companion does; the rest are
for display and for future use. AFK matters together with PAUSED: the addon
pauses a session by itself when the player goes AFK, and keeps the AFK flag
set for as long as that automatic pause lasts. The companion treats a pause
*without* AFK as something the player asked for (pause then resume is a
deliberate way to restart a belt that was stopped at the treadmill); an
automatic one is not.

### Why "ESTIMATED" exists

WoW: Forever runs on the modern client, which hides some values from addons
during combat ("secret values"). The character's exact speed is one of them.
Walka does not try to get around that: while the real speed is hidden the
addon estimates it from what is still public (is the character moving? is it
mounted or swimming? what was its run speed last time it was readable?) and
sets this flag. A hidden value is never drawn on screen.

### What the fields mean to the companion

* **seq** proves the game is alive. If valid frames keep arriving but `seq`
  stops changing, the picture is frozen (a loading screen over a stale frame,
  a hung client) and the companion treats the signal as lost. Only the `seq`
  of live frames counts here (section 4).
* **session** changing means a new walking session started; the companion
  starts a new recording. It counts as an explicit start - one that may
  start the belt - only if the companion watched it begin (the frame before
  it said "no session"); a session id that simply changes under it does
  not, and a stopped belt then waits for a deliberate act (SAFETY.md, rule
  6).
* **target speed** is a *request*. The companion clamps it to the treadmill's
  range and to the user's hard speed cap, rate-limits changes, and ignores it
  entirely unless ACTIVE is set and PAUSED is not. `0` with ACTIVE set means
  "stop the belt but keep the session" (the character is standing still and
  the user chose a stand speed of zero).

## 2. The strip

The 80 frame bits are drawn as one row of square cells, white = 1, black = 0,
framed by fixed guard cells:

```
cell:   0 1 2 3 | 4 .............. 83 | 84 85
        W B W B |    80 frame bits    |  W  B
        preamble                        postamble
```

86 cells in total. Every cell is `N`×`N` physical pixels (`N` = 4 by default,
adjustable 2–8), so the strip is 344×4 pixels at the default size. It sits at
the top-left corner of the game's client area, on top of everything else,
with a black margin one cell wide to its right and below it.

Why only black and white, when colour could carry three bits per cell or
more: brightness, contrast and gamma settings, HDR tone-mapping, night-light
filters, colour-blind modes and sharpening all shift colours, and projects
that used multi-level colour cells report misreads in the field. Black versus
white survives all of them, because the decoder only asks "brighter or darker
than halfway?" and measures where halfway is from the preamble of every
frame.

### Drawing it (addon)

The strip's frame has no parent and is anchored to the screen itself, so it
is unaffected by the UI scale, is not hidden with the rest of the UI (Alt+Z),
and does not move when the game letterboxes the world view for a cinematic.
Its own scale is set to `768 / physicalScreenHeight`: WoW's UI coordinate
space is always 768 units high, so at that scale one unit is exactly one
physical pixel. The scale is re-applied when the resolution or UI scale
changes. Cell positions and sizes are whole pixels, so the strip does not
depend on the client's pixel-snapping (which some UI replacement addons turn
off for every texture).

### Reading it (companion)

The decoder works on rows of pixel brightness values and does not assume a
cell size. A frame is only accepted if the picture is what the addon draws:

1. **The opening.** Every frame begins the same way: the preamble, the
   protocol version and the first bit of the frame type - as runs of pixels:
   a white, a black and a white cell, four cells of black, a white cell. The
   decoder looks for that pattern (cells of one width, at least about 1.9 px
   wide) and measures the cell pitch and the black and white levels from it.
   "Bright" is judged from the pixels right there, not from the whole row,
   so a strip whose white is dimmer than highlights in the game world next
   to it (HDR, Auto-HDR) is still found.
2. **The cells.** The centre of each of the 86 cells is sampled at that
   pitch; pitches a fraction of a pixel either side are tried too (this is
   what makes fractional cell sizes work, e.g. when the game window is being
   scaled). Preamble, postamble and CRC-16 must match.
3. **The content.** The frame must be one the addon can have sent: a known
   frame type, reserved bits zero, no unassigned flag bits, and for a stat
   frame a known stat id.
4. **The surroundings.** To the right of the last white cell it must be
   black for two cells (the last cell and the quiet margin). The rows next
   to the row that decoded must show the very same cells over a height of
   one cell - the cells are squares - and right under them it must be black
   across the strip's width.

Text, tables and window decorations are rows of light and dark runs too, and
a 16-bit CRC by itself does not keep them out: before these checks, about
one screenful of source code in a hundred "decoded". They make that rare.
What makes it harmless is the next rule.

### Believing it

**One decoded frame is never acted on.** The addon redraws the strip ten
times a second and every redraw carries the next value of `seq`. A place on
the screen is believed only after it has been watched ticking:

* frames decoded at the same place with the same cell size,
* whose `seq` goes up by no more than the time between two looks allows
  (one step per tenth of a second, plus a little for a hitch),
* agreeing on the session id,
* for three frames in a row at the game window's corner, six anywhere else.

Until then the companion behaves exactly as if nothing had been decoded.
Belief is lost when `seq` has not moved for 1.5 seconds or the strip turns
up somewhere else, and has to be earned again (about a quarter of a second
for the real strip). A still picture never passes - a screenshot, a paused
video, text that decodes by accident - because its counter does not move;
two pictures taking turns do not either, because the counter would have to
run backwards.

### Where it looks

When the system can say where the game window is (Windows and macOS), the
strip is at the top-left corner of that window's client area or it is
nowhere: nothing else on the screen is looked at, so nothing else on the
screen can be mistaken for the strip. If the game is minimised or closed,
nothing is read.

macOS gives the window's frame with its title bar, and not the height of
that bar. There the corner is looked for from the window's top edge down
to 48 points below it, and the first strip found is the one. Positions on
a Mac are in points; the picture is read at the display's full resolution
(two pixels to the point on a Retina display), so a cell is as many pixels
wide as the addon drew it.

Only where the window cannot be asked for (other platforms, or a window
search that has never found the game since the companion started) the
reader falls back to the top-left corner of each monitor and, at most every
few seconds, a search of the whole screen. A place found that way has to
tick for twice as long before it is believed, and a place whose picture does
not move is given up and remembered.

`walka beacon` prints what the reader sees; if the game window is found but
no strip is at its corner, it says whether there is a strip anywhere else
on screen.

### When the strip is not visible

The strip is not drawn during loading screens, and the companion cannot see
it while the game is minimised or another window covers that corner. That is
expected and safe: see "signal loss" in [SAFETY.md](SAFETY.md).

## 3. The return path

There is no live return path - an addon cannot receive anything while the
game runs. The companion writes `Walka/CompanionData.lua` (one global table,
`WalkaCompanionData`: measured sessions, body profile, treadmill limits),
and the game loads it at login and on `/reload`, like the data files written
by other addon companion apps. Until then the in-game HUD shows the addon's
own estimates, which replay the companion's rate limits and so stay close to
the real numbers.

There is one more way out of the game, a slow one: what an addon keeps in
its saved variables, the game writes to a file at logout and on `/reload`.
The road a character has travelled reaches the companion that way: see
[5. The saved file](#5-the-saved-file-the-road).

## 4. Stat frames

Besides driving the belt, the addon keeps a record of each character's
journey: its level and time played, how far it has travelled on foot, and
the real-world walking done while playing it. The companion's dashboard
turns those numbers into a picture to share. The game only writes an addon's
saved data to disk at logout or `/reload`, so to reach the companion while
the game runs the numbers take the same road as everything else: the strip.

A stat frame carries one number. It has the same ten bytes, the same CRC and
the same place on the strip as a live frame; only the payload differs:

| Bits   | Width | Field      | Meaning |
|--------|------:|------------|---------|
| 0–3    | 4     | version    | `1` |
| 4–5    | 2     | frame type | `1` |
| 6–13   | 8     | seq        | The same counter as in live frames: +1 on every redraw of either kind. |
| 14–19  | 6     | stat id    | Which number this is: see below. |
| 20–59  | 40    | value      | An unsigned whole number. |
| 60–63  | 4     | reserved   | `0` |

### The stats

| Id    | Name             | Value |
|------:|------------------|-------|
| 1     | CHAR_KEY         | A hash of the character's "Name-Realm" (below). Tells characters apart. |
| 2     | CHAR_INFO        | `level` + `level cap` × 2^8 + `class id` × 2^16 + `faction` × 2^24, one byte each. Faction: `0` unknown, `1` Alliance, `2` Horde. |
| 3     | PLAYED_S         | Total time played (`/played`), in seconds. `0` = not known yet. |
| 4     | FOOT_YD          | Yards travelled on foot since Walka began tracking this character. |
| 5     | TRAVEL_YD        | Yards travelled by any means: mounts, flight paths and swimming included. |
| 6     | REAL_STEPS       | Real-world steps walked while playing this character, over all its walks. |
| 7     | REAL_M           | Real-world distance walked, in metres. |
| 8     | REAL_KCAL        | Energy used, in kilocalories (gross, whole number). |
| 9     | REAL_S           | Seconds on the moving belt. |
| 10    | WALKS            | Number of walks counted. |
| 11    | SINCE            | When tracking began for this character: Unix time, in seconds. |
| 12    | INFO_FLAGS       | Bit 0 (value 1): the real-world totals include estimates the companion has not confirmed yet. |
| 16–19 | NAME_0 to NAME_3 | The character's name (below). |

"On foot" means moving while not mounted, not on a flight path, not flying
and not swimming; running as a ghost and jumping count.

Ids 0, 13–15 and 20–63 are not assigned. The addon never sends 0, and the
companion ignores ids it does not know, so stats can be added later without
a new protocol version.

Every value is a whole number that fits the field. The addon rounds, and
clamps instead of wrapping: a number too large for 40 bits is sent as
2^40 - 1, a negative or unusable one as 0.

**CHAR_KEY** is the djb2 hash of the UTF-8 bytes of the "Name-Realm" string,
kept to 32 bits: start with `h = 5381`, then `h = (h × 33 + byte) mod 2^32`
for every byte. Check value: the hash of the ASCII string `hello` is
`261238937`. A hash is sent, and not the text, so that the realm is never
drawn on screen; the companion only needs to know *that* two sets of numbers
belong to different characters.

**NAME_0 to NAME_3** carry the character's name without the realm - which the
game shows on screen anyway - as UTF-8: up to 20 bytes, five per frame,
padded with zero bytes. Within a value the first byte is the most
significant. A name longer than 20 bytes is cut between two characters,
never inside one (twelve Cyrillic letters are 24 bytes: the first ten are
sent).

### When they are sent

Every 5th redraw is a stat frame instead of a live frame - never two in a
row - so the belt still hears from the game four times out of five, and the
longest gap between two live frames grows from 0.1 to 0.2 seconds. The stats
go round in a fixed order:

```
CHAR_KEY, NAME_0, NAME_1, NAME_2, NAME_3, CHAR_INFO, PLAYED_S, FOOT_YD,
TRAVEL_YD, REAL_STEPS, REAL_M, REAL_KCAL, REAL_S, WALKS, SINCE, INFO_FLAGS
```

Sixteen stats, two a second: a full round takes about 8 seconds. CHAR_KEY
opens the round, and what follows belongs to that character; a different
CHAR_KEY means another character has logged in.

### A stat frame is never a live frame

A stat frame says nothing about what the belt should do, and the companion
makes sure it can never be mistaken for a frame that does:

* Only live frames (type 0) feed the reading the belt is driven by: its
  content, its age, the frozen-picture check and the "game window closed"
  rule. To all of these a stat frame is the same as no frame at all.
* Stat frames are handed to the part of the companion that keeps the
  journey numbers, which has no way to reach the belt.
* A frame of type 2 or 3, like a frame of another version, is refused: for
  the companion there is no beacon in that picture.
* So a picture that froze while a stat frame was on the strip is a lost
  signal like any other ([SAFETY.md](SAFETY.md)), although a valid frame
  can still be read from it.

The frame type is covered by the CRC like every other bit, so a misread
pixel cannot turn one kind of frame into the other.

### Putting a round together

The companion reads the strip through a screen capture, so it sees most
frames twice and now and then misses one - also the CHAR_KEY of a character
who has just logged in. To be sure that two characters' numbers are never
mixed, it keeps a round only if the CHAR_KEY that ends it is the one that
began it, every other stat was seen in between, and the strip never went
away for more than a few seconds meanwhile (a loading screen, a frozen
picture). Anything else is dropped and the next round is waited for, so the
numbers it holds are about eight seconds old. Each value is checked against
what can be true, and the name is treated as text from a stranger.

Implementations: `Protocol.packStatBits` in the addon; `protocol.decode`,
`beacon.Decoded` (which keeps the two kinds in separate fields),
`stat_route.py` and `journey.py` (the rounds, and the cache of every
character's latest numbers) in the companion.

## 5. The saved file (the road)

The road a character travels (`Walka/Core/Road.lua`) is a megabyte by level
60 - nothing for a strip that carries 40 bits five times a second. It is
only wanted for the picture on the Share page, which can wait, so it goes
the way every addon's data goes: the per-character saved variable
`WalkaCharDB`, which the game writes to

```
<game>\WTF\Account\<account>\<realm>\<character>\SavedVariables\Walka.lua
```

when the player logs out, reloads the interface or quits. Nothing can make
the game write it sooner.

The table:

```lua
WalkaCharDB = {
    version = 1,
    guid = "Player-...",        -- a new character with an old one's name starts afresh
    key = "Name-Realm",         -- what CHAR_KEY is the djb2 hash of
    yards = 12345, beltYards = 6789,
    maps = {                    -- per continent id: the game's map that shows that land
        [1] = { id = 1414, name = "Kalimdor" },
        [2600] = { id = 2500, name = "Zephras Isle" },  -- (whatever ids the game gives the isle)
    },
    segments = {                -- unbroken stretches travelled the same way
        { c = 1,                -- continent id (the game's, of the world position)
          m = "w",              -- on foot; "r" mounted, "t" flight path, "g" ghost
          b = 1,                -- the treadmill belt was moving (absent otherwise)
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
draws the land on that map; the companion uses the name.

How the companion reads it (`savedvars.py`, `road.py`):

* **Whose road.** The companion knows a character by the hash the beacon
  sends as `CHAR_KEY`. It hashes each file's `key` the same way and takes
  the file whose hash matches (the newest, if the character exists in two
  account folders). The folder names are not relied on, and neither they
  nor the realm are passed on or logged.
* **Data, not a program.** The file is Lua source, but it is not run: the
  reader accepts assignments of tables, strings, numbers, booleans and nil,
  and refuses anything else - a call, an operator, a name used as a value.
  A file cut short (the game was writing it, or crashed while it did) can
  therefore never be mistaken for a whole one: its tables do not close.
* **Out of the game's way.** The file is read in one go and never held
  open, with every kind of sharing allowed on Windows, so the game can
  replace it at any moment. A file changed within the last two seconds is
  left for the next look; one that cannot be read is tried again only once
  it has changed, and the road read before it stands meanwhile.
* **Only when asked.** The files are looked at when the Share page asks for
  a road (`GET /api/road?character=<id>`), at most every three seconds, and
  each is read again only when its size or date has changed. None of this
  runs on the loop that drives the belt, and nothing here can reach it.
* **For the page** the road is thinned to at most 6,000 points
  (Ramer-Douglas-Peucker, the same tolerance everywhere) and grouped by
  continent and by how it was travelled (`walked` with the belt moving,
  `road` without, `flight`), with its length measured before the thinning.
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

