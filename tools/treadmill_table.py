"""The table of treadmills in docs/TREADMILLS.md, made from treadmill/known.py.

    python tools/treadmill_table.py          # rewrites the table between its markers
    python tools/treadmill_table.py --check  # fails if it is out of date (the tests run this)
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "companion" / "src"))

from walka.treadmill.known import KNOWN_TREADMILLS, VERIFIED  # noqa: E402

DOC = ROOT / "docs" / "TREADMILLS.md"
START = "<!-- treadmill-table:start (made by tools/treadmill_table.py from treadmill/known.py - edit that) -->"
END = "<!-- treadmill-table:end -->"

PROTOCOL_WORDS = {
    "ftms": "standard Bluetooth FTMS",
    "rsc": "Running Speed and Cadence",
}


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def table() -> str:
    rows = [
        "| Treadmill | Bluetooth name | Read as | Evidence | Where from |",
        "|---|---|---|---|---|",
    ]
    for entry in KNOWN_TREADMILLS:
        name = f"{entry.brand} {entry.model}".strip()
        name = f"**{name}**" if entry.evidence == VERIFIED else name
        evidence = f"**{entry.evidence}**" if entry.evidence == VERIFIED else entry.evidence
        if entry.checked:
            evidence += f" ({entry.checked})"
        if entry.evidence != VERIFIED:
            evidence += "; untested on hardware with Walka"
        where = entry.source + (f" {entry.notes}" if entry.notes else "")
        protocol = PROTOCOL_WORDS.get(entry.protocol, entry.protocol)
        rows.append(f"| {_cell(name)} | `{entry.prefix}...` | {protocol} | {_cell(evidence)} | {_cell(where)} |")
    rows.append(
        "| **Any other FTMS treadmill** | (found by the service it advertises, after a 15-second scan - or pick "
        "it: *Treadmill → Scan*) | standard Bluetooth FTMS | depends on the treadmill | Check its numbers as in "
        "[Before your first walk](INSTALL.md#before-your-first-walk). |"
    )
    return "\n".join([START, *rows, END])


def render(text: str) -> str:
    start, end = text.index(START), text.index(END) + len(END)
    return text[:start] + table() + text[end:]


def main(argv: list[str]) -> int:
    current = DOC.read_text("utf-8")
    wanted = render(current)
    if "--check" in argv:
        if wanted != current:
            print(f"The table in {DOC} is out of date: run python tools/treadmill_table.py")
            return 1
        return 0
    DOC.write_text(wanted, "utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
