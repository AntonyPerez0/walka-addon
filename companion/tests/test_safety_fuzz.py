"""A bounded, deterministic fuzz of the whole belt path.

The app, the governor and a simulated pad run on fake clocks while random
things happen: sessions start and stop in the game, the picture comes and
goes, the walker uses the pad's own buttons and its remote, STOP is pressed,
the dashboard is used, the cap is changed - and the Bluetooth link drops,
fails commands or silently loses them. The physical pad keeps running when
the link drops, as real pads do; the app only ever sees it through the link.

After every tick the commands that went out are checked against the rules
of docs/SAFETY.md, using a small reference model that knows what the walker
and the game really did. Seeds are fixed, so a failure is reproducible: the
message names the seed and shows the last events before it.
"""

from __future__ import annotations

import asyncio
import collections
import random
from pathlib import Path

import pytest

from walka.app import TICK_S, App
from walka.config import TERMS_VERSION, Config
from walka.protocol import Flags, Frame, kmh_to_raw, yps_to_raw
from walka.sources import BeaconSource
from walka.treadmill import BeltState, Treadmill, TreadmillError, TreadmillEvent
from walka.treadmill.simulated import SimulatedTreadmill

TICKS = 6000  # twenty simulated minutes per run
SEEDS = range(4)
# Pads report their speed cut off to 0.01 km/h; the governor allows for it.
REPORT_SLACK_KMH = 0.02
# How long the belt may take to stand still once it has to: the settle after a
# reconnect, plus two repeats of a stop that got lost.
OFF_WITHIN_S = 25.0

MODES = {
    "clean": dict(faults=False, links=False),
    "lost-and-failed-commands": dict(links=False),
    "link-drops": dict(faults=False),
    "everything": dict(),
    "a-link-that-is-dying": dict(dying=True),
    "remote-and-garbled-data": dict(remote=0.004, glitch=0.003),
    "remote-on-a-pad-that-does-not-tell": dict(remote=0.004, glitch=0.003, tells_target=False, links=False),
}


class Clock:
    def __init__(self, t: float) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


class LossyLink(Treadmill):
    """The driver's view of a physical pad that keeps running when the link
    drops, and whose commands can fail or silently get lost on the way."""

    kind = "proxy"

    def __init__(self, phys: SimulatedTreadmill, clock: Clock, note, *, tells_target: bool) -> None:
        super().__init__()
        self.phys, self.clock, self.note = phys, clock, note
        self.tells_target = tells_target  # does the pad say when its speed is set at the pad?
        self.link = False
        self.fail = False  # commands raise (definitely not applied)
        self.lose = False  # commands are silently lost
        self.last_ok = False
        self.sent: list[tuple[str, float | None]] = []
        phys.on_event = self._phys_event
        mine, theirs = self.status, phys.status
        mine.name, mine.address = "pad", "SIM"
        mine.min_kmh, mine.max_kmh, mine.step_kmh = theirs.min_kmh, theirs.max_kmh, theirs.step_kmh
        mine.mph_grid = theirs.mph_grid

    def _phys_event(self, event: TreadmillEvent) -> None:
        if self.link and event in (TreadmillEvent.EXTERNAL_STOP, TreadmillEvent.EXTERNAL_START):
            self._emit(event)

    def sync(self) -> None:
        """A data packet arrives."""
        if not self.link:
            return
        mine, theirs = self.status, self.phys.status
        mine.speed_kmh, mine.state = theirs.speed_kmh, theirs.state
        mine.distance_m, mine.steps, mine.elapsed_s = theirs.distance_m, theirs.steps, theirs.elapsed_s
        if self.tells_target:
            mine.target_kmh = theirs.target_kmh
        mine.updated_at = self.clock()

    async def connect(self) -> None:
        if self.link:
            return
        self.link = True
        status = self.status
        status.connected, status.state, status.speed_kmh, status.target_kmh = True, BeltState.UNKNOWN, 0.0, None
        status.updated_at = self.clock()
        self._emit(TreadmillEvent.CONNECTED)

    async def disconnect(self) -> None:
        self.drop()

    def drop(self) -> None:
        if not self.link:
            return
        self.link = False
        status = self.status
        status.connected, status.state, status.speed_kmh, status.target_kmh = False, BeltState.UNKNOWN, 0.0, None
        self._emit(TreadmillEvent.DISCONNECTED)

    async def _command(self, name: str, *args: float) -> None:
        self.sent.append((name, args[0] if args else None))
        self.note(f"-> {name} {args[0]:.3f}" if args else f"-> {name}")
        self.last_ok = False
        if not self.link:
            raise TreadmillError("not connected")
        if self.fail:
            raise TreadmillError("write failed")
        if self.lose:
            return
        await getattr(self.phys, name)(*args)
        if name == "set_speed" and not self.tells_target:
            self.status.target_kmh = self.phys.status.target_kmh  # what the driver believes it set
        self.last_ok = True

    async def start(self) -> None:
        await self._command("start")

    async def pause(self) -> None:
        await self._command("pause")

    async def stop(self) -> None:
        await self._command("stop")

    async def set_speed(self, kmh: float) -> None:
        await self._command("set_speed", kmh)


