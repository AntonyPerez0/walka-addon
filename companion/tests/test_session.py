"""Recording walks: counters, integration, storage, totals."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from walka import metrics
from walka.metrics import Profile
from walka.session import (
    MIN_SESSION_SECONDS,
    SAMPLE_EVERY_S,
    CounterTracker,
    SessionRecord,
    SessionRecorder,
    SessionStore,
    _plausible,
    day_key,
)

PROFILE = Profile()  # 75 kg, 172 cm
T0 = 1_800_000_000.0
DT = 0.2


def recorder(source: str = "wow") -> SessionRecorder:
    return SessionRecorder(source=source, beacon_session=3, treadmill="URTM030", started_at=T0)


def walk(rec: SessionRecorder, seconds: float, speed: float, *, start: float | None = None, profile=PROFILE, **kwargs):
    """Feed constant-speed updates; returns the time after the last one."""
    now = rec.last_at if start is None else start
    for _ in range(int(round(seconds / DT))):
        now += DT
        rec.update(now, speed_kmh=speed, profile=profile, **kwargs)
    return now


# ---- CounterTracker ----------------------------------------------------------------


def test_counter_baseline_and_deltas():
    counter = CounterTracker()
    assert counter.update(500) == 0  # whatever was on the display before us does not count
    assert counter.total == 0
    assert counter.update(503) == 3
    assert counter.update(503) == 0
    assert counter.update(510.5) == 7.5
    assert counter.total == 10.5
    assert counter.update(None) == 0  # a missing reading changes nothing
    assert counter.update(511.5) == 1
    assert counter.total == 11.5


def test_counter_reset_counts_from_zero():
    counter = CounterTracker()
    counter.update(900)
    counter.update(950)
    assert counter.update(0) == 0  # the pad zeroed itself
    assert counter.update(4) == 4
    assert counter.update(2) == 2  # reset again, 2 counted since
    assert counter.total == 56


def test_counter_rejects_implausible_jumps():
    counter = CounterTracker()
    counter.update(100)
    assert counter.update(105, max_delta=20) == 5
    assert counter.update(5000, max_delta=20) == 0  # glitch: ignored ...
    assert counter.update(5003, max_delta=20) == 3  # ... and counting resumes from it
    assert counter.total == 8
    assert counter.update(5023, max_delta=20) == 20  # exactly the allowance is fine
    assert counter.total == 28


def test_counter_survives_a_one_sample_spike_and_dip():
    spike = CounterTracker()
    for value in (100, 102, 65_535, 104, 106):
        spike.update(value, max_delta=15)
    assert spike.total == pytest.approx(4, abs=2)  # the spike added nothing big
    dip = CounterTracker()
    for value in (1000, 1002, 0, 1004, 1006):
        dip.update(value, max_delta=15)
    assert dip.total <= 4


def test_counter_rebase_forgets_the_last_reading():
    counter = CounterTracker()
    counter.update(10)
    counter.update(15)
    counter.rebase()
    assert counter.update(400) == 0  # new baseline after a reconnect
    assert counter.update(402) == 2
    assert counter.total == 7


def test_plausibility_window():
    assert _plausible(0, 0)
    assert not _plausible(5, 0)
    assert _plausible(100, 100) and _plausible(60, 100) and _plausible(160, 100)
    assert not _plausible(59, 100) and not _plausible(161, 100)
    assert not _plausible(0, 100)  # a counter that never moved


# ---- SessionRecorder: integration ----------------------------------------------------


def test_speed_integration():
    rec = recorder()
    end = walk(rec, 600, 3.6)
    assert rec.seconds == pytest.approx(600)
    assert rec.distance_speed_m == pytest.approx(600.0)
    assert rec.distance_m == pytest.approx(600.0) and rec.distance_source == "speed"
    assert rec.steps == pytest.approx(metrics.steps_per_minute(3.6, 172) * 10)
    assert rec.steps_source == "estimate"
    assert rec.kcal == pytest.approx(metrics.kcal_per_minute(3.6, PROFILE) * 10)
    assert rec.calories_source == "speed"
    assert rec.max_kmh == 3.6 and rec.speed_kmh == 3.6
    assert end == pytest.approx(T0 + 600)


def test_varying_speed_is_integrated_piecewise():
    rec = recorder()
    walk(rec, 60, 2.0)
    walk(rec, 60, 5.0)
    walk(rec, 60, 0.0)  # standing: belt paused
    walk(rec, 60, 3.0)
    assert rec.seconds == pytest.approx(180)  # only time the belt was moving
    assert rec.distance_m == pytest.approx((2.0 + 5.0 + 3.0) / 3.6 * 60)
    assert rec.max_kmh == 5.0
    expected_kcal = sum(metrics.kcal_per_minute(v, PROFILE) for v in (2.0, 5.0, 3.0))
    assert rec.kcal == pytest.approx(expected_kcal)


def test_a_creeping_belt_below_the_noise_floor_is_not_walking():
    rec = recorder()
    walk(rec, 60, 0.05)
    assert rec.seconds == 0 and rec.distance_m == 0 and rec.kcal == 0
    walk(rec, 60, 0.06)
    assert rec.seconds == pytest.approx(60)


def test_profile_changes_the_calories_not_the_distance():
    light, heavy = recorder(), recorder()
    walk(light, 300, 4.0, profile=Profile(weight_kg=55, height_cm=160))
    walk(heavy, 300, 4.0, profile=Profile(weight_kg=110, height_cm=190))
    assert light.distance_m == pytest.approx(heavy.distance_m)
    assert heavy.kcal > 1.7 * light.kcal
    assert light.steps > heavy.steps  # shorter legs, more steps


def test_gaps_longer_than_five_seconds_are_not_integrated():
    rec = recorder()
    now = walk(rec, 30, 4.0)
    seconds, distance = rec.seconds, rec.distance_m
    now += 3600  # the laptop slept for an hour with the "speed" still at 4
    rec.update(now, speed_kmh=4.0, profile=PROFILE)
    assert rec.seconds == seconds and rec.distance_m == distance
    walk(rec, 30, 4.0)
    assert rec.seconds == pytest.approx(60)
    # A gap of exactly five seconds is still a (slow) tick.
    rec.update(rec.last_at + 5.0, speed_kmh=4.0, profile=PROFILE)
    assert rec.seconds == pytest.approx(65)
    rec.update(rec.last_at + 5.01, speed_kmh=4.0, profile=PROFILE)
    assert rec.seconds == pytest.approx(65)


def test_time_standing_still_or_running_backwards_is_ignored():
    rec = recorder()
    now = walk(rec, 30, 4.0)
    rec.update(now, speed_kmh=4.0, profile=PROFILE)  # same instant
    rec.update(now - 100, speed_kmh=4.0, profile=PROFILE)  # the clock was set back
    assert rec.seconds == pytest.approx(30)
    walk(rec, 10, 4.0)
    assert rec.seconds == pytest.approx(40)  # carries on from the new time


def test_disconnected_time_is_not_recorded_and_counters_are_rebased():
    rec = recorder()
    now = T0
    distance = 0.0
    for _ in range(300):  # a minute connected at 3.6 km/h = 1 m/s
        now += DT
        distance += DT
        rec.update(now, speed_kmh=3.6, profile=PROFILE, machine_distance_m=distance)
    assert rec.distance_source == "treadmill" and rec.distance_m == pytest.approx(60, abs=0.5)
    # Link lost for a minute; the pad carried on and counted another 60 m.
    for _ in range(300):
        now += DT
        rec.update(now, speed_kmh=0.0, profile=PROFILE, machine_distance_m=distance, connected=False)
    assert rec.speed_kmh == 0.0
    distance += 60
    for _ in range(300):
        now += DT
        distance += DT
        rec.update(now, speed_kmh=3.6, profile=PROFILE, machine_distance_m=distance)
    # Only what we saw is recorded: two minutes, ~120 m - not the 60 m in the dark.
    assert rec.seconds == pytest.approx(120, abs=0.5)
    assert rec.distance_m == pytest.approx(120, abs=1.0)
    assert rec.distance_source == "treadmill"


# ---- SessionRecorder: the pad's own counters -----------------------------------------


def machine_walk(rec: SessionRecorder, seconds: float, speed: float, *, distance_factor=1.0, steps_per_m=1.25, base=0.0):
    """Walk with machine counters derived from the true distance."""
    now = rec.last_at
    metres = 0.0
    for _ in range(int(round(seconds / DT))):
        now += DT
        metres += speed / 3.6 * DT
        rec.update(
            now,
            speed_kmh=speed,
            profile=PROFILE,
            machine_distance_m=base + int(metres * distance_factor),
            machine_steps=int(base + metres * steps_per_m),
        )
    return metres


def test_plausible_machine_counters_are_preferred():
    rec = recorder()
    metres = machine_walk(rec, 600, 3.2, base=4000)  # the pad already showed 4 km / 4000 steps
    assert rec.distance_source == "treadmill" and rec.steps_source == "treadmill"
    assert rec.distance_m == pytest.approx(metres, abs=1.5)
    assert rec.steps == pytest.approx(metres * 1.25, abs=2)
    assert rec.distance_m < 1000  # the 4 km that were already on the display are not ours
    record = rec.finish(rec.last_at)
    assert record is not None
    assert record.steps_source == "treadmill" and record.distance_source == "treadmill"


def test_frozen_machine_counters_fall_back_to_the_estimate():
    rec = recorder()
    now = rec.last_at
    for _ in range(3000):
        now += DT
        rec.update(now, speed_kmh=3.2, profile=PROFILE, machine_distance_m=250, machine_steps=900)
    assert rec.distance_source == "speed" and rec.steps_source == "estimate"
    assert rec.distance_m == pytest.approx(3.2 / 3.6 * 600)
    assert rec.steps == pytest.approx(metrics.steps_per_minute(3.2, 172) * 10)


@pytest.mark.parametrize("factor", [10.0, 3.0, 0.1, 0.3])
def test_wild_machine_counters_fall_back_to_the_estimate(factor: float):
    """A counter in other units (0.1 m, 10 m, strides) or simply broken."""
    rec = recorder()
    machine_walk(rec, 600, 3.2, distance_factor=factor, steps_per_m=1.25 * factor)
    assert rec.distance_source == "speed" and rec.steps_source == "estimate"
    assert rec.distance_m == pytest.approx(3.2 / 3.6 * 600)
    assert rec.steps == pytest.approx(metrics.steps_per_minute(3.2, 172) * 10)


def test_machine_counter_reset_mid_walk_is_bridged():
    """The pad zeroes its counters after a long pause; the walk's totals must not."""
    rec = recorder()
    first = machine_walk(rec, 300, 3.2, base=100)
    walk(rec, 30, 0.0, machine_distance_m=100 + int(first), machine_steps=int(100 + first * 1.25))
    second = machine_walk(rec, 300, 3.2, base=0)  # counts from zero again
    assert rec.distance_source == "treadmill"
    assert rec.distance_m == pytest.approx(first + second, abs=3)
    assert rec.steps == pytest.approx((first + second) * 1.25, abs=4)


