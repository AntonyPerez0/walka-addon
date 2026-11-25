"""FitShow treadmills and walking pads (the "FitShow" app) - read-only.

Many treadmills made for the FitShow app speak its protocol on a vendor
service (0xFFF0: notifications on 0xFFF1, commands to 0xFFF2), and report
their status only when asked. Some also speak FTMS; those are read through
FTMS, which needs no query at all.

The protocol, from public open-source projects and the FitShow protocol
document (docs/HARDWARE.md cites them):

* A frame: ``02 <command> <data...> <XOR of command and data> 03``,
  multi-byte numbers little-endian.
* The one query Walka sends (``proprietary.QUERIES``): ``02 51 51 03``
  (SYS_STATUS), once a second - and only to a device named ``FS-...``:
  0xFFF1/0xFFF2 are on many devices that are not FitShow machines.
* The answer: ``02 51 <state> ...``. While running, ending, stopping or
  paused it carries speed (byte 3, 0.1 km/h - or 0.1 mph on a machine set
  to miles: *Settings -> Treadmill -> Advanced -> Firmware speed grid*),
  incline (4), time (5-6, s) and steps (11-12). Distance and calories (7-8,
  9-10) are left alone: projects disagree on their units, so Walka works
  them out itself, as for any treadmill that does not count them.
* Every other frame (machine information, workout data, echoes of unknown
  commands) is ignored.

Only the 0xFFF0 variant is read (two others, on 0xFFE0 and 0xAE00, would
need their own query). Untested on hardware with Walka.
"""

from __future__ import annotations

from functools import reduce

from .. import ftms
from .base import KMH_PER_MPH, BeltState
from .ftms_ble import FoundDevice, name_matches
from .known import prefixes_for
from .proprietary import PadReading, ProprietaryTreadmill

NOTIFY = ftms.uuid16(0xFFF1)
STATUS = 0x51
STATES = {
    0: BeltState.STOPPED,  # idle
    1: BeltState.STOPPED,  # ended
    2: BeltState.STARTING,  # counting down
    3: BeltState.RUNNING,
    4: BeltState.STOPPED,  # slowing down to a stop
    5: BeltState.UNKNOWN,  # an error
    6: BeltState.STOPPED,  # safety key out, or asleep
    9: BeltState.STOPPED,  # ready
    10: BeltState.PAUSED,
}
WITH_NUMBERS = (1, 3, 4, 10)  # the states whose answer carries the numbers


def xor(data: bytes) -> int:
    return reduce(lambda a, b: a ^ b, data, 0)


def decode(data: bytes, *, mph: bool = False) -> PadReading | None:
    if len(data) < 5 or data[0] != 0x02 or data[-1] != 0x03 or data[1] != STATUS:
        return None
    if xor(data[1:-2]) != data[-2]:
        return None
    state = data[2]
    reading = PadReading(state=STATES.get(state, BeltState.UNKNOWN))
    if state in WITH_NUMBERS and len(data) >= 15:
        speed = data[3] / 10.0
        reading.speed_kmh = (speed * KMH_PER_MPH if mph else speed) if state == 3 else 0.0
        reading.elapsed_s = int.from_bytes(data[5:7], "little")
        reading.steps = int.from_bytes(data[11:13], "little")
    else:
        reading.speed_kmh = 0.0
    return reading


class FitShowTreadmill(ProprietaryTreadmill):
    kind = "fitshow"
    PROTOCOL = "fitshow"
    DATA_CHARACTERISTIC = NOTIFY
    NOT_READABLE = "does not have the FitShow service"
    NAME_PREFIXES = prefixes_for("fitshow")

    def decode(self, data: bytes) -> PadReading | None:  # type: ignore[override]
        return decode(data, mph=self.units == "mph")

    def _accepts(self, device: FoundDevice) -> bool:
        # The query goes only to a machine that calls itself FitShow.
        return name_matches(device.name, self.NAME_PREFIXES)
