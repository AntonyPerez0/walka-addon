"""Running Speed and Cadence: a foot pod, or a treadmill's own speed sensor.

Some walking pads do not speak FTMS but carry (or can be read through) a
standard Running Speed and Cadence sensor (service 0x1814, RSC Measurement
0x2A53): the instantaneous speed, the cadence, and on some sensors a stride
length and a total distance. A foot pod on a shoe says the same of the
walker.

What Walka makes of it:

* **Speed** is the sensor's. RSC has no "stopped" or "paused" events: the
  belt counts as moving while the speed is above a walking crawl.
* **Steps** are counted from the cadence - steps a minute, over the time
  between two measurements - so the walk has real steps, not an estimate
  from speed and height. Some sensors report strides (one per two steps)
  instead: a cadence under ``STRIDE_CADENCE_MAX`` at a walking speed is
  taken for strides and doubled.
* **Distance** is the sensor's own total when it sends one; otherwise the
  companion works it out from the speed, as for any treadmill.

Like every reader here it only listens: it switches notifications on for
RSC Measurement, and that is all it ever writes. The rest - finding the
sensor, connecting, a lost link - is ``FtmsTreadmill``'s. An RSC sensor has
no name Walka knows, so it is picked by address: *Treadmill -> Scan -> Show
all Bluetooth devices -> Check*.
"""

from __future__ import annotations

import time

from .. import ftms
from .base import BeltState, TreadmillError
from .ftms_ble import BleClient, FtmsTreadmill

MOVING_KMH = 0.3  # a sensor at rest can report a crawl
WALKING_KMH = 2.0
STRIDE_CADENCE_MAX = 75  # at walking speed fewer than this a minute are strides, not steps
MAX_GAP_S = 5.0  # between two measurements: longer is a gap, not a walk


def steps_per_minute(cadence: int, speed_kmh: float) -> float:
    """The cadence as steps a minute (some sensors send strides: see the top of this file)."""
    if speed_kmh >= WALKING_KMH and 0 < cadence < STRIDE_CADENCE_MAX:
        return cadence * 2.0
    return float(cadence)


class RscTreadmill(FtmsTreadmill):
    kind = "rsc"
    DATA_CHARACTERISTIC = ftms.RSC_MEASUREMENT
    NOT_READABLE = "does not send running speed and cadence"

    def __init__(self, address: str | None = None, **kwargs) -> None:
        kwargs.setdefault("name_prefixes", ())
        super().__init__(address, **kwargs)
        self._steps = 0.0
        self._last_at: float | None = None

    async def connect(self) -> None:
        if not self.address:
            self.status.detail = "no sensor chosen: pick it under Treadmill -> Scan -> Show all Bluetooth devices"
            raise TreadmillError(self.status.detail)
        await super().connect()

    async def _setup(self, client: BleClient) -> None:
        """No speed range to read: subscribe to the measurements, and that is all."""
        self._steps = 0.0
        self._last_at = None
        self.raw_speed_range = None
        self._apply_speed_range(None)
        await client.subscribe(ftms.RSC_MEASUREMENT, self._on_rsc)

    def _on_rsc(self, data: bytes) -> None:
        measurement = ftms.parse_rsc_measurement(bytes(data))
        if measurement is None:
            return
        status = self.status
        now = time.monotonic()
        if self._last_at is not None and 0 < now - self._last_at <= MAX_GAP_S and measurement.speed_kmh > MOVING_KMH:
            self._steps += steps_per_minute(measurement.cadence_spm, measurement.speed_kmh) * (now - self._last_at) / 60.0
        self._last_at = now
        status.updated_at = now
        status.speed_kmh = measurement.speed_kmh if measurement.speed_kmh > MOVING_KMH else 0.0
        status.state = BeltState.RUNNING if status.speed_kmh > 0 else BeltState.STOPPED
        status.steps = int(self._steps)
        if measurement.total_distance_m is not None:
            status.distance_m = measurement.total_distance_m