def test_a_counter_glitch_cannot_add_a_kilometre():
    rec = recorder()
    now = rec.last_at
    metres = 0.0
    for i in range(3000):
        now += DT
        metres += 3.2 / 3.6 * DT
        glitch = 5000 if i == 1500 else 0  # one corrupt packet
        rec.update(
            now,
            speed_kmh=3.2,
            profile=PROFILE,
            machine_distance_m=int(metres) + glitch,
            machine_steps=int(metres * 1.25) + glitch,
        )
    assert rec.distance_m == pytest.approx(metres, abs=30)
    assert rec.steps == pytest.approx(metres * 1.25, rel=0.05)


def test_no_machine_counters_at_all():
    rec = recorder()
    walk(rec, 120, 3.0, machine_distance_m=None, machine_steps=None)
    assert rec.distance_source == "speed" and rec.steps_source == "estimate"
    assert rec.live()["steps_source"] == "estimate"


def test_counters_only_present_for_part_of_the_walk_are_not_trusted_blindly():
    """Steps arrive for the first minute only: what the counter saw is far
    less than what was walked, so the estimate is used."""
    rec = recorder()
    machine_walk(rec, 60, 3.2)
    walk(rec, 600, 3.2)
    assert rec.steps_source == "estimate" and rec.distance_source == "speed"


