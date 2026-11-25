"""Tests for the Walka addon, run outside the game.

Nobody can start World of Warcraft: Forever in CI, so these tests load the
addon's Lua files into a mock client (``lua/wow_env.lua`` driven by
``harness.py``) under Lua 5.1 - the game's Lua version - and exercise it
through the same entry points the game uses: file load order from the TOC,
ADDON_LOADED / PLAYER_LOGOUT, OnUpdate, slash commands, widget scripts.

The mock is deliberately strict. What exists is decided by a snapshot of
Blizzard's own interface code for client 1.60.1.70959
(``data/forever_api.json.gz``, made by ``tools/gen_api_snapshot.py``), not by
anyone's memory of the API.

What a green run proves
-----------------------
* Every file parses and runs under Lua 5.1 in TOC order, with the
  SavedVariables arriving where the game delivers them (after the files
  have run, before ADDON_LOADED) and surviving the client's serialiser.
* Every global the addon reads exists in this client (documented API,
  Blizzard Lua/XML, or a verified C global) - or is on a short allowlist -
  and the addon creates no globals beyond its own.
* Every widget method it calls exists on that widget type in this client;
  script handlers, events, templates, font objects, frame strata, draw
  layers and anchor points are valid; frames are not anchored in cycles.
* The addon draws nothing on the screen. What the companion needs - the
  sessions and the journeys - is in the saved variables the game writes at
  logout and on /reload, and the companion's own reader reads it from the
  file as the game writes it (test_saved_file.py).
* With unit stats "secret" (Midnight combat rules) nothing errors and no
  secret value ends up in the saved variables, the session, the addon's
  tables, its widgets or the upvalues of its functions.
* The logic: speed model, calorie/step estimates, session
  lifecycle, history merge and totals, slash commands.
* A character's journey: distance on foot and in all (walking, riding,
  flying, swimming, as a ghost, in combat with the speed hidden), level-up,
  the bookkeeping of /played, and the real-world totals - which include the
  session in progress, count no walk twice and do not shrink when the
  history is pruned or the companion's file drops old walks
  (test_journey.py). At logout they are saved with the journey for the
  companion (test_saved_file.py).
* The addon has no windows of its own (the app shows everything): every
  file in the TOC loads, the session is worked from slash commands and the
  game's AddOns menu, and the commands of the windows it once had point to
  the app (test_harness.py, test_slash.py). Starting a session asks the
  game for the time played, once per login (test_journey.py).
* Nothing a data file can contain breaks the addon: the companion's file
  and the saved variables are fed in damaged in many ways. That includes
  timestamps the game cannot format - the mock's date() and time() have the
  limits of the Windows C runtime (nil before 1970 and after the year
  3000), because the game runs on Windows and the tests do not.

How to run
----------
``python -m pytest`` from the repository root (needs ``lupa`` and
``pytest``; the companion's ``dev`` extra installs both). To regenerate the
API snapshot from a copy of Blizzard's interface source::

    python tools/gen_api_snapshot.py /path/to/Interface/AddOns

and with ``WALKA_UI_SOURCE`` set to that folder the test-suite checks that
the committed snapshot is exactly what the generator produces.

What it cannot prove
--------------------
* Anything visual: fonts, textures, colours, layout quality, whether a
  frame is where a person would want it. Sizes of text are a crude guess.
* Real engine behaviour that the mock only approximates: frame-level
  ordering, clipping, pixel snapping, the exact timing of OnUpdate, taint
  and protected-action blocking (only Settings.OpenToCategory in combat is
  modelled), and what Blizzard's own Lua does inside templates and mixins
  (those are stubs unless ``wow_env.lua`` gives them behaviour). The
  snapshot has no sizes either: a frame made from a template is 0 x 0 until
  the addon sizes it, except for the templates listed with their ``<Size>``
  in ``wow_env.lua`` (the close button, which the history window's Sync
  button is placed against).
* A reload asked for by the addon: ``ReloadUI()`` is only recorded
  (``rt.reload_requests``). What a reload does to the addon is tested with
  ``rt.reload()``. ``Screenshot()`` is only recorded, too.
* What the game's chat window really does with the time played. The mock
  answers ``RequestTimePlayed()`` a frame later with ``TIME_PLAYED_MSG`` and
  prints the two lines Blizzard's chat frame prints for every answer, so
  the tests can count them; that the addon leaves the chat frames alone is
  a property of its source (it never touches them), not something the mock
  enforces.
* What the client reports for a real character: the mock's ``UnitLevel``,
  ``GetMaxPlayerLevel``, ``UnitClass`` and ``UnitFactionGroup`` answer from
  the test's game state.
* Secret-value rules that Lua 5.1 cannot observe from the outside. In the
  game, comparing a secret with ``==``/``~=``, using it as a table key and
  testing its truthiness (``if secret then``, ``secret and x``, ``not
  secret``) all raise an error. Here a secret is a userdata: ``==`` against
  an ordinary value is just false, it is always truthy, and it works as a
  key. Arithmetic, ordering, concatenation, length, indexing, calling,
  ``tostring`` and passing it to a widget or library function do raise. So
  code that only *branches* on a secret is not caught by running it.
  ``test_static.py`` covers the careless cases by reading the source: only
  Walka/Game.lua may call an API that can return secrets, and it may not
  test or combine such a result before issecretvalue() has cleared it.
* That the snapshot is right. It is built from Blizzard's published UI
  source: documented functions are exact, widget method lists are exact for
  documented widget types (GameTooltip's own methods are inferred from
  Blizzard's usage), and "defined by Blizzard Lua/XML" is a regular-
  expression scan of the files the TOCs load for this game type.
* The treadmill, Bluetooth, screen capture or the companion app; those are
  covered by companion/tests.

So: a green run means "no Lua error and correct logic under a faithful
model of the API surface". It does not replace the in-game checklist in
docs/IN-GAME-CHECKLIST.md.
"""
