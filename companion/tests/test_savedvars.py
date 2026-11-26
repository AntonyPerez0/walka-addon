"""The reader of the game's saved-variables files: what the client writes is
read exactly, and anything else - a file cut short, a file that is a program
rather than data - is refused."""

from __future__ import annotations

import random

import pytest

from walka import savedvars
from walka.savedvars import SavedVariablesError, parse, sequence

# A file as this client writes one: tabs, CRLF line ends, string keys in
# brackets, array parts as bare values with a comment that counts them.
REAL = (
    b"\r\n"
    b"WalkaCharDB = {\r\n"
    b'\t["version"] = 1,\r\n'
    b'\t["guid"] = "Player-4321-0ABCDEF1",\r\n'
    b'\t["key"] = "T\xc3\xa9st\\195\\171r-Azeroth",\r\n'
    b'\t["yards"] = 1234.5,\r\n'
    b'\t["beltYards"] = -0.25,\r\n'
    b'\t["tiny"] = 1e-05,\r\n'
    b'\t["big"] = 1.5E+3,\r\n'
    b'\t["on"] = true,\r\n'
    b'\t["off"] = false,\r\n'
    b'\t["lost"] = nil --[[ inf ]],\r\n'
    b'\t["segments"] = {\r\n'
    b"\t\t{\r\n"
    b'\t\t\t["c"] = 1,\r\n'
    b'\t\t\t["m"] = "w",\r\n'
    b'\t\t\t["x"] = -600,\r\n'
    b'\t\t\t["y"] = 4200,\r\n'
    b'\t\t\t["d"] = "3,20,0;3,19,-4",\r\n'
    b"\t\t}, -- [1]\r\n"
    b"\t\tnil, -- [2]\r\n"
    b"\t\t{\r\n"
    b'\t\t\t["note"] = "a \\"quoted\\" word, a back\\\\slash,\\na new line and a\\ttab",\r\n'
    b"\t\t}, -- [3]\r\n"
    b"\t},\r\n"
    b"\t[12] = {\r\n"
    b"\t},\r\n"
    b"\t[-3.5] = 7,\r\n"
    b"}\r\n"
)


def test_a_file_of_this_client_is_read_exactly():
    assert parse(REAL) == {
        "WalkaCharDB": {
            "version": 1,
            "guid": "Player-4321-0ABCDEF1",
            "key": "Téstër-Azeroth",  # raw bytes and escaped bytes, both UTF-8
            "yards": 1234.5,
            "beltYards": -0.25,
            "tiny": 1e-05,
            "big": 1500.0,
            "on": True,
            "off": False,
            "segments": {
                1: {"c": 1, "m": "w", "x": -600, "y": 4200, "d": "3,20,0;3,19,-4"},
                3: {"note": 'a "quoted" word, a back\\slash,\na new line and a\ttab'},
            },
            12: {},
            -3.5: 7,
        }
    }


def test_whole_numbers_stay_whole_and_the_rest_are_floats():
    values = parse(b"A = 7\nB = -7\nC = 7.0\nD = 7e0\nE = 0\nF = -0.5\n")
    assert values == {"A": 7, "B": -7, "C": 7.0, "D": 7.0, "E": 0, "F": -0.5}
    assert [type(values[name]) for name in "ABCDEF"] == [int, int, float, float, int, float]


def test_several_variables_and_one_saved_as_nil():
    source = b'-- a comment\nFirst = {\n}\nGone = nil\nSecond = "two" --[[ note ]] Third = {1, 2, 3}\n'
    assert parse(source) == {"First": {}, "Second": "two", "Third": {1: 1, 2: 2, 3: 3}}


def test_what_older_clients_and_other_writers_produce():
    # Bare names as keys, semicolons, no trailing comma, a byte order mark,
    # "inf" and "nan" written as such (nothing can be made of those), a
    # comment in every gap.
    source = b'\xef\xbb\xbfDB={a=1;b = {x = -1.#IND, y = inf, z = -nan, 4}; ["c"]--[[k]]=--[[v]]"v" , [ 5 ] = "five"}'
    assert parse(source) == {"DB": {"a": 1, "b": {1: 4}, "c": "v", 5: "five"}}


def test_a_gap_in_the_array_part_keeps_the_places_of_what_follows():
    table = parse(b"T = { 10, nil, 30, nil }")["T"]
    assert table == {1: 10, 3: 30}
    assert sequence(table) == [10]
    assert sequence(parse(b"T = { 10, 20, 30, [5] = 50 }")["T"]) == [10, 20, 30]
    assert sequence({}) == [] and sequence(None) == [] and sequence("text") == []


