# Developing Walka

[← Back to the README](../README.md) · [Disclaimer](../DISCLAIMER.md) · [Safety](SAFETY.md)

## Development

[![Tests and builds](https://github.com/AntonyPerez0/walka-addon/actions/workflows/ci.yml/badge.svg)](https://github.com/AntonyPerez0/walka-addon/actions/workflows/ci.yml)

```powershell
py -m pip install -e "companion[dev]"
py -m pytest          # companion tests + addon tests (the addon runs under a mocked game client)
```

(Python 3.14 or newer: what the build and its tests run on, and what the
programs people download carry. That takes the newest versions of the
libraries. The build and its tests take pinned ones: `py -m pip install
--require-hashes -r companion/requirements-dev.lock`, and no install of
Walka itself.)

(`python3` in place of `py` on a Mac or Linux.)

* `Walka/` - the addon. `Core/` is pure Lua (no game API) and is unit
  tested; `Game.lua` is the only file that touches values the game may hide.
* `companion/src/walka/` - `treadmill/` (the Bluetooth FTMS reader, which
  has no way to write to a treadmill; the readers of makers' own
  protocols, two of which send one fixed status query through
  `query_link.py`; and a simulator), `ftms.py` (decoding
  what a treadmill says), `game_file.py` (the addon's saved file
  `WalkaDB`: the game's walking sessions, and filing walks under them),
  `session.py` (recording walks), `addon_sync.py` (`CompanionData.lua`,
  and finding and installing the addon), `web.py` + `static/`
  (dashboard), `walka_account/` (signing in to a Walka account and sending
  walks to it), `updates.py` (asking GitHub for the newest release, and
  "Update now") - the two parts that ask anything over the internet, `window.py` (Walka's own window, which shows the dashboard), `app.py` (wiring), `cli.py`,
  `winsession.py` (hearing that Windows is shutting down, to save the walk); `journey.py` (each character's
  journey, put together from the game's saved file), `savedvars.py` (the
  reader of the game's saved files, which runs no Lua) + `road.py` (the
  road, read from the per-character saved file), `sketch.py` (the ground of a land
  there is no map of, drawn from the road) and `static/share.js`, `card.js`,
  `cardmodel.js` (the Share page and the picture it draws), `lands.js` (the
  low-poly continents behind the road; made by `tools/gen_lands.py`).
* `tests/addon/` - loads the real addon files into a mock of the game's
  API that is checked against Blizzard's published API documentation for
  this client build (`tools/gen_api_snapshot.py` regenerates the snapshot).
* `docs/demo/index.html` - the 3D demo at the top of the README: a walking
  pad under a standing desk, drawn in three.js (no models or pictures are
  loaded - the room, its textures, the person and the game are code; it
  fetches three.js from a CDN). `?fast` leaves the post-processing out.
  `node tools/record_demo.cjs docs/demo/walka-demo.mp4` records it with
  Playwright's Chromium and ffmpeg, frame by frame: each frame is the
  average of six renders with the lamp, the lens and the moment nudged
  (soft shadows, depth of field, motion blur), which takes about an hour
  and a quarter on a machine without a graphics card. `--frames 3,12`
  writes stills only, `--samples 2` is quicker and rougher. The README's
  moving picture is made from that video (an animated WebP, a fifth the
  size of a GIF):
  `ffmpeg -i docs/demo/walka-demo.mp4 -vf "fps=12,scale=800:-1:flags=lanczos" -c:v libwebp -quality 55 -compression_level 6 -loop 0 docs/images/demo.webp`.
* `py -m walka --demo` runs everything against a simulated treadmill, a
  made-up walker who works its remote, and a made-up game that "saves its
  file" every few seconds, with one walking session and a character.
  `py -m walka game` prints what the real game has saved for Walka.
* **The one rule for changes:** the companion listens to the treadmill and
  sends it nothing that could start, stop or steer a belt. The only bytes
  it ever writes are the two fixed status queries in
  `treadmill/proprietary.py` (`QUERIES`), for pads that report only when
  asked. `companion/tests/test_reader_writes.py`, `test_vendor_queries.py`,
  `test_ftms_ble.py` and `test_app.py` fail if anything else is written.
* **Publishing a version:** put the same number in
  `companion/pyproject.toml`, `companion/src/walka/__init__.py` and
  `Walka/Walka.toc`, commit and push. Then, on GitHub, open **Actions** →
  **CI** → **Run workflow**, type the number (`0.1.0`) and run it. (Pushing
  a tag does the same: `git tag v0.1.0`, `git push origin v0.1.0`. Only a
  commit that is on the default branch is published: a tag on any other
  commit is refused.) The publishing job runs in the GitHub environment
  `release`: a reviewer added to it under *Settings → Environments* has to
  approve each release before anything is published. When
  the run finishes, the **Releases** page has `Walka.exe`, the two Mac
  zips (the download buttons at the top of the README always give the
  latest ones) and `Walka-addon.zip`, which you upload to CurseForge
  yourself. If the run
  fails nothing is published: fix it and run it again. Once it is
  published, every Walka that is running shows *Walka 0.x.y is out* with
  an **Update now** button within a day (at once when it is next opened):
  the update takes `Walka.exe` or the Mac zip from the release and checks
  it against `SHA256SUMS.txt` (`companion/src/walka/updates.py`). So keep
  those file names as they are. A trial (`v0.2.0-rc1`) is never offered;
  a Walka that is itself a trial is offered its final release.
* **What a release is built from** is fixed in the repository: the
  packages in `companion/requirements-build.lock` and
  `requirements-dev.lock` (exact versions with checksums; the top of each
  file says how to write them again after changing `pyproject.toml`; a
  test fails if they fall behind it, and the build writes them again with
  those commands and fails if anything comes out different: `tools/check_locks.py`), and the GitHub actions in
  `.github/workflows/ci.yml` by commit (Dependabot proposes newer ones).
  `companion/third_party.py` writes the licences of the packed libraries
  into the program at build time. A release publishes `SHA256SUMS.txt`
  and a build attestation with the files, and a release that is already
  published is refused, not overwritten. On GitHub, *Settings → General →
  Releases → Enable release immutability* makes GitHub enforce that too.
* **The virus scan** is a job of the build
  (`.github/workflows/ci.yml`), on a Windows machine: Microsoft Defender
  scans the files of the release - `Walka.exe`, the Mac programs, the
  addon - after showing that it catches the standard test file. A
  release needs that job, and it writes `virus-scan.json`, which is
  published next to `Walka.exe` and is what the badge at the top of the
  README shows. Nothing to set up. The green *Scanned for viruses* picture
  above the badge is a fixed picture (`docs/images/virus-scan.svg`). It
  says what is done, not what a scan can never prove: that a file is
  harmless.

More: [docs/PROTOCOL.md](PROTOCOL.md) ·
[docs/SAFETY.md](SAFETY.md) · [docs/HARDWARE.md](HARDWARE.md)

---

<p align="center">
  <sub>Walka is a fan-made tool. It is not affiliated with or endorsed by Blizzard Entertainment, UREVO, WELLFIT or any other maker of treadmills.<br>
  It is provided as is, with no warranty, and you use it at your own risk: see <a href="../DISCLAIMER.md">DISCLAIMER.md</a>. Published under the <a href="../LICENSE">MIT licence</a>.</sub>
</p>

## Ideas for later

* A foot pod or other Bluetooth cadence sensor for true step counts.
* Streak rewards in the floating window; milestones announced in chat.
* Per-game plugins for other games (walks are already recorded with any
  game, or none; a plugin would say which character they are for).
* Reading daily totals back from Garmin's personal API.
