# Walka

Walk where your character walks. Walka links an under-desk walking pad to
**World of Warcraft: Forever**: the belt speeds up when your character runs,
eases off when it stands, and an in-game HUD counts your speed, steps,
distance and calories - for this session and every one before it.

It is two pieces:

* **the addon** (`Walka/`) - reads your character's movement, decides how
  fast the belt should go, and shows the HUD, history and settings in game;
* **the companion** (`companion/`) - a small Windows app that reads the
  addon's signal off the screen, drives the treadmill over Bluetooth with a
  set of safety rules, records your walks, and shows a live dashboard in
  your browser.

Built for the **UREVO Strol 2E (URTM030)**; it should work with any
treadmill that speaks standard Bluetooth FTMS with speed control. The
companion also works without any game (manual sessions), and logs walks you
do with just the remote.

> **Status: untested on real hardware and in the real game.** Everything
> here was built and tested against a simulated treadmill and a simulated
> game client (about 2,000 automated tests). Read
> [Before your first walk](#before-your-first-walk).

## How it works

```
 WoW: Forever                         Windows                      Treadmill
┌───────────────────────┐      ┌───────────────────────┐      ┌──────────────┐
│ Walka addon           │      │ Walka companion       │      │ UREVO pad    │
│  character speed      │      │                       │      │              │
│   → belt target       │      │  screen capture       │      │              │
│   → pixel strip ░█░█  │ ───► │   → safety governor   │ ───► │ belt speed   │
│  HUD: live estimates  │ look │   → Bluetooth (FTMS)  │ BLE  │              │
│                       │      │  records the walk     │ ◄─── │ speed, dist. │
│  history ◄────────────│◄──── │  writes CompanionData │      │              │
└───────────────────────┘ file └───────────────────────┘      └──────────────┘
```

An addon cannot use Bluetooth, files or the network, so it *draws* its state
as a thin strip of black and white squares in the top-left corner of the
game window, ten times a second. The companion takes a tiny screenshot of
that corner and decodes it. **Nothing is ever sent to the game** - no key
presses, no mouse input, no memory reading.

An addon also cannot receive live data, so the in-game HUD shows the
addon's own estimate of the belt (it knows the speed it asked for and how
the companion ramps), and the companion's *measured* numbers are written to
a file the game loads at login or on `/reload`. The dashboard always shows
the measured numbers live.

Details: [docs/PROTOCOL.md](docs/PROTOCOL.md).

## What you need

* Windows 11 (Windows 10 generally works) with Bluetooth 4.0 / "LE" or
  newer.
* The walking pad, switched on, **not** paired in Windows Bluetooth
  settings, and with the UREVO phone app **closed** (the pad accepts one
  connection at a time).
* WoW: Forever in *Windowed* or *Windowed (Fullscreen)* mode, with the
  top-left corner of the game visible.

## Install

1. **Get the companion.** Either download `Walka.exe` from this
   repository's *Actions* tab (latest run → *Walka-windows* artifact), or
   run it from source (Python 3.10 or newer):

   ```
   cd companion
   py -m pip install -e .
   py -m walka
   ```

2. **Run it.** A console window opens and your browser shows the dashboard
   at <http://127.0.0.1:8787>.