# ---- SessionRecorder: heart rate -------------------------------------------------------


def test_heart_rate_calories_need_age_and_sex():
    with_profile = Profile(weight_kg=75, height_cm=172, age=40, sex="male")
    rec = recorder()
    walk(rec, 600, 4.0, profile=with_profile, heart_rate=120)
    assert rec.calories_source == "heart_rate"
    assert rec.kcal == pytest.approx(metrics.kcal_per_minute_from_hr(120, with_profile) * 10)
    assert rec.avg_hr == pytest.approx(120) and rec.max_hr == 120 and rec.last_hr == 120

    for incomplete in (Profile(), Profile(age=40), Profile(sex="female"), Profile(age=40, sex="other")):
        plain = recorder()
        walk(plain, 600, 4.0, profile=incomplete, heart_rate=120)
        assert plain.calories_source == "speed"
        assert plain.kcal == pytest.approx(metrics.kcal_per_minute(4.0, incomplete) * 10)
        assert plain.avg_hr == pytest.approx(120)  # still recorded and shown


def test_heart_rate_below_the_floor_is_not_used_for_calories():
    profile = Profile(age=40, sex="female")
    rec = recorder()
    walk(rec, 600, 2.0, profile=profile, heart_rate=metrics.HR_FLOOR_BPM - 1)
    assert rec.calories_source == "speed"
    assert rec.kcal == pytest.approx(metrics.kcal_per_minute(2.0, profile) * 10)
    assert rec.avg_hr == pytest.approx(metrics.HR_FLOOR_BPM - 1)
    at_floor = recorder()
    walk(at_floor, 600, 2.0, profile=profile, heart_rate=metrics.HR_FLOOR_BPM)
    assert at_floor.calories_source == "heart_rate"


