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
- [ ] Or run `Walka.exe beacon` (`Walka beacon` on a Mac): it should print
      `OK ... cell=4.00px`. A
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

## 6. The windows

- [ ] HUD: it has a border and a dark fill; the play icon and the small
      target arrow are triangles, not rectangles; no text is clipped
      ("Paused (AFK)" fits beside the four buttons, in mph and in km/h).
- [ ] Right-click the HUD: a menu with *Lock position*, *Keep walking when
      standing*, *History*, *Journey*, *Road map*, *Options* and *Hide*. All of them
      work, also in combat (there *Road map* opens Walka's own road window,
      not the world map: section 8).
- [ ] Drag the HUD into a corner, then `/reload`: it is back in the same
      place.
- [ ] *Keep walking*: tick it (menu, options or `/walka keepwalking on`),
      run, then stand still - the target eases down to the walking pace and
      stays there.
- [ ] `/afk` during a session: "Paused (AFK)". Type something in chat: still
      paused. Move your character: it resumes.
- [ ] `/walka history`: opens centred; Escape and the X close it; the wheel
      scrolls the list. *Sync* reloads the interface and a running session
      is still running afterwards.
- [ ] Esc → Options → AddOns → Walka exists; `/walka options` opens it on
      that page; it scrolls; sliders, checkboxes and edit boxes work; the
      units switch converts every value.
- [ ] In combat, `/walka options` prints a "cannot be opened in combat"
      line and nothing else. After Walka has opened the settings window,
      change a keybinding in that same window - if "Interface action failed
      because of an AddOn" appears, tell me.
- [ ] Minimap addon compartment: left-click toggles the HUD, right-click
      opens the options.

## 7. The journey

Walka keeps a record for each character - level, time played, how far it
has travelled - next to the walking you did while playing it, and sends the
numbers to the companion for the picture on its *Share* page.

First, what the game says about your character. Compare with the character
pane:

```
/run print(UnitLevel("player"), GetMaxPlayerLevel(), select(3, UnitClass("player")), UnitFactionGroup("player"))
```

- [ ] It prints your level, the level cap (60), a class number and
      `Alliance` or `Horde`. If the cap is not 60, or anything is `nil`,
      tell me what it printed.

Time played. The game tells an addon the time played only when asked, and
prints every answer in the chat; Walka therefore asks only when you open the
Journey window.

- [ ] Log in and play for a minute without opening the Journey window: the
      chat shows **no** "Total time played" lines.
- [ ] `/walka journey` (or `/walka card`): the window opens in the middle of
      the screen. The chat shows the game's two lines ("Total time played",
      "Time played this level") **once**, and *TIME PLAYED* in the window
      agrees with the first of them.
- [ ] Close the window and open it again, several times: no further lines
      in the chat.
- [ ] `/reload`, open the window: still no new lines, and the time played is
      right (within a few seconds).
- [ ] Type `/played` yourself: the two lines appear as always - Walka has
      not broken the game's own command - and the window agrees.
- [ ] Log out and in again; open the window after ten minutes of play: one
      new pair of lines, and the figures agree.

The window:

- [ ] It shows your character's name, *LEVEL* as `17 / 60`, *TIME PLAYED*,
      *MY STEPS* with the distance walked, *CHARACTER STEPS* with in-game
      miles, *CALORIES*, *TIME ON THE BELT*, *WALKS* and "Totals through"
      with today's date. No text is clipped or overlaps, also with
      `/walka units kmh` (the distances switch to km).
- [ ] Run 100 yards in a straight line (a target dummy's range ring, or
      count seconds: 100 yards is about 14 seconds of running): *CHARACTER
      STEPS* goes up by about 114. Mount up and ride: it does not move.
      Swim, take a flight path: it does not move. Jump while running, run
      as a ghost: it moves.
- [ ] In combat the character steps still count, and nothing errors.
- [ ] Level up (or check after your next level): the window shows the new
      level without a reload.
- [ ] Walk a session of a minute: *MY STEPS* starts counting about twenty
      seconds in, with a `~`. After the walk, *Sync* in the history window:
      the `~` goes away once every walk of this character is measured.
- [ ] Escape and the X close the window. Drag it somewhere, `/reload`,
      open it: it is where you left it. It opens from the HUD's right-click
      menu and from the *Journey* button of the history window, also in
      combat.
- [ ] *Screenshot*: the game says "Screen Captured" and a picture of the
      whole screen is in `World of Warcraft\_forever_\Screenshots`.
- [ ] Log in on a second character: its window starts from zero; the first
      character's numbers are unchanged when you go back.

And the companion:

- [ ] With the Journey numbers on screen, the dashboard's *Share* page shows
      the same character, level and steps within about ten seconds, and the
      belt keeps following the character as before (the *Game signal* stays
      green).

