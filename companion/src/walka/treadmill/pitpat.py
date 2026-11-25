"""PitPat / SupeRun / DeerRun walking pads (the "PitPat" app) - read-only.

These pads speak their maker's protocol, and **send their status by
themselves**: Walka subscribes and listens, and writes nothing at all (no
query is needed, so none is in ``proprietary.QUERIES`` for them). Other
apps send a keep-alive after every status; several working ones never do,
and so neither does Walka.

The protocol, from public open-source projects (docs/HARDWARE.md cites
them; lapo-luchini/pitpat-exporter, ISC, holds hardware captures):

* Two transports. Most pads: service 0xFBA0, notifications on 0xFBA2. Some
  (an ESP32 board): service 0xFFFF, notifications on 0xFF02, each frame
  wrapped in four bytes ``4D 04 <sequence> <length>``.
* A status frame: start byte 0x66/0x67/0x68, its length, ..., an XOR of
  bytes 1 to n-3, and 0x43. Big-endian: speed (2 bytes at 3, km/h x 1000,
  also when the pad shows mph), distance (4 bytes at 7, metres), calories
  (2 bytes at 18), time (4 bytes at 20: milliseconds from firmware 20 on,
  seconds before; the firmware is byte 25) and a flags byte (26) whose bits
  0x18 say stopped / running / paused / starting.
* Steps: four bytes at 14 according to the maker's app - but always 0 on
  the pads that were captured, which count them in two bytes at 43 of the
  47- and 50-byte frames. Walka reads 14 when it is not 0, else 43 in those
  frames, else keeps the last count.

Untested on hardware with Walka.
"""

from __future__ import annotations

from functools import reduce

from .. import ftms
from .base import BeltState
from .known import prefixes_for
from .proprietary import PadReading, ProprietaryTreadmill

NOTIFY = ftms.uuid16(0xFBA2)  # service 0xFBA0
NOTIFY_ESP32 = ftms.uuid16(0xFF02)  # service 0xFFFF, frames wrapped (see above)
STARTS = (0x66, 0x67, 0x68)
END = 0x43
STATES = {0x00: BeltState.STOPPED, 0x08: BeltState.RUNNING, 0x10: BeltState.PAUSED, 0x18: BeltState.STARTING}
STEP_FRAMES = (47, 50)  # the frames whose bytes 43-44 are the step count


def xor(data: bytes) -> int:
    return reduce(lambda a, b: a ^ b, data, 0)


def unwrap(data: bytes) -> bytes:
    """The status frame, without the ESP32 transport's four bytes if it has them."""
    if len(data) > 4 and data[0] == 0x4D and data[1] == 0x04 and data[3] == len(data) - 4:
        return data[4:]
    return data


def decode(data: bytes) -> PadReading | None:
    frame = unwrap(bytes(data))
    n = len(frame)
    if n < 31 or frame[0] not in STARTS or frame[1] != n or frame[-1] != END:
        return None
    if xor(frame[1:-2]) != frame[-2]:
        return None
    big = int.from_bytes
    firmware = frame[25]
    elapsed = big(frame[20:24], "big")
    steps = big(frame[14:18], "big") or None
    if steps is None and n in STEP_FRAMES and n >= 45:
        steps = big(frame[43:45], "big")
    return PadReading(
        state=STATES.get(frame[26] & 0x18),
        speed_kmh=big(frame[3:5], "big") / 1000.0,
        distance_m=float(big(frame[7:11], "big")),
        energy_kcal=float(big(frame[18:20], "big")),
        elapsed_s=elapsed // 1000 if firmware > 19 else elapsed,
        steps=steps,
    )


class PitPatTreadmill(ProprietaryTreadmill):
    kind = "pitpat"
    PROTOCOL = "pitpat"
    NEEDS_QUERY = False  # it says its status by itself: nothing is written
    DATA_CHARACTERISTIC = NOTIFY
    NOT_READABLE = "does not have the PitPat service"
    NAME_PREFIXES = prefixes_for("pitpat")

    decode = staticmethod(decode)

    def data_characteristics(self) -> tuple[str, ...]:
        return (NOTIFY, NOTIFY_ESP32)
