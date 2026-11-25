"""The dashboard can be translated: the machinery is there, and there are no translations yet."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "companion" / "src" / "walka" / "static"
NODE = shutil.which("node")


def test_the_list_of_strings_is_up_to_date():
    done = subprocess.run([sys.executable, str(ROOT / "tools" / "dashboard_strings.py"), "--check"], capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr


def test_the_list_holds_the_pages_words():
    strings = set(json.loads((STATIC / "locales" / "strings.json").read_text("utf-8"))["strings"])
    for words in ("Settings", "Check my treadmill", "Walka only listens to your treadmill.", "Miles or kilometres?",
                  "Tell me when a new version of Walka is out", "Walka {1} is out."):  # fmt: skip
        assert words in strings, words
    for not_words in ("muted small", "/api/treadmill/check", "#i-check", "noopener noreferrer"):
        assert not_words not in strings, not_words


def test_there_are_no_translations_yet():
    assert sorted(p.name for p in (STATIC / "locales").iterdir()) == ["strings.json"]
    assert "export const AVAILABLE = []; // no translations yet" in (STATIC / "i18n.js").read_text("utf-8")


def test_every_word_written_goes_through_the_translation():
    util = (STATIC / "util.js").read_text("utf-8")
    assert 'el.textContent = tr(text);' in util
    assert 'el.textContent = tr(value);' in util  # h(..., {text})
    assert "WORDS.has(name) ? tr(text) : text" in util  # setAttr: aria-label, title, placeholder, alt


@pytest.mark.skipif(NODE is None, reason="Node.js is not installed")
def test_a_catalog_translates_exact_text_and_text_put_together(tmp_path):
    script = tmp_path / "run.mjs"
    script.write_text(
        f"""
import {{ tr, t, useCatalog }} from {json.dumps((STATIC / "i18n.js").as_uri())};
const out = [];
out.push(tr("Settings"));  // no catalog: as it is
useCatalog({{
  "Settings": "Einstellungen",
  "Walka {{1}} is out.": "Walka {{1}} ist da.",
  "Step {{1}} of {{2}}": "Schritt {{1}} von {{2}}",
  "running": "läuft",
  "Belt: {{1}}": "Band: {{1}}",
}});
out.push(tr("Settings"), tr("Walka 1.0.0 is out."), tr("Step 2 of 5"), tr("Belt: running"),
         tr("Not in the catalog"), t("Walka {{1}} is out.", "2.0"));
console.log(JSON.stringify(out));
""",
        "utf-8",
    )
    # (Node writes UTF-8; Windows would otherwise read it in its own code page.)
    done = subprocess.run([NODE, str(script)], capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout) == [
        "Settings",
        "Einstellungen",
        "Walka 1.0.0 ist da.",
        "Schritt 2 von 5",
        "Band: läuft",
        "Not in the catalog",
        "Walka 2.0 ist da.",
    ]