def test_heart_rate_for_part_of_the_walk():
    profile = Profile(age=35, sex="male")
    speed_rate = metrics.kcal_per_minute(4.0, profile)
    hr_rate = metrics.kcal_per_minute_from_hr(130, profile)
    # Sensor present for 80 % of the walk: HR calories for that part, speed model for the rest.
    mostly = recorder()
    walk(mostly, 480, 4.0, profile=profile, heart_rate=130)
    walk(mostly, 120, 4.0, profile=profile, heart_rate=None)
    assert mostly.calories_source == "heart_rate"
    assert mostly.kcal == pytest.approx(hr_rate * 8 + speed_rate * 2, rel=1e-6)
    assert mostly.last_hr is None  # the live value disappears with the sensor
    # Sensor present for only 20 %: not representative, speed model throughout.
    barely = recorder()
    walk(barely, 120, 4.0, profile=profile, heart_rate=130)
    walk(barely, 480, 4.0, profile=profile, heart_rate=None)
    assert barely.calories_source == "speed"
    assert barely.kcal == pytest.approx(speed_rate * 10)
    assert barely.avg_hr == pytest.approx(130)


def test_heart_rate_statistics_only_cover_time_on_the_belt():
    rec = recorder()
    walk(rec, 60, 4.0, heart_rate=110)
    walk(rec, 60, 0.0, heart_rate=70)  # standing
    walk(rec, 60, 4.0, heart_rate=130)
    assert rec.avg_hr == pytest.approx(120)
    assert rec.max_hr == 130
    assert rec.last_hr == 130
    walk(rec, 1, 4.0, heart_rate=0)
    assert rec.last_hr is None


# ---- SessionRecorder: samples, finish ---------------------------------------------------


