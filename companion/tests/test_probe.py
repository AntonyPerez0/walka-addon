"""``walka probe``: what it connects to, what it listens to, what it says.

bleak is replaced by a scripted scanner and client; what matters here is
which characteristics get their notifications switched on (the only thing
the probe ever writes).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import bleak
import pytest

from walka import ftms, probe

VENDOR_SERVICE = "0000fff0-0000-1000-8000-00805f9b34fb"
VENDOR_NOTIFY = "0000fff1-0000-1000-8000-00805f9b34fb"
VENDOR_WRITE = "0000fff2-0000-1000-8000-00805f9b34fb"
MODEL_NUMBER = "00002a24-0000-1000-8000-00805f9b34fb"


def characteristic(uuid: str, *properties: str):
    return SimpleNamespace(uuid=uuid, properties=list(properties))


def service(uuid: str, *characteristics):
    return SimpleNamespace(uuid=uuid, characteristics=list(characteristics))


PAD_SERVICES = [
    service(ftms.DEVICE_INFORMATION_SERVICE, characteristic(MODEL_NUMBER, "read")),
    service(
        ftms.FTMS_SERVICE,
        characteristic(ftms.FITNESS_MACHINE_FEATURE, "read"),
        characteristic(ftms.TREADMILL_DATA, "notify"),
        characteristic(ftms.SUPPORTED_SPEED_RANGE, "read"),
        characteristic(ftms.CONTROL_POINT, "write", "indicate"),
        characteristic(ftms.FITNESS_MACHINE_STATUS, "notify"),
        characteristic(ftms.TRAINING_STATUS, "read", "notify"),
    ),
    service(VENDOR_SERVICE, characteristic(VENDOR_NOTIFY, "notify"), characteristic(VENDOR_WRITE, "write")),
]
READS = {
    MODEL_NUMBER: b"URTM030",
    ftms.FITNESS_MACHINE_FEATURE: bytes.fromhex("0c12000001000000"),
    ftms.SUPPORTED_SPEED_RANGE: bytes.fromhex("600083021000"),
    ftms.TRAINING_STATUS: bytes.fromhex("0001"),
}


class Radio:
    """What is in range, and everything the probe did to it."""

    def __init__(self) -> None:
        self.adverts: dict[str, tuple] = {}
        self.connected_to: list[str] = []
        self.subscribed: list[str] = []
        self.written: list[str] = []

    def add(
        self,
        address: str,
        name: str | None,
        *,
        fitness_machine: bool = False,
        rssi: int = -60,
        kind: bytes | None = None,  # FTMS service data: what kind of machine it says it is
    ) -> None:
        device = SimpleNamespace(address=address, name=name)
        uuids = [ftms.FTMS_SERVICE] if fitness_machine else []
        service_data = {ftms.FTMS_SERVICE: kind} if kind is not None else {}
        self.adverts[address] = (
            device,
            SimpleNamespace(local_name=name, service_uuids=uuids, service_data=service_data, rssi=rssi),
        )


@pytest.fixture
def radio(monkeypatch) -> Radio:
    radio = Radio()

    class Scanner:
        @staticmethod
        async def discover(timeout: float, return_adv: bool):
            assert return_adv
            return dict(radio.adverts)

    class Client:
        services = PAD_SERVICES

        def __init__(self, device, timeout: float) -> None:
            self.device = device

        async def __aenter__(self):
            radio.connected_to.append(self.device.address)
            return self

        async def __aexit__(self, *_exc) -> None:
            pass

        async def read_gatt_char(self, char) -> bytes:
            return READS[char.uuid]

        async def start_notify(self, char, callback) -> None:
            radio.subscribed.append(char.uuid)
            if char.uuid == ftms.TREADMILL_DATA:
                callback(char, bytearray.fromhex("8404600000000000ffffff0000"))

        async def write_gatt_char(self, char, data, response=None) -> None:  # the probe must never get here
            radio.written.append(getattr(char, "uuid", str(char)))

    monkeypatch.setattr(bleak, "BleakScanner", Scanner)
    monkeypatch.setattr(bleak, "BleakClient", Client)
    return radio


def run_probe(**kwargs) -> list[str]:
    kwargs.setdefault("listen_s", 0.0)
    return asyncio.run(asyncio.wait_for(probe.probe(emit=lambda line: None, **kwargs), 10))


def test_only_the_standard_characteristics_are_listened_to(radio: Radio):
    radio.add("F4:12:FA:00:30:AB", "URTM030", fitness_machine=True)
    report = "\n".join(run_probe())
    assert radio.subscribed == [
        ftms.TREADMILL_DATA,
        ftms.CONTROL_POINT,
        ftms.FITNESS_MACHINE_STATUS,
        ftms.TRAINING_STATUS,
    ]
    assert radio.written == []
    # The vendor channel is still listed and read from - just not switched on - and the report says so.
    assert VENDOR_NOTIFY in report
    assert "Not listening to 1 characteristic(s) outside the standard services: fff1" in report
    assert "walka probe --all" in report
    assert "Listening to 4 characteristic(s)" in report
    assert "2acd=1" in report  # the packet that did arrive is in the report
    assert "min=0.96 max=6.43 step=0.16 km/h (0.1 mph grid: US firmware)" in report


def test_all_listens_to_the_vendor_channels_too(radio: Radio):
    radio.add("F4:12:FA:00:30:AB", "URTM030", fitness_machine=True)
    report = "\n".join(run_probe(all_characteristics=True))
    assert radio.subscribed[-1] == VENDOR_NOTIFY and len(radio.subscribed) == 5
    assert "Not listening" not in report and "Listening to 5 characteristic(s)" in report
    assert radio.written == []


def test_the_report_says_truthfully_what_the_probe_writes(radio: Radio):
    radio.add("F4:12:FA:00:30:AB", "URTM030", fitness_machine=True)
    head = " ".join(run_probe()[:4])
    assert "never writes a command" in head and "belt cannot move" in head
    assert "turns notifications on" in head
    assert "never writes to the treadmill" not in head  # the old, too generous, claim


@pytest.mark.parametrize(
    "service_uuid,expected",
    [
        (ftms.FTMS_SERVICE, True),
        (ftms.FTMS_SERVICE.upper(), True),
        (ftms.DEVICE_INFORMATION_SERVICE, True),
        (ftms.HEART_RATE_SERVICE, True),
        (VENDOR_SERVICE, False),
        ("0000fee0-0000-1000-8000-00805f9b34fb", False),
        (ftms.RSC_SERVICE, False),
    ],
)
def test_which_services_are_listened_to(service_uuid: str, expected: bool):
    assert probe.listens_to(service_uuid) is expected
    assert probe.listens_to(service_uuid, all_characteristics=True) is True


def test_the_named_pad_is_probed_rather_than_the_nearest_fitness_machine(radio: Radio):
    radio.add("11:11:11:11:11:11", "KICKR BIKE", fitness_machine=True, rssi=-30)
    radio.add("F4:12:FA:00:30:AB", "URTM030", fitness_machine=True, rssi=-80)
    radio.add("22:22:22:22:22:22", "TV", rssi=-20)
    report = run_probe()
    assert radio.connected_to == ["F4:12:FA:00:30:AB"]
    listed = [line for line in report if line.startswith((" * ", "   ")) and "rssi=" in line]
    assert [line.split()[1] if line.startswith(" *") else line.split()[0] for line in listed] == [
        "F4:12:FA:00:30:AB",
        "11:11:11:11:11:11",
        "22:22:22:22:22:22",
    ]


def test_an_address_picks_the_device_and_nothing_found_is_explained(radio: Radio):
    radio.add("11:11:11:11:11:11", "KICKR BIKE", fitness_machine=True)
    radio.add("F4:12:FA:00:30:AB", "URTM030", fitness_machine=True)
    run_probe(address="11:11:11:11:11:11")
    assert radio.connected_to == ["11:11:11:11:11:11"]

    radio.adverts.clear()
    radio.add("22:22:22:22:22:22", "TV")
    report = "\n".join(run_probe())
    assert "No treadmill found" in report and radio.connected_to == ["11:11:11:11:11:11"]


def test_a_treadmill_of_another_make_is_probed_and_a_bike_is_not(radio: Radio):
    """The report is how a new treadmill gets to be supported: it must pick
    the treadmill by itself, and say what each machine claims to be."""
    radio.add("11:11:11:11:11:11", "KICKR BIKE", fitness_machine=True, rssi=-30, kind=bytes.fromhex("012000"))
    radio.add("88:88:88:88:88:88", "FS-A1B2C3", fitness_machine=True, rssi=-80, kind=bytes.fromhex("010100"))
    report = run_probe()
    assert radio.connected_to == ["88:88:88:88:88:88"]
    bike = next(line for line in report if "KICKR BIKE" in line)
    treadmill = next(line for line in report if "FS-A1B2C3" in line and "rssi=" in line)
    assert bike.startswith("   ") and bike.endswith("kind=indoor bike")
    assert treadmill.startswith(" * ") and treadmill.endswith("kind=treadmill")

    radio.adverts.pop("88:88:88:88:88:88")
    assert "No treadmill found" in "\n".join(run_probe())