## 8. The road map

The road recorder and the maps use game calls that could only be checked
against Blizzard's documentation and other addons' source, not in the
game. This section matters most.

- [ ] Run around outdoors for a minute, then `/walka mapcheck`. It should
      list two or more continents with "art 1002x668 ... 12 picture
      file(s)" (the numbers may differ), say which continent you are on,
      and count more than one point. If it says "0 continent map(s)" or
      "the recorder ran into: ...", copy the lines for me.

On the game's world map (first without OpenMap, if you can spare the
minute to switch it off; then with it):

- [ ] Open the world map as you always do. Your road is on the zone's map
      as a line through where you ran, ending at your arrow. **Take a
      screenshot.** If there is no line, `/walka mapcheck` and copy what
      it prints (the lines from "world map:" on).
- [ ] Zoom out to the continent and to the whole world: the line is in the
      same place on each (the right zone, not mirrored, not out at sea).
- [ ] Is the line **over** the map's picture everywhere - also over the
      places you have explored - and **under** the icons and your arrow?
      If it is hidden in places or covers icons, tell me the "frame level"
      number from `/walka mapcheck`. (With OpenMap, Questie's townsfolk
      icons and Zygor's waypoint sit at the road's own level: either may
      be on top there.)
- [ ] Zoom the map with the wheel and drag it: the line stays the same
      width, follows without lagging behind, and nothing flickers.
- [ ] Walk with the map open: the line grows up to your arrow.
- [ ] The small *Walka road* button in the top-left corner of the map: is
      anything of the game's under it or over it? Click it: the road goes
      and comes back. Its tooltip explains the colours. On a dungeon's map
      the button is not there.
- [ ] `/walka map` opens the map. In a fight it says that it cannot, and
      opens Walka's own road window instead - and with the world map open
      during a fight, does the game print "interface action failed because
      of an addon" or blame Walka for a blocked action? (That would be the
      one serious problem here.)
- [ ] With OpenMap: all of the above on its map. Drag slowly across a zone
      border and stop: half a second later OpenMap swaps the map
      underneath - the road must stay where it is, with no jump (a blink
      of one frame is expected). Zoom far in and far out.
- [ ] With OpenMap: its menu has *My road (Walka)* under *On the map*; the
      switch hides and shows the road; star it, and the button on
      OpenMap's rail does the same.

And Walka's own road window (`/walka map window`), which is also what
`/walka map` falls back to if the world map cannot be found:

- [ ] The continent's map appears - the real picture, not black or green
      squares - with your road on it as a line and an arrow where you
      stand. **Take a screenshot** (the window has a button): only a
      picture shows whether the art loaded.
- [ ] Mouse wheel zooms towards the cursor, dragging moves the map, *Me*
      centres on your character, *Fit* shows the whole continent, `<` `>`
      change continent. The line stays about two pixels wide at every zoom.
- [ ] The window opens, moves and closes in combat; Escape closes it.

On either map:

- [ ] Walk a square: the corners are kept (not cut diagonally).
- [ ] Start a session and walk: that stretch is brighter than the rest,
      with a glow.
- [ ] Take a flight path: a thin pale line. Use your hearthstone or a
      portal: no line across the map between the two places.
- [ ] Enter a dungeon and leave again: nothing drawn for the time inside,
      no Lua error.
- [ ] `/reload`: the road is still there and carries on.

And the road on the picture (the companion reads it from the file the game
writes at a `/reload` or logout):

- [ ] After that `/reload`, open the dashboard's *Share* page: under the
      tick boxes it says "My road: as the game last saved it (...)" with
      the time of the `/reload`, and the picture has a *My road* panel
      whose line has the shape of the road on `/walka map` (north up).
      If it says "none found for this character yet" or "the game's folder
      was not found", tell me which - and whether this file exists:
      `World of Warcraft\_forever_\WTF\Account\<your account>\<realm>\<character>\SavedVariables\Walka.lua`
      (the folder names are the thing I could not check).
- [ ] **Does the road lie on the land?** The panel draws a low-poly map of
      the continent behind the road, and a gem marked *NOW* where your
      character is. The gem should sit where you really are - in your
      starting zone, on the right side of the right continent - and the
      road should run over land, not water. This is the one thing about
      the map I could not check without the game: if the road is out at
      sea, mirrored, or on the wrong continent, send me a screenshot of the
      picture together with what this prints (the continent's number, then
      how far north and how far west you are, in yards):
      `/run local m=C_Map.GetBestMapForUnit("player") local c,p=C_Map.GetWorldPosFromMapPos(m,C_Map.GetPlayerMapPosition(m,"player")) print(c,p.x,p.y)`
- [ ] Walk on, `/reload` again: within about a quarter of a minute the
      line on the Share page has grown.

## 9. With the treadmill (after the checks in the README)

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