def test_samples_are_taken_every_five_seconds():
    rec = recorder()
    walk(rec, 61, 3.0, heart_rate=101)
    offsets = [s[0] for s in rec.samples]
    assert offsets[0] == pytest.approx(DT)
    assert len(offsets) == 13
    gaps = [b - a for a, b in zip(offsets, offsets[1:])]
    assert all(SAMPLE_EVERY_S - 1e-6 <= gap <= SAMPLE_EVERY_S + DT + 1e-6 for gap in gaps)
    assert all(s[1] == 3.0 and s[2] == 101 for s in rec.samples)
    quiet = recorder()
    walk(quiet, 12, 3.0)
    assert [s[2] for s in quiet.samples] == [None, None, None]


def test_too_short_walks_are_dropped():
    rec = recorder()
    end = walk(rec, MIN_SESSION_SECONDS - 1, 3.0)
    assert rec.finish(end) is None
    walk(rec, 600, 0.0)  # ten minutes of standing does not make it a walk
    assert rec.finish(rec.last_at) is None
    end = walk(rec, 1.2, 3.0)
    assert rec.finish(end) is not None


def test_finished_record_fields():
    rec = recorder()
    end = walk(rec, 300, 3.2, heart_rate=95)
    record = rec.finish(end + 12.34)
    assert record is not None
    assert record.id == time.strftime("%Y%m%d-%H%M%S", time.localtime(T0))
    assert record.start == T0 and record.end == pytest.approx(T0 + 312.3, abs=0.06)
    assert record.seconds == pytest.approx(300, abs=0.05)
    assert record.distance_m == pytest.approx(3.2 / 3.6 * 300, abs=0.06)
    assert isinstance(record.steps, int) and record.steps == round(metrics.steps_per_minute(3.2, 172) * 5)
    assert record.kcal == pytest.approx(metrics.kcal_per_minute(3.2, PROFILE) * 5, abs=0.06)
    assert record.max_kmh == 3.2 and record.avg_kmh == pytest.approx(3.2, abs=0.01)
    assert record.avg_hr == 95 and record.max_hr == 95
    assert (record.source, record.beacon_session, record.treadmill) == ("wow", 3, "URTM030")
    assert record.samples == rec.samples and record.samples is not rec.samples
    json.dumps(record.to_json())
    summary = record.summary()
    assert "samples" not in summary and summary["avg_kmh"] == pytest.approx(3.2, abs=0.01)
    assert SessionRecord.from_json(json.loads(json.dumps(record.to_json()))) == record


def test_snapshot_never_ends_before_it_starts():
    rec = recorder()
    snap = rec.snapshot(T0 - 50)
    assert snap.end == snap.start
    assert snap.seconds == 0 and snap.avg_kmh == 0.0 and snap.avg_hr is None and snap.max_hr is None


def test_live_numbers_are_json_and_consistent():
    rec = recorder("manual")
    walk(rec, 90, 4.0, heart_rate=99)
    live = rec.live()
    json.dumps(live)
    assert live["source"] == "manual" and live["seconds"] == pytest.approx(90, abs=0.1)
    assert live["distance_m"] == pytest.approx(100, abs=0.2)
    assert live["steps"] == int(rec.steps) and live["hr"] == 99 and live["avg_hr"] == 99.0
    assert live["speed_kmh"] == 4.0 and live["max_kmh"] == 4.0
    fresh = recorder().live()
    assert fresh["avg_hr"] is None and fresh["max_hr"] is None and fresh["hr"] is None


def test_record_from_json_ignores_unknown_keys_and_needs_the_required_ones():
    data = {"id": "x", "start": 1.0, "end": 2.0, "seconds": 1.0, "distance_m": 0.5, "steps": 1, "kcal": 0.1}
    record = SessionRecord.from_json({**data, "added_in_a_later_version": True})
    assert record.id == "x" and record.source == "manual" and record.samples == []
    with pytest.raises(TypeError):
        SessionRecord.from_json({"id": "x"})


# ---- SessionStore -----------------------------------------------------------------------


