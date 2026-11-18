"""The bleak adapter: which advertiser ``find`` settles on.

Only the choice is tested here, with a scripted scanner in bleak's place -
no radio is touched. (Connecting and GATT traffic are covered against the
emulated pad in test_ftms_ble*.py.)
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from walka import ftms
from walka.treadmill import bleak_backend
from walka.treadmill.bleak_backend import BleakBackend

URTM = ("URTM",)


TREADMILL, BIKE = bytes.fromhex("010100"), bytes.fromhex("012000")  # FTMS service data: flags, machine type


def advert(
    address: str,
    name: str | None = None,
    *,
    fitness_machine: bool = False,
    rssi: int = -60,
    kind: bytes | None = None,
):
    """One advertisement as bleak reports it: ``(device, advertisement data)``.

    ``kind`` is the service data a fitness machine may add to say what it is."""
    device = SimpleNamespace(address=address, name=None)
    data = SimpleNamespace(
        local_name=name,
        service_uuids=[ftms.FTMS_SERVICE] if fitness_machine else [],
        service_data={ftms.FTMS_SERVICE: kind} if kind is not None else {},
        rssi=rssi,
    )
    return device, data


class Air(list):
    """Advertisements "on the air", in the order they will be heard - and
    the scanners that were started to listen to them."""

    def __init__(self) -> None:
        super().__init__()
        self.scanners: list = []


@pytest.fixture
def air(monkeypatch) -> Air:
    heard = Air()

    class ScriptedScanner:
        def __init__(self, **_kwargs) -> None:
            self.running = False
            heard.scanners.append(self)

        async def __aenter__(self):
            self.running = True
            return self

        async def __aexit__(self, *_exc) -> None:
            self.running = False

        async def advertisement_data(self):
            for item in heard:
                await asyncio.sleep(0)
                yield item
            await asyncio.Event().wait()  # nothing more: the scan goes on until it is stopped

    monkeypatch.setattr(bleak_backend, "BleakScanner", ScriptedScanner)
    return heard


def find(address=None, prefixes=URTM, timeout=0.05):
    return asyncio.run(asyncio.wait_for(BleakBackend().find(address, prefixes, timeout), 5))


def test_the_named_pad_is_preferred_to_a_fitness_machine_heard_first(air):
    air += [
        advert("11:11:11:11:11:11", "KICKR BIKE 1234", fitness_machine=True, rssi=-40),
        advert("22:22:22:22:22:22", "NordicTrack T6.5", fitness_machine=True, rssi=-50),
        advert("33:33:33:33:33:33", "URTM030", fitness_machine=True, rssi=-80),
    ]
    found = find(timeout=30.0)  # ... and the scan ends as soon as the name is heard
    assert (found.name, found.address, found.ftms, found.rssi) == ("URTM030", "33:33:33:33:33:33", True, -80)
    assert air.scanners and not air.scanners[0].running


def test_a_name_that_arrives_in_a_later_packet_still_wins(air):
    """Windows often delivers the service list first and the name with the
    scan response: the first packet alone must not decide."""
    air += [
        advert("33:33:33:33:33:33", None, fitness_machine=True),
        advert("11:11:11:11:11:11", "Some Bike", fitness_machine=True, rssi=-30),
        advert("33:33:33:33:33:33", "urtm030", fitness_machine=True),
    ]
    assert find(timeout=30.0).address == "33:33:33:33:33:33"


def test_a_pad_that_only_matches_by_name_is_found(air):
    air += [advert("44:44:44:44:44:44", "URTM054")]
    found = find(timeout=30.0)
    assert found.address == "44:44:44:44:44:44" and found.ftms is False


def test_without_a_known_name_the_strongest_fitness_machine_is_taken_at_the_end_of_the_scan(air):
    air += [
        advert("11:11:11:11:11:11", "Treadmill X", fitness_machine=True, rssi=-75),
        advert("55:55:55:55:55:55", "Living Room TV", rssi=-20),
        advert("22:22:22:22:22:22", "Treadmill Y", fitness_machine=True, rssi=-48),
        advert("11:11:11:11:11:11", "Treadmill X", fitness_machine=True, rssi=-70),
    ]
    found = find()
    assert (found.name, found.address) == ("Treadmill Y", "22:22:22:22:22:22")


def test_nothing_suitable_is_none(air):
    air += [advert("55:55:55:55:55:55", "Living Room TV"), advert("66:66:66:66:66:66", None)]
    assert find() is None
    assert find(prefixes=()) is None
    assert not air.scanners[-1].running  # the scan is stopped either way


def test_a_configured_address_is_the_only_thing_that_matches(air):
    air += [
        advert("33:33:33:33:33:33", "URTM030", fitness_machine=True),
        advert("AA:BB:CC:DD:EE:FF", "Treadmill X", fitness_machine=True),
    ]
    assert find("aa:bb:cc:dd:ee:ff", timeout=30.0).name == "Treadmill X"
    # The pad at that address is off: nothing else will do, however well it is named.
    assert find("AA:BB:CC:DD:EE:00") is None


# ---- treadmills of other makes ----------------------------------------------------------

KNOWN = ("URTM", "WELLFIT TM Linker")


def test_a_wellfit_is_taken_by_its_name_and_its_loudspeaker_is_not(air):
    """A WELLFIT advertises two names; only "Linker" is the treadmill."""
    air += [
        advert("77:77:77:77:77:77", "WELLFIT TM Audio", rssi=-30),
        advert("88:88:88:88:88:88", "WELLFIT TM Linker", fitness_machine=True, rssi=-70),
    ]
    found = find(prefixes=KNOWN, timeout=30.0)  # at once: not at the end of the scan
    assert (found.name, found.address) == ("WELLFIT TM Linker", "88:88:88:88:88:88")

    del air[1]  # the treadmill is off: the loudspeaker is no substitute
    assert find(prefixes=KNOWN) is None


def test_a_machine_that_says_it_is_not_a_treadmill_is_never_settled_for(air):
    air += [
        advert("11:11:11:11:11:11", "KICKR BIKE", fitness_machine=True, rssi=-30, kind=BIKE),
        advert("22:22:22:22:22:22", "Treadmill Y", fitness_machine=True, rssi=-80),
    ]
    found = find()
    assert found.name == "Treadmill Y" and found.treadmill is None  # it does not say: that is allowed

    del air[1]
    assert find() is None  # "Start" in the game must not reach for a bike trainer


def test_what_the_advertisement_says_is_passed_on(air):
    air += [advert("22:22:22:22:22:22", "Treadmill Y", fitness_machine=True, kind=TREADMILL)]
    found = find()
    assert found.ftms is True and found.treadmill is True


def test_a_known_name_counts_for_more_than_the_kind_it_claims(air):
    """Firmware gets the type bits wrong more often than a model its name."""
    air += [advert("33:33:33:33:33:33", "URTM030", fitness_machine=True, kind=BIKE)]
    assert find(timeout=30.0).address == "33:33:33:33:33:33"