3. **Install the addon.** With the game closed, on the dashboard:
   *Settings → Game addon → Install automatically*. (Or copy the `Walka`
   folder to `World of Warcraft\_forever_\Interface\AddOns\` yourself.)
   Start the game and tick *Walka* in the AddOns list.
4. **Connect the treadmill.** Dashboard → *Treadmill → Scan*, pick
   `URTM030`, *Connect*.
5. **Tell it about you.** *Settings → Body profile*: weight and height
   (used for calories and steps).

## Using it

In game, type `/walka` to show the HUD, step onto the pad and press
**Start** (or `/walka start`). The treadmill counts down and the belt
starts slowly; from then on it follows your character:

| Your character is…          | Belt (defaults)  |
|-----------------------------|------------------|
| standing still              | 0.8 mph          |
| walking                     | 1.2 mph          |
| running                     | 1.6 mph          |
| on a fast mount             | 2.0 mph          |
| on a flight path            | running pace     |
| anything faster/slower      | in between - Sprint really does speed you up |

A real run speed (14 mph!) is not something to match under a desk, so every
one of those numbers is yours to set in `/walka options`. The defaults sit
where people settle on treadmill desks (1-2 mph); raise them as you get
used to it. Also there:

* how quickly the belt reacts (*Steady*, *Balanced*, *Responsive*);
* a maximum (2.5 mph by default; the companion has its own hard cap,
  3.0 mph by default);
* **Keep walking when my character stands still** - the belt holds your
  walking pace through stops (looting, casting, reading a quest), so they
  cost no steps. Also on the HUD's right-click menu and as
  `/walka keepwalking`;
* or the opposite: stop the belt outright when you stand still.

**Pause** / **Stop** are on the HUD too. Going AFK pauses automatically;
the session resumes when your character next moves.

| Command | |
|---|---|
| `/walka` | show or hide the HUD |
| `/walka start` · `pause` · `resume` · `stop` | control the session |
| `/walka keepwalking on` \| `off` | hold the walking pace when your character stops |
| `/walka history` | past sessions, totals, 7-day chart |
| `/walka journey` | your character's journey so far (see [below](#your-journey-and-a-picture-to-share)) |
| `/walka options` | settings |
| `/walka status` | what the addon is doing right now |
| `/walka weight 180lb` · `/walka height 5'11` | your body, if you do not run the companion |
| `/walka units mph` \| `kmh` | display units |
| `/walka beacon on` \| `off` \| `size 2-8` | the signal strip |

### Numbers: estimated now, measured later

The HUD's live numbers are marked `~`: they are the addon's estimate. After
a walk, press **Sync** in the history window (it reloads the interface; a
session in progress carries on) or just log in next time, and the history
switches that session to what the treadmill measured. The dashboard shows
measured values the whole time - put it on a second monitor. (It is only
reachable from the PC itself: it can start and stop the belt, so it is not
offered to other devices on your network.)

### In combat

WoW: Forever hides your character's exact speed from addons during combat.
Walka does not work around that. While it is hidden the addon estimates:
moving or not, at your last known run speed. The HUD tooltip tells you when
this is happening.

### Calories and steps

* **Calories** come from belt speed and your weight, using an equation
  validated at slow desk-walking speeds (the usual treadmill formula reads
  11-35% low there). Expect roughly ±10% for an individual. The dashboard
  also shows how much of it is *on top of* what sitting would have burned.
* **Steps** are the treadmill's own count if it reports one; otherwise they
  are estimated from belt speed and your height. People differ by 6-20% at
  slow speeds, so there is a *step calibration* setting: count your steps
  for a minute, divide by what Walka counted.
* A **wrist watch will not count these steps** - with your hands on the
  keyboard your arm does not swing. Walka's count is the record.

## Stopping the belt

Fastest first:

1. the treadmill's **remote**, stop button or safety key - Walka then
   leaves the belt stopped;
2. **Ctrl+Alt+X** anywhere on the PC, even inside the game (Windows; change
   it under *Settings → Safety limits*);
3. the red **STOP** on the dashboard;
4. *Stop* on the HUD, or `/walka stop`.

## Heart rate (optional)

Any Bluetooth heart-rate sensor works: a chest strap, an arm band, or a
watch that can broadcast. Dashboard → *Settings → Heart-rate sensor → Scan*.

**Garmin Instinct 2 / Instinct 2 Solar:** hold *MENU* → *Sensors &
Accessories* → *Wrist Heart Rate* → *Broadcast Heart Rate*, then press
*GPS* to start broadcasting (press it again to stop). Watch-to-Windows
Bluetooth is known to be hit-and-miss and broadcasting uses battery; a
cheap Bluetooth chest strap or arm band is the dependable option.

Heart rate is shown live and stored with each walk. It only drives the
calorie figure when you have entered age and sex *and* your heart rate is
in real exercise territory (above 60% of the maximum for your age, for a
minute) - at desk-walking heart rates the heart-rate formula would read up
to double.

## Garmin Connect, Strava and others

Each walk can be downloaded as a **TCX** file (dashboard → *History* → a
walk → *TCX*) and imported by hand: on connect.garmin.com, *Activities* →
*Import*. Imported walks show time, distance, calories and heart rate.
Garmin does not add their steps to your daily step total.

## Your journey, and a picture to share

Walka keeps a record for every character you play: the level it has
reached, its time played and how far it has travelled on foot - and next to
that the real walking *you* did while playing it. `/walka journey` shows it
in game, and the dashboard's **Share** page turns it into a picture you can
post.

1. Log in with the companion running. The character's numbers reach the
   companion within about ten seconds (over the same strip as everything
   else) and are remembered there, so the picture can also be made later,
   with the game closed.
2. Dashboard → *Share*. Pick the character if you have several, change the
   words, tick what should be on the picture, and choose a theme (Sapphire,
   Ember, Wildwood, Frost or Parchment) and a size: portrait 1080×1350,
   square 1080×1080, story 1080×1920 or wide 1200×675.
3. **Download PNG** - or **Copy image** and paste it straight into a post or
   a chat.

What is on it:

| On the picture | What it is |
|---|---|
| **My steps** | Your real-world steps, and under them the distance you walked. With *This character's walks* these are the walks you did while playing that character, as the addon counts them; with *All my walks* they are everything the companion has recorded, with any game or none. |
| **My road** | The road your character has travelled, as a line: see [The road on the picture](#the-road-on-the-picture). |
| **Character level** | The level, and the level cap of the game. |
| **Time played** | The game's own `/played` for this character. |
| **Character steps** | How far the character has gone on foot since Walka began tracking it, as steps, with the in-game distance under it. |
| **Calories** | The same estimate as everywhere else in Walka (see [Calories and steps](#calories-and-steps)), rounded. |
| *Totals through ...* | The day the numbers were last received from the game. |

A `~` in front of a number means "about":

* **Character steps are a convention.** The game does not count steps, so
  Walka says a character takes 2,000 steps to the in-game mile of 1,760
  yards - 0.88 yards a step. "On foot" is moving while not mounted, not on
  a flight path, not flying and not swimming; running back as a ghost and
  jumping count. In combat, where the game hides the exact speed, the
  addon's estimate is used. Distance is counted from the day the addon was
  installed, not from level 1.
* **Calories** are always an estimate.
* **Your own steps** carry a `~` only while part of them is still the
  addon's estimate, until the game has loaded what the treadmill measured
  (*Sync*, or the next login).

### The road on the picture

The road the addon records (see [The road map](#the-road-map)) is drawn on
the picture over a small map of the continent - not the game's map, but a
low-poly drawing of Walka's own: the shape of the land in facets, in the
picture's colours.

* The stretches you walked **on the treadmill** are a bright line; what the
  character travelled while the belt stood still is the same line, faded;
  flight paths are fine dashes. A piece of the bright line stands in front
  of *"... % of it walked for real"*, so nobody has to guess which is which.
* *START* marks where the road begins, a gem with the character's level
  where it is now, and a small numbered stud every tenth level reached on
  the way.
* Kalimdor and the Eastern Kingdoms are shown whole, side by side on one
  sea and to one scale, with a dashed arc where you crossed from one to
  the other (the boat, the zeppelin).
* A character that has only been on one continent and has not gone far yet
  gets a **close-up**: the part of the coast it has walked, with a small
  map of the whole continent in a corner that shows where that is. Once the
  road spans about a third of the continent, all of it is shown.
* Under the map: the length of the road in in-game miles (flights not
  counted) and how much of it you walked for real. This is the road the
  addon *recorded* - mounted stretches included, dungeons not - so it
  differs from the miles on foot in the *Character steps* tile.

The coasts are generalised - straight edges, small inlets filled in - but
every town, harbour and quest area lies on the land, so a line across the
water means you really were at sea. A continent the companion has no map
of (should the game ever have one) still gets its road, on an empty chart.

The road is far too much for the strip on the screen, so it takes the slow
way: **the game saves it when you log out or type `/reload`**, and the
companion reads it from the game's own file
(`WTF\Account\...\SavedVariables\Walka.lua` in the game's folder). So the
road on the picture is as of your last logout or `/reload` - the Share page
says when that was - while the numbers next to it are live. Type `/reload`
before you make the picture and both are current.

The companion has to know where the game is for this; it does when the
addon is installed (*Settings → Game addon*). The file is only ever read, never
changed. Untick *My road* and the picture is as it was without it.

Private by default:

* The picture is drawn in your browser from numbers that are already on
  your PC. Nothing is uploaded and nothing is posted for you.
* The road is read from a file on your PC and goes no further than the
  page that draws it. On the picture it is a line on a simplified map and
  the names of the continents - no zones, no place names, no coordinates.
* Your character's name, and the small line with your weight and average
  pace, are **off** until you tick them. The realm never leaves the game:
  the companion is only sent a number that tells your characters apart.
* The downloaded file is named after the date, never after the character.
* The design is Walka's own and is drawn by the page itself: no game
  artwork, logos or fonts are used. The continents on the map are Walka's
  own drawing too (see [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md) for
  how their outlines were made).

`py -m walka --demo` has a made-up character to try the page with; it is
never saved.

## The road map

From the moment the addon is installed it keeps the road your character
travels - install it before you start a character and the road begins at
level 1. `/walka map` (or *Road map* in the HUD's right-click menu) shows
it on the game's own map of the continent:

* the road is coloured by level, a colour for every ten levels;
* the stretches you walked **on the treadmill** are bright, with a soft
  glow; what your character travelled while the belt stood still is dim;
* flight paths are thin and pale;
* a mark shows where you are now. The mouse wheel zooms, dragging moves the
  map, *Me* centres it on your character, *Fit* shows the whole continent,
  `<` and `>` change continent, and *Screenshot* saves a picture of the
  screen, map included, to the game's Screenshots folder.

What is recorded: a point every twenty yards (fewer on flight paths, more
where the road bends), nothing while you stand still, nothing inside
dungeons, raids and battlegrounds (the game does not say where you are
there). It is saved per character with your other addon settings and never
leaves your PC (the companion reads it there, to draw it on the
[Share picture](#the-road-on-the-picture)). `/walka road off` stops the
recording (`on` starts it again); what is recorded so far is kept. A road
from level 1 to 60 comes to about a megabyte.

If the map does not show, `/walka mapcheck` prints what the game answered
when Walka asked for its maps.

The way the road is recorded and the map is drawn follows
[Road to Sixty](https://github.com/Jordanburch101/road-to-sixty) by Jordan
Burch (MIT licence, see [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md)) -
an addon made for exactly this, with a replay of the whole journey, a
journal of quests and kills, and much more. It runs fine next to Walka.

## Other games, or no game

The dashboard has a **manual session**: start, pause, stop and speed
buttons. Use it with any game, or for a plain walk. And if you just walk
with the treadmill's remote while the companion is running, that walk is
recorded too.

## Safety

Short version - the long one is [docs/SAFETY.md](docs/SAFETY.md):

* Nothing moves until you press Start.
* A hard speed cap (3.0 mph until you raise it, never above 4.0 mph) that
  no game state can exceed, and gentle, rate-limited speed changes.
* If the companion loses sight of the game it holds, then slows, then
  pauses the belt. It never speeds up on a lost signal.
* Stop the belt at the treadmill and Walka leaves it stopped.
* After any pause it did not watch you start, the belt waits for your
  character to move.

Bluetooth is not an emergency stop: keep the remote within reach.

## Is this allowed?

Honestly: **it is not approved by Blizzard, and you use it at your own
risk.**

What Walka does and does not do:

* The addon uses only the public addon API, is free and unobfuscated, and
  follows the addon policy.
* The companion never sends input to the game, never automates anything
  in the game, never reads the game's memory or files (other than its own
  addon folder). It looks at one corner of your screen, the way a
  screenshot tool does, and what it reads is your own character's movement
  state.
* When the game hides a value from addons (speed in combat), Walka does not
  relay it; it estimates instead.

Blizzard's enforcement statements target automation, input broadcasting,
client modification and tools that get around addon restrictions for an
advantage; Walka is none of those. But Blizzard's EULA is written broadly
(it prohibits unauthorised software that "reads" information from the
game), Blizzard does not pre-approve third-party tools, and no statement
says a passive screen reader like this is fine. Similar addon-plus-reader
projects exist publicly for this client; that is not the same as
permission.

## Before your first walk

The pad and the game have not been tested against this code. Do these, in
order:

1. **`Walka.exe probe`** (or `py -m walka probe`). Read-only: it lists what
   the pad exposes and listens while you operate it with the remote. It
   cannot move the belt. Keep the report - it settles the open questions in
   [docs/HARDWARE.md](docs/HARDWARE.md).
2. **A manual session from the dashboard while standing next to the pad**,
   not on it. Start, change speed, pause, stop, press STOP, press
   Ctrl+Alt+X. Stop it with the remote and check Walka leaves it stopped.
3. **`Walka.exe beacon`** with the game running: checks that the strip can
   be read from your screen.
4. **In game**, the checks in
   [docs/IN-GAME-CHECKLIST.md](docs/IN-GAME-CHECKLIST.md).
5. Then walk - slowly at first.

## Troubleshooting

| Symptom | Try |
|---|---|
| Treadmill not found | Is it on? Close the UREVO phone app (force-quit it). Remove the pad from Windows *Bluetooth & devices* if you paired it there. A USB Bluetooth 5 adapter helps with weak built-in radios. |
| Dashboard says *no game signal* | Run the game windowed or borderless; keep the top-left corner uncovered; `/walka beacon on`; try `/walka beacon size 6`; run `Walka.exe beacon`. |
| Belt does not follow | Is a session started (`/walka status`)? Does the dashboard say *waiting for you to move your character* or *stopped at the treadmill*? Press Resume. |
| "Waiting for the treadmill to finish its previous workout" | Normal for about ten seconds after a stop: the pad ignores a start until then. |
| Speed is one step off | Set *Settings → Treadmill → Firmware speed grid* to mph (or km/h) instead of auto. |
| History shows `~` numbers | Press *Sync* in the history window after the companion has saved the walk; check *Settings → Game addon* shows the right folder. |
| Addon marked out of date after a game patch | Tick *Load out of date AddOns*, and update the `## Interface:` line in `Walka/Walka.toc`. |
| The pad keeps waking up | The companion only reconnects when a session starts or you press Connect; closing the companion releases the pad entirely. |

## Ideas for later

* A foot pod or other Bluetooth cadence sensor for true step counts.
* Goals and streak rewards in game; milestones announced in chat.
* Per-game plugins for other games (the companion's manual mode already
  works with anything).
* Reading daily totals back from Garmin's personal API.

## Development

```
py -m pip install -e "companion[dev]"
py -m pytest          # companion tests + addon tests (the addon runs under a mocked game client)
```

* `Walka/` - the addon. `Core/` is pure Lua (no game API) and is unit
  tested; `Game.lua` is the only file that touches values the game may hide.
* `companion/src/walka/` - `governor.py` (the safety rules), `treadmill/`
  (Bluetooth FTMS driver and a simulator), `capture.py` + `beacon.py`
  (reading the strip), `session.py`, `addon_sync.py`, `web.py` + `static/`
  (dashboard), `app.py` (wiring), `cli.py`; `journey.py` (each character's
  journey, put together from the strip), `savedvars.py` + `road.py` (the
  road, read from the game's saved file) and `static/share.js`, `card.js`,
  `cardmodel.js` (the Share page and the picture it draws), `lands.js` (the
  low-poly continents behind the road; made by `tools/gen_lands.py`).
* `tests/addon/` - loads the real addon files into a mock of the game's
  API that is checked against Blizzard's published API documentation for
  this client build (`tools/gen_api_snapshot.py` regenerates the snapshot).
* `py -m walka --demo` runs everything against a simulated treadmill and a
  scripted character.

More: [docs/PROTOCOL.md](docs/PROTOCOL.md) ·
[docs/SAFETY.md](docs/SAFETY.md) · [docs/HARDWARE.md](docs/HARDWARE.md)
