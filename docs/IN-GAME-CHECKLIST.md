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
- [ ] After logging in, `/walka` prints what the addon is for and its
      commands, and `/walka status` prints a line. The addon draws no
      window of its own. No Lua error.
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
      chip, and the session goes on counting.

If it prints `false` in combat, even better - the estimate is simply never
used.

## 5. A session, without the treadmill

A session is how you tell Walka "I am walking now". Walka does not start,
stop or steer the belt: you set its speed at the treadmill.

- [ ] `/walka start`. A chat line says that the walk counts for this
      character; the first time in a login the game's two "Total time
      played" lines follow it (that is Walka asking, for the journey).
      `/walka status` says *session active*.
- [ ] `/walka pace 2.0`: a chat line confirms it. `/walka pace` alone says
      what it is.
- [ ] `/walka pause`, `/walka resume`, `/walka stop`: chat lines say so; a
      chat line summarises the session when it is stopped.
- [ ] `/reload` in the middle of a session: the session is still running
      afterwards.
- [ ] `/afk` during a session: a chat line says the session is paused.
      Move your character (which also ends AFK): it carries on.
- [ ] `/walka history`, `/walka journey`, `/walka map`, `/walka options`:
      each prints one line pointing to the Walka app - and opens nothing.

## 6. The game's AddOns menu

- [ ] Minimap addon compartment (the AddOns button by the minimap):
      *Walka* is in it. A click starts a session (chat line), another
      pauses it, another resumes it. A right-click stops it. Also in
      combat.

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
prints every answer in the chat; Walka therefore asks only when you start a
session, once a login.

- [ ] Log in and play for a minute without starting a session: the chat
      shows **no** "Total time played" lines.
- [ ] `/walka start`: the chat shows the game's two lines ("Total time
      played", "Time played this level") **once**, and the dashboard's
      *Share* page agrees with the first of them within about ten seconds.
- [ ] Stop and start the session again, several times: no further lines in
      the chat. `/reload` and start one: still none.
- [ ] Type `/played` yourself: the two lines appear as always - Walka has
      not broken the game's own command.

On the dashboard's *Share* page:

- [ ] It shows your character's name, level, time played, your steps,
      the character's steps, calories, time on the belt and walks.
- [ ] Run 100 yards in a straight line (a target dummy's range ring, or
      count seconds: 100 yards is about 14 seconds of running): the
      character steps go up by about 114 within a round or two (about
      eight seconds each). Mount up and ride: they do not move. Swim, take
      a flight path: they do not move.
- [ ] In combat the character steps still count, and nothing errors.
- [ ] Log in on a second character: its numbers start from zero; the first
      character's are unchanged when you go back.

And the companion:

- [ ] The dashboard's *Live* view says *Playing: <your character> · level
      <n>* within about ten seconds of logging in (the *Game signal* stays
      green).

## 8. The road

The addon records the road your character travels, for the picture on the
app's *Share* page. (It no longer draws it on the game's world map.)

- [ ] Walk a square, take a flight path, enter a dungeon and leave again:
      no Lua error. `/reload`.

And the road on the picture (the companion reads it from the file the game
writes at a `/reload` or logout):

- [ ] After that `/reload`, open the dashboard's *Share* page: under the
      tick boxes it says "My road: as the game last saved it (...)" with
      the time of the `/reload`, and the picture has a *My road* panel
      whose line has the shape of the way you went (north up).
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

## 9. With the treadmill

Walka only listens to the treadmill. Start, stop and speed are yours, at
the pad or with its remote - before, during and after a session.

- [ ] With the companion running and the treadmill connected, start the
      belt with the remote: the dashboard shows its speed and starts
      recording a walk. Nothing in the game or on the dashboard changed
      the belt.
- [ ] Stand on the pad, `/walka start`, set `/walka pace` to the speed on
      the treadmill's display: the dashboard's walk now belongs to the game
      session.
- [ ] `/walka pause`, `/walka resume`, `/walka stop`, going AFK, covering
      the game's top-left corner with another window, closing the game:
      **the belt does not react to any of them.** It runs at the speed you
      set until you stop it at the treadmill.
- [ ] Change the speed with the remote: the dashboard follows within a
      second or two.
- [ ] After the walk, `/reload`: the character's numbers on the *Share*
      page are the treadmill's measured ones, also if the pace you gave was
      off.

## 10. The floating window

- [ ] In the app, *Settings → Floating window → Show the floating window*,
      *Save*: a small dark Walka panel opens in the top-right corner of the
      screen.
- [ ] With the game in *Windowed (Fullscreen)* mode and in front, the
      panel stays on top of it. Also in *Windowed* mode.
- [ ] Click the panel, then press a movement key: the character moves
      (the click did not take the keyboard from the game). Tell me if it
      does not.
- [ ] Drag the panel somewhere else. Close Walka and start it again: the
      panel opens where you left it.
- [ ] Start the belt: the panel's speed follows it, *Walking* with a green
      dot, and the walk's steps, calories, distance and time count up.
      `/walka pause` in game: *Paused in game*. Stop the belt: *Today*'s
      totals, and the bar along the bottom fills towards the step goal.
- [ ] The panel's ×: it closes, and the setting in Settings is off.
- [ ] Close Walka's main window with the panel open: both go, and Walka
      ends (the walk is saved).