class Fuzz:
    def __init__(
        self,
        seed: int,
        data_dir: Path,
        *,
        faults: bool = True,
        links: bool = True,
        remote: float = 0.0,
        glitch: float = 0.0,
        tells_target: bool = True,
        dying: bool = False,
    ) -> None:
        self.rng = random.Random(seed)
        self.mono, self.wall = Clock(1000.0), Clock(1.7e9)
        self.trace: collections.deque[str] = collections.deque(maxlen=40)
        self.config = Config()
        # The walker has accepted the disclaimer (the time before that: test_terms.py).
        self.config.terms.accepted_version = TERMS_VERSION
        self.phys = SimulatedTreadmill(us_firmware=seed % 2 == 0, clock=self.mono, auto_run=False)
        self.phys.status.connected = True  # the pad itself is always switched on
        self.phys.status.state = BeltState.STOPPED
        self.pad = LossyLink(self.phys, self.mono, self.note, tells_target=tells_target)
        self.beacon = BeaconSource(None, clock=self.mono)
        self.app = App(
            self.config, data_dir, treadmill=self.pad, beacon=self.beacon, wall_clock=self.wall, mono_clock=self.mono
        )
        self.faults, self.links, self.remote, self.glitch = faults, links, remote, glitch
        self.dying = dying  # a link that loses commands is about to drop altogether
        self.seq = 0
        self.game = dict(visible=True, active=False, paused=False, afk=False, moving=False, target=2.0, session=0)
        # ---- the reference model: what the walker and the game really did ----
        self.latched = ""  # why the belt must stay off until a deliberate resume
        self.remote_run = False  # the belt was started with the pad's own button
        self.last_speed = None  # the last speed command known to have reached the pad
        # A speed set with the remote, far below our command: [speed, the
        # belt speed below which the governor can tell, packets seen below it].
        self.pressed: list | None = None
        self.ceiling = None  # ... which is binding once the governor can know of it
        self.off_since = None
        self.clean_since: float | None = self.mono.t
        self.violations: list[str] = []

    def note(self, text: str) -> None:
        self.trace.append(f"{self.mono.t - 1000:8.1f}  {text}")

    def violate(self, text: str) -> None:
        self.violations.append(text)

    def lift(self) -> None:
        """A deliberate act: the stop latch and the walker's ceiling go."""
        self.latched = ""
        self.pressed = self.ceiling = None

    # ---- random events -----------------------------------------------------------------
    def random_events(self) -> bool:
        """Returns whether STOP was pressed in this tick."""
        rng, game, app = self.rng, self.game, self.app
        chance = rng.random
        stop_pressed = False
        if chance() < 0.004:
            game["visible"] = not game["visible"]
            self.note(f"game visible: {game['visible']}")
        if chance() < 0.004:
            if game["active"]:
                game["active"] = game["paused"] = False
                self.note("game: session stopped")
            else:
                game["active"], game["paused"] = True, False
                game["session"] = game["session"] % 255 + 1
                self.note(f"game: session {game['session']} started")
        if game["active"] and chance() < 0.004:
            if game["paused"]:
                game["paused"] = game["afk"] = False
                self.note("game: resumed")
            else:
                game["paused"], game["afk"] = True, chance() < 0.5
                self.note(f"game: paused (afk: {game['afk']})")
        if chance() < 0.02:
            game["moving"] = not game["moving"]
        if chance() < 0.03:
            game["target"] = rng.choice([0.0, 0.5, 1.3, 2.0, 3.0, 4.0, 4.8, 6.0, 9.0, 40.95])
        if chance() < 0.002:
            was = self.phys.status.state
            self.phys.press_stop()
            self.note(f"pad: stop pressed (was {was.value})")
            if was is not BeltState.STOPPED and self.pad.link and (app.governor.driving or app.governor.belt_on):
                self.latched = "stopped at the pad"
            self.remote_run = False
            self.pressed = self.ceiling = None
        if chance() < 0.001:
            was = self.phys.status.state
            self.phys.press_start()
            self.note("pad: start pressed")
            if was not in (BeltState.RUNNING, BeltState.STARTING):
                # (Also with the link down, when the app cannot know: the
                # walker has restarted the belt, it is theirs.)
                self.remote_run = True
                self.lift()
        if self.remote and self.phys.status.state is BeltState.RUNNING and chance() < self.remote:
            self.press_remote(rng.choice([self.phys.status.min_kmh, 2.0, 3.0, 4.0]))
        if chance() < 0.002:
            self.note("STOP pressed")
            stop_pressed = True
        if chance() < 0.002:
            if app.manual.active:
                app.manual_stop()
                self.note("dashboard: manual session ended")
            else:
                app.manual_start(rng.choice([1.0, 2.0, 3.0, 5.0]))
                self.note("dashboard: manual session started")
            self.lift()
        if app.manual.active and chance() < 0.003:
            if app.manual.paused:
                app.manual_resume()
                self.note("dashboard: manual session resumed")
                self.lift()
            else:
                app.manual_pause()
                self.note("dashboard: manual session paused")
        if chance() < 0.001:
            app.resume()
            self.note("dashboard: Resume")
            self.lift()
        if chance() < 0.002:
            cap = rng.choice([0.8, 2.0, 3.0, 4.83, 6.5])
            app.update_config({"limits": {"max_speed_kmh": cap}})
            self.note(f"cap set to {cap}")
        faulty = self.pad.fail or self.pad.lose
        if self.links and self.pad.link and chance() < (0.02 if self.dying and faulty else 0.0015):
            self.pad.drop()
            self.note("LINK down")
            self.pressed = self.ceiling = None
            if self.dying:
                self.pad.fail = self.pad.lose = False  # the new connection works
        if self.faults and chance() < (0.006 if self.dying and not faulty else 0.002):
            self.pad.fail = not self.pad.fail
            self.note(f"commands fail: {self.pad.fail}")
        if self.faults and chance() < (0.003 if self.dying and not faulty else 0.001):
            self.pad.lose = not self.pad.lose
            self.note(f"commands get lost: {self.pad.lose}")
        return stop_pressed

    def press_remote(self, kmh: float) -> None:
        """The walker sets a speed with the pad's remote."""
        before = self.phys.status.target_kmh
        self.phys.press_speed(kmh)
        now = self.phys.status.target_kmh
        self.note(f"pad: speed set to {now:.2f} with the remote")
        status = self.pad.status
        limits = self.config.limits
        far = max(limits.accel_kmh_per_s * limits.command_interval_s, status.step_kmh) + status.step_kmh
        ours = self.app.governor.belt_on and self.last_speed is not None
        if ours and now < self.last_speed - far:
            # The governor can tell at once if the pad says so; otherwise
            # only when the belt has slowed by more than a lost command of
            # ours could explain.
            self.pressed = [now, self.last_speed - far, 0]
        elif before is not None and now > before:
            self.pressed = self.ceiling = None  # raised: the ceiling moves, or goes
        self.last_speed = None  # from here on the pad is not at the speed we set

    def frame(self) -> None:
        game = self.game
        if not game["visible"]:
            return
        self.seq = (self.seq + 1) % 256
        flags = Flags(0)
        if game["active"]:
            flags |= Flags.ACTIVE
            if game["paused"]:
                flags |= Flags.PAUSED
        if game["moving"]:
            flags |= Flags.MOVING
        if game["afk"] and game["paused"]:
            flags |= Flags.AFK
        target = 0.0 if (not game["active"] or game["paused"]) else game["target"]
        self.beacon.inject(
            Frame(
                seq=self.seq,
                session=game["session"] if game["active"] else 0,
                flags=flags,
                target_raw=kmh_to_raw(target),
                char_speed_raw=yps_to_raw(7.0 if game["moving"] else 0.0),
            ),
            self.mono.t,
        )

    # ---- one tick -------------------------------------------------------------------------
    async def tick(self) -> None:
        self.mono.t += TICK_S
        self.wall.t += TICK_S
        self.phys.status.connected = True
        self.phys.advance(TICK_S)
        stop_pressed = self.random_events()
        self.pad.sync()
        if self.glitch and self.pad.link and self.rng.random() < self.glitch:
            self.pad.status.speed_kmh = self.rng.choice([0.0, 12.0])
            self.note(f"data packet garbled: speed reads {self.pad.status.speed_kmh}")
        self.frame()
        app, governor = self.app, self.app.governor
        if self.links and not self.pad.link and app.connection_wanted() and self.rng.random() < 0.05:
            await self.pad.connect()
            self.note("LINK up")
        mark = len(self.pad.sent)
        if stop_pressed:
            await app.emergency_stop()
            self.latched = "STOP pressed"
            self.pressed = self.ceiling = None
        phys_speed = self.phys.status.speed_kmh
        session_before = governor._session
        await app.tick()
        self.check(mark, phys_speed, stop_pressed, session_before)

    # ---- the rules ---------------------------------------------------------------------------
    def check(self, mark: int, phys_speed: float, stop_pressed: bool, session_before: int | None) -> None:
        app, governor = self.app, self.app.governor
        reading = app._last_reading
        limits = self.config.limits
        status = self.pad.status
        fresh = reading.age_s <= limits.signal_timeout_s
        recent = reading.age_s <= 1.0
        # What lifts a stop on the game side (and with it the walker's ceiling).
        if fresh and (not reading.active or (reading.paused and not reading.afk)):
            self.lift()
        if governor._session != session_before or not governor.driving:
            self.pressed = self.ceiling = None  # another session, or none: a new start
        top = min(limits.max_speed_kmh, status.max_kmh)
        up = max(limits.accel_kmh_per_s * limits.command_interval_s, status.step_kmh)
        down = max(limits.decel_kmh_per_s * limits.command_interval_s, status.step_kmh)
        for name, speed in self.pad.sent[mark:]:
            if stop_pressed and name == "stop":
                continue
            if name == "start":
                self.last_speed = None
                if not (recent and reading.active and not reading.paused):
                    self.violate(f"rule 6: START without a live, unpaused session in view (reading: {reading})")
                if self.latched:
                    self.violate(f"rules 5/9: START although the belt was {self.latched}")
                if reading.target_kmh <= 0:
                    self.violate("START with nothing asked for")
            elif name == "set_speed":
                assert speed is not None
                base = self.last_speed if self.last_speed is not None else max(phys_speed, status.min_kmh)
                if speed > top + 1e-6 and speed > base - 1e-6:
                    self.violate(f"rule 2: SET_SPEED {speed:.3f} above the cap {top:.3f} (and not a step down to it)")
                if speed < status.min_kmh - 1e-6:
                    self.violate(f"SET_SPEED {speed:.3f} below the pad's minimum")
                if self.pad.link and speed > base + up + REPORT_SLACK_KMH:
                    self.violate(
                        f"rule 3: SET_SPEED {speed:.3f} is {speed - base:.3f} above the belt's {base:.3f} "
                        f"(one increment is {up:.3f})"
                    )
                # (Measured from where the belt is, if it had not reached the last speed set.)
                if self.last_speed is not None and speed < min(self.last_speed, phys_speed) - down - REPORT_SLACK_KMH:
                    self.violate(f"rule 3: SET_SPEED {speed:.3f} is a drop of {self.last_speed - speed:.3f} in one go")
                if not recent and speed > base + 1e-6:
                    self.violate(f"rule 4: SET_SPEED {speed:.3f} speeds up from {base:.3f} on a stale picture")
                if self.latched:
                    self.violate(f"rules 5/9: SET_SPEED although the belt was {self.latched}")
                if self.ceiling is not None and speed > self.ceiling + 1e-6:
                    self.violate(
                        f"rule 12: SET_SPEED {speed:.3f} above the {self.ceiling:.3f} the walker set with the remote"
                    )
                if self.pressed is not None and speed > self.pressed[0] + 1e-6:
                    self.pressed = None  # sent before the governor could know: the remote lost that race
                self.last_speed = speed if self.pad.last_ok else None
        if not governor.belt_on:
            self.last_speed = None
        # The walker's remote is binding from the moment the governor can
        # know of it: two packets in which the pad says so, or in which the
        # belt runs far below what was commanded.
        if self.pressed is not None:
            speed, tell_below, packets = self.pressed
            if self.phys.status.state is not BeltState.RUNNING or not self.pad.link:
                self.pressed = None
            else:
                if self.pad.tells_target or self.phys.status.speed_kmh < tell_below:
                    self.pressed[2] = packets = packets + 1
                if packets > 2:
                    self.ceiling, self.pressed = speed, None
        # Rules 1, 7, 9: with no live session, a paused one, or a stop latched,
        # the belt has to come to rest - given a link that works.
        healthy = self.pad.link and not self.pad.fail and not self.pad.lose
        if not healthy:
            self.clean_since = None
        elif self.clean_since is None:
            self.clean_since = self.mono.t
        must_be_off = bool(self.latched) or (fresh and (not reading.active or reading.paused))
        if self.remote_run and not (fresh and reading.active):
            must_be_off = False  # walking with the remote, no session: hands off
        running = self.phys.status.state in (BeltState.RUNNING, BeltState.STARTING)
        if must_be_off and running and not self.pad.link and not self.remote_run and not app.connection_wanted():
            # (The belt we drove runs on out of reach. Somebody has to go and stop it.)
            self.violate("rule 7: the belt has to be stopped, the link is down - and nothing tries to reconnect")
        if must_be_off and healthy and self.clean_since is not None:
            if self.off_since is None:
                self.off_since = self.mono.t
            waited = self.mono.t - max(self.off_since, self.clean_since)
            if waited > OFF_WITHIN_S and running and self.phys.status.speed_kmh > 0.05:
                self.violate(
                    f"rule 7: the belt still runs {waited:.0f} s after it had to be off "
                    f"(latched: {self.latched or 'no'}, session active: {reading.active}, paused: {reading.paused}, "
                    f"governor: {governor.phase.value})"
                )
                self.off_since = self.mono.t + 1e9  # say it once
        else:
            self.off_since = None
        if self.pad.sent[mark:]:
            self.note(
                f"   governor {governor.phase.value}, belt_on {governor.belt_on}; "
                f"pad {self.phys.status.state.value} at {self.phys.status.speed_kmh:.2f}"
            )


