"""Treadmills Walka knows by their Bluetooth name - a table, with the evidence.

Any treadmill that reports its data over standard Bluetooth FTMS can be
read: it is found by the service it advertises, or picked from a scan in
the dashboard. A name listed here buys one thing - the pad is taken as soon
as it is heard, rather than at the end of the scan.

Every entry says how much is known about it (``evidence``) and where that
comes from (``source``). The table of treadmills in docs/TREADMILLS.md is
made from this one (tools/treadmill_table.py; a test keeps it current), so
a new entry goes here, and nowhere else.
"""

from __future__ import annotations

from dataclasses import dataclass

# How much is known about an entry.
VERIFIED = "verified by a Walka user"  # walked on with Walka, numbers checked against the display
REPORTED = "reported by another project"  # another program reads it the same way; untested with Walka

EVIDENCE_LEVELS = (VERIFIED, REPORTED)


@dataclass(frozen=True)
class KnownTreadmill:
    prefix: str  # what the Bluetooth name starts with (letter case is ignored)
    brand: str
    model: str = ""  # for people; "" when the entry is for a whole make
    protocol: str = "ftms"  # how it is read: treadmill/readers.py
    evidence: str = REPORTED
    checked: str = ""  # for VERIFIED: on what and when
    source: str = ""  # where the evidence is (a sentence, may hold a link)
    notes: str = ""


KNOWN_TREADMILLS: tuple[KnownTreadmill, ...] = (
    # Most specific first: known_treadmill() takes the first entry that matches.
    KnownTreadmill(
        "URTM030",
        "UREVO",
        "Strol 2E (URTM030)",
        evidence=VERIFIED,
        checked="Windows 11, November 2026",
        source="A Walka user: speed, distance and steps match the pad's own display, "
        "and walks are filed under the character in the game.",
        notes="Sends steps in its treadmill data.",
    ),
    KnownTreadmill(
        # UREVO pads advertise their model code.
        "URTM",
        "UREVO",
        "pads (URTM054, URTM059, URTM041, ...)",
        source="[qdomyos-zwift](https://github.com/cagnulein/qdomyos-zwift) reads every `URTM...` "
        "device as a standard FTMS treadmill; others measured sibling models.",
    ),
    KnownTreadmill(
        # WELLFIT treadmills advertise two names: "WELLFIT TM Linker" is the one for
        # apps, "WELLFIT TM Audio" is the loudspeaker - hence the whole name.
        "WELLFIT TM Linker",
        "WELLFIT",
        "TM029 Pro and other WELLFIT treadmills",
        source="[qdomyos-zwift issue 2659](https://github.com/cagnulein/qdomyos-zwift/issues/2659): "
        "a WELLFIT owner and the project's author got it working over FTMS.",
    ),
)


def _minimal_prefixes(entries: tuple[KnownTreadmill, ...], protocol: str) -> tuple[str, ...]:
    """The name prefixes to listen for, in the table's order: each one that
    no shorter prefix of the table already covers ("URTM" covers "URTM030")."""
    mine = [entry.prefix for entry in entries if entry.protocol == protocol]
    kept: list[str] = []
    for prefix in mine:
        covered = any(other != prefix and prefix.upper().startswith(other.upper()) for other in mine)
        if not covered and prefix not in kept:
            kept.append(prefix)
    return tuple(kept)


DEFAULT_NAME_PREFIXES: tuple[str, ...] = _minimal_prefixes(KNOWN_TREADMILLS, "ftms")

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
