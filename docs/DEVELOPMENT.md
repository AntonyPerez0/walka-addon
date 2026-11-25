# Developing Walka

[← Back to the README](../README.md) · [Disclaimer](../DISCLAIMER.md) · [Safety](SAFETY.md)

## Development

[![Tests and builds](https://github.com/AntonyPerez0/walka-addon/actions/workflows/ci.yml/badge.svg)](https://github.com/AntonyPerez0/walka-addon/actions/workflows/ci.yml)

```powershell
py -m pip install -e "companion[dev]"
py -m pytest          # companion tests + addon tests (the addon runs under a mocked game client)
```

(That takes the newest versions of the libraries. The build and its tests
take pinned ones: `py -m pip install --require-hashes -r
companion/requirements-dev.lock`, and no install of Walka itself.)

(`python3` in place of `py` on a Mac or Linux.)

* `Walka/` - the addon. `Core/` is pure Lua (no game API) and is unit
  tested; `Game.lua` is the only file that touches values the game may hide.
* `companion/src/walka/` - `treadmill/` (the Bluetooth FTMS reader, which
  has no way to write to a treadmill, and a simulator), `ftms.py` (decoding
  what a treadmill says), `capture.py` + `beacon.py`
  (reading the strip), `session.py` (recording walks), `addon_sync.py`, `web.py` + `static/`
  (dashboard), `walka_account/` (signing in to a Walka account and sending
  walks to it - the only part that asks anything over the internet), `window.py` (Walka's own window, which shows the dashboard), `app.py` (wiring), `cli.py`, `macos.py` (what the companion
  asks macOS: where the game window is, whether it may see the screen),
  `winsession.py` (hearing that Windows is shutting down, to save the walk); `journey.py` (each character's
  journey, put together from the strip), `savedvars.py` + `road.py` (the
  road, read from the game's saved file), `sketch.py` (the ground of a land
  there is no map of, drawn from the road) and `static/share.js`, `card.js`,
  `cardmodel.js` (the Share page and the picture it draws), `lands.js` (the
  low-poly continents behind the road; made by `tools/gen_lands.py`).
* `tests/addon/` - loads the real addon files into a mock of the game's
  API that is checked against Blizzard's published API documentation for
  this client build (`tools/gen_api_snapshot.py` regenerates the snapshot).
* `docs/demo/index.html` - the 3D demo at the top of the README: a walking
  pad under a standing desk, drawn in three.js (no models or pictures are
  loaded; it fetches three.js from a CDN). `node tools/record_demo.cjs
  docs/demo/walka-demo.mp4` records it with Playwright's Chromium and
  ffmpeg, frame by frame, and `--frames 3,12` writes stills only. The
  README's moving picture is made from that video:
  `ffmpeg -i docs/demo/walka-demo.mp4 -vf "fps=12,scale=720:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=160[p];[b][p]paletteuse=dither=sierra2_4a" docs/images/demo.gif`.
* `py -m walka --demo` runs everything against a simulated treadmill, a
  made-up walker who works its remote, and a scripted character.
* **The one rule for changes:** the companion listens to the treadmill and
  never writes to it. `companion/tests/test_ftms_ble.py` and `test_app.py`
  fail if a way to do so comes back.
* **Publishing a version:** put the same number in
  `companion/pyproject.toml`, `companion/src/walka/__init__.py` and
  `Walka/Walka.toc`, commit and push. Then, on GitHub, open **Actions** →
  **CI** → **Run workflow**, type the number (`0.1.0`) and run it. (Pushing
  a tag does the same: `git tag v0.1.0`, `git push origin v0.1.0`.) When
  the run finishes, the **Releases** page has `Walka.exe`, the two Mac
  zips (the download buttons at the top of this page always give the
  latest ones) and `Walka-addon.zip`, which you upload to CurseForge
  yourself. If the run
  fails nothing is published: fix it and run it again.
* **What a release is built from** is fixed in the repository: the
  packages in `companion/requirements-build.lock` and
  `requirements-dev.lock` (exact versions with checksums; the top of each
  file says how to write them again after changing `pyproject.toml`, and a
  test fails if they fall behind it), and the GitHub actions in
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
  published next to `Walka.exe` and is what the badge at the top of this
  page shows. Nothing to set up. The green *Scanned for viruses* picture
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