async def run_fuzz(seed: int, data_dir: Path, **options) -> Fuzz:
    fuzz = Fuzz(seed, data_dir, **options)
    await fuzz.pad.connect()
    for _ in range(TICKS):
        await fuzz.tick()
        if fuzz.violations:
            break
    return fuzz


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("mode", MODES)
def test_random_events_never_break_the_safety_rules(tmp_path: Path, mode: str, seed: int):
    fuzz = asyncio.run(run_fuzz(seed, tmp_path, **MODES[mode]))
    if fuzz.violations:
        trace = "\n".join(fuzz.trace)
        pytest.fail(
            f"{fuzz.violations[0]}\n\n(seed {seed}, mode {mode!r}, {fuzz.mono.t - 1000:.1f} s in)\n"
            f"The last events before it:\n{trace}",
            pytrace=False,
        )
    # The run was not idle: the belt was started, and stopped again.
    names = [name for name, _speed in fuzz.pad.sent]
    assert "start" in names and ("stop" in names or "pause" in names)


def test_the_fuzz_notices_a_broken_rule(tmp_path: Path, monkeypatch):
    """The checker itself: a governor that builds on what it commanded
    instead of what the belt reports (the review's first finding) is caught."""
    from walka.governor import Governor

    monkeypatch.setattr(Governor, "_reach", lambda self, belt: 1e9)
    monkeypatch.setattr(Governor, "_check_belt_follows", lambda self, now, belt, want, fresh, *, may_rise: want)
    found = []
    for seed in SEEDS:
        fuzz = asyncio.run(run_fuzz(seed, tmp_path / str(seed), **MODES["lost-and-failed-commands"]))
        found += fuzz.violations
    assert any("rule 3" in text for text in found), found
