"""Export a recorded walk as a TCX file.

TCX (Garmin's Training Center XML) is the simplest format that Garmin
Connect, Strava and most other fitness sites import by hand, and it carries
everything an indoor walk has: time, distance, calories, heart rate, speed
and cadence.
"""

from __future__ import annotations

import time
from xml.sax.saxutils import escape

from . import metrics
from .session import SessionRecord

TCX_NS = "http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2"
EXT_NS = "http://www.garmin.com/xmlschemas/ActivityExtension/v2"


def _iso(epoch: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


def record_to_tcx(record: SessionRecord, *, sport: str = "Other", height_cm: float = 172.0) -> str:
    """Render a walk as TCX.

    ``sport`` must be one of TCX's three values: "Running", "Biking" or
    "Other" (importers map "Other" to a generic/walking activity).
    """
    if sport not in ("Running", "Biking", "Other"):
        sport = "Other"

    # Rebuild a cumulative distance for each stored sample by integrating
    # the speed series, then scale it so the last point equals the recorded
    # total (which may come from the treadmill's own counter).
    points: list[tuple[float, float, float, int | None]] = []  # (t, metres, kmh, bpm)
    metres = 0.0
    previous_t: float | None = None
    previous_kmh = 0.0
    for sample in record.samples:
        if len(sample) < 2 or sample[0] is None or sample[1] is None:
            continue
        t, kmh = float(sample[0]), float(sample[1])
        bpm = int(sample[2]) if len(sample) > 2 and sample[2] else None
        if previous_t is not None and t > previous_t:
            metres += (previous_kmh + kmh) / 2.0 / 3.6 * (t - previous_t)
        points.append((t, metres, kmh, bpm))
        previous_t, previous_kmh = t, kmh
    scale = record.distance_m / metres if metres > 0 else 1.0

    start = _iso(record.start)
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<TrainingCenterDatabase xmlns="{TCX_NS}" xmlns:ns3="{EXT_NS}">',
        "  <Activities>",
        f'    <Activity Sport="{sport}">',
        f"      <Id>{start}</Id>",
        f'      <Lap StartTime="{start}">',
        f"        <TotalTimeSeconds>{record.seconds:.1f}</TotalTimeSeconds>",
        f"        <DistanceMeters>{record.distance_m:.1f}</DistanceMeters>",
        f"        <MaximumSpeed>{record.max_kmh / 3.6:.3f}</MaximumSpeed>",
        f"        <Calories>{int(round(record.kcal))}</Calories>",
    ]
    if record.avg_hr:
        lines.append(f"        <AverageHeartRateBpm><Value>{int(round(record.avg_hr))}</Value></AverageHeartRateBpm>")
    if record.max_hr:
        lines.append(f"        <MaximumHeartRateBpm><Value>{int(record.max_hr)}</Value></MaximumHeartRateBpm>")
    lines += [
        "        <Intensity>Active</Intensity>",
        "        <TriggerMethod>Manual</TriggerMethod>",
    ]
    # The schema allows a Lap without a Track, but not a Track without
    # Trackpoints - so a walk with no stored samples gets no Track at all.
    if points:
        lines.append("        <Track>")
    for t, distance, kmh, bpm in points:
        # TCX cadence counts one foot (strides per minute), max 254.
        cadence = min(254, int(round(metrics.steps_per_minute(kmh, height_cm) / 2.0)))
        lines.append("          <Trackpoint>")
        lines.append(f"            <Time>{_iso(record.start + t)}</Time>")
        lines.append(f"            <DistanceMeters>{distance * scale:.1f}</DistanceMeters>")
        if bpm:
            lines.append(f"            <HeartRateBpm><Value>{bpm}</Value></HeartRateBpm>")
        lines.append(f"            <Cadence>{cadence}</Cadence>")
        lines.append(
            f"            <Extensions><ns3:TPX><ns3:Speed>{kmh / 3.6:.3f}</ns3:Speed>"
            f"<ns3:RunCadence>{cadence}</ns3:RunCadence></ns3:TPX></Extensions>"
        )
        lines.append("          </Trackpoint>")
    if points:
        lines.append("        </Track>")
    lines += [
        f"        <Extensions><ns3:LX><ns3:Steps>{int(record.steps)}</ns3:Steps></ns3:LX></Extensions>",
        "      </Lap>",
        "      <Notes>"
        + escape(
            f"Walka walking-pad session ({record.source}); {int(record.steps)} steps "
            f"({record.steps_source}), {record.treadmill or 'treadmill'}"
        )
        + "</Notes>",
        "    </Activity>",
        "  </Activities>",
        "</TrainingCenterDatabase>",
        "",
    ]
    return "\n".join(lines)
