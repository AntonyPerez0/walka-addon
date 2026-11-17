"""TCX export: well-formed, schema-ordered, and true to the record."""

from __future__ import annotations

import itertools
import re
import time
import xml.etree.ElementTree as ET

import pytest

from walka import metrics
from walka.export import EXT_NS, TCX_NS, record_to_tcx
from walka.session import SessionRecord

T0 = 1_800_000_000.0
NS = {"t": TCX_NS, "x": EXT_NS}

# Element order from Garmin's TrainingCenterDatabase v2 schema (xs:sequence).
ACTIVITY_ORDER = ["Id", "Lap", "Notes", "Training", "Creator", "Extensions"]
LAP_ORDER = [
    "TotalTimeSeconds", "DistanceMeters", "MaximumSpeed", "Calories", "AverageHeartRateBpm", "MaximumHeartRateBpm",
    "Intensity", "Cadence", "TriggerMethod", "Track", "Notes", "Extensions",
]  # fmt: skip
LAP_REQUIRED = {"TotalTimeSeconds", "DistanceMeters", "Calories", "Intensity", "TriggerMethod"}
TRACKPOINT_ORDER = [
    "Time", "Position", "AltitudeMeters", "DistanceMeters", "HeartRateBpm", "Cadence", "SensorState", "Extensions",
]  # fmt: skip


def local(element: ET.Element) -> str:
    return element.tag.split("}", 1)[-1]


def assert_schema_order(element: ET.Element, order: list[str], required: set[str] = frozenset(), repeat: str = ""):
    names = [local(child) for child in element]
    assert all(child.tag.startswith("{" + TCX_NS + "}") for child in element), names
    positions = [order.index(name) for name in names]  # ValueError = an element the schema does not know
    assert positions == sorted(positions), f"children out of schema order: {names}"
    for name in set(names):
        assert names.count(name) == 1 or name == repeat, f"{name} repeated"
    assert required <= set(names), f"missing {required - set(names)}"


def make_record(samples, **overrides) -> SessionRecord:
    fields = dict(
        id="20270115-080000",
        start=T0,
        end=T0 + 640.0,
        seconds=600.0,
        distance_m=540.0,
        steps=930,
        kcal=38.6,
        max_kmh=3.25,
        avg_hr=None,
        max_hr=None,
        source="wow",
        steps_source="treadmill",
        treadmill="URTM030",
        samples=samples,
    )
    fields.update(overrides)
    return SessionRecord(**fields)


def steady_samples(seconds: int = 600, kmh: float = 3.2, bpm: int | None = None):
    return [[float(t), kmh, bpm] for t in range(0, seconds + 1, 5)]


def parse(text: str) -> ET.Element:
    assert text.startswith('<?xml version="1.0" encoding="UTF-8"?>\n')
    root = ET.fromstring(text.encode("utf-8"))
    assert root.tag == f"{{{TCX_NS}}}TrainingCenterDatabase"
    return root


def lap_of(root: ET.Element) -> ET.Element:
    activities = root.findall("t:Activities/t:Activity", NS)
    assert len(activities) == 1
    laps = activities[0].findall("t:Lap", NS)
    assert len(laps) == 1
    return laps[0]


def test_well_formed_and_in_schema_order():
    text = record_to_tcx(make_record(steady_samples(bpm=104), avg_hr=104.2, max_hr=121))
    root = parse(text)
    assert [local(c) for c in root] == ["Activities"]
    activity = root.find("t:Activities/t:Activity", NS)
    assert activity is not None and activity.get("Sport") == "Other"
    assert_schema_order(activity, ACTIVITY_ORDER, {"Id", "Lap"})
    lap = lap_of(root)
    assert_schema_order(lap, LAP_ORDER, LAP_REQUIRED)
    tracks = lap.findall("t:Track", NS)
    assert len(tracks) == 1
    points = tracks[0].findall("t:Trackpoint", NS)
    assert len(points) == 121 and len(points) == len(list(tracks[0]))
    for point in points:
        assert_schema_order(point, TRACKPOINT_ORDER, {"Time"})
    # Extension elements live in Garmin's ActivityExtension namespace.
    tpx = points[0].find("t:Extensions/x:TPX", NS)
    assert tpx is not None and [local(c) for c in tpx] == ["Speed", "RunCadence"]
    lx = lap.find("t:Extensions/x:LX", NS)
    assert lx is not None and [local(c) for c in lx] == ["Steps"]


