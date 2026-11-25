"""The table of known treadmills: evidence for every entry, and the docs made from it."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from walka.treadmill import known, readers

ROOT = Path(__file__).resolve().parents[2]


def test_every_entry_says_how_much_is_known_and_where_from():
    for entry in known.KNOWN_TREADMILLS:
        assert entry.evidence in known.EVIDENCE_LEVELS, entry
        assert entry.source, entry
        assert entry.protocol in {reader.key for reader in readers.READERS}, entry
        # "Verified" means a walk on it with Walka, checked - and says on what and when.
        assert bool(entry.checked) == (entry.evidence == known.VERIFIED), entry


def test_only_what_a_user_checked_is_verified():
    verified = [entry.model for entry in known.KNOWN_TREADMILLS if entry.evidence == known.VERIFIED]
    assert verified == ["Strol 2E (URTM030)"]


def test_the_most_specific_entry_wins_and_the_prefixes_stay_as_they_were():
    assert known.known_treadmill("URTM030").evidence == known.VERIFIED
    assert known.known_treadmill("URTM054").evidence == known.REPORTED
    assert known.DEFAULT_NAME_PREFIXES == ("URTM", "WELLFIT TM Linker")


def test_the_docs_table_is_made_from_this_one():
    done = subprocess.run([sys.executable, str(ROOT / "tools" / "treadmill_table.py"), "--check"], capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr
    doc = (ROOT / "docs" / "TREADMILLS.md").read_text("utf-8")
    for entry in known.KNOWN_TREADMILLS:
        assert f"`{entry.prefix}...`" in doc