def test_strings_with_every_escape_this_client_writes():
    source = rb'S = "\"\\\n\r\t\a\b\f\v\'\0\000\65\065\255end"'
    assert parse(source)["S"].encode("utf-8", "surrogateescape")[:14] == b"\"\\\n\r\t\a\b\f\v'\0\0AA"
    # A byte that is no UTF-8 does not stop the file from being read.
    assert parse(b'S = "caf\xe9"')["S"] == "caf\ufffd"
    # Lua's own way of writing a line break inside a string.
    assert parse(b'S = "one\\\ntwo"')["S"] == "one\ntwo"


@pytest.mark.parametrize(
    "source",
    [
        b"",
        b"   \r\n\t ",
        b"-- only a comment\n",
        b"X",
        b"X =",
        b"X = {",
        b'X = "never ends',
        b'X = "a line\nbreak"',
        b"X = --[[ never ends",
        b"X = { 1, 2",
        b"X = { [1] 2 }",
        b"X = { [1 = 2 }",
        b"X = { 1 2 }",
        b"X = { [nil] = 1 }",
        b"X = { [true] = 1 }",
        b"X = { [{}] = 1 }",
        b'X = "\\q"',
        b'X = "\\999"',
        b"X = 1e999",
        b"X = 0x10",
        b"X = .5",
        b"X = 'single'",
        b"X = [[long]]",
        b"X = Y",
        b"X = { Y }",
        b"X = { y = z }",
        b"X = 1 + 1",
        b'X = "a" .. "b"',
        b"X = -{}",
        b"X = #Y",
        b"X = not true",
        b"X = print('hello')",
        b"X = os.time()",
        b"X = setmetatable({}, {})",
        b"X = function() end",
        b"X = (1)",
        b"X = {} X.y = 1",
        b"X.y = 1",
        b"X == 1",
        b"local X = 1",
        b"return { 1 }",
        b"X = 1; Y = 2",
        b"X = { 1 } }",
        b"X = 1 junk",
        b"= 1",
        b"X = true1",
        b"{ 1 }",
        b"\x00\x01\x02",
    ],
)
def test_anything_that_is_not_saved_data_is_refused(source: bytes):
    with pytest.raises(SavedVariablesError):
        parse(source)


def test_a_file_cut_short_anywhere_is_refused():
    # (While the game is writing the file, or after a crash while it did.)
    whole = REAL.rstrip()
    assert parse(whole) == parse(REAL)
    for length in range(len(whole)):
        with pytest.raises(SavedVariablesError):
            parse(whole[:length])


def test_the_error_says_where():
    with pytest.raises(SavedVariablesError, match="line 3"):
        parse(b"X = {\n  1,\n  oops(),\n}\n")


def test_nesting_and_size_have_limits():
    deep = savedvars.MAX_DEPTH
    assert parse(b"X = " + b"{" * deep + b"}" * deep) is not None
    with pytest.raises(SavedVariablesError, match="too deep"):
        parse(b"X = " + b"{" * (deep + 1) + b"}" * (deep + 1))
    with pytest.raises(SavedVariablesError, match="too deep"):
        parse(b"X = " + b"{[" * 5000)  # (nor through the keys)
    with pytest.raises(SavedVariablesError, match="too big"):
        parse(b" " * (savedvars.MAX_BYTES + 1))


def test_a_big_file_is_read():
    segment = b'{ ["c"] = 1, ["m"] = "w", ["x"] = 0, ["y"] = 0, ["d"] = "' + b"3,20,-1;" * 400 + b'" }, -- [1]\n'
    table = parse(b"WalkaCharDB = {\n" + segment * 300 + b"}\n")["WalkaCharDB"]
    assert len(sequence(table)) == 300


def test_damaged_files_never_raise_anything_else():
    rng = random.Random(20261118)
    alphabet = b'{}[]="\\,;-\n nil true 0 1 9 . e x'
    for _ in range(3000):
        data = bytearray(REAL)
        for _ in range(rng.randint(1, 6)):
            at = rng.randrange(len(data))
            action = rng.randrange(3)
            if action == 0:
                data[at] = rng.choice(alphabet)
            elif action == 1:
                del data[at : at + rng.randint(1, 40)]
            else:
                data[at:at] = bytes(rng.choice(alphabet) for _ in range(rng.randint(1, 8)))
        try:
            values = parse(bytes(data))
        except SavedVariablesError:
            continue
        assert isinstance(values, dict)


def test_a_number_of_thousands_of_digits_is_a_damaged_file_not_a_crash():
    """Python refuses to make an int of more than 4,300 digits (ValueError):
    that must come out as what is wrong with the file, like any other damage."""
    import pytest

    from walka import savedvars

    with pytest.raises(savedvars.SavedVariablesError):
        savedvars.parse(b"WalkaDB = { steps = " + b"9" * 5000 + b" }")
    with pytest.raises(savedvars.SavedVariablesError):
        savedvars.parse(b"WalkaDB = { steps = -" + b"9" * 5000 + b" }")
    assert savedvars.parse(b"WalkaDB = { steps = " + b"9" * 300 + b" }")["WalkaDB"]["steps"] == int("9" * 300)