def make_record(start: float, *, seconds: float = 600.0, steps: int = 1000, distance: float = 500.0, kcal: float = 40.0):
    return SessionRecord(
        id=time.strftime("%Y%m%d-%H%M%S", time.localtime(start)),
        start=start,
        end=start + seconds + 5,
        seconds=seconds,
        distance_m=distance,
        steps=steps,
        kcal=kcal,
        max_kmh=3.2,
        source="wow",
        samples=[[0.2, 3.2, None], [5.2, 3.2, 101]],
    )


def local_midnight(epoch: float) -> float:
    t = time.localtime(epoch)
    return time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1))


def test_store_round_trip(tmp_path: Path):
    store = SessionStore(tmp_path)
    assert store.records == [] and (tmp_path / "sessions").is_dir()
    late, early = make_record(T0 + 5000), make_record(T0)
    store.add(late)
    store.add(early)
    assert [r.start for r in store.records] == [T0, T0 + 5000]  # oldest first whatever the insertion order
    reloaded = SessionStore(tmp_path)
    assert reloaded.records == store.records
    assert reloaded.get(early.id) == early and reloaded.get("nope") is None
    assert [r.id for r in reloaded.recent()] == [late.id, early.id]  # newest first
    assert [r.id for r in reloaded.recent(1)] == [late.id]
    assert reloaded.recent(0) == []
    assert sorted(p.name for p in (tmp_path / "sessions").iterdir()) == sorted(f"{r.id}.json" for r in store.records)


def test_store_makes_ids_unique(tmp_path: Path):
    store = SessionStore(tmp_path)
    ids = [store.add(make_record(T0)).id for _ in range(4)]
    base = ids[0]
    assert ids == [base, f"{base}-2", f"{base}-3", f"{base}-4"]
    assert len(list((tmp_path / "sessions").glob("*.json"))) == 4
    assert len(SessionStore(tmp_path).records) == 4


def test_store_delete(tmp_path: Path):
    store = SessionStore(tmp_path)
    keep, drop = store.add(make_record(T0)), store.add(make_record(T0 + 100))
    assert store.delete(drop.id) is True
    assert store.delete(drop.id) is False and store.delete("unknown") is False
    assert [r.id for r in store.records] == [keep.id]
    assert not (tmp_path / "sessions" / f"{drop.id}.json").exists()
    assert [r.id for r in SessionStore(tmp_path).records] == [keep.id]
    # The file vanishing behind our back is not an error.
    (tmp_path / "sessions" / f"{keep.id}.json").unlink()
    assert store.delete(keep.id) is True


def test_store_skips_corrupted_files(tmp_path: Path):
    store = SessionStore(tmp_path)
    good = store.add(make_record(T0))
    sessions = tmp_path / "sessions"
    (sessions / "20200101-000000.json").write_text("{ not json", "utf-8")
    (sessions / "20200101-000001.json").write_text("[1, 2, 3]", "utf-8")
    (sessions / "20200101-000002.json").write_text('{"id": "only-an-id"}', "utf-8")
    (sessions / "20200101-000003.json").write_bytes(b"\xff\xfe\x00garbage")
    (sessions / "20200101-000004.json").write_text("", "utf-8")
    (sessions / "20200101-000005.json.tmp").write_text("{}", "utf-8")  # an interrupted write
    (sessions / "notes.txt").write_text("hello", "utf-8")
    reloaded = SessionStore(tmp_path)
    assert [r.id for r in reloaded.records] == [good.id]
    reloaded.totals(T0)  # and the survivors still add up


def test_store_writes_are_atomic(tmp_path: Path):
    store = SessionStore(tmp_path)
    store.add(make_record(T0))
    store.checkpoint(make_record(T0 + 50))
    leftovers = [p for p in tmp_path.rglob("*") if p.name.endswith(".tmp")]
    assert leftovers == []


def test_checkpoint_recovery_after_a_crash(tmp_path: Path):
    store = SessionStore(tmp_path)
    finished = store.add(make_record(T0))
    in_progress = make_record(T0 + 9000, seconds=1234.0, steps=2222)
    store.checkpoint(in_progress)
    assert (tmp_path / "active-session.json").exists()
    # ... the process dies here. Next start:
    recovered = SessionStore(tmp_path)
    assert [r.id for r in recovered.records] == [finished.id, in_progress.id]
    assert recovered.records[-1] == in_progress
    assert not (tmp_path / "active-session.json").exists()
    # It is recovered once, not on every start.
    assert len(SessionStore(tmp_path).records) == 2