def test_totals_match_the_record():
    record = make_record(steady_samples(bpm=104), avg_hr=104.4, max_hr=121)
    lap = lap_of(parse(record_to_tcx(record)))
    assert float(lap.findtext("t:TotalTimeSeconds", namespaces=NS)) == pytest.approx(record.seconds)
    assert float(lap.findtext("t:DistanceMeters", namespaces=NS)) == pytest.approx(record.distance_m)
    assert float(lap.findtext("t:MaximumSpeed", namespaces=NS)) == pytest.approx(record.max_kmh / 3.6, abs=0.001)
    calories = lap.findtext("t:Calories", namespaces=NS)
    assert calories == "39" and re.fullmatch(r"\d+", calories)  # xs:unsignedShort
    assert lap.findtext("t:AverageHeartRateBpm/t:Value", namespaces=NS) == "104"
    assert lap.findtext("t:MaximumHeartRateBpm/t:Value", namespaces=NS) == "121"
    assert lap.findtext("t:Intensity", namespaces=NS) == "Active"
    assert lap.findtext("t:TriggerMethod", namespaces=NS) == "Manual"
    assert lap.findtext("t:Extensions/x:LX/x:Steps", namespaces=NS) == "930"


def test_times_are_utc_and_consistent():
    record = make_record(steady_samples())
    root = parse(record_to_tcx(record))
    start = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(T0))
    assert root.findtext("t:Activities/t:Activity/t:Id", namespaces=NS) == start
    lap = lap_of(root)
    assert lap.get("StartTime") == start
    times = [p.findtext("t:Time", namespaces=NS) for p in lap.iterfind("t:Track/t:Trackpoint", NS)]
    assert times[0] == start
    assert all(re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", t) for t in times)
    assert times == sorted(times) and len(set(times)) == len(times)
    assert times[-1] == time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(T0 + 600))


def test_trackpoint_distances_are_monotonic_and_end_at_the_recorded_distance():
    # A realistic walk: ramp up, cruise, pause, cruise, stop.
    samples = []
    for t in range(0, 905, 5):
        if t < 20:
            kmh = 0.96 + t * 0.1
        elif t < 400:
            kmh = 3.2
        elif t < 460:
            kmh = 0.0
        elif t < 880:
            kmh = 4.8
        else:
            kmh = 0.0
        samples.append([float(t), round(kmh, 2), None])
    # The recorded total comes from the treadmill's own counter and differs
    # a little from the integral of the sampled speeds.
    record = make_record(samples, distance_m=912.3, seconds=830.0)
    lap = lap_of(parse(record_to_tcx(record)))
    distances = [float(p.findtext("t:DistanceMeters", namespaces=NS)) for p in lap.iterfind("t:Track/t:Trackpoint", NS)]
    assert distances[0] == 0.0
    assert all(b >= a for a, b in itertools.pairwise(distances))
    assert distances[-1] == pytest.approx(912.3, abs=0.05)
    # Flat while the belt was paused.
    paused = [d for s, d in zip(samples, distances, strict=True) if 405 <= s[0] <= 455]
    assert max(paused) - min(paused) < 0.2


def test_speed_and_cadence_per_trackpoint():
    record = make_record(steady_samples(60, 4.0))
    lap = lap_of(parse(record_to_tcx(record, height_cm=180.0)))
    expected_cadence = round(metrics.steps_per_minute(4.0, 180.0) / 2)
    for point in lap.iterfind("t:Track/t:Trackpoint", NS):
        assert float(point.findtext("t:Extensions/x:TPX/x:Speed", namespaces=NS)) == pytest.approx(4.0 / 3.6, abs=0.001)
        assert int(point.findtext("t:Cadence", namespaces=NS)) == expected_cadence
        assert int(point.findtext("t:Extensions/x:TPX/x:RunCadence", namespaces=NS)) == expected_cadence
    # TCX's Cadence is an unsigned byte capped at 254.
    fast = lap_of(parse(record_to_tcx(make_record(steady_samples(30, 40.0)), height_cm=120.0)))
    cadences = [int(p.findtext("t:Cadence", namespaces=NS)) for p in fast.iterfind("t:Track/t:Trackpoint", NS)]
    assert max(cadences) <= 254
    standing = lap_of(parse(record_to_tcx(make_record(steady_samples(30, 0.0)))))
    assert {p.findtext("t:Cadence", namespaces=NS) for p in standing.iterfind("t:Track/t:Trackpoint", NS)} == {"0"}


