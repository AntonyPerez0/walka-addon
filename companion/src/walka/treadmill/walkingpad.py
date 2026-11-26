"""Older KingSmith / Xiaomi WalkingPads (the "WiLink" protocol) - read-only.

Pads such as the A1, A1 Pro, C1, C2 and R1 do not speak FTMS: they report
on a vendor service (0xFE00), and only when asked. Newer KingSmith models
(the KS-HD Z1 and others) speak FTMS and are read by the FTMS reader; which
one a pad is, is decided on connecting, by what it offers.

The protocol, from public open-source projects (docs/HARDWARE.md cites
them; ph4r05/ph4-walkingpad, MIT, is the reference):

* notifications on 0xFE01; queries are written to 0xFE02, without response.
* The one query Walka sends (``proprietary.QUERIES``): ``F7 A2 00 00 A2 FD``,
  "your current status", once a second.
* The answer, 20 bytes: ``F8 A2``, belt state, speed (0.1 km/h), mode, time
  (3 bytes, big-endian, seconds), distance (3 bytes, units of 10 m), steps
  (3 bytes), four more bytes, a checksum (the sum of bytes 1-17) and ``FD``.
  Anything else - the "last record" answer ``F8 A7``, a damaged packet - is
  ignored.

Untested on hardware with Walka.
"""

from __future__ import annotations

from .. import ftms
from .base import BeltState
from .ftms_ble import FoundDevice, name_matches
from .known import prefixes_for
from .proprietary import PadReading, ProprietaryTreadmill

SERVICE = ftms.uuid16(0xFE00)
NOTIFY = ftms.uuid16(0xFE01)

# Belt state (byte 2). 2 "paused" is reported by one project only; 7-9 count down to the start.
STATES = {
    0: BeltState.STOPPED,
    1: BeltState.RUNNING,
    2: BeltState.PAUSED,
    5: BeltState.STOPPED,  # standby
    7: BeltState.STARTING,
    8: BeltState.STARTING,
    9: BeltState.STARTING,
}

# Names these pads advertise (the start of them), from treadmill/known.py: what a scan by name
# looks for.
NAME_PREFIXES = prefixes_for("walkingpad")
# The query is written only to a device whose name says it is a KingSmith pad: 0xFE01 / 0xFE02
# could be on another device. (Wider than the known names: KingSmith names its pads "KS-...".)
ACCEPTED_NAMES = ("WalkingPad", "KS-")


def checksum(packet: bytes) -> int:
    """The sum of the bytes between the header and the checksum, modulo 256."""
    return sum(packet[1:-2]) & 0xFF


def decode(data: bytes) -> PadReading | None:
    if len(data) != 20 or data[0] != 0xF8 or data[1] != 0xA2 or data[19] != 0xFD:
        return None
    if checksum(data) != data[18]:
        return None
    return PadReading(
        state=STATES.get(data[2], BeltState.UNKNOWN),
        speed_kmh=data[3] / 10.0,
        elapsed_s=int.from_bytes(data[5:8], "big"),
        distance_m=int.from_bytes(data[8:11], "big") * 10.0,
        steps=int.from_bytes(data[11:14], "big"),
    )


class WalkingPadTreadmill(ProprietaryTreadmill):
    kind = "walkingpad"
    PROTOCOL = "walkingpad"
    DATA_CHARACTERISTIC = NOTIFY
    NOT_READABLE = "does not have the WalkingPad service"
    NAME_PREFIXES = NAME_PREFIXES

    decode = staticmethod(decode)

    def _accepts(self, device: FoundDevice) -> bool:
        # The query goes only to a machine that calls itself a WalkingPad.
        return name_matches(device.name, ACCEPTED_NAMES)