def test_checkpoint_is_replaced_and_cleared(tmp_path: Path):
    store = SessionStore(tmp_path)
    store.checkpoint(make_record(T0, seconds=30))
    store.checkpoint(make_record(T0, seconds=60))
    assert json.loads((tmp_path / "active-session.json").read_text("utf-8"))["seconds"] == 60
    store.clear_checkpoint()
    store.clear_checkpoint()  # clearing twice is fine
    assert not (tmp_path / "active-session.json").exists()
    assert SessionStore(tmp_path).records == []


def test_too_short_or_corrupt_checkpoints_are_dropped(tmp_path: Path):
    store = SessionStore(tmp_path)
    store.checkpoint(make_record(T0, seconds=MIN_SESSION_SECONDS - 0.1))
    assert SessionStore(tmp_path).records == []
    assert not (tmp_path / "active-session.json").exists()
    (tmp_path / "active-session.json").write_text("{ torn write", "utf-8")
    assert SessionStore(tmp_path).records == []
    assert not (tmp_path / "active-session.json").exists()


def test_recovered_checkpoint_gets_a_unique_id(tmp_path: Path):
    """The walk was saved, and a stale checkpoint with the same start survived."""
    store = SessionStore(tmp_path)
    saved = store.add(make_record(T0))
    (tmp_path / "active-session.json").write_text(json.dumps(make_record(T0).to_json()), "utf-8")
    recovered = SessionStore(tmp_path)
    assert [r.id for r in recovered.records] == [saved.id, f"{saved.id}-2"]


# ---- totals ---------------------------------------------------------------------------------


def test_totals_today_week_all(tmp_path: Path):
    store = SessionStore(tmp_path)
    midnight = local_midnight(T0)
    now = midnight + 15 * 3600  # three in the afternoon
    store.add(make_record(now - 3600, steps=100, distance=50, kcal=5, seconds=60))  # today
    store.add(make_record(midnight + 60, steps=200, distance=100, kcal=10, seconds=120))  # today, just after midnight
    store.add(make_record(midnight - 60, steps=400, distance=200, kcal=20, seconds=240))  # yesterday, just before
    store.add(make_record(now - 6 * 86400, steps=800, distance=400, kcal=40, seconds=480))  # 6 days ago
    store.add(make_record(now - 7 * 86400, steps=1600, distance=800, kcal=80, seconds=960))  # outside the week
    store.add(make_record(now - 40 * 86400, steps=3200, distance=1600, kcal=160, seconds=1920))
    totals = store.totals(now)
    assert totals["today"] == {"sessions": 2, "seconds": 180.0, "distance_m": 150.0, "steps": 300, "kcal": 15.0}
    assert totals["week"] == {"sessions": 4, "seconds": 900.0, "distance_m": 750.0, "steps": 1500, "kcal": 75.0}
    assert totals["all"] == {"sessions": 6, "seconds": 3780.0, "distance_m": 3150.0, "steps": 6300, "kcal": 315.0}
    days = totals["days"]
    assert [d["day"] for d in days] == [day_key(now - k * 86400) for k in range(6, -1, -1)]
    assert [d["steps"] for d in days] == [800, 0, 0, 0, 0, 400, 300]
    assert days[-1] == {"day": day_key(now), "steps": 300, "distance_m": 150.0, "kcal": 15.0, "seconds": 180.0}
    json.dumps(totals)


def test_totals_of_an_empty_store(tmp_path: Path):
    totals = SessionStore(tmp_path).totals(T0)
    empty = {"sessions": 0, "seconds": 0.0, "distance_m": 0.0, "steps": 0, "kcal": 0.0}
    assert totals["today"] == empty and totals["week"] == empty and totals["all"] == empty
    assert totals["streak"] == 0 and len(totals["days"]) == 7


