"""Reading the game's saved-variables files.

An addon cannot write files, but the game writes one for it: what the addon
keeps in its saved variables is stored as a small Lua program -
``WalkaCharDB = { ... }`` - when the player logs out or reloads the
interface. That file is how the companion gets at things too big for the
strip on the screen (the road a character has travelled).

This is a reader for exactly that kind of file and nothing more. It does not
run Lua: it accepts assignments of *data* - tables, strings, numbers,
booleans, nil - and refuses everything else (a function call, an operator, a
name used as a value), so a file that has been tampered with, or cut short
while the game was writing it, is an error rather than a surprise.

What the client writes (seen in real files of this client):

* ``Name = value`` at the top level, one or more;
* tables as ``{`` ... ``}`` with entries ``["key"] = value,`` /
  ``[12] = value,`` or bare values for the array part - where ``nil,``
  holds the place of a gap;
* strings in double quotes with ``\\"``, ``\\\\``, ``\\n``, ``\\r`` and
  ``\\000``-style escapes, every other byte as it is;
* numbers like ``-8913.25``, ``1e-05``; ``nil --[[ inf ]]`` for what cannot
  be saved; comments ``-- [1]`` in older clients.
"""

from __future__ import annotations

import re
from typing import Any

# Real files nest about eight tables deep.
MAX_DEPTH = 32
MAX_BYTES = 64 * 1024 * 1024

_SKIP = re.compile(rb"(?:[ \t\r\n\f\v]+|--\[\[.*?\]\]|--(?!\[\[)[^\r\n]*)*", re.S)
_NAME = re.compile(rb"([A-Za-z_][A-Za-z0-9_]*)[ \t\r\n]*=(?!=)")
_PLAIN_STRING = re.compile(rb'"([^"\\\r\n]*)"')
_NUMBER = re.compile(rb"-?\d+(\.\d*)?([eE][-+]?\d+)?")
_NOT_A_NUMBER = re.compile(rb"-?(?:inf|nan|1\.#[A-Z]+)")  # what older clients wrote for inf and nan
_DIGITS = re.compile(rb"\d{1,3}")
_ESCAPES = {
    ord("n"): b"\n",
    ord("r"): b"\r",
    ord("t"): b"\t",
    ord('"'): b'"',
    ord("'"): b"'",
    ord("\\"): b"\\",
    ord("a"): b"\a",
    ord("b"): b"\b",
    ord("f"): b"\f",
    ord("v"): b"\v",
    ord("\n"): b"\n",
}
_WORDS = ((b"true", True), (b"false", False), (b"nil", None))


class SavedVariablesError(ValueError):
    """The bytes are not a complete saved-variables file."""


def _fail(data: bytes, pos: int, what: str) -> Any:
    line = data.count(b"\n", 0, pos) + 1
    raise SavedVariablesError(f"line {line}: {what}")


def _skip(data: bytes, pos: int) -> int:
    """Past blanks and comments."""
    match = _SKIP.match(data, pos)
    pos = match.end() if match else pos
    if data.startswith(b"--[[", pos):
        _fail(data, pos, "a comment that never ends")
    return pos


def _string(data: bytes, pos: int) -> tuple[str, int]:
    plain = _PLAIN_STRING.match(data, pos)
    if plain:
        return plain.group(1).decode("utf-8", "replace"), plain.end()
    out = bytearray()
    i = pos + 1
    size = len(data)
    while True:
        if i >= size:
            _fail(data, pos, "a string that never ends")
        byte = data[i]
        if byte == 0x22:  # the closing quote
            # (Escapes are resolved before decoding: "\195\171" is one letter.)
            return out.decode("utf-8", "replace"), i + 1
        if byte in (0x0A, 0x0D):
            _fail(data, i, "a line break inside a string")
        if byte != 0x5C:  # not a backslash
            out.append(byte)
            i += 1
            continue
        following = data[i + 1 : i + 2]
        if following and following[0] in _ESCAPES:
            out += _ESCAPES[following[0]]
            i += 2
            continue
        digits = _DIGITS.match(data, i + 1)
        if not digits or int(digits.group()) > 255:
            _fail(data, i, "an escape this reader does not know")
        out.append(int(digits.group()))
        i = digits.end()


