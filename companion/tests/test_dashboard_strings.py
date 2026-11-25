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


def test_nothing_on_the_page_is_about_reading_the_screen():
    """The game is read from the file it saves (at logout or /reload), not off the screen."""
    strings = json.loads((STATIC / "locales" / "strings.json").read_text("utf-8"))["strings"]
    text = "\n".join(strings).lower()
    for stale in ("top-left", "borderless", "game signal", "paused in game", "no signal yet", "screen reading",
                  "screen capture", "screen recording", "walking - no game session", "in the game now"):  # fmt: skip
        assert stale not in text, stale
    for words in ("Let the game save once", "Saved by the game", "Walking", "In game"):
        assert words in strings, words


@pytest.mark.skipif(NODE is None, reason="Node.js is not installed")
def test_the_game_link_says_what_the_saved_file_holds(tmp_path):
    script = tmp_path / "run.mjs"
    script.write_text(
        f"""
import {{ gameHealth, setupSteps, describe, walkLabel }} from {json.dumps((STATIC / "status.js").as_uri())};
const base = {{ time: 100000, treadmill: {{ connected: true, moving: false, state: "stopped", age_s: 0 }},
               addon: {{ dir: "AddOns" }}, setup: {{}}, session: null }};
const games = {{
  saved: {{ game_found: true, files: 1, saved_at: 100000 - 7200, read_at: 100000, sessions: 3, characters: 1, demo: false }},
  waiting: {{ game_found: true, files: 0, saved_at: null, read_at: 100000, sessions: 0, characters: 0, demo: false }},
  none: {{ game_found: false, files: 0, saved_at: null, read_at: null, sessions: 0, characters: 0, demo: false }},
}};
const out = {{}};
for (const [name, game] of Object.entries(games)) {{
  const s = {{ ...base, game }};
  out[name] = {{ ...gameHealth(s), step: setupSteps(s).game }};
}}
out.walking = describe({{ ...base, game: games.none, treadmill: {{ ...base.treadmill, moving: true }} }});
out.labels = [walkLabel({{ source: "wow", character: "Thrall" }}), walkLabel({{ source: "wow", character: "" }}),
              walkLabel({{ source: "free", character: "" }})];
console.log(JSON.stringify(out));
""",
        "utf-8",
    )
    done = subprocess.run([NODE, str(script)], capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert done.returncode == 0, done.stderr
    out = json.loads(done.stdout)
    saved = out["saved"]
    assert (saved["tone"], saved["pill"], saved["state"], saved["step"]) == ("ok", "found", "saved 2 h ago", True)
    assert "3 game sessions and 1 character" in saved["detail"] and "/reload" in saved["detail"]
    assert (out["waiting"]["tone"], out["waiting"]["step"]) == ("idle", False), "not a fault: the game saves later"
    assert "has not saved" in out["waiting"]["detail"]
    assert (out["none"]["tone"], out["none"]["state"], out["none"]["step"]) == ("idle", "game not found", False)
    assert "recorded all the same" in out["none"]["detail"]
    assert out["walking"]["title"] == "Recording your walk" and "/reload" in out["walking"]["detail"]
    assert out["labels"] == ["In game \N{MIDDLE DOT} Thrall", "In game", "No game session"]
