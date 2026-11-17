# In-game checklist

The addon was written against Blizzard's published UI source for WoW:
Forever 1.60.1 and tested under a mock of the game client - never in the
game itself. These checks take about ten minutes and cover what only the
real game can confirm. If something fails, the result (and any Lua error
text) is exactly what is needed to fix it.

Tip: `/console scriptErrors 1` makes Lua errors pop up instead of hiding.

## 1. It loads

- [ ] *Walka* appears in the AddOns list at character select and is not
      marked out of date.
- [ ] After logging in, `/walka` shows the HUD and `/walka status` prints a
      line. No Lua error.
- [ ] Top-left corner of the screen: a thin strip of tiny black and white
      squares. Some of them flicker.

## 2. The companion can read it

- [ ] With the companion running, the dashboard's *Game signal* turns green
      within a few seconds.
- [ ] Or run `Walka.exe beacon`: it should print `OK ... cell=4.00px`. A
      cell size that is not a whole number means the strip is being scaled
      (it still works; tell me the number).
- [ ] Press Alt+Z (hide the UI). The strip should stay. Press Alt+Z again.
- [ ] Try *Windowed* and *Fullscreen (Windowed)* display modes.

## 3. Speed readings

Run each line and compare with the expected number:

```
/run print(GetUnitSpeed("player"))
```

- [ ] Standing: `0 7 ...` (second number is your run speed).
- [ ] Running: first number `7`. Walking (toggle with `/` on the numpad):
      `2.5`.
- [ ] Mounted, swimming, as a ghost, on a flight path: note the numbers.
      `/walka status` should show the same character speed.

## 4. The combat question

The game's API documentation says speed is hidden from addons when "unit
stats are restricted", which includes combat. Confirm it:

```
/run print(issecretvalue((GetUnitSpeed("player"))), C_Secrets.ShouldUnitStatsBeSecret())
```

- [ ] Out of combat: `false false`.
- [ ] In combat (hit a target dummy or a mob): probably `true true`.
- [ ] You can also force it without fighting:
      `/console addonCombatRestrictionsForced 1` (and `0` to undo).
- [ ] While it is `true`: no Lua errors, `/walka status` says
      *(estimated: hidden in combat)*, the dashboard shows an `ESTIMATED`
      chip, and the belt target still follows whether you are moving.

If it prints `false` in combat, even better - the estimate is simply never
used.

## 5. A session, without the treadmill

- [ ] `/walka start`. The HUD shows *Walking*; run around: the big speed
      rises towards your running pace over a few seconds, steps and
      calories count up.
- [ ] Stand still: after a few seconds the speed eases down to the standing
      pace.
- [ ] Pause, resume, stop from the HUD buttons. A chat line summarises the
      session.
- [ ] `/reload` in the middle of a session: the session is still running
      afterwards.
- [ ] `/walka history` lists the session; `/walka options` opens the
      settings page; sliders and checkboxes work.

## 6. With the treadmill (after the checks in the README)

- [ ] Stand on the pad, `/walka start`: countdown, belt starts at minimum
      speed, then follows.
- [ ] Alt-tab to another window that covers the game's top-left corner:
      within about 8 seconds the belt slows to the safe speed. Alt-tab
      back: it follows again.
- [ ] Press stop on the remote: the belt stays stopped; the dashboard says
      *stopped at the treadmill*. Pause then resume on the HUD restarts it.
- [ ] `/walka stop`: the belt stops.
- [ ] After the walk, `/reload`: the session in `/walka history` now shows
      measured numbers (no `~`).
