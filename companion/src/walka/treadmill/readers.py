"""What Walka can read a treadmill from, by what a device offers.

A device is worth connecting to as a treadmill when it has one of the
characteristics below. The first that matches decides how it is read (see
``ftms_ble.FtmsTreadmill``, which picks the protocol on connecting). Used by
"show all Bluetooth devices" in the dashboard's scan, to say of any device
whether Walka can read it.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from .. import ftms


@dataclass(frozen=True)
class Reader:
    key: str
    characteristic: str  # the one the walk comes from
    words: str  # for the dashboard


READERS: tuple[Reader, ...] = (
    Reader("ftms", ftms.TREADMILL_DATA, "standard treadmill data (Bluetooth FTMS)"),
    Reader("rsc", ftms.RSC_MEASUREMENT, "running speed and cadence (a foot pod or a treadmill's sensor)"),
)
KINDS = tuple(reader.key for reader in READERS)


def build(kind: str, address: str | None, *, name_prefixes=None, units: str = "auto", backend=None):
    """The reader for ``kind`` (one of KINDS), for the treadmill at ``address``."""
    if kind == "rsc":
        from .rsc import RscTreadmill

        return RscTreadmill(address or None, units=units, backend=backend)
    from .ftms_ble import FtmsTreadmill

    kwargs = {"units": units, "backend": backend}
    if name_prefixes is not None:
        kwargs["name_prefixes"] = name_prefixes
    return FtmsTreadmill(address or None, **kwargs)


# Names for the services a device may offer, so that "what it has" reads as words.
SERVICE_NAMES = {
    ftms.FTMS_SERVICE: "Fitness Machine",
    ftms.HEART_RATE_SERVICE: "Heart Rate",
    ftms.RSC_SERVICE: "Running Speed and Cadence",
    ftms.DEVICE_INFORMATION_SERVICE: "Device Information",
    ftms.uuid16(0x180F): "Battery",
    ftms.uuid16(0x1816): "Cycling Speed and Cadence",
    ftms.uuid16(0x1818): "Cycling Power",
    ftms.uuid16(0x1800): "Generic Access",
    ftms.uuid16(0x1801): "Generic Attribute",
}


def reader_for(characteristics: Iterable[str]) -> Reader | None:
    """How Walka would read a device with these characteristics, if it can."""
    present = {uuid.lower() for uuid in characteristics}
    return next((reader for reader in READERS if reader.characteristic in present), None)


def service_words(services: Iterable[str]) -> list[str]:
    """The services, as names where they have one ("vendor service fff0" otherwise)."""
    words = []
    for uuid in services:
        uuid = uuid.lower()
        name = SERVICE_NAMES.get(uuid)
        if name is None:
            short = uuid[4:8] if uuid.endswith("-0000-1000-8000-00805f9b34fb") else uuid[:8]
            name = f"vendor service {short}"
        if name not in words:
            words.append(name)
    return words
