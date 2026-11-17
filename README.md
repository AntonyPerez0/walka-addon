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
> game client. Read [Before your first walk](#before-your-first-walk).

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

* Windows 10 or 11 with Bluetooth (4.0 / "LE" or newer).
* The walking pad, switched on, **not** paired in Windows Bluetooth
  settings, and with the UREVO phone app **closed** (the pad accepts one
  connection at a time).
* WoW: Forever running in *Windowed* or *Fullscreen (Windowed)* mode, with
  the top-left corner of the game visible.

## Install

1. **Get the companion.** Either download `Walka.exe` from this
   repository's *Actions* tab (latest run → *Walka-windows* artifact), or
   run it from source:

   ```
   cd companion
   py -m pip install -e .
   py -m walka
   ```

2. **Run it.** A console window opens and your browser shows the dashboard
   at <http://127.0.0.1:8787>.
3. **Install the addon.** On the dashboard: *Settings → Addon → Install*.
   (Or copy the `Walka` folder to
   `World of Warcraft\_forever_\Interface\AddOns\` yourself.) Restart the
   game if it was running and tick *Walka* in the AddOns list.
4. **Connect the treadmill.** Dashboard → *Treadmill → Scan*, pick
   `URTM030`, *Connect*.
5. **Tell it about you.** *Settings → Profile*: weight and height (used for
   calories and steps). Add age and sex if you want heart-rate based
   calories.

## Using it

In game, type `/walka` to show the HUD, step onto the pad and press
**Start** (or `/walka start`). The treadmill counts down and the belt
starts slowly; from then on it follows your character:

| Your character is…          | Belt (defaults)  |
|-----------------------------|------------------|
| standing still              | 1.0 mph          |
| walking                     | 1.5 mph          |
| running                     | 2.0 mph          |
| on a fast mount             | 2.5 mph          |
| on a flight path            | running pace     |
| anything faster/slower      | in between - Sprint really does speed you up |

A real run speed (14 mph!) is not something to match under a desk, so every
one of those numbers is yours to set in `/walka options`, along with how
quickly the belt reacts (*Steady*, *Balanced*, *Responsive*), a maximum, and
whether the belt should stop outright when you stand still.

**Pause** / **Stop** are on the HUD too. Going AFK pauses automatically.

| Command | |
|---|---|
| `/walka` | show or hide the HUD |
| `/walka start` · `pause` · `resume` · `stop` | control the session |
| `/walka history` | past sessions, totals, 7-day chart |
| `/walka options` | settings |
| `/walka status` | what the addon is doing right now |
| `/walka weight 180lb` · `/walka height 5'11` | your body, if you do not run the companion |
| `/walka units mph` \| `kmh` | display units |
| `/walka beacon on` \| `off` \| `size 2-8` | the signal strip |

### Numbers: estimated now, measured later

The HUD's live numbers are marked `~`: they are the addon's estimate. After
a walk, `/reload` (or just your next login) loads what the treadmill
actually measured, and the history switches that session to the measured
values. The dashboard shows measured values the whole time - put it on a
second monitor or your phone.

### In combat

WoW: Forever hides your character's exact speed from addons during combat.
Walka does not work around that. While it is hidden the addon estimates:
moving or not, at your last known run speed. The HUD tooltip tells you when
this is happening.

## Other games, or no game

The dashboard has a **manual session**: start, pause, stop and speed
buttons. Use it with any game, or for a plain walk. And if you just walk
with the treadmill's remote while the companion is running, that walk is
recorded too.

## Safety

Short version - the long one is [docs/SAFETY.md](docs/SAFETY.md):

* Nothing moves until you press Start.
* A hard speed cap (3.0 mph until you raise it) that no game state can
  exceed, and gentle, rate-limited speed changes.
* If the companion loses sight of the game it holds, then slows, then
  pauses the belt. It never speeds up on a lost signal.
* Stop the belt at the treadmill and Walka leaves it stopped.
* After any pause it did not watch you start, the belt waits for your
  character to move.
* A big STOP button on the dashboard.

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
   not on it. Start, change speed, pause, stop, press STOP. Stop it with
   the remote and check Walka leaves it stopped.
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
| Speed is one step off | Set *Settings → Treadmill → firmware units* to mph (or km/h) instead of auto. |
| History shows `~` numbers | `/reload` after the companion has saved the walk; check *Settings → Addon* shows the right folder. |
| HDR / odd colours | The strip is plain black and white and should survive HDR; if not, make it bigger. |

## Development

```
py -m pip install -e "companion[dev]" pytest-asyncio
py -m pytest          # companion tests + addon tests (the addon runs under a mocked game client)
```

* `Walka/` - the addon. `Core/` is pure Lua (no game API) and is unit
  tested; `Game.lua` is the only file that touches values the game may hide.
* `companion/src/walka/` - `governor.py` (the safety rules), `treadmill/`
  (Bluetooth FTMS driver and a simulator), `capture.py` + `beacon.py`
  (reading the strip), `session.py`, `addon_sync.py`, `web.py` + `static/`
  (dashboard), `app.py` (wiring), `cli.py`.
* `tests/addon/` - loads the real addon files into a mock of the game's
  API that is checked against Blizzard's published API documentation for
  this client build.
* `py -m walka --demo` runs everything against a simulated treadmill and a
  scripted character.

More: [docs/PROTOCOL.md](docs/PROTOCOL.md) ·
[docs/SAFETY.md](docs/SAFETY.md) · [docs/HARDWARE.md](docs/HARDWARE.md)