def test_heart_rate_only_where_it_was_measured():
    samples = steady_samples(60)
    for sample in samples[4:9]:
        sample[2] = 111
    record = make_record(samples, avg_hr=111.0, max_hr=111)
    lap = lap_of(parse(record_to_tcx(record)))
    values = [p.findtext("t:HeartRateBpm/t:Value", namespaces=NS) for p in lap.iterfind("t:Track/t:Trackpoint", NS)]
    assert values == [None] * 4 + ["111"] * 5 + [None] * 4

    without = record_to_tcx(make_record(steady_samples(60)))
    assert "HeartRateBpm" not in without  # neither per point nor average/maximum
    lap_of(parse(without))


def test_empty_samples_still_give_a_valid_file():
    record = make_record([])
    root = parse(record_to_tcx(record))
    lap = lap_of(root)
    assert_schema_order(lap, LAP_ORDER, LAP_REQUIRED)
    assert float(lap.findtext("t:DistanceMeters", namespaces=NS)) == pytest.approx(540.0)
    assert lap.findtext("t:Extensions/x:LX/x:Steps", namespaces=NS) == "930"
    # The schema requires at least one Trackpoint inside a Track, so with no
    # samples there must be no Track element at all.
    for track in lap.findall("t:Track", NS):
        assert len(track.findall("t:Trackpoint", NS)) >= 1


def test_malformed_samples_are_skipped():
    samples = [[0.0, 3.0, None], [None, 3.0, None], [5.0], [5.0, None, 90], [], [10.0, 3.0, 0], [15.0, 3.0, 95.0]]
    lap = lap_of(parse(record_to_tcx(make_record(samples))))
    points = lap.findall("t:Track/t:Trackpoint", NS)
    assert len(points) == 3
    assert [p.findtext("t:HeartRateBpm/t:Value", namespaces=NS) for p in points] == [None, None, "95"]


def test_a_single_sample_or_a_motionless_walk_does_not_divide_by_zero():
    for samples in ([[0.0, 3.2, None]], steady_samples(30, 0.0)):
        lap = lap_of(parse(record_to_tcx(make_record(samples))))
        distances = [
            float(p.findtext("t:DistanceMeters", namespaces=NS)) for p in lap.iterfind("t:Track/t:Trackpoint", NS)
        ]
        assert distances and set(distances) == {0.0}


def test_sport_attribute():
    record = make_record(steady_samples(30))
    for sport in ("Running", "Biking", "Other"):
        assert f'<Activity Sport="{sport}">' in record_to_tcx(record, sport=sport)
    for bad in ("Walking", "", 'Other"><evil/>', "running"):
        text = record_to_tcx(record, sport=bad)
        assert '<Activity Sport="Other">' in text
        parse(text)


def test_notes_are_escaped():
    record = make_record(steady_samples(30), treadmill='Pad <1> & "co" é ✓', source="wow", steps_source="estimate")
    text = record_to_tcx(record)
    root = parse(text)  # would fail on a raw < or &
    notes = root.findtext("t:Activities/t:Activity/t:Notes", namespaces=NS)
    assert notes == 'Walka walking-pad session (wow); 930 steps (estimate), Pad <1> & "co" é ✓'
    unnamed = parse(record_to_tcx(make_record([], treadmill="")))
    assert unnamed.findtext("t:Activities/t:Activity/t:Notes", namespaces=NS).endswith(", treadmill")


def test_output_ends_with_a_newline_and_declares_both_namespaces():
    text = record_to_tcx(make_record(steady_samples(30)))
    assert text.endswith("</TrainingCenterDatabase>\n")
    assert f'xmlns="{TCX_NS}"' in text and f'xmlns:ns3="{EXT_NS}"' in text
