"""Read-only Bluetooth probe: what does this treadmill actually expose?

``walka probe`` connects to the pad, lists every GATT service and
characteristic, reads the readable ones and listens to notifications for a
while. It never writes a command, so the belt cannot move. The one thing it
does write is the switch that turns notifications on, and by default only
for the standard characteristics Walka knows (Fitness Machine, Device
Information, Heart Rate): what switching them on does on a vendor's own
channels is not known, so those are listened to only with ``--all``.

The report answers the questions no manual does (speed range and step, where
the step counter lives, what the pad says when you press its buttons) and is
the thing to attach to a bug report.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from . import ftms
from .treadmill.ftms_ble import DEFAULT_NAME_PREFIXES, is_treadmill_candidate, name_matches

Emit = Callable[[str], None]

_KNOWN = {
    ftms.FTMS_SERVICE: "Fitness Machine Service",
    ftms.FITNESS_MACHINE_FEATURE: "Fitness Machine Feature",
    ftms.TREADMILL_DATA: "Treadmill Data",
    ftms.TRAINING_STATUS: "Training Status",
    ftms.SUPPORTED_SPEED_RANGE: "Supported Speed Range",
    ftms.SUPPORTED_INCLINATION_RANGE: "Supported Inclination Range",
    ftms.CONTROL_POINT: "Fitness Machine Control Point",
    ftms.FITNESS_MACHINE_STATUS: "Fitness Machine Status",
    ftms.HEART_RATE_SERVICE: "Heart Rate Service",
    ftms.HEART_RATE_MEASUREMENT: "Heart Rate Measurement",
    ftms.RSC_SERVICE: "Running Speed and Cadence Service",
    ftms.RSC_MEASUREMENT: "RSC Measurement",
    ftms.DEVICE_INFORMATION_SERVICE: "Device Information",
}

# Services whose notifications the probe switches on without being asked to:
# standard ones, where doing so is what every fitness app does.
LISTEN_SERVICES = frozenset({ftms.FTMS_SERVICE, ftms.DEVICE_INFORMATION_SERVICE, ftms.HEART_RATE_SERVICE})


def listens_to(service_uuid: str, *, all_characteristics: bool = False) -> bool:
    """Does the probe switch on notifications for characteristics of this service?"""
    return all_characteristics or service_uuid.lower() in LISTEN_SERVICES


def describe_value(uuid: str, data: bytes) -> str:
    """Human-readable decoding of the characteristics we understand."""
    uuid = uuid.lower()
    try:
        if uuid == ftms.TREADMILL_DATA:
            d = ftms.parse_treadmill_data(data)
            parts = [f"flags=0x{d.flags:04X}"]
            for label, value in (
                ("speed", d.speed_kmh),
                ("avg", d.average_speed_kmh),
                ("dist_m", d.total_distance_m),
                ("kcal", d.total_energy_kcal),
                ("hr", d.heart_rate_bpm),
                ("elapsed_s", d.elapsed_time_s),
                ("vendor_steps", d.vendor_steps),
            ):
                if value is not None:
                    parts.append(f"{label}={value}")
            if d.truncated:
                parts.append("TRUNCATED")
            return " ".join(parts)
        if uuid == ftms.SUPPORTED_SPEED_RANGE:
            r = ftms.parse_supported_speed_range(data)
            if r:
                mph = " (0.1 mph grid: US firmware)" if 0.15 <= r.step_kmh <= 0.17 else ""
                return f"min={r.min_kmh:.2f} max={r.max_kmh:.2f} step={r.step_kmh:.2f} km/h{mph}"
        if uuid == ftms.FITNESS_MACHINE_FEATURE:
            f = ftms.parse_features(data)
            if f:
                return (
                    f"features=0x{f.features:08X} targets=0x{f.target_settings:08X} "
                    f"speed_target={'yes' if f.speed_target_supported else 'NO'}"
                )
        if uuid == ftms.FITNESS_MACHINE_STATUS:
            s = ftms.parse_machine_status(data)
            if s:
                return s.describe()
        if uuid == ftms.TRAINING_STATUS and len(data) >= 2:
            names = {0x00: "other", 0x01: "idle", 0x0D: "manual mode (running)", 0x0E: "pre-workout (countdown)",
                     0x0F: "post-workout"}  # fmt: skip
            return names.get(data[1], f"status 0x{data[1]:02X}")
        if uuid == ftms.HEART_RATE_MEASUREMENT:
            return f"bpm={ftms.parse_heart_rate(data)}"
    except Exception as exc:  # a probe must never die on odd data
        return f"(could not decode: {exc})"
    if data and all(32 <= b < 127 for b in data):
        return repr(data.decode("ascii"))
    return ""


async def probe(
    *,
    address: str | None = None,
    name_prefixes: Sequence[str] = DEFAULT_NAME_PREFIXES,
    listen_s: float = 30.0,
    scan_s: float = 8.0,
    all_characteristics: bool = False,
    emit: Emit = print,
) -> list[str]:
    """Run the probe; returns the report lines (also passed to ``emit``).

    ``all_characteristics`` also listens to the vendor's own channels.
    """
    from bleak import BleakClient, BleakScanner

    from .treadmill.bleak_backend import prepare_windows_threading

    prepare_windows_threading()
    report: list[str] = []

    def out(line: str = "") -> None:
        report.append(line)
        emit(line)

    out(f"Walka Bluetooth probe - {time.strftime('%Y-%m-%d %H:%M:%S')}")
    out("This tool only reads and listens. It never writes a command to the treadmill, so the belt")
    out("cannot move. (The only thing it writes is the switch that turns notifications on.)")
    out("")
    out(f"Scanning for {scan_s:.0f} s ...")
    found = await BleakScanner.discover(timeout=scan_s, return_adv=True)
    target: Any = None
    rows = []
    for device, adv in found.values():
        name = adv.local_name or device.name or ""
        candidate = is_treadmill_candidate(name, adv.service_uuids, name_prefixes)
        # Known names first: the first candidate listed is the one probed.
        order = (not name_matches(name, name_prefixes), not candidate, -(adv.rssi or -999))
        rows.append((order, name, device, adv, candidate))
    rows.sort(key=lambda r: r[0])
    for _, name, device, adv, candidate in rows:
        marker = "*" if candidate else " "
        services = ",".join(u[4:8] for u in adv.service_uuids) or "-"
        out(f" {marker} {device.address}  rssi={adv.rssi:>4}  name={name or '(none)':<24} services={services}")
        if address and device.address.upper() == address.upper():
            target = device
        elif not address and candidate and target is None:
            target = device
    out("   (* = looks like a treadmill)")
    out("")
    if target is None:
        out("No treadmill found. Is it switched on and in range? Is the UREVO phone app closed")
        out("(the pad accepts one connection at a time)? Do not pair it in Windows Bluetooth settings.")
        return report

    out(f"Connecting to {target.address} ({target.name or 'unnamed'}) ...")
    async with BleakClient(target, timeout=20.0) as client:
        out("Connected. GATT table:")
        notifiable = []
        skipped = []
        for service in client.services:
            out(f"  service {service.uuid}  {_KNOWN.get(service.uuid.lower(), '')}")
            for char in service.characteristics:
                props = ",".join(char.properties)
                out(f"    char {char.uuid}  [{props}]  {_KNOWN.get(char.uuid.lower(), '')}")
                if "read" in char.properties:
                    try:
                        value = bytes(await client.read_gatt_char(char))
                        out(f"      read: {value.hex(' ')}  {describe_value(char.uuid, value)}")
                    except Exception as exc:
                        out(f"      read failed: {exc}")
                if "notify" in char.properties or "indicate" in char.properties:
                    if listens_to(service.uuid, all_characteristics=all_characteristics):
                        notifiable.append(char)
                    else:
                        skipped.append(char)
        out("")
        if skipped:
            out(
                f"Not listening to {len(skipped)} characteristic(s) outside the standard services: "
                + ", ".join(char.uuid[4:8] for char in skipped)
            )
            out("(run 'walka probe --all' to listen to those too, for example to look for a step counter).")
        out(f"Listening to {len(notifiable)} characteristic(s) for {listen_s:.0f} s.")
        out("Now is a good time to: start the belt with the remote, change speed, pause, stop.")
        started = time.monotonic()
        counts: dict[str, int] = {}
        last: dict[str, bytes] = {}

        def handler_for(uuid: str) -> Callable[[Any, bytearray], None]:
            def handle(_char: Any, data: bytearray) -> None:
                value = bytes(data)
                counts[uuid] = counts.get(uuid, 0) + 1
                # Log every packet for rare characteristics, but only changes
                # for the chatty data stream.
                if counts[uuid] > 5 and last.get(uuid) == value:
                    return
                last[uuid] = value
                out(
                    f"  +{time.monotonic() - started:6.2f}s {uuid[4:8]}: {value.hex(' ')}"
                    f"  {describe_value(uuid, value)}"
                )

            return handle

        for char in notifiable:
            try:
                await client.start_notify(char, handler_for(char.uuid.lower()))
            except Exception as exc:
                out(f"  could not subscribe to {char.uuid}: {exc}")
        await asyncio.sleep(listen_s)
        out("")
        out("Packets received: " + (", ".join(f"{u[4:8]}={n}" for u, n in sorted(counts.items())) or "none"))
    out("Done.")
    return report


def save_report(lines: Sequence[str], directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"walka-probe-{time.strftime('%Y%m%d-%H%M%S')}.txt"
    path.write_text("\n".join(lines) + "\n", "utf-8")
    return path