def test_streak(tmp_path: Path):
    store = SessionStore(tmp_path)
    now = local_midnight(T0) + 12 * 3600
    for days_ago in (1, 2, 3, 5):
        store.add(make_record(now - days_ago * 86400))
    # Nothing walked today yet: yesterday's run of three still stands.
    assert store.totals(now)["streak"] == 3
    store.add(make_record(now - 600))
    assert store.totals(now)["streak"] == 4
    store.add(make_record(now - 4 * 86400))  # fill the gap
    assert store.totals(now)["streak"] == 6
    # Two walks on one day count once.
    store.add(make_record(now - 3600))
    assert store.totals(now)["streak"] == 6
    # A whole day off resets it.
    assert store.totals(now + 2 * 86400)["streak"] == 0
    assert store.totals(now + 86400)["streak"] == 6


def test_live_walk_is_included_as_extra(tmp_path: Path):
    store = SessionStore(tmp_path)
    now = local_midnight(T0) + 12 * 3600
    store.add(make_record(now - 86400, steps=500))
    live = make_record(now - 300, steps=77, distance=40, kcal=3, seconds=290)
    totals = store.totals(now, [live])
    assert totals["today"]["steps"] == 77 and totals["today"]["sessions"] == 1
    assert totals["all"]["steps"] == 577 and totals["week"]["sessions"] == 2
    assert totals["streak"] == 2
    assert totals["days"][-1]["steps"] == 77
    assert len(store.records) == 1  # it is not stored by being counted


@pytest.fixture
def pacific_time():
    """US Pacific time with its daylight-saving rules (POSIX TZ string, no tz database needed)."""
    if not hasattr(time, "tzset"):
        pytest.skip("needs time.tzset()")
    old = os.environ.get("TZ")
    os.environ["TZ"] = "PST8PDT,M3.2.0,M11.1.0"
    time.tzset()
    yield
    if old is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = old
    time.tzset()


def test_totals_across_the_spring_daylight_saving_change(tmp_path: Path, pacific_time):
    """Sunday 2026-03-08 has 23 hours. Half past midnight on Monday, "24 hours
    ago" is Saturday 23:30 - the Sunday must not vanish from the week."""
    now = time.mktime((2026, 3, 9, 0, 30, 0, 0, 0, -1))
    assert day_key(now) == "2026-03-09" and day_key(now - 86400) == "2026-03-07"
    store = SessionStore(tmp_path)
    store.add(make_record(time.mktime((2026, 3, 8, 12, 0, 0, 0, 0, -1)), steps=1111))  # Sunday
    store.add(make_record(time.mktime((2026, 3, 9, 0, 10, 0, 0, 0, -1)), steps=22))  # Monday
    totals = store.totals(now)
    assert [d["day"] for d in totals["days"]] == [f"2026-03-{d:02d}" for d in range(3, 10)]
    assert totals["week"]["steps"] == 1133
    assert totals["days"][-2]["steps"] == 1111
    assert totals["streak"] == 2


def test_totals_across_the_autumn_daylight_saving_change(tmp_path: Path, pacific_time):
    """Sunday 2026-11-01 has 25 hours; late at night a few days later, stepping
    back 24 hours at a time lands on that Sunday twice."""
    now = time.mktime((2026, 11, 3, 23, 30, 0, 0, 0, -1))
    store = SessionStore(tmp_path)
    for day in (1, 2, 3):
        store.add(make_record(time.mktime((2026, 11, day, 9, 0, 0, 0, 0, -1)), steps=100))
    store.add(make_record(time.mktime((2026, 10, 28, 9, 0, 0, 0, 0, -1)), steps=5))  # 6 days back
    totals = store.totals(now)
    assert [d["day"] for d in totals["days"]] == ["2026-10-28", "2026-10-29", "2026-10-30", "2026-10-31"] + [
        f"2026-11-{d:02d}" for d in (1, 2, 3)
    ]
    assert totals["week"]["steps"] == 305
    assert totals["streak"] == 3  # not 4: no walk on 31 October
