"""The dashboard's English strings, for a translator to start from.

    python tools/dashboard_strings.py          # writes companion/src/walka/static/locales/strings.json
    python tools/dashboard_strings.py --check  # fails if that file is out of date (the tests run this)

What it collects:

* from index.html and floating.html: every piece of text and every
  aria-label, title, placeholder and alt;
* from the dashboard's scripts: every string literal that reads like words
  for people. A template literal becomes a key with numbered gaps -
  `Walka ${version} is out.` -> "Walka {1} is out." - which is how
  static/i18n.js matches text put together while the page runs.

It is a template: a translation is locales/<language>.json with
{"language": ..., "strings": {English: translation}} (see i18n.js). The
scripts' list is found by looking, so it can hold a few strings no one
sees; a translation can leave those out.
"""

from __future__ import annotations

import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "companion" / "src" / "walka" / "static"
OUT = STATIC / "locales" / "strings.json"
PAGES = ("index.html", "floating.html")
SKIP_SCRIPTS = {"i18n.js"}
ATTRIBUTES = {"aria-label", "title", "placeholder", "alt"}
SKIP_TAGS = {"script", "style", "svg", "code", "kbd", "symbol", "path", "title"}


class _Page(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.strings: set[str] = set()
        self.skipping: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in SKIP_TAGS:
            self.skipping.append(tag)
        for name, value in attrs:
            if name in ATTRIBUTES and value and re.search(r"[A-Za-z]", value):
                self.strings.add(" ".join(value.split()))

    def handle_endtag(self, tag: str) -> None:
        if self.skipping and self.skipping[-1] == tag:
            self.skipping.pop()

    def handle_data(self, data: str) -> None:
        text = " ".join(data.split())
        if text and not self.skipping and re.search(r"[A-Za-z]", text):
            self.strings.add(text)


# A string literal: "...", '...' or `...` (no nesting of template literals).
_LITERAL = re.compile(r'"((?:[^"\\\n]|\\.)*)"|\'((?:[^\'\\\n]|\\.)*)\'|`((?:[^`\\]|\\.)*)`', re.S)
_NOT_WORDS = re.compile(
    r"^[#./@:\[(]|^--|^https?:|^data-|^aria-|^i-|=>|\bpx\b|^[a-z0-9_-]+$|^[A-Z0-9_]+$|[{};]\s*$|^\w+\(|^\$|%|\\u"
    r"|^noopener|\}-[a-z]"
)


def _template_key(body: str) -> str:
    count = 0

    def gap(_match: re.Match) -> str:
        nonlocal count
        count += 1
        return f"{{{count}}}"

    return re.sub(r"\$\{(?:[^{}]|\{[^{}]*\})*\}", gap, body)


def _reads_like_words(text: str) -> bool:
    text = text.strip()
    if len(text) < 2 or not re.search(r"[A-Za-z]{2}", text) or _NOT_WORDS.search(text):
        return False
    stripped = re.sub(r"\{\d+\}", "", text).strip()
    if not stripped or not re.search(r"[A-Za-z]{2}", stripped):
        return False
    # Words: a space between letters, or one capitalised word ("Connect", "Live").
    return bool(re.search(r"[A-Za-z][^\s]*\s+\S", stripped) or re.fullmatch(r"[A-Z][a-z]+[.!?]?", stripped))


def script_strings(source: str) -> set[str]:
    found: set[str] = set()
    # (Comments out first: their words are for programmers.)
    source = re.sub(r"(?m)^\s*//.*$|/\*[\s\S]*?\*/", "", source)
    for match in _LITERAL.finditer(source):
        double, single, template = match.groups()
        body = double if double is not None else single if single is not None else template
        if body is None:
            continue
        if template is not None:
            body = _template_key(body)
        body = re.sub(r"\\(.)", lambda m: "\n" if m.group(1) == "n" else m.group(1), body)
        text = " ".join(body.split())
        if "${" in text or "`" in text:
            continue  # a template inside a template: its parts are found on their own
        if all(token in CLASS_NAMES for token in text.split()):
            continue  # class names ("muted small")
        if _reads_like_words(text):
            found.add(text)
    return found


CLASS_NAMES = {
    name
    for sheet in ("app.css", "floating.css")
    for name in re.findall(r"\.([a-z][a-z0-9-]*)", (STATIC / sheet).read_text("utf-8"))
}


def collect() -> list[str]:
    strings: set[str] = set()
    for name in PAGES:
        page = _Page()
        page.feed((STATIC / name).read_text("utf-8"))
        strings |= page.strings
    for path in sorted(STATIC.glob("*.js")):
        if path.name not in SKIP_SCRIPTS:
            strings |= script_strings(path.read_text("utf-8"))
    return sorted(strings)


def render() -> str:
    return json.dumps({"language": "en", "strings": collect()}, ensure_ascii=False, indent=1) + "\n"


def main(argv: list[str]) -> int:
    text = render()
    if "--check" in argv:
        if not OUT.exists() or OUT.read_text("utf-8") != text:
            print(f"{OUT} is out of date: run python tools/dashboard_strings.py")
            return 1
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, "utf-8")
    print(f"{len(json.loads(text)['strings'])} strings -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