def _value(data: bytes, pos: int, depth: int) -> tuple[Any, int]:
    first = data[pos : pos + 1]
    if first == b"{":
        return _table(data, pos + 1, depth + 1)
    if first == b'"':
        return _string(data, pos)
    for word, value in _WORDS:
        if data.startswith(word, pos) and not data[pos + len(word) : pos + len(word) + 1].isalnum():
            return value, pos + len(word)
    odd = _NOT_A_NUMBER.match(data, pos)
    if odd:
        return None, odd.end()
    number = _NUMBER.match(data, pos)
    if number:
        text = number.group()
        value: int | float = float(text) if number.group(1) or number.group(2) else int(text)
        if value in (float("inf"), float("-inf")):
            _fail(data, pos, "a number too large to be one")
        return value, number.end()
    return _fail(data, pos, "expected a value (a table, a string, a number, true, false or nil)")


def _table(data: bytes, pos: int, depth: int) -> tuple[dict[Any, Any], int]:
    if depth > MAX_DEPTH:
        _fail(data, pos, "tables nested too deep")
    table: dict[Any, Any] = {}
    index = 0
    while True:
        pos = _skip(data, pos)
        first = data[pos : pos + 1]
        if first == b"}":
            return table, pos + 1
        if first == b"":
            _fail(data, pos, "a table that never ends")
        name = _NAME.match(data, pos)
        if first == b"[":
            key, pos = _value(data, _skip(data, pos + 1), depth)
            if key is None or isinstance(key, (dict, bool)):
                # (No boolean keys: Python would file True under 1.)
                _fail(data, pos, "a key that is not a string or a number")
            pos = _skip(data, pos)
            if data[pos : pos + 1] != b"]":
                _fail(data, pos, "expected ]")
            pos = _skip(data, pos + 1)
            if data[pos : pos + 1] != b"=":
                _fail(data, pos, "expected =")
            value, pos = _value(data, _skip(data, pos + 1), depth)
        elif name and name.group(1) not in (b"true", b"false", b"nil"):
            key = name.group(1).decode("ascii")
            value, pos = _value(data, _skip(data, name.end()), depth)
        else:
            index += 1  # a positional value keeps its place even when it is nil
            key = index
            value, pos = _value(data, pos, depth)
        if value is not None:
            table[key] = value
        pos = _skip(data, pos)
        first = data[pos : pos + 1]
        if first in (b",", b";"):
            pos += 1
        elif first != b"}":
            _fail(data, pos, "expected , or }")


def parse(data: bytes) -> dict[str, Any]:
    """Every ``Name = value`` of a saved-variables file, as ``{name: value}``.

    Tables come back as dicts (array parts under the keys 1, 2, ...; see
    ``sequence``); a variable saved as nil is left out. Raises
    ``SavedVariablesError`` for anything that is not such a file - also for
    an empty one, which is what a file looks like while the game rewrites it.
    """
    if len(data) > MAX_BYTES:
        raise SavedVariablesError("the file is too big")
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    out: dict[str, Any] = {}
    seen = False
    pos = _skip(data, 0)
    while pos < len(data):
        name = _NAME.match(data, pos)
        if not name:
            _fail(data, pos, "expected the name of a saved variable")
        value, pos = _value(data, _skip(data, name.end()), 0)
        seen = True
        if value is not None:
            out[name.group(1).decode("ascii")] = value
        pos = _skip(data, pos)
    if not seen:
        raise SavedVariablesError("no saved variables in the file")
    return out


def sequence(table: Any) -> list[Any]:
    """The array part of a table: the values at 1, 2, ... up to the first gap."""
    out: list[Any] = []
    while isinstance(table, dict) and len(out) + 1 in table:
        out.append(table[len(out) + 1])
    return out
