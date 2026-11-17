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

Payload, most-significant bit first:

| Bits   | Width | Field           | Meaning |
|--------|------:|-----------------|---------|
| 0–3    | 4     | version         | `1` |
| 4–5    | 2     | frame type      | `0` = live state (others reserved) |
| 6–13   | 8     | seq             | Heartbeat. +1 on every redraw (10 per second), wraps at 256. |
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
  a hung client) and the companion treats the signal as lost.
* **session** changing means a new walking session started; the companion
  starts a new recording and treats it as a fresh, explicit start.
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

The decoder works on one row of pixel brightness values and does not assume
a cell size:

1. Find a rising edge near the left of the row and measure the first three
   runs (white, black, white). They must be roughly equal; their average is
   the first estimate of the cell pitch, and their brightness gives the
   white and black reference levels. A row with less than about 20% contrast
   is not a beacon.
2. Sample the centre of each of the 86 cells at that pitch. If the guard
   cells or the CRC do not match, retry with pitches up to ±0.4 px either
   side in fine steps (this is what makes fractional cell sizes work, e.g.
   when the game window is being scaled).
3. Accept the frame only if the preamble, the postamble and the CRC-16 all
   match, and the version is one this build understands.

Where to look: first the game window's client-area origin (found through the
Win32 API), then the top-left corner of each monitor, and - at most every few
seconds - a scan of the whole screen. Once found, the position is remembered.

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
