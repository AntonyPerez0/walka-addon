"""Decision 1: for pads that only report when asked, Walka may send one fixed
"report your numbers" query per protocol - these exact bytes - and nothing else.

These tests pin every byte, and that nothing else in Walka writes to a
characteristic. The FTMS reader's own door still cannot write at all (see
test_ftms_ble.py, which is unchanged)."""

from __future__ import annotations

import asyncio
import inspect
import re
from pathlib import Path
from types import SimpleNamespace

from walka import ftms
from walka.treadmill import proprietary, query_link

COMPANION = Path(__file__).resolve().parents[1]
SRC = COMPANION / "src" / "walka"

# Every byte Walka may send a treadmill, besides switching notifications on.
ALLOWED = {
    # protocol: (service, characteristic, payload)
    "walkingpad": (ftms.uuid16(0xFE00), ftms.uuid16(0xFE02), bytes.fromhex("F7 A2 00 00 A2 FD")),
    "fitshow": (ftms.uuid16(0xFFF0), ftms.uuid16(0xFFF2), bytes.fromhex("02 51 51 03")),
}


def test_the_table_of_queries_is_exactly_this():
    assert set(proprietary.QUERIES) == set(ALLOWED)
    for protocol, (service, characteristic, payload) in ALLOWED.items():
        query = proprietary.QUERIES[protocol]
        assert (query.service, query.characteristic, query.payload) == (service, characteristic, payload), protocol
        assert query.every_s >= 0.5, "no faster than twice a second"
    # Frozen: nothing can add a query, or change one, at run time.
    try:
        proprietary.QUERIES["x"] = None  # type: ignore[index]
    except TypeError:
        pass
    else:
        raise AssertionError("the table can be written to")


def test_each_query_only_asks_for_the_status():
    walkingpad = proprietary.QUERIES["walkingpad"].payload
    # F7 header, A2 message, 00 = "stats" (01 would set a speed, 02 a mode, 04 start), FD footer.
    assert walkingpad[0] == 0xF7 and walkingpad[1:3] == b"\xa2\x00" and walkingpad[-1] == 0xFD
    fitshow = proprietary.QUERIES["fitshow"].payload
    # 02 start, 51 = SYS_STATUS (53 would be SYS_CONTROL: start, stop, speed), 51 XOR, 03 end.
    assert fitshow == bytes([0x02, 0x51, 0x51, 0x03])
    for query in proprietary.QUERIES.values():
        assert query.characteristic != ftms.CONTROL_POINT and query.service != ftms.FTMS_SERVICE


def test_only_the_query_link_writes_and_only_the_table():
    writers = []
    # Walka's code, the program's own starter, and the build script.
    for path in [*SRC.rglob("*.py"), *COMPANION.glob("*.py")]:
        text = path.read_text("utf-8")
        for match in re.finditer(r"write_gatt_char|write_gatt_descriptor|\.write\(", text):
            writers.append((path.relative_to(SRC if path.is_relative_to(SRC) else COMPANION).as_posix(), match.group(0)))
    assert (COMPANION / "walka_launcher.py").is_file()
    # (.write( of files and streams are not about Bluetooth; only gatt writes count here.)
    gatt = [w for w in writers if w[1].startswith("write_gatt")]
    assert gatt == [("treadmill/query_link.py", "write_gatt_char")]
    # ask() takes nothing: the bytes and the characteristic are the table's.
    assert list(inspect.signature(query_link.QueryClientAdapter.ask).parameters) == ["self"]
    source = inspect.getsource(query_link.QueryClientAdapter.ask)
    assert "write_gatt_char(self._query.characteristic, self._query.payload, response=False)" in source


def test_ask_writes_exactly_the_query_and_nothing_else(monkeypatch):
    written: list[tuple] = []

    class Bleak:
        def __init__(self, device, disconnected_callback=None, timeout=None) -> None:
            pass

        async def write_gatt_char(self, char, data, response=None) -> None:
            written.append((char, bytes(data), response))

    monkeypatch.setattr("walka.treadmill.bleak_backend.BleakClient", Bleak)
    adapter = query_link.QueryClientAdapter("F4:12:FA:00:00:01", lambda: None, "walkingpad")
    asyncio.run(adapter.ask())
    assert written == [(ftms.uuid16(0xFE02), bytes.fromhex("F7A20000A2FD"), False)]
    try:
        query_link.QueryClientAdapter("F4:12:FA:00:00:01", lambda: None, "anything else")
    except KeyError:
        pass
    else:
        raise AssertionError("a protocol with no query got a door to write through")


def test_the_ftms_reader_never_gets_the_query_door():
    from walka.treadmill.ftms_ble import FtmsTreadmill

    backend = SimpleNamespace(client=lambda device, cb: "plain", query_client=lambda *a: "query")
    assert FtmsTreadmill()._make_client(backend, None, lambda: None) == "plain"
