"""Treadmills Walka knows by their Bluetooth name.

Any treadmill that reports its data over standard Bluetooth FTMS can be
read: it is found by the service it advertises, or picked from a scan in
the dashboard. A name listed here buys one thing - the pad is taken as soon
as it is heard, rather than at the end of the scan.

A name belongs here when there is evidence that the machine sends its speed
as plain FTMS Treadmill Data. docs/HARDWARE.md says where each entry comes
from.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class KnownTreadmill:
    prefix: str  # what the Bluetooth name starts with (letter case is ignored)
    brand: str


KNOWN_TREADMILLS: tuple[KnownTreadmill, ...] = (
    # UREVO pads advertise their model code: URTM030 is the Strol 2E.
    KnownTreadmill("URTM", "UREVO"),
    # WELLFIT treadmills (TM029 Pro and others) advertise two names: "WELLFIT
    # TM Linker" is the one for apps, "WELLFIT TM Audio" is the loudspeaker -
    # hence the whole name, not "WELLFIT TM".
    KnownTreadmill("WELLFIT TM Linker", "WELLFIT"),
)

DEFAULT_NAME_PREFIXES: tuple[str, ...] = tuple(known.prefix for known in KNOWN_TREADMILLS)

# What DEFAULT_NAME_PREFIXES was before other makes were added. A settings
# file written then holds this list, and is moved on to the current one.
LEGACY_NAME_PREFIXES: tuple[str, ...] = ("URTM",)


def known_treadmill(name: str | None) -> KnownTreadmill | None:
    """The entry for a Bluetooth name, if it is one Walka knows."""
    upper = (name or "").upper()
    for known in KNOWN_TREADMILLS:
        if upper.startswith(known.prefix.upper()):
            return known
    return None
