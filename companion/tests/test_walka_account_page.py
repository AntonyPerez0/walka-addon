"""The Walka-account card of the dashboard, and the few words elsewhere on the page that follow it.

The card is written by one script, ``static/walkaaccount.js``. It is checked
here in two ways, as the Share page is (test_share_page.py):

- as text: the script is part of the page, keeps nothing in the browser,
  never reads anything as markup, names no address but four pages on
  walka.dev, and holds the sentences the owner's rules give word for word;
- under Node, when Node is installed: the script runs against a small
  stand-in for the page (a few elements, a ``fetch`` that notes what is
  asked), is given made-up snapshots, and what it shows and what it asks of
  the companion is looked at. Those tests are skipped without Node.

The rule behind most of it: Walka works in full without a Walka account and
never reminds anybody of one. Without that part of the snapshot the page is
as it always was, and while nobody is signed in nothing but the card says a
word about it.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from aiohttp.test_utils import TestClient, TestServer

from test_app import Rig
from walka import cli, web
from walka.walka_account import row

STATIC = web.STATIC_DIR
SCRIPT = "walkaaccount.js"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="Node.js is not installed")

PAGES = {
    "https://walka.dev/terms/",
    "https://walka.dev/privacy/",
    "https://walka.dev/account/",
    "https://walka.dev/account/walks/",
}
DISCLAIMER = "https://github.com/AntonyPerez0/walka-addon/blob/HEAD/DISCLAIMER.md"

# The words. Where a sentence is the brief's, it says "Walka account" where the brief has "account" alone:
# the owner's answer of 24 November 2026 (decision 2 wants it so everywhere). Decision 11:
ENDED = (
    "Walka is no longer signed in to your Walka account, and has stopped sending. You signed out elsewhere, "
    "or the Walka account was deleted. Your walks on this computer are untouched."
)
# Decision 4 (with what the files say of it: a second, empty sign-in comes from another e-mail address).
NO_ACCOUNT_YET = (
    "This sign-in has no Walka account yet. To open one - or to delete this sign-in - go to walka.dev/account/ "
    "and sign in there the same way (with GitHub). If you already have a Walka account, it is under another "
    "e-mail address: sign out here and sign in the way that uses that address."
)
# Decision 5, the seven points. What goes: all sixteen things of a row - the last sentence names the two
# that are no number of the walk (its id there, and "origin": "walka"), in the README's own words.
GOES = (
    "What goes, of each walk: when it started and ended, the day, and how far this computer's clock was from "
    "world time; how long the belt moved; distance, steps and calories (estimates), and how many of the calories "
    "were on top of sitting; how each number was come by (for the calories: from speed or from heart rate - the "
    "word, not the heart rate); whether a walking session was running in the game; and the walk's name on this "
    "computer, which is its start time. Each walk goes under a random number made for it, and says that it came "
    "from Walka."
)
STAYS = (
    "What stays here: the samples, the heart rate, the top speed, the treadmill's name, the character, your "
    "weight, height, age and sex."
)
THEN = "From then on each walk goes when it is saved, without asking again."
PUBLIC = (
    "Your page on walka.dev is public. Walks you send count on it as soon as they are sent, so anyone who "
    "reloads it can tell when you were on the pad."
)
# ... and only of a page that lists days or walks (a page that lists neither gives out sums alone):
PUBLIC_ALL = 'With "All of them" your whole history shows there at once.'
WHOEVER = "Every walk recorded on this computer goes to this Walka account, whoever walked."
STOP = (
    "To stop: switch it off here, or sign out. Stopping deletes nothing in your Walka account. There a walk is "
    "deleted one at a time: walka.dev/account/walks/"
)
SIGN_OUT_HERE = '"Sign out" on walka.dev does not sign this computer out: sign out here.'
BEFORE_THE_BROWSER = (
    "Signing in makes a Walka account if you have none: terms (walka.dev/terms/), privacy (walka.dev/privacy/). "
    "Nothing is sent until you switch sending on. Your web browser opens for this, and Walka asks accounts.example."
)
RAIL_FOOT = "Runs on this computer. Nothing is uploaded."
RAIL_FOOT_SENDING = "Runs on this computer. Your walks are sent to your Walka account."
# What of it stays in sight in a window too narrow for the rail's foot.
WALKS_ARE_SENT = "Your walks are sent to your Walka account."
PAGE_STAYS = "Everything on this page stays on this computer."
PAGE_STAYS_SENDING = "Your walks are sent to your Walka account. Everything else on this page stays on this computer."
DELETE_SENT = (
    "Delete this walk from this computer? It disappears from your totals here and from the addon's history. "
    "This cannot be undone. It stays in your Walka account, and on your public page if you have one, until you "
    "delete it there: walka.dev/account/walks/"
)
# Decision 8, for a copy in a Walka account that is NOT the one signed in now (after signing out, say). It
# promises nothing Walka cannot know, names nobody, and does not say "your".
DELETE_SENT_ELSEWHERE = (
    "Delete this walk from this computer? It disappears from your totals here and from the addon's history. "
    "This cannot be undone. A copy of this walk was sent to a Walka account from this computer. Deleting it "
    "here does not delete that copy: that is done on walka.dev/account/walks/, signed in to that Walka account."
)
# ... and for a walk that has a copy in both.
ANOTHER_COPY = (
    ". Another copy was sent to a different Walka account from this computer. Deleting the walk here does not "
    "delete that copy either."
)
# Signing out, and the system's store did not say that the sign-in is gone from it. While sending was on
# the store held a second entry, the mark, and its name begins the same way: if the store let go of neither,
# "the entry" is two, and whoever deletes one by hand may leave the sign-in. So the sentence says so.
LEFT_IN = "Walka could not make sure that your sign-in was taken out of "
NAMED = 'its name begins "Walka account". There may be two such entries: delete both.'
LEFT_WINDOWS = LEFT_IN + "Windows' Credential Manager. Try again, or delete the entry there by hand: " + NAMED
LEFT_MAC = LEFT_IN + "your Mac's keychain. Try again, or delete the entry by hand in Keychain Access: " + NAMED
LEFT_ELSEWHERE = (
    LEFT_IN + "the password store of the computer that runs Walka. Try again, or delete the entry there by "
    "hand: " + NAMED
)


def read(name: str) -> str:
    return (STATIC / name).read_text("utf-8")


def flat(text: str) -> str:
    """A script's text with its line breaks and indents taken out of the sentences: one space for each run."""
    return re.sub(r"\s+", " ", text)


def card_markup(page: str) -> str:
    """The card's own part of index.html."""
    found = re.search(r'<section class="card wide" id="set-walka-account".*?</section>', page, re.S)
    assert found, "the card is not in index.html"
    return found.group(0)


# ---- the page's files, as text -------------------------------------------------------------------


def test_the_script_is_part_of_the_page(tmp_path: Path):
    assert cli.missing_page_files(STATIC) == []
    assert 'from "./walkaaccount.js";' in read("settings.js")
    # ... and the page is found wanting if it is left out: it is reached from index.html.
    copy = tmp_path / "static"
    shutil.copytree(STATIC, copy)
    (copy / SCRIPT).unlink()
    assert cli.missing_page_files(copy) == [SCRIPT]


def test_the_script_is_served(tmp_path: Path):
    rig = Rig(tmp_path)

    async def scenario():
        async with TestClient(TestServer(web.create_app(rig.app))) as client:
            response = await client.get(f"/static/{SCRIPT}")
            assert response.status == 200
            assert response.content_type == "text/javascript"
            assert response.headers["X-Content-Type-Options"] == "nosniff"
            page = await client.get("/")
            assert 'id="set-walka-account"' in await page.text()

    asyncio.run(asyncio.wait_for(scenario(), 30))


def test_nothing_is_kept_in_the_browser():
    """No key, no e-mail address and no choice ever reaches the page's storage: that is a file on the disk."""
    text = read(SCRIPT)
    for banned in ("localStorage", "sessionStorage", "indexedDB", "cookie", "caches", "postMessage", "BroadcastChannel"):
        assert banned not in text, banned
    # (The two scripts that do keep something there are the ones they always were.)
    keepers = sorted(path.name for path in STATIC.glob("*.js") if "localStorage" in path.read_text("utf-8"))
    assert keepers == ["live.js", "share.js"]


def test_what_the_account_says_is_only_ever_written_as_text():
    text = read(SCRIPT)
    banned = ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "DOMParser", "createContextualFragment")
    for word in banned:
        assert word not in text, word
    for word in ("eval(", "new Function", 'setAttribute("style"', ".style.", "@import", "import("):
        assert word not in text, word
    # It talks to the companion through the house's helpers and to nobody else.
    for word in ("fetch(", "XMLHttpRequest", "WebSocket", "EventSource", "sendBeacon", "window.open", "location.href"):
        assert word not in text, word
    assert re.search(r'^import \{[^}]*\bpost\b[^}]*\} from "\./util\.js";$', text, re.M)
    assert len(re.findall(r"^\s*import\b", text, re.M)) == 1


def test_the_only_outside_addresses_are_four_pages_on_walka_dev():
    text = read(SCRIPT)
    assert set(re.findall(r"https?://[^\s\"'`)]+", text)) == PAGES
    assert not re.search(r"""["'`]//""", text)
    # A link is one of those four and nothing else: no address is ever put together from what the snapshot says.
    assert len(re.findall(r"\bhref\b", text)) == 2  # taken from the table, and given to the link
    assert "const [href, shown] = PAGES[page];" in text
    assert 'target: "_blank", rel: "noopener noreferrer"' in flat(text)  # as the link to the disclaimer is
    # The scripts that use it bring no address of their own, and the page has the one it always had.
    for name in ("history.js", "settings.js", "app.js"):
        assert "https://" not in read(name), name
    assert set(re.findall(r"https?://[^\s\"'<]+", read("index.html"))) == {DISCLAIMER}


def test_every_element_the_script_looks_up_exists():
    page = read("index.html")
    ids = set(re.findall(r'\$\("([\w-]+)"\)', read(SCRIPT)))
    assert ids == {"set-walka-account", "walka-account-state", "walka-account-body", "rail-foot", "foot-stays"}
    assert sorted(i for i in ids if f'id="{i}"' not in page) == []


def test_the_card_is_last_in_settings_hidden_and_quiet():
    page = read("index.html")
    # The pins of the Share page's test hold for the page as it is now.
    assert not re.search(r"<[^>]+\sstyle\s*=", page)
    assert not re.search(r"<[^>]+\son\w+\s*=", page)
    assert not re.search(r"<script(?![^>]*\ssrc=)[^>]*>", page)

    card = card_markup(page)
    assert card.startswith(
        '<section class="card wide" id="set-walka-account" aria-labelledby="set-walka-account-h" tabindex="-1" '
        "hidden inert>"
    )
    assert '<h3 id="set-walka-account-h">Walka account</h3>' in card
    # Quiet: no lamp and no badge, and not a word of its own until the script writes one.
    assert "lamp" not in card and "badge" not in card and "<svg" not in card
    assert '<div class="walka-account" id="walka-account-body"></div>' in card
    assert '<span class="head-state" id="walka-account-state"></span>' in card
    # The last child of the settings grid: after it the grid ends, and then comes the foot of Settings.
    grid = page.index('<div class="settings-grid">')
    after = page[page.index(card) + len(card) :]
    assert page.index(card) > grid
    assert re.match(r'\s*</div>\s*<p class="foot muted small">', after)
    assert page.count("<section", grid, page.index(card)) == 6  # the six cards that were there, all before it


def test_nothing_is_called_plain_account():
    """ "Account" alone is the player's game account in Walka's files: the part is the Walka account's."""
    page = read("index.html")
    names = re.findall(r'\b(?:id|class|for|name|aria-labelledby)="([^"]*)"', page)
    names += re.findall(r'class: [`"]([^`"]*)[`"]', read(SCRIPT))
    names += re.findall(r"\.([a-z][\w-]*)", "\n".join(re.findall(r"^[^{}\n]*\{", read("app.css"), re.M)))
    plain = sorted({token for name in names for token in name.split() if re.match(r"account\b", token)})
    assert plain == []
    for route in re.findall(r'"/api/[^"]*"', read(SCRIPT)):
        assert route.startswith('"/api/walka-account/'), route


def test_the_rest_of_the_page_says_nothing_of_a_walka_account():
    """No pill, no line in "Getting started", no word on the Live view: the card and nothing else."""
    page = read("index.html")
    start = page.index("<!-- Sending walks to a Walka account.")
    rest = page[:start] + page[page.index(card_markup(page)) + len(card_markup(page)) :]
    for word in ("Walka account", "walka-account", "walka_account", "walka.dev", "Sign in", "sign in", "signed in"):
        assert word not in rest, word
    assert len(re.findall(r'id="pill-', page)) == 4
    # The scripts of the other views do not know of it. (history.js asks whether a walk was sent; app.js sets the feet.)
    knows = sorted(
        path.name
        for path in STATIC.glob("*.js")
        if re.search(r"walka.?account", path.read_text("utf-8"), re.I) and path.name != SCRIPT
    )
    assert knows == ["app.js", "history.js", "settings.js"]
    assert len(re.findall(r"walka.?account", read("app.js"), re.I)) == 2  # the import, and a comment at the call
    assert "updateFeet(s);" in read("app.js")
    # No toast of the script's own: what a button could not do is shown by the house's `run`, and that is all.
    assert "toast(" not in read(SCRIPT) and not re.search(r"import \{[^}]*\btoast\b", read(SCRIPT))


def test_the_words_that_are_given_are_there_word_for_word():
    text = flat(read(SCRIPT)).replace('\\"', '"')
    for sentence in (ENDED, GOES, STAYS, THEN, PUBLIC, PUBLIC_ALL, WHOEVER, WALKS_ARE_SENT):
        assert f'"{sentence}"' in text or f"'{sentence}'" in text, sentence
    assert NO_ACCOUNT_YET.replace(" (with GitHub)", "${same}") in text
    assert STOP.removesuffix(" walka.dev/account/walks/") in text
    for sentence in (
        "Optional. Walka works in full without it.",
        "Signing in makes a Walka account if you have none: terms (",
        "). Nothing is sent until you switch sending on. Your web browser opens for this, and Walka asks ",
        "Waiting for your web browser. Sign in there, then come back.",
        '"Sign out" on walka.dev does not sign this computer out: sign out here.',
        "Look again (asks your Walka account)",
        "Open walka.dev/account/",
        "Send my walks to my Walka account...",
        "Asks your Walka account first.",
        "Send my walks to my Walka account?",
        "All of them",
        "Only walks from now on",
        "Send my walks",
        "Not now",
        "Sending is on. Each walk goes to your Walka account when it is saved.",
        "Stop sending",
        "Your Walka account is full: it has room for 20,000 walks.",
        "This Walka is too old for your Walka account as it is now: get the newest Walka.",
        "The sign-in is not on this computer any more. Nothing is sent.",
        "macOS did not let this version of Walka read the stored sign-in. Sign in again.",
        RAIL_FOOT,
        RAIL_FOOT_SENDING,
        PAGE_STAYS,
        PAGE_STAYS_SENDING,
        DELETE_SENT.removesuffix("walka.dev/account/walks/"),
        DELETE_SENT_ELSEWHERE.split("walka.dev/account/walks/")[0],
        ", signed in to that Walka account.",
        ANOTHER_COPY,
        "Delete from this computer",
        "Walk deleted from this computer.",
        "Try again",
    ):
        assert sentence in text, sentence
    for sentence in (LEFT_WINDOWS, LEFT_MAC, LEFT_ELSEWHERE):
        assert sentence.endswith(": " + NAMED) and sentence.replace(NAMED, "${named}") in text, sentence
    assert f"'{NAMED}'" in text
    assert NAMED.endswith(" There may be two such entries: delete both.")  # (the sign-in, and the mark beside sending)
    # Neither invites anybody to sign in: the Walka account may be gone (decision 11).
    for sentence in (LEFT_WINDOWS, LEFT_MAC, LEFT_ELSEWHERE, DELETE_SENT_ELSEWHERE, ANOTHER_COPY):
        assert "sign in" not in sentence.lower().replace("sign-in", "") and "again." not in sentence
    # The house voice: " - " for a dash, and the words that are taken stay out.
    raw = read(SCRIPT)
    assert "\N{EM DASH}" not in raw and "\N{EN DASH}" not in raw and "\N{HORIZONTAL ELLIPSIS}" not in raw
    words = " ".join(re.findall(r'"[^"\n]*"|`[^`\n]*`', raw))
    for taken in ("sync", "connected", "offline", "log in", "logged", "profile"):
        assert taken not in words.lower(), taken
    assert "!" not in re.sub(r"\$\{[^}]*\}", "", words)


# Five sentences of the brief's own (decisions 11, 5, 5, 4 and 8) said "account" alone, and stood here as
# they were until the owner had been asked. The owner's answer of 24 November 2026: all five say "Walka
# account". So there is no exception any more - and these are the five, as they read now.
ONCE_ACCOUNT_ALONE = {
    "or the Walka account was deleted.",
    "goes to this Walka account, whoever walked.",
    "Stopping deletes nothing in your Walka account.",
    "If you already have a Walka account, it is under another e-mail address:",
    "It stays in your Walka account, and on your public page",
}


def account_alone(raw: str) -> list[str]:
    """Every place in a script's strings where "account" stands without "Walka" before it."""
    # What a person can read: the script's strings. (Not its comments, nor the names of routes and classes.)
    code = "\n".join(line for line in raw.splitlines() if not line.lstrip().startswith(("//", "*", "/*")))
    strings = re.findall(r'"(?:[^"\\\n]|\\.)*"|`[^`]*`|\'(?:[^\'\\\n]|\\.)*\'', code)
    alone = []
    for string in strings:
        if re.fullmatch(r"[a-z][\w-]*", string[1:-1]):
            continue  # a name (the key of the page walka.dev/account/), not words
        for found in re.finditer(r"(?<![\w/-])[Aa]ccounts?\b(?![/-])", string):
            if string[: found.start()].endswith("Walka "):
                continue
            alone.append(string[max(0, found.start() - 40) : found.end() + 60])
    return alone


def test_no_sentence_says_account_alone():
    """ "Account" alone is the player's game account elsewhere in Walka (decision 2): never, in any sentence."""
    script = flat(read(SCRIPT))
    for words in ONCE_ACCOUNT_ALONE:
        assert script.count(words) == 1, words
    for name in (SCRIPT, "history.js"):
        assert account_alone(read(name)) == [], name
    # The search itself: it finds the word alone - as the sentences once had it, at the start of a
    # sentence, in a piece of a sentence that is put together, in the many - and nothing else.
    for words in (
        'const a = "Stopping deletes nothing in the account.";',
        'line("Account deleted.")',
        'const a = "It stays in your " + "account.";',
        "const a = `${n} accounts did not answer.`;",
        "const a = `Sign out of the\n  account.`;",
        'const a = "Your Walka account is full, and so is the other account.";',
    ):
        assert len(account_alone(words)) == 1, words
    for words in (
        'outside("account", { text: "Open walka.dev/account/", button: true })',
        'press(el, "/api/walka-account/sign-out")',
        'h("div", { class: "walka-account-panel" })',
        'const a = "Your Walka account is full: it has room for 20,000 walks.";',
        'const a = "Walka could not check that it is talking to your Walka account\'s server.";',
        "// the account, in a comment",
    ):
        assert account_alone(words) == [], words


# What goes with each walk: the sixteen things of a row (walka_account/row.py), and for each the words of
# the question that name it. The question says what goes, so it says all of it - and nothing that stays.
NAMED_IN_THE_QUESTION = {
    "started_at": "when it started",
    "ended_at": "and ended",
    "local_day": "the day",
    "utc_offset_min": "how far this computer's clock was from world time",
    "seconds": "how long the belt moved",
    "distance_m": "distance",
    "steps": "steps",
    "kcal": "calories (estimates)",
    "kcal_extra": "how many of the calories were on top of sitting",
    "steps_source": "how each number was come by",
    "distance_source": "how each number was come by",
    "calories_source": "for the calories: from speed or from heart rate - the word, not the heart rate",
    "session": "whether a walking session was running in the game",
    "client_id": "the walk's name on this computer, which is its start time",
    "id": "Each walk goes under a random number made for it",
    "origin": "says that it came from Walka",
}


def test_the_question_names_every_thing_that_goes_with_a_walk_as_the_readme_does():
    # A row that gains a thing needs words in the question first: this list is the row's, to the last key.
    assert sorted(NAMED_IN_THE_QUESTION) == sorted(row.ROW_KEYS) and len(row.ROW_KEYS) == 16
    for key, words in NAMED_IN_THE_QUESTION.items():
        assert words in GOES, key
    # Nothing is said twice over, and nothing is named that the row does not have: the sentence is made of
    # these words and of what joins them.
    rest = GOES
    for words in sorted(set(NAMED_IN_THE_QUESTION.values()), key=len, reverse=True):
        assert rest.count(words) == 1, words
        rest = rest.replace(words, "~")
    assert rest == "What goes, of each walk: ~ ~, ~, and ~; ~; ~, ~ and ~, and ~; ~ (~); ~; and ~. ~, and ~."
    # What stays is not among it.
    for kept in ("sample", "top speed", "treadmill", "character", "weight", "height", "age and sex"):
        assert kept in STAYS and kept not in GOES, kept
    assert not [key for key in row.ROW_KEYS if re.search(r"hr|heart|max|treadmill|sample|weight|height|beacon", key)]
    # The documentation (docs/WALKA-ACCOUNT.md, linked from the top of the README) says what goes in
    # the same words, to the letter - but for "your computer", which the dashboard calls "this computer".
    readme = flat((Path(__file__).resolve().parents[2] / "docs" / "WALKA-ACCOUNT.md").read_text("utf-8")).replace("*", "")
    assert GOES.replace("this computer", "your computer") in readme


def test_the_words_elsewhere_on_the_page():
    page = read("index.html")
    # The rail's foot and the foot of Settings: exactly the words they had. They follow the state from there.
    assert f'<p class="rail-foot" id="rail-foot">{RAIL_FOOT}</p>' in page
    assert (
        f'<p class="foot muted small">Walka companion <span id="version"></span>. <span id="foot-stays">{PAGE_STAYS}'
        "</span> Provided as is, with no warranty: "
    ) in page
    # The Share view says it of the picture.
    assert "The picture is made on this computer. It is not uploaded: you choose where to post it." in page
    assert "Nothing is uploaded:" not in page
    assert page.count("Nothing is uploaded") == 1
    # Deleting a walk: the words there were, unless the walk is in the Walka account.
    history = flat(read("history.js"))
    for words in (
        "Delete this walk? It disappears from your totals here and from the addon's history. This cannot be undone.",
        '"Delete permanently"',
        '"Walk deleted."',
        # ... or a copy of it was sent to a Walka account that is not the one signed in now.
        "const elsewhere = walk.in_other_walka_account === true;",
        "if (walk.in_walka_account === true) sent = sentWalkWords(elsewhere);",
        "else if (elsewhere) sent = otherAccountWalkWords();",
        "s.walka_account ? s.walka_account.rev : null",
    ):
        assert words in history, words


# ---- the script, under Node ----------------------------------------------------------------------

# The stand-in for the page. Elements keep children, attributes and listeners; nothing here reads
# markup, so whatever the script shows is text by construction - what is checked is which text.
HARNESS = r"""
import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

const [pageDir, outPath] = process.argv.slice(2);
const request = JSON.parse(readFileSync(0, "utf-8"));

// Numbers and days are written one way here, whatever the language and the time zone of the
// machine that runs the test (and whatever its Node puts between "12:00" and "PM").
const NumberFormat = Intl.NumberFormat;
Intl.NumberFormat = function (_locales, options) {
  return new NumberFormat("en-US", options);
};
for (const name of ["toLocaleString", "toLocaleDateString"]) {
  const real = Date.prototype[name];
  Date.prototype[name] = function (_locales, options) {
    return real.call(this, "en-US", { ...options, timeZone: "UTC" }).replace(/[\u202f\u00a0]/g, " ");
  };
}

let made = 0;
const scrolled = [];
const BLOCKS = new Set(["p", "h4", "li", "button", "label"]);

class Text {
  constructor(text) {
    this.nodeType = 3;
    this.text = String(text);
    this.parentNode = null;
  }
  get textContent() {
    return this.text;
  }
}

class El {
  constructor(tag) {
    made += 1;
    this.nodeType = 1;
    this.tagName = tag.toUpperCase();
    this.tag = tag;
    this.childNodes = [];
    this.parentNode = null;
    this.attributes = {};
    this.listeners = {};
    this.dataset = {};
    this.className = "";
    this.hidden = false;
    this.inert = false;
    this.disabled = false;
    this.checked = false;
    this.scrollTop = 0;
    this.offsetTop = 0;
    this.scrollHeight = 0;
  }
  get children() {
    return this.childNodes.filter((node) => node.nodeType === 1);
  }
  get firstElementChild() {
    return this.children[0] || null;
  }
  get isConnected() {
    return document.body.contains(this);
  }
  get tabIndex() {
    return "tabindex" in this.attributes ? Number(this.attributes.tabindex) : 0;
  }
  get textContent() {
    return this.childNodes.map((node) => node.textContent).join("");
  }
  set textContent(value) {
    this.replaceChildren(...(value === "" ? [] : [new Text(value)]));
  }
  setAttribute(name, value) {
    this.attributes[name] = String(value);
    if (name === "data-kind" || name === "data-sticky") this.dataset[name.slice(5)] = String(value);
  }
  getAttribute(name) {
    return name in this.attributes ? this.attributes[name] : null;
  }
  removeAttribute(name) {
    delete this.attributes[name];
  }
  append(...nodes) {
    for (const given of nodes) {
      const node = typeof given === "string" ? new Text(given) : given;
      if (node.parentNode) node.remove();
      node.parentNode = this;
      this.childNodes.push(node);
    }
  }
  replaceChildren(...nodes) {
    for (const node of this.childNodes) node.parentNode = null;
    this.childNodes = [];
    this.append(...nodes);
  }
  remove() {
    if (!this.parentNode) return;
    this.parentNode.childNodes = this.parentNode.childNodes.filter((node) => node !== this);
    this.parentNode = null;
  }
  contains(node) {
    for (let at = node; at; at = at.parentNode) if (at === this) return true;
    return false;
  }
  addEventListener(type, listener) {
    (this.listeners[type] ||= []).push(listener);
  }
  fire(type) {
    for (const listener of this.listeners[type] || []) listener({ type, target: this, currentTarget: this });
  }
  focus() {
    if (shown(this) && !this.disabled && !inert(this)) document.activeElement = this;
  }
  scrollIntoView() {
    scrolled.push(this.className);
  }
  get nextElementSibling() {
    const siblings = this.parentNode ? this.parentNode.children : [];
    return siblings[siblings.indexOf(this) + 1] || null;
  }
  // The few selectors the page's scripts ask for here: a tag, a class, an attribute with its value.
  querySelectorAll(selector) {
    const found = /^([a-z]+)?(?:\.([\w-]+))?(?:\[([\w-]+)="([^"]*)"\])?$/.exec(selector);
    if (!found) throw new Error(`a selector the stand-in does not know: ${selector}`);
    const [, tag, cls, name, value] = found;
    return all(this).filter(
      (el) =>
        (!tag || el.tag === tag) &&
        (!cls || el.className.split(" ").includes(cls)) &&
        (!name || el.getAttribute(name) === value),
    );
  }
  querySelector(selector) {
    return this.querySelectorAll(selector)[0] || null;
  }
}

function shown(el) {
  for (let at = el; at; at = at.parentNode) if (at.hidden) return false;
  return true;
}
function inert(el) {
  for (let at = el; at; at = at.parentNode) if (at.inert) return true;
  return false;
}
function all(el, found = []) {
  for (const node of el.childNodes) {
    if (node.nodeType !== 1) continue;
    found.push(node);
    all(node, found);
  }
  return found;
}

const byId = {};
const document = {
  body: new El("body"),
  activeElement: null,
  createElement: (tag) => new El(tag),
  createElementNS: (_ns, tag) => new El(tag),
  createTextNode: (text) => new Text(text),
  // (The History view writes into many places that say nothing here: for it, any id is there.)
  getElementById: (id) => byId[id] || (request.history ? place(id, "div") : null),
};
document.activeElement = document.body;
function place(id, tag, props = {}, parent = document.body) {
  const el = new El(tag);
  Object.assign(el, props);
  el.attributes.id = id;
  byId[id] = el;
  parent.append(el);
  return el;
}
// The few elements of index.html the script looks up, as they stand there before any script has run.
const card = place("set-walka-account", "section", { hidden: true, inert: true });
card.attributes.tabindex = "-1";
const head = place("walka-account-state", "span", {}, card);
place("walka-account-body", "div", {}, card);
place("rail-foot", "p", { className: request.page.railFootClass }).textContent = request.page.railFoot;
place("foot-stays", "span").textContent = request.page.footStays;
const toasts = place("toasts", "div");
if (request.history) place("week-table", "table").append(new El("tbody"));
made = 0;

let clock = 1000;
const asked = [];
let answer = { status: 200, body: { ok: true } };
Object.defineProperty(globalThis, "document", { value: document, configurable: true });
Object.defineProperty(globalThis, "location", { value: { hostname: request.hostname ?? "127.0.0.1" }, configurable: true });
Object.defineProperty(globalThis, "navigator", {
  value: { platform: request.platform ?? "Win32", userAgent: "" },
  configurable: true,
});
Object.defineProperty(globalThis, "performance", { value: { now: () => clock }, configurable: true });
globalThis.setTimeout = () => 0; // (a toast's own clock: nothing here waits for it)
globalThis.clearTimeout = () => {};
let walks = []; // what GET /api/sessions answers
let hold = false; // the next such answer waits for "release"
const waiting = [];
globalThis.fetch = async (path, init) => {
  const body = init.body === undefined ? null : JSON.parse(init.body);
  asked.push({ path, method: init.method, type: init.headers["Content-Type"] ?? null, body });
  let { status, body: said } = answer;
  if (init.method === "GET") {
    // The list of walks as it is at this moment - or one walk's record, with nothing in it.
    status = 200;
    said = path.startsWith("/api/sessions?") ? { sessions: JSON.parse(JSON.stringify(walks)) } : { samples: [] };
    if (hold && path.startsWith("/api/sessions?")) {
      hold = false;
      await new Promise((resolve) => waiting.push(resolve));
    }
  }
  return { ok: status >= 200 && status < 300, status, text: async () => JSON.stringify(said) };
};

// The page's own scripts, from a copy of the folder they are served from (see `play`).
const load = (name) => import(pathToFileURL(join(pageDir, name)).href);
const util = await load("util.js");
const script = await load("walkaaccount.js");
script.initWalkaAccount();
const history = request.history ? await load("history.js") : null;
if (history) history.initHistory();
const madeAtStart = made;

function blocks(root) {
  const out = [];
  const walk = (el) => {
    for (const node of el.childNodes) {
      if (node.nodeType !== 1 || node.hidden) continue;
      const lone = node.tag === "span" && node.parentNode.className.split(" ").includes("btn-row");
      const button = node.tag === "a" && node.className.split(" ").includes("btn");
      if (BLOCKS.has(node.tag) || lone || button) out.push(node.textContent);
      else walk(node);
    }
  };
  if (!root.hidden) walk(root);
  return out;
}

// An element as markup would have it: its tag, its class, its attributes and what is in it.
function as(el) {
  return {
    tag: el.tag,
    class: el.className,
    attributes: { ...el.attributes },
    holds: el.childNodes.map((node) => (node.nodeType === 3 ? node.textContent : as(node))),
  };
}

function look() {
  const inCard = all(card).filter(shown);
  const active = document.activeElement;
  const classes = (el) => el.className.split(" ").filter(Boolean);
  return {
    hidden: card.hidden,
    inert: card.inert,
    tone: card.getAttribute("data-tone"),
    head: head.textContent,
    lines: card.hidden ? [] : blocks(byId["walka-account-body"]),
    text: card.hidden ? "" : card.textContent,
    everything: document.body.textContent,
    buttons: inCard.filter((el) => el.tag === "button").map((el) => ({ text: el.textContent, disabled: el.disabled })),
    links: inCard
      .filter((el) => el.tag === "a")
      .map((el) => ({ text: el.textContent, ...el.attributes, button: classes(el).includes("btn") })),
    radios: inCard
      .filter((el) => el.tag === "input")
      .map((el) => ({ ...el.attributes, checked: el.checked, label: el.parentNode.textContent })),
    groups: inCard.filter((el) => el.attributes.role === "radiogroup").length,
    panels: inCard.filter((el) => classes(el).includes("walka-account-panel")).map((el) => classes(el).join(" ")),
    live: inCard.filter((el) => el.attributes.role === "status").map((el) => el.textContent),
    marks: inCard.filter((el) => ["svg", "img"].includes(el.tag) || /lamp|badge|pill/.test(el.className)).length,
    focus: active === document.body ? "body" : active === card ? "card" : (active.attributes.id || classes(active).join(" ") || active.tag) + ": " + active.textContent.slice(0, 60),
    railFoot: byId["rail-foot"].textContent,
    railFootAs: as(byId["rail-foot"]),
    footStays: byId["foot-stays"].textContent,
    toasts: toasts.children.map((el) => ({ role: el.attributes.role, kind: el.dataset.kind, text: el.textContent })),
    made: made - madeAtStart,
    scrolled: scrolled.slice(),
    asked: asked.slice(),
    // The History view: its rows, and the question before a walk is deleted (when one is open).
    rows: document.body.querySelectorAll(".srow-btn").map((el) => el.getAttribute("aria-expanded")),
    confirms: document.body
      .querySelectorAll(".confirm")
      .filter(shown)
      .map((el) => ({
        text: el.children.filter((child) => child.tag === "p").map((child) => child.textContent).join(" "),
        buttons: el.children.filter((child) => child.tag === "button").map((child) => child.textContent),
        links: all(el).filter((child) => child.tag === "a").map((child) => ({ text: child.textContent, ...child.attributes })),
      })),
  };
}

const settle = async () => {
  for (let i = 0; i < 5; i += 1) await new Promise((resolve) => setImmediate(resolve));
};

function find(text) {
  const el = all(document.body)
    .filter(shown)
    .find((el) => (el.tag === "button" || el.tag === "label") && el.textContent === text);
  if (!el) throw new Error(`nothing to press that says "${text}"`);
  return el;
}

const out = [];
for (const step of request.steps) {
  if (step.op === "snapshot") {
    // As app.js does it: the feet with every snapshot, the card while Settings is the view shown.
    util.state.snapshot = step.s;
    util.state.online = step.online ?? true;
    script.updateFeet(step.s);
    if (step.settings ?? true) script.updateWalkaAccount(step.s);
  } else if (step.op === "press") {
    const el = find(step.text);
    if (el.tag === "label") {
      const input = el.children.find((child) => child.tag === "input");
      for (const other of all(card)) {
        if (other.tag === "input" && other.attributes.name === input.attributes.name) other.checked = other === input;
      }
      document.activeElement = input;
      input.fire("change");
    } else if (!el.disabled) {
      document.activeElement = el;
      el.fire("click");
    }
    await settle();
  } else if (step.op === "history") {
    // As app.js does it while History is the view shown.
    util.state.snapshot = step.s;
    util.state.online = true;
    script.updateFeet(step.s);
    history.updateHistory(step.s);
    await settle();
  } else if (step.op === "walks") {
    walks = step.walks;
  } else if (step.op === "hold") {
    hold = true;
  } else if (step.op === "release") {
    for (const resolve of waiting.splice(0)) resolve();
    await settle();
  } else if (step.op === "row") {
    const row = document.body.querySelectorAll(".srow-btn")[step.at];
    document.activeElement = row;
    row.fire("click");
    await settle();
  } else if (step.op === "answer") {
    answer = { status: step.status, body: step.body };
  } else if (step.op === "later") {
    clock += step.ms;
  } else if (step.op === "look") {
    // (Nothing happens: the page as it is.)
  } else if (step.op === "tab") {
    find(step.text).focus();
  } else if (step.op === "sent_words") {
    const words = step.other ? script.otherAccountWalkWords() : script.sentWalkWords(step.elsewhere);
    const p = new El("p");
    p.append(...words.question);
    document.body.append(p);
    out.push({
      question: p.textContent,
      links: all(p).map((el) => ({ text: el.textContent, ...el.attributes })),
      button: words.button,
      toast: words.toast,
    });
    p.remove();
    continue;
  } else {
    throw new Error(`unknown step ${step.op}`);
  }
  out.push(look());
}
// (Into a file: what a pipe does with a long answer differs from one system to the next.)
writeFileSync(outPath, JSON.stringify(out), "utf-8");
process.exit(0);
"""

# The charts of the History view draw on a canvas, which is not what is looked at here.
CHARTS_STAND_IN = """
export class WeekChart {
  setData() {}
}
export class SessionChart {
  destroy() {}
}
"""


def page_now() -> dict[str, str]:
    """The words of the two feet as index.html has them, before any script runs."""
    page = read("index.html")
    return {
        "railFoot": re.search(r'id="rail-foot">([^<]*)<', page).group(1),
        "railFootClass": re.search(r'<p class="([^"]*)" id="rail-foot">', page).group(1),
        "footStays": re.search(r'id="foot-stays">([^<]*)<', page).group(1),
    }


def play(tmp_path: Path, *steps: dict[str, Any], **where: Any) -> list[dict[str, Any]]:
    """Run the script against the stand-in for the page, step by step. What the page shows after each step."""
    harness = tmp_path / "walka_account_page_harness.mjs"
    harness.write_text(HARNESS, "utf-8")
    # The scripts as they are served, in a folder of their own that says they are modules (a browser is
    # told so by the page; Node by this file). They import one another by name, as on the page.
    page = tmp_path / "page"
    if not page.exists():
        page.mkdir()
        for script in STATIC.glob("*.js"):
            shutil.copyfile(script, page / script.name)
        (page / "charts.js").write_text(CHARTS_STAND_IN, "utf-8")
        (page / "package.json").write_text('{"type": "module"}', "utf-8")
    seen = tmp_path / "seen.json"
    seen.unlink(missing_ok=True)
    done = subprocess.run(
        [NODE, str(harness), str(page), str(seen)],
        input=json.dumps({"steps": steps, "page": page_now(), **where}),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        env={**os.environ, "TZ": "UTC"},
    )
    assert done.returncode == 0, done.stderr
    return json.loads(seen.read_text("utf-8"))


def part(**changes: Any) -> dict[str, Any]:
    """The Walka account's part of a snapshot, as ``WalkaAccount.snapshot()`` gives it: nobody signed in."""
    made = {
        "state": "signed_out",
        "host": "accounts.example",
        "store": "lasting",
        "problem": "",
        "notice": "",
        "left_in_store": "",
        "email": "",
        "way": "",
        "first_step": "unknown",
        "can_send": False,
        "sending": False,
        "chosen": "",
        "from": None,
        "busy": "",
        "counts": {"waiting": 0, "sent": 0, "aside": 0, "held": 0, "unnamed": 0, "unreadable": 0},
        "last": {"word": "", "at": 0.0, "added": 0},
        "aside": [],
        "held": [],
        "question": None,
        "rev": 1,
    }
    counts = changes.pop("counts", {})
    made.update(changes)
    made["counts"] = {**made["counts"], **counts}
    return made


def signed_in(**changes: Any) -> dict[str, Any]:
    told = {"state": "signed_in", "email": "walker@mail.example", "way": "github", "first_step": "taken", "can_send": True}
    return part(**{**told, **changes})


def question(**changes: Any) -> dict[str, Any]:
    asked = {"id": "q-1", "email": "walker@mail.example", "way": "github", "waiting": 3, "unnamed": 0, "held": 0}
    return {**asked, "public": False, "all_shows": False, **changes}


def snap(account: dict[str, Any] | None = None, **how: Any) -> dict[str, Any]:
    """A step: a snapshot comes. Only what the script reads of it is there."""
    s: dict[str, Any] = {"version": "0.8.0", "units": "mph"}
    if account is not None:
        s["walka_account"] = account
    return {"op": "snapshot", "s": s, **how}


def press(text: str) -> dict[str, str]:
    return {"op": "press", "text": text}


def answer(status: int, error: str) -> dict[str, Any]:
    return {"op": "answer", "status": status, "body": {"ok": False, "error": error}}


def posts(seen: dict[str, Any]) -> list[tuple[str, Any]]:
    for request in seen["asked"]:
        # Whatever is asked of the companion is a POST with a JSON body: its guard takes nothing else.
        assert request["method"] == "POST" and request["type"] == "application/json", request
        assert request["path"].startswith("/api/walka-account/"), request
    return [(request["path"].removeprefix("/api/walka-account/"), request["body"]) for request in seen["asked"]]


def names(seen: dict[str, Any]) -> list[str]:
    return [button["text"] for button in seen["buttons"]]


@needs_node
def test_without_the_part_there_is_no_card_and_the_page_is_as_it_was(tmp_path: Path):
    before = page_now()
    seen = play(
        tmp_path,
        snap(None),
        snap(part(state="unavailable")),
        snap(part(state="something new")),
        snap({"state": "signed_in"}, settings=False),
        snap(signed_in(sending=True, counts={"sent": 2})),  # (it shows ...)
        snap(None, settings=False),  # ... and Walka's part is gone while another view is shown
        snap(None),
    )
    for at in (0, 1, 2, 5, 6):
        now = seen[at]
        assert now["hidden"] is True and now["inert"] is True, at
        assert now["lines"] == [] and now["text"] == "" and now["head"] == "" and now["tone"] is None, at
        assert (now["railFoot"], now["footStays"]) == (before["railFoot"], before["footStays"]), at
        assert now["toasts"] == [] and now["asked"] == [], at
        assert "walker@mail.example" not in now["everything"], at  # nothing of an account stays behind
    assert before == {"railFoot": RAIL_FOOT, "railFootClass": "rail-foot", "footStays": PAGE_STAYS}
    assert seen[4]["hidden"] is False and "walker@mail.example" in seen[4]["text"]
    # Hidden, it is out of the keyboard's reach too - also right after the page has heard the companion again.
    assert seen[3]["hidden"] is False and seen[3]["inert"] is False  # (a part with nothing in it but its state: shown)


@needs_node
def test_while_nobody_is_signed_in_only_the_card_says_anything(tmp_path: Path):
    seen = play(tmp_path, snap(part()), snap(part(rev=2)), snap(part(), online=False))
    now = seen[0]
    assert now["hidden"] is False and now["inert"] is False
    assert now["lines"] == [
        "Optional. Walka works in full without it.",
        "Sign in with Discord",
        "Sign in with GitHub",
        "Sign in with Google",
        BEFORE_THE_BROWSER,
    ]
    # Quiet: no state in the head, no lamp, no badge, no tone but the idle one.
    assert now["head"] == "" and now["marks"] == 0 and now["tone"] == "idle"
    assert now["panels"] == [] and now["radios"] == []
    # Nothing outside the card has changed, and nothing was asked of anybody.
    assert (now["railFoot"], now["footStays"]) == (RAIL_FOOT, PAGE_STAYS)
    assert now["toasts"] == [] and now["asked"] == [] and now["focus"] == "body"
    outside = now["everything"].replace(now["text"], "")
    assert outside == RAIL_FOOT + PAGE_STAYS
    # The two links are plain ones that open in the web browser, as the link to the disclaimer does.
    assert now["links"] == [
        {
            "text": f"walka.dev/{page}/",
            "href": f"https://walka.dev/{page}/",
            "target": "_blank",
            "rel": "noopener noreferrer",
            "button": False,
        }
        for page in ("terms", "privacy")
    ]
    # A snapshot comes twice a second: the card is not built again for one that shows the same.
    assert seen[1]["made"] == now["made"] and seen[1]["lines"] == now["lines"]
    # While the companion is silent the card cannot be used, like every card of Settings.
    assert seen[2]["hidden"] is False and seen[2]["inert"] is True


@needs_node
def test_signing_in_and_what_can_go_wrong(tmp_path: Path):
    seen = play(
        tmp_path,
        snap(part()),
        press("Sign in with GitHub"),
        snap(part(state="signing_in")),
        press("Stop waiting"),
        snap(part()),
        press("Sign in with Discord"),
        press("Sign in with Google"),
    )
    assert posts(seen[1]) == [("sign-in", {"way": "github"})]
    waiting = seen[2]
    assert waiting["lines"] == [
        "Optional. Walka works in full without it.",
        "Waiting for your web browser. Sign in there, then come back.",
        "Stop waiting",
    ]
    assert "Waiting for your web browser. Sign in there, then come back." in waiting["live"]
    assert waiting["head"] == "" and waiting["links"] == []
    # The button that was pressed is gone: the keyboard stays in the card.
    assert waiting["focus"] == "card"
    assert posts(seen[3])[1:] == [("sign-out", {})]
    assert posts(seen[6])[2:] == [("sign-in", {"way": "discord"}), ("sign-in", {"way": "google"})]
    assert seen[6]["toasts"] == []

    said = {
        "refused": "Nothing was signed in.",
        "failed": "Walka could not finish signing in. Try again.",
        "timed_out": "Nobody came back from the browser. Try again.",
        "no_store": "Walka could not keep the sign-in on this computer, so nothing was signed in.",
        "certificate": "Walka could not check that it is talking to your Walka account's server, so it asked nothing.",
        "no_answer": "There was no answer. Try again later.",
        "no_stored_sign_in": "The sign-in is not on this computer any more. Nothing is sent.",
        "store_refused": "Windows did not let Walka read the stored sign-in. Sign in again.",
    }
    seen = play(tmp_path, *(snap(part(problem=word)) for word in said), snap(part(problem="something new")))
    for now, sentence in zip(seen, said.values(), strict=False):
        assert now["lines"][:2] == ["Optional. Walka works in full without it.", sentence]
        assert now["live"] == [sentence]  # read out when it comes
        assert names(now) == ["Sign in with Discord", "Sign in with GitHub", "Sign in with Google"]
        assert now["toasts"] == [] and now["head"] == ""
    # A word this page does not know says nothing: never the word itself.
    assert seen[-1]["lines"][1] == "Sign in with Discord" and "something new" not in seen[-1]["text"]

    # The Mac's refusal, in the words drafted - and no system is named by a page that is open elsewhere.
    mac = play(tmp_path, snap(part(problem="store_refused")), platform="MacIntel")
    assert mac[0]["lines"][1] == "macOS did not let this version of Walka read the stored sign-in. Sign in again."
    away = play(tmp_path, snap(part(problem="store_refused")), platform="MacIntel", hostname="192.168.1.20")
    assert away[0]["lines"][1] == "The computer that runs Walka did not let it read the stored sign-in. Sign in again."


@needs_node
def test_signed_in_says_which_account_and_where_the_sign_in_is_kept(tmp_path: Path):
    long_address = "a.very.long.name.that.goes.on.and.on.and.on@a-mail-server-with-a-long-name.example"
    seen = play(
        tmp_path,
        snap(signed_in(counts={"waiting": 5})),
        press("Sign out"),
        snap(signed_in(email=long_address, way="google", store="until_closed")),
        snap(signed_in(email="", way="discord", can_send=False)),
        snap(signed_in(way="something new")),
    )
    now = seen[0]
    assert now["lines"] == [
        "Signed in as walker@mail.example, with GitHub.",
        "Sign out",
        f"{SIGN_OUT_HERE} Signing out here may ask your Walka account one last time, to tell it so. "
        "The sign-in is kept in Windows' Credential Manager.",
        "Nothing is sent. Walka has not sent 5 walks on this computer to your Walka account.",
        "Send my walks to my Walka account...",
        "Asks your Walka account first.",
    ]
    assert now["head"] == "sending off" and now["tone"] == "idle" and now["marks"] == 0
    assert (now["railFoot"], now["footStays"]) == (RAIL_FOOT, PAGE_STAYS)  # sending is off: the words of always
    assert posts(seen[1]) == [("sign-out", {})]
    # The e-mail address whole, however long; the store that is not one says so.
    assert seen[2]["lines"][0] == f"Signed in as {long_address}, with Google."
    assert seen[2]["lines"][2].endswith("The sign-in is kept in memory, until Walka is closed.")
    # No e-mail address: nobody could tell which Walka account it is, so sending cannot be switched on.
    none = seen[3]
    assert none["lines"][0] == "Signed in with Discord."
    assert none["lines"][3] == (
        "The sign-in gave Walka no e-mail address, so nobody could tell which Walka account your walks would go "
        "to. Sending cannot be switched on."
    )
    assert names(none) == ["Sign out"]
    # A way this page does not know is not named - and never shown as it came.
    assert seen[4]["lines"][0] == "Signed in as walker@mail.example." and "something new" not in seen[4]["text"]

    mac = play(tmp_path, snap(signed_in()), platform="MacIntel")
    assert mac[0]["lines"][2].endswith("The sign-in is kept in your Mac's keychain.")
    away = play(tmp_path, snap(signed_in()), hostname="192.168.1.20")
    assert away[0]["lines"][2].endswith("The sign-in is kept in the password store of the computer that runs Walka.")


# Walks the companion knows of that did not go, or will not: each kind once.
KNOWN_FROM_BEFORE: dict[str, Any] = {
    "counts": {"waiting": 2, "aside": 1, "held": 1, "unnamed": 1, "unreadable": 1},
    "aside": [{"id": "20261115-120000", "start": 1_794_744_000.0, "code": "22003", "message": "numeric field overflow"}],
    "held": [{"id": "20261114-120000", "start": 1_794_657_600.0, "why": "There are more than 400,000 steps."}],
}


@needs_node
def test_the_first_step_is_taken_on_the_website(tmp_path: Path):
    seen = play(
        tmp_path,
        snap(signed_in(first_step="not_taken", counts={"waiting": 2})),
        press("Look again (asks your Walka account)"),
        snap(signed_in(first_step="not_taken", busy="asking", counts={"waiting": 2})),
        press("Look again (asks your Walka account)"),
        snap(signed_in(first_step="unknown", counts={"waiting": 2})),
        snap(signed_in(first_step="unknown", busy="asking", counts={"waiting": 2})),
        snap(signed_in(first_step="not_taken", counts={"waiting": 0, "sent": 4})),
        snap(signed_in(first_step="taken", counts={"waiting": 0, "sent": 4})),
        snap(signed_in(first_step="not_taken", **KNOWN_FROM_BEFORE)),
        snap(signed_in(first_step="unknown", **KNOWN_FROM_BEFORE)),
    )
    now = seen[0]
    # Beside "no Walka account yet" the card counts no walks: only that nothing is sent.
    assert now["lines"][3:] == [
        NO_ACCOUNT_YET,
        "Open walka.dev/account/",
        "Look again (asks your Walka account)",
        "Nothing is sent.",
    ]
    assert not re.search(r"\d", " ".join(now["lines"][3:]))
    assert seen[6]["lines"][-1] == "Nothing is sent."  # nor walks that were sent before the step was lost
    assert seen[7]["lines"][3] == (
        "Nothing is sent. 4 walks on this computer were sent before, or were in your Walka account already."
    )
    # Nor does it list walks beside it. The companion can still know such walks from the question of a
    # Walka account that was signed in before - and "your Walka account did not take ..." would speak of one.
    assert seen[8]["lines"][3:] == now["lines"][3:]
    assert not re.search(r"\d|walks?\b", " ".join(seen[8]["lines"][3:]).replace("walka.dev", ""))
    # (They are listed as soon as that sentence is gone.)
    assert seen[9]["lines"][3] == "Nothing is sent. Walka has not sent 2 walks on this computer to your Walka account."
    assert seen[9]["lines"][-6:] == [
        "Your Walka account did not take 1 walk. It stays here.",
        "Sun, Nov 15, 12:00 PMnumeric field overflow",
        "1 walk is outside what your Walka account takes, and stays here.",
        "Sat, Nov 14, 12:00 PMThere are more than 400,000 steps.",
        "1 walk does not say which treadmill made it, and is not sent.",
        "1 walk could not be read, and is not sent.",
    ]
    # Sending cannot be switched on from here: no button for it.
    assert names(now) == ["Sign out", "Look again (asks your Walka account)"]
    assert now["panels"] == ["walka-account-panel"]  # a plain panel, not the red one of deleting
    assert now["links"] == [
        {
            "text": "Open walka.dev/account/",
            "href": "https://walka.dev/account/",
            "target": "_blank",
            "rel": "noopener noreferrer",
            "button": True,
        }
    ]
    assert posts(seen[1]) == [("look-again", {})]
    # While Walka asks, the button cannot be pressed again, and the card says what is going on.
    asking = seen[2]
    assert {"text": "Look again (asks your Walka account)", "disabled": True} in asking["buttons"]
    assert "Asking your Walka account..." in asking["lines"]
    assert posts(seen[3]) == [("look-again", {})]
    # Not known yet (nothing is asked at a start): the button that asks first is there.
    assert seen[4]["lines"][3:] == [
        "Nothing is sent. Walka has not sent 2 walks on this computer to your Walka account.",
        "Send my walks to my Walka account...",
        "Asks your Walka account first.",
    ]
    assert {"text": "Send my walks to my Walka account...", "disabled": True} in seen[5]["buttons"]


@needs_node
def test_sending_off_says_how_many_walks_wait(tmp_path: Path):
    seen = play(
        tmp_path,
        snap(signed_in(counts={"waiting": 1})),
        snap(signed_in(counts={"waiting": 1234})),
        snap(signed_in(counts={"waiting": 0, "sent": 1})),
        snap(signed_in(counts={"waiting": 0, "sent": 7})),
        snap(signed_in()),
        press("Send my walks to my Walka account..."),
        snap(signed_in(last={"word": "no_answer", "at": 1.0, "added": 0})),
        snap(signed_in(last={"word": "certificate", "at": 1.0, "added": 0})),
        snap(signed_in(last={"word": "stopped", "at": 1.0, "added": 0})),
    )
    lines = [now["lines"][3] for now in seen[:5]]
    assert lines == [
        "Nothing is sent. Walka has not sent 1 walk on this computer to your Walka account.",
        "Nothing is sent. Walka has not sent 1,234 walks on this computer to your Walka account.",
        "Nothing is sent. 1 walk on this computer was sent before, or was in your Walka account already.",
        "Nothing is sent. 7 walks on this computer were sent before, or were in your Walka account already.",
        "Nothing is sent.",
    ]
    # The button says that it asks, and asks for the question - which switches nothing on.
    assert posts(seen[5]) == [("question", {})]
    assert seen[5]["toasts"] == []
    assert seen[6]["lines"][-1] == "There was no answer. Try again later."
    assert seen[7]["lines"][-1] == (
        "Walka could not check that it is talking to your Walka account's server, so it asked nothing."
    )
    assert seen[8]["lines"][-1] == "That did not work just now. Try again later."


@needs_node
def test_the_question_before_sending(tmp_path: Path):
    off = signed_in(counts={"waiting": 3})
    asked = signed_in(counts={"waiting": 3}, question=question(), rev=5)
    seen = play(
        tmp_path,
        snap(off),
        press("Send my walks to my Walka account..."),
        snap(signed_in(counts={"waiting": 3}, busy="asking", rev=3)),
        snap(asked),
        press("Send my walks"),  # (disabled: nothing happens)
        press("Only walks from now on"),
        snap({**asked, "rev": 6, "counts": {**asked["counts"], "waiting": 4}}),  # a walk is saved meanwhile
        snap({**asked, "rev": 6, "counts": {**asked["counts"], "waiting": 4}}),
        press("All of them"),
        press("Send my walks"),
        snap(signed_in(counts={"waiting": 4}, rev=8)),  # the companion has not said "on" yet
        snap(signed_in(sending=True, chosen="all", busy="sending", counts={"waiting": 4}, rev=9)),
    )
    assert posts(seen[1]) == [("question", {})]
    assert "Asking your Walka account..." in seen[2]["lines"]

    now = seen[3]
    assert now["lines"][3:] == [
        "Nothing is sent. Walka has not sent 3 walks on this computer to your Walka account.",
        "Send my walks to my Walka account?",
        "To walker@mail.example, signed in with GitHub.",
        GOES,
        STAYS,
        "Where: your Walka account, in the database the site's owner runs (accounts.example). walka.dev/privacy/",
        "3 walks would go now.",
        "All of them",
        "Only walks from now on",
        THEN,
        WHOEVER,
        STOP,
        "Send my walks",
        "Not now",
        "Choose which walks first.",
    ]
    # A panel in the card - a plain one, not the red one - and the keyboard is moved into it.
    assert now["panels"] == ["walka-account-panel walka-account-question"]
    assert now["focus"].startswith("walka-account-panel walka-account-question: Send my walks to my Walka account?")
    assert now["scrolled"] == ["walka-account-panel walka-account-question"]  # shown from its first line
    # The choice: one group of two, and NEITHER is chosen. Until one is, nothing can be sent.
    assert now["groups"] == 1
    assert [(radio["type"], radio["value"], radio["checked"]) for radio in now["radios"]] == [
        ("radio", "all", False),
        ("radio", "from_now_on", False),
    ]
    assert len({radio["name"] for radio in now["radios"]}) == 1
    assert {"text": "Send my walks", "disabled": True} in now["buttons"]
    assert {"text": "Not now", "disabled": False} in now["buttons"]
    assert "Send my walks to my Walka account..." not in names(now)  # (the question is open already)
    assert [(link["text"], link["href"]) for link in now["links"]] == [
        ("walka.dev/privacy/", "https://walka.dev/privacy/"),
        ("walka.dev/account/walks/", "https://walka.dev/account/walks/"),
    ]
    assert all(link["target"] == "_blank" and link["rel"] == "noopener noreferrer" for link in now["links"])
    assert posts(seen[4]) == [("question", {})]  # pressing the disabled button asked nothing

    chosen = seen[5]
    assert [radio["checked"] for radio in chosen["radios"]] == [False, True]
    assert {"text": "Send my walks", "disabled": False} in chosen["buttons"]
    assert "Choose which walks first." not in chosen["lines"]
    # Snapshots keep coming, and the count of the card changes: the question is not built again,
    # so the choice and the place of the keyboard stay as the person left them.
    assert [radio["checked"] for radio in seen[6]["radios"]] == [False, True]
    assert seen[6]["focus"] == chosen["focus"] and seen[6]["focus"].startswith("input")
    assert seen[6]["lines"][3] == "Nothing is sent. Walka has not sent 4 walks on this computer to your Walka account."
    assert "3 walks would go now." in seen[6]["lines"]  # (the question says what it was made with)
    assert seen[7]["made"] == seen[6]["made"]

    # The answer spells the choice out, with the question's own id.
    assert [radio["checked"] for radio in seen[8]["radios"]] == [True, False]
    assert posts(seen[9]) == [("question", {}), ("sending", {"question": "q-1", "send": "all"})]
    # At once the question is gone, and the button that asks for it does not come back in its place.
    after = seen[9]
    assert after["panels"] == [] and "Switching sending on..." in after["lines"]
    assert names(after) == ["Sign out"] and after["focus"] == "card" and after["toasts"] == []
    assert names(seen[10]) == ["Sign out"]
    on = seen[11]
    assert on["head"] == "sending on" and "Sending now..." in on["lines"] and "Stop sending" in names(on)


@needs_node
def test_the_question_says_what_a_public_page_shows_and_which_walks_stay(tmp_path: Path):
    plain = play(tmp_path, snap(signed_in(question=question(waiting=1))))[0]
    assert "1 walk would go now." in plain["lines"]
    assert not any("public" in line or "whole history" in line for line in plain["lines"])
    # (A page that is not public shows nothing, whatever else is said of it.)
    private = play(tmp_path, snap(signed_in(question=question(public=False, all_shows=True))))[0]
    assert not any("public" in line or "whole history" in line for line in private["lines"])
    assert not any("treadmill made" in line or "outside what" in line for line in plain["lines"])

    # Public: said whenever the page is public. That the whole history shows there at once is said only of a
    # page that lists days or walks: one that lists neither gives out sums, and no history shows on it.
    for shows, said in ((False, PUBLIC), (True, f"{PUBLIC} {PUBLIC_ALL}"), ("yes", PUBLIC), (None, PUBLIC)):
        asked = question(public=True, all_shows=shows, waiting=0, unnamed=2, held=1)
        now = play(tmp_path, snap(signed_in(question=asked)))[0]
        at = now["lines"].index("No walk would go now.")
        assert now["lines"][at:] == [
            "No walk would go now.",
            "All of them",
            "Only walks from now on",
            THEN,
            "2 walks do not say which treadmill made them, and are not sent.",
            "1 walk is outside what your Walka account takes, and stays here.",
            said,
            WHOEVER,
            STOP,
            "Send my walks",
            "Not now",
            "Choose which walks first.",
        ]
    held = [{"id": "20261115-120000", "start": 1_794_744_000.0, "why": "It lasts longer than two days."}]
    counted = signed_in(counts={"unnamed": 1, "held": 3}, held=held)
    seen = play(tmp_path, snap({**counted, "question": question(unnamed=1, held=3)}), press("Not now"))
    one = seen[0]["lines"]
    assert "3 walks are outside what your Walka account takes, and stay here." in one[: one.index("Send my walks")]
    # Under the question, which of them and why. What the question has said in full is not said twice.
    assert one[one.index("Choose which walks first.") + 1 :] == [
        "3 walks are outside what your Walka account takes, and stay here.",
        "Sun, Nov 15, 12:00 PMIt lasts longer than two days.",
        "and 2 more",
    ]
    assert one.count("1 walk does not say which treadmill made it, and is not sent.") == 1
    assert seen[1]["lines"][-1] == "1 walk does not say which treadmill made it, and is not sent."
    # The question names the Walka account it was made for, as the question itself says it.
    other = play(tmp_path, snap(signed_in(question=question(email="other@mail.example", way="google"))))[0]
    assert "To other@mail.example, signed in with Google." in other["lines"]


@needs_node
def test_not_now_closes_the_question_and_it_is_not_asked_again_by_itself(tmp_path: Path):
    asked = signed_in(counts={"waiting": 3}, question=question())
    seen = play(
        tmp_path,
        snap(signed_in(counts={"waiting": 3})),
        press("Send my walks to my Walka account..."),
        snap(asked),
        press("All of them"),
        press("Not now"),
        snap(asked),  # the companion still holds the question: this page has put it off
        snap(signed_in(counts={"waiting": 3})),  # ... and it runs out there
        press("Send my walks to my Walka account..."),
        snap(signed_in(counts={"waiting": 3}, question=question(id="q-2"))),
    )
    closed = seen[4]
    assert closed["panels"] == [] and closed["radios"] == []
    assert names(closed) == ["Sign out", "Send my walks to my Walka account..."]
    # The keyboard is back on the button that opened the question; nothing was asked of the companion.
    assert closed["focus"] == "btn: Send my walks to my Walka account..."
    assert posts(closed) == [("question", {})]
    assert seen[5]["panels"] == [] and seen[6]["panels"] == []
    assert not any("ran out" in line for line in seen[6]["lines"])  # (put off by the person: nothing to say)
    assert posts(seen[6]) == [("question", {})]
    # Asked for again, it is a new question: nothing is chosen.
    again = seen[8]
    assert posts(again) == [("question", {}), ("question", {})]
    assert again["panels"] == ["walka-account-panel walka-account-question"]
    assert [radio["checked"] for radio in again["radios"]] == [False, False]
    assert {"text": "Send my walks", "disabled": True} in again["buttons"]


@needs_node
def test_a_question_that_ran_out_says_so_and_is_not_prepared_again(tmp_path: Path):
    off = signed_in(counts={"waiting": 3})
    asked = signed_in(counts={"waiting": 3}, question=question())
    ran_out = "The question ran out after ten minutes. Nothing was switched on."
    seen = play(
        tmp_path,
        snap(off),
        press("Send my walks to my Walka account..."),
        snap(asked),
        press("All of them"),
        snap(off),  # ten minutes later the companion has dropped the question
        snap(off),
        snap(off),
        press("Send my walks to my Walka account..."),
    )
    gone = seen[4]
    assert gone["panels"] == [] and ran_out in gone["lines"] and ran_out in " ".join(gone["live"])
    assert "Send my walks to my Walka account..." in names(gone)
    assert gone["focus"] == "card"  # the choice the keyboard was on is gone: it stays in the card
    # Not prepared again by itself: over the next snapshots nothing is asked of the companion.
    assert posts(seen[6]) == [("question", {})] and ran_out in seen[6]["lines"]
    assert posts(seen[7]) == [("question", {}), ("question", {})]
    assert ran_out not in seen[7]["lines"]

    # The companion refuses an answer that comes too late (400): nothing is on, and the page says so.
    seen = play(
        tmp_path,
        snap(asked),
        press("Only walks from now on"),
        answer(400, "not an answer to the question"),
        press("Send my walks"),
        snap(asked),
    )
    late = seen[3]
    assert posts(late) == [("sending", {"question": "q-1", "send": "from_now_on"})]
    assert late["toasts"] == [
        {"role": "alert", "kind": "error", "text": "The question ran out. Nothing was switched on."}
    ]
    assert late["panels"] == [] and ran_out in late["lines"] and late["head"] == "sending off"
    assert late["focus"] == "btn: Send my walks to my Walka account..."
    assert seen[4]["panels"] == []  # the same question is not shown again


@needs_node
def test_what_the_companion_refuses_is_shown_the_house_way(tmp_path: Path):
    said = {
        "busy": "Walka is still asking your Walka account. Try again in a moment.",
        "closing": "Walka is closing.",
        "not available": "The Walka account is not available in this Walka.",
        "not signed in": "Walka is not signed in to a Walka account.",
        "no e-mail address": "The sign-in gave Walka no e-mail address, so sending cannot be switched on.",
    }
    steps: list[dict[str, Any]] = [snap(signed_in(counts={"waiting": 3}))]
    for error in said:
        steps += [answer(409, error), press("Send my walks to my Walka account...")]
    steps += [answer(500, "failed"), press("Sign out")]
    seen = play(tmp_path, *steps)
    shown = [seen[2 * at + 2]["toasts"][-1] for at in range(len(said))]
    assert shown == [{"role": "alert", "kind": "error", "text": sentence} for sentence in said.values()]
    assert seen[-1]["toasts"][-1]["text"] == "That did not work. Try again."
    # Nothing of the card changed for it: the next snapshot says what is.
    assert seen[-1]["lines"] == seen[0]["lines"]

    seen = play(tmp_path, snap(part()), answer(409, "signed in already"), press("Sign in with Google"))
    assert seen[2]["toasts"] == [{"role": "alert", "kind": "error", "text": "Walka is signed in already."}]
    seen = play(tmp_path, snap(part()), answer(404, "not available"), press("Sign in with Google"))
    assert seen[2]["toasts"][0]["text"] == "The Walka account is not available in this Walka."


@needs_node
def test_sending_on_says_so_in_the_card_and_in_both_feet(tmp_path: Path):
    on = {"sending": True, "chosen": "all"}
    said = {
        "sent": "Sent.",
        "later": "No answer - Walka tries again later.",
        "too_old": "This Walka is too old for your Walka account as it is now: get the newest Walka.",
        "full": "Your Walka account is full: it has room for 20,000 walks.",
        "stopped": "Walka stopped for now and tries again when it is next started.",
        "certificate": "Walka could not check that it is talking to your Walka account's server, so it sent nothing.",
        "no_answer": "There was no answer. Try again later.",
    }
    seen = play(
        tmp_path,
        snap(signed_in(**on, counts={"sent": 12, "waiting": 1})),
        press("Stop sending"),
        snap(signed_in(counts={"sent": 12, "waiting": 1})),
        snap(signed_in(**on, counts={"sent": 1}), settings=False),  # another view is shown: the feet follow all the same
        snap(part(), settings=False),
        snap(signed_in(**{**on, "chosen": "from", "from": 1_794_830_400.0}, counts={"sent": 2})),
        snap(signed_in(**on, last={"word": "nothing", "at": 1.0, "added": 0})),
        *(snap(signed_in(**on, last={"word": word, "at": 1.0, "added": 1})) for word in said),
    )
    now = seen[0]
    assert now["lines"][3:] == [
        "Sending is on. Each walk goes to your Walka account when it is saved.",
        "12 sent, 1 waiting.",
        "Stop sending",
    ]
    assert now["head"] == "sending on" and now["tone"] == "ok"
    assert (now["railFoot"], now["footStays"]) == (RAIL_FOOT_SENDING, PAGE_STAYS_SENDING)
    # Stopping asks nobody but the companion, and the words of always are back with the next snapshot.
    assert posts(seen[1]) == [("sending/off", {})]
    assert (seen[2]["railFoot"], seen[2]["footStays"]) == (RAIL_FOOT, PAGE_STAYS)
    assert (seen[3]["railFoot"], seen[3]["footStays"]) == (RAIL_FOOT_SENDING, PAGE_STAYS_SENDING)
    assert (seen[4]["railFoot"], seen[4]["footStays"]) == (RAIL_FOOT, PAGE_STAYS)
    assert seen[5]["lines"][4] == "2 sent, 0 waiting. Only walks from Mon, Nov 16, 12:00 PM on are sent."
    assert seen[6]["lines"][-1] == "Stop sending" and seen[6]["live"] == []  # nothing to send: nothing to say
    for at, sentence in enumerate(said.values(), start=7):
        assert seen[at]["lines"][-1] == sentence, at
        assert seen[at]["live"] == [sentence], at
    assert all(step["toasts"] == [] for step in seen)


# The rail's foot as index.html has it, and as it is while sending is on: marked, and its first sentence
# in a part of its own - so that a narrow window, which has no room for the foot, can keep the rest in sight.
FOOT_AS_IT_WAS = {"tag": "p", "class": "rail-foot", "attributes": {"id": "rail-foot"}, "holds": [RAIL_FOOT]}
FOOT_SENDING = {
    "tag": "p",
    "class": "rail-foot",
    "attributes": {"id": "rail-foot", "data-sending": "1"},
    "holds": [
        {"tag": "span", "class": "rail-foot-here", "attributes": {}, "holds": ["Runs on this computer. "]},
        WALKS_ARE_SENT,
    ],
}


@needs_node
def test_the_rails_foot_is_marked_while_sending_is_on_and_only_then(tmp_path: Path):
    on = {"sending": True, "chosen": "all"}
    not_on = [
        {"op": "look"},  # before any snapshot: what index.html has
        snap(None),  # Walka has no such part
        snap(part(state="unavailable")),  # ... or it is not available (the simulated treadmill)
        snap(part()),  # nobody ever signed in
        snap(part(state="signing_in")),
        snap(part(problem="refused")),
        snap(signed_in(first_step="not_taken")),
        snap(signed_in(counts={"waiting": 3})),  # signed in, sending off
        snap(signed_in(question=question())),  # the question is open: nothing is switched on yet
        snap(signed_in(sending="yes")),  # (only a plain yes is on)
        snap(part(sending=True)),  # (nobody is signed in: not on, whatever else is said)
        snap(part(notice="ended")),
        snap(part(left_in_store="sign_in")),
    ]
    seen = play(
        tmp_path,
        *not_on,
        snap(signed_in(**on)),
        snap(signed_in(**on, rev=2)),
        snap(signed_in(**on, rev=3), settings=False),  # another view is shown: the foot is on every view
        snap(signed_in()),  # stopped
        snap(signed_in(**on), settings=False),
        snap(None, settings=False),  # ... and Walka's part is gone while another view is shown
    )
    # Not sending: the foot is what the page always had - its words, its class, no mark and no part.
    # By that the stylesheet treats it as it always did, at every width (the test of app.css, below).
    assert FOOT_AS_IT_WAS["holds"] == [page_now()["railFoot"]]
    for at in (*range(len(not_on)), len(not_on) + 3, len(not_on) + 5):
        assert seen[at]["railFootAs"] == FOOT_AS_IT_WAS, at
    # Sending: the same words as before in all, marked, and the sentence that says so apart from the first.
    for at in (len(not_on), len(not_on) + 1, len(not_on) + 2, len(not_on) + 4):
        assert seen[at]["railFootAs"] == FOOT_SENDING, at
        assert seen[at]["railFoot"] == RAIL_FOOT_SENDING, at
    # A snapshot comes twice a second: the foot is written when it changes, not each time.
    assert seen[len(not_on) + 1]["made"] == seen[len(not_on)]["made"]
    assert seen[len(not_on) + 2]["made"] == seen[len(not_on)]["made"]
    assert all(step["toasts"] == [] and step["asked"] == [] for step in seen)


def css_rules(css: str) -> list[tuple[tuple[str, ...], list[str], str]]:
    """A stylesheet's rules, in order: the @-rules each stands in, its selectors, and what it sets."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    found: list[tuple[tuple[str, ...], list[str], str]] = []
    inside: list[str] = []
    at = 0
    while True:
        brace = min((i for i in (css.find("{", at), css.find("}", at)) if i >= 0), default=-1)
        if brace < 0:
            return found
        if css[brace] == "}":
            inside.pop()
            at = brace + 1
            continue
        head = flat(css[at:brace]).strip()
        if head.startswith("@"):
            inside.append(head)
            at = brace + 1
            continue
        end = css.index("}", brace)
        assert "{" not in css[brace + 1 : end], head  # (no rule inside a rule in this stylesheet)
        found.append((tuple(inside), [s.strip() for s in head.split(",")], flat(css[brace + 1 : end]).strip()))
        at = end + 1


def test_a_narrow_window_keeps_the_rails_foot_in_sight_only_while_it_is_marked():
    """Below 1180 px the page has no room for the rail's foot and puts it away. While sending is on the
    foot is what says, on Live, History and Share, that walks are sent: then it stays, marked by the script."""
    css = read("app.css")
    foot = [
        (inside, selector, sets)
        for inside, selectors, sets in css_rules(css)
        for selector in selectors
        if "rail-foot" in selector
    ]
    narrow, phone = ("@media (max-width: 1179px)",), ("@media (max-width: 719px)",)
    assert [(inside, selector) for inside, selector, _ in foot] == [
        ((), ".rail-foot"),
        (narrow, ".rail-foot:not([data-sending])"),
        (narrow, ".rail-foot-here"),
        (narrow, ".rail-foot[data-sending]"),
        (phone, ".rail-foot[data-sending]"),
    ]
    sets = [found[2] for found in foot]
    # Without the mark - sending off, signed out, never signed in, no such part - two rules reach the foot,
    # and both are the ones the page always had: how it looks, and that a narrow window puts it away.
    assert sets[0] == (
        "padding: 10px 24px 14px; border-top: 1px solid var(--line); color: var(--ink-3); font-size: 12px; "
        "line-height: 1.4;"
    )
    put_away = (
        "position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); white-space: nowrap;"
    )
    assert sets[1] == put_away
    together = [selectors for _, selectors, _ in css_rules(css) if ".rail-foot-here" in selectors]
    assert together == [[".brand-name", ".rail-label", ".rail-foot:not([data-sending])", ".rail-foot-here"]]
    # With the mark nothing puts it away, at any width: all that is set is how much room it has and how
    # big its letters are. It stays where it always was - at the rail's end.
    for at in (3, 4):
        names = set(re.findall(r"(?:^|; )([\w-]+):", sets[at]))
        assert names <= {"padding", "border-top", "font-size", "text-align"}, at
    # Its first sentence is put away with the unmarked foot (the same rule, above): the narrow foot is cut
    # down to the sentence that says walks are sent. Nothing reaches the foot by another name.
    selectors = [selector for _, found, _ in css_rules(css) for selector in found]
    assert "#rail-foot" not in css and not [s for s in selectors if re.search(r"\.chrome\s*[>+~\s]", s)]
    # (Nor as "what comes after the lamps", nor by the mark alone.)
    assert not [s for s in selectors if re.search(r"\.rail-status\s*[+~]", s)]
    assert [s for s in selectors if "data-sending" in s and ".rail-foot" not in s] == []
    # The mark and the part are the script's own: the page has neither until sending is on.
    page = read("index.html")
    assert "data-sending" not in page and "rail-foot-here" not in page
    knows = sorted(
        path.name for path in STATIC.glob("*.js") if re.search(r"data-sending|rail-foot", path.read_text("utf-8"))
    )
    assert knows == [SCRIPT]
    script = read(SCRIPT)
    assert script.count('"data-sending"') == 2 and script.count('"rail-foot-here"') == 1  # set, and taken off
    assert RAIL_FOOT_SENDING.endswith(" " + WALKS_ARE_SENT)


@needs_node
def test_walks_the_account_did_not_take_and_walks_that_stay_are_listed(tmp_path: Path):
    message = 'new row for relation "walks" violates check constraint <b onclick="x">walks_distance_m_check</b>'
    account = signed_in(
        sending=True,
        chosen="all",
        counts={"sent": 4, "aside": 2, "held": 25, "unnamed": 1, "unreadable": 3},
        aside=[
            {"id": "20261116-120000", "start": 1_794_830_400.0, "code": "23514", "message": message},
            {"id": "20261115-120000", "start": 1_794_744_000.0, "code": "22003", "message": "numeric field overflow"},
        ],
        held=[
            {"id": f"202611{day:02}-080000", "start": 1_794_830_400.0 - 86400 * day, "why": "It lasts longer than two days."}
            for day in range(1, 21)
        ],
    )
    now = play(tmp_path, snap(account))[0]
    at = now["lines"].index("Your Walka account did not take 2 walks. They stay here.")
    assert now["lines"][at : at + 4] == [
        "Your Walka account did not take 2 walks. They stay here.",
        f"Mon, Nov 16, 12:00 PM{message}",  # the day, and what the Walka account said - as text, whatever it says
        "Sun, Nov 15, 12:00 PMnumeric field overflow",
        "25 walks are outside what your Walka account takes, and stay here.",
    ]
    held = now["lines"][at + 4 : at + 25]
    assert held[0] == "Sun, Nov 15, 12:00 PMIt lasts longer than two days."
    assert len(held) == 21 and held[-1] == "and 5 more"  # the companion names twenty at most
    assert now["lines"][at + 25 :] == [
        "1 walk does not say which treadmill made it, and is not sent.",
        "3 walks could not be read, and are not sent.",
    ]
    assert now["links"] == [] and now["marks"] == 0
    one = play(tmp_path, snap(signed_in(counts={"aside": 1, "unreadable": 1})))[0]
    assert one["lines"][-2:] == [
        "Your Walka account did not take 1 walk. It stays here.",
        "1 walk could not be read, and is not sent.",
    ]


@needs_node
def test_when_the_sign_in_ended_it_is_said_once_in_the_card(tmp_path: Path):
    seen = play(
        tmp_path,
        snap(signed_in(sending=True, chosen="all")),
        snap(part(notice="ended")),
        press("OK"),
        snap(part()),
    )
    now = seen[1]
    assert now["lines"] == [
        ENDED,
        "OK",
        "Optional. Walka works in full without it.",
        "Sign in with Discord",
        "Sign in with GitHub",
        "Sign in with Google",
        BEFORE_THE_BROWSER,
    ]
    # It asks for nothing; it is read out; no pop-up; and the feet say again that nothing is uploaded.
    assert now["panels"] == ["walka-account-panel"] and ENDED in " ".join(now["live"])
    assert now["toasts"] == [] and now["head"] == ""
    assert (now["railFoot"], now["footStays"]) == (RAIL_FOOT, PAGE_STAYS)
    assert posts(seen[2]) == [("notice/ok", {})]
    assert ENDED not in seen[3]["text"] and seen[3]["focus"] == "card"


@needs_node
def test_deleting_a_walk_that_was_sent_says_where_its_copy_is(tmp_path: Path):
    words = play(tmp_path, {"op": "sent_words"})[0]
    assert words["question"] == DELETE_SENT
    assert words["links"] == [
        {
            "text": "walka.dev/account/walks/",
            "href": "https://walka.dev/account/walks/",
            "target": "_blank",
            "rel": "noopener noreferrer",
        }
    ]
    assert words["button"] == "Delete from this computer" and words["toast"] == "Walk deleted from this computer."

    # A copy in a Walka account that is not the one signed in now: words of its own, and the same link.
    other, both = play(tmp_path, {"op": "sent_words", "other": True}, {"op": "sent_words", "elsewhere": True})
    assert other["question"] == DELETE_SENT_ELSEWHERE and other["links"] == words["links"]
    assert other["button"] == "Delete from this computer" and other["toast"] == "Walk deleted from this computer."
    assert "your" not in other["question"].replace("your totals here", "")
    # A copy in both: the words for the Walka account that is signed in, and one sentence more.
    assert both["question"] == DELETE_SENT + ANOTHER_COPY and both["links"] == words["links"]
    assert (both["button"], both["toast"]) == (words["button"], words["toast"])


@needs_node
def test_a_sign_in_that_may_still_be_in_the_store_is_said_in_the_card_with_one_button(tmp_path: Path):
    left = part(left_in_store="sign_in")
    seen = play(
        tmp_path,
        snap(left),
        snap(left, settings=False),
        press("Try again"),
        snap(part(left_in_store="sign_in", state="signing_in")),
        snap(part(left_in_store="sign_in", notice="ended")),
        snap(part()),
        snap(signed_in(left_in_store="sign_in")),
    )
    now = seen[0]
    assert now["lines"] == [
        LEFT_WINDOWS,
        "Try again",
        "Optional. Walka works in full without it.",
        "Sign in with Discord",
        "Sign in with GitHub",
        "Sign in with Google",
        BEFORE_THE_BROWSER,
    ]
    # In the card and nowhere else: no pop-up, no lamp, no word in the head, and the feet as always.
    assert now["panels"] == ["walka-account-panel"] and LEFT_WINDOWS in " ".join(now["live"])
    assert now["toasts"] == [] and now["head"] == "" and now["marks"] == 0 and now["tone"] == "idle"
    assert (now["railFoot"], now["footStays"]) == (RAIL_FOOT, PAGE_STAYS)
    assert now["everything"].replace(now["text"], "") == RAIL_FOOT + PAGE_STAYS
    assert now["asked"] == []  # (showing it asks nothing of anybody)
    # The button is the route of "Sign out": the companion then asks the store once more, and nobody else.
    assert posts(seen[2]) == [("sign-out", {})] and seen[2]["toasts"] == []
    # While a new sign-in waits for the browser it is not said: that sign-in's key is written over the entry.
    assert LEFT_IN not in seen[3]["text"] and "Try again" not in names(seen[3])
    # Beside decision 11's sentence, not instead of it - and each with its own button.
    assert seen[4]["lines"][:4] == [ENDED, "OK", LEFT_WINDOWS, "Try again"]
    assert seen[4]["panels"] == ["walka-account-panel"] * 2
    # Gone when the companion no longer says so; and never beside somebody who is signed in.
    assert LEFT_IN not in seen[5]["text"] and LEFT_IN not in seen[6]["text"]
    assert "Try again" not in names(seen[5]) + names(seen[6])
    # It names the system's own store - of the computer that runs Walka, and only if that is this one.
    mac = play(tmp_path, snap(left), platform="MacIntel")
    assert mac[0]["lines"][0] == LEFT_MAC
    away = play(tmp_path, snap(left), hostname="192.168.1.20")
    assert away[0]["lines"][0] == LEFT_ELSEWHERE
    # Any other word of the companion's is not this one: nothing is said.
    for word in ("", "yes", True, 1, None, "SIGN_IN"):
        assert LEFT_IN not in play(tmp_path, snap(part(left_in_store=word)))[0]["text"], word


@needs_node
def test_a_word_about_one_try_does_not_stand_beside_the_next(tmp_path: Path):
    """The companion keeps its last word until it has another. The card says it of the try it was about."""
    no_answer = "There was no answer. Try again later."
    failed = {"word": "no_answer", "at": 5.0, "added": 0}
    seen = play(
        tmp_path,
        snap(signed_in(counts={"waiting": 3}, last=failed)),
        press("Send my walks to my Walka account..."),
        snap(signed_in(counts={"waiting": 3}, last=failed, busy="asking", rev=2)),
        snap(signed_in(counts={"waiting": 3}, last=failed, question=question(), rev=3)),  # this time it was answered
        press("Not now"),
        snap(signed_in(counts={"waiting": 3}, last=failed, rev=4)),
        press("Send my walks to my Walka account..."),
        snap(signed_in(counts={"waiting": 3}, last={**failed, "at": 9.0}, rev=5)),  # ... and this time not
    )
    assert seen[0]["lines"][-1] == no_answer
    assert no_answer not in seen[1]["lines"]  # pressed again: what the last try came to is not said of this one
    assert no_answer not in seen[2]["lines"] and "Asking your Walka account..." in seen[2]["lines"]
    assert seen[3]["panels"] == ["walka-account-panel walka-account-question"] and no_answer not in seen[3]["lines"]
    assert no_answer not in seen[5]["lines"]
    assert seen[7]["lines"][-1] == no_answer and seen[7]["live"] == [no_answer]

    # A word about sending is not said of anything once sending was stopped - but what is true of the
    # Walka account whoever asks (it is full; this Walka is too old for it) stays said.
    stopped = {"word": "stopped", "at": 7.0, "added": 0}
    full = {"word": "full", "at": 8.0, "added": 0}
    seen = play(
        tmp_path,
        snap(signed_in(sending=True, chosen="all", last=stopped)),
        press("Stop sending"),
        snap(signed_in(last=stopped, rev=2)),
        snap(signed_in(sending=True, chosen="all", last=full, rev=3)),
        snap(signed_in(last=full, rev=4)),
    )
    assert seen[0]["lines"][-1] == "Walka stopped for now and tries again when it is next started."
    assert seen[2]["lines"][-2:] == ["Send my walks to my Walka account...", "Asks your Walka account first."]
    assert seen[2]["live"] == []
    assert seen[3]["lines"][-1] == seen[4]["lines"][-1] == "Your Walka account is full: it has room for 20,000 walks."

    # "Look again" is such a button too.
    seen = play(
        tmp_path,
        snap(signed_in(first_step="not_taken", last=failed)),
        press("Look again (asks your Walka account)"),
        snap(signed_in(first_step="taken", last=failed, rev=2)),
    )
    assert seen[0]["lines"][-1] == no_answer
    assert no_answer not in seen[1]["lines"] and no_answer not in seen[2]["lines"]
    assert posts(seen[2]) == [("look-again", {})]


@needs_node
def test_a_question_that_does_not_say_which_walka_account_is_not_shown(tmp_path: Path):
    """Decision 10: the person must be able to tell which Walka account before a walk leaves."""
    for nameless in (question(email=""), {key: value for key, value in question().items() if key != "email"}):
        now = play(tmp_path, snap(signed_in(counts={"waiting": 3}, question=nameless)))[0]
        assert now["panels"] == [] and now["radios"] == [] and "Send my walks" not in names(now)
        assert not any("ran out" in line for line in now["lines"])


# ---- the History view, under Node: deleting a walk that is in the Walka account ------------------

DELETE_PLAIN = "Delete this walk? It disappears from your totals here and from the addon's history. This cannot be undone."


def walk(walk_id: str, start: float, **more: Any) -> dict[str, Any]:
    """One walk as ``GET /api/sessions`` lists it."""
    made = {"id": walk_id, "start": start, "end": start + 600.0, "seconds": 600.0, "distance_m": 500.0, "steps": 800}
    made.update(kcal=30.0, avg_kmh=3.0, max_kmh=3.5, avg_hr=None, max_hr=None, source="free", treadmill="A pad")
    made.update(steps_source="estimate", distance_source="speed", calories_source="speed")
    return {**made, **more}


def history(account: dict[str, Any] | None, walks: list[dict[str, Any]]) -> dict[str, Any]:
    """A step: a snapshot comes while History is the view shown. Only what that view reads of it is there."""
    sums = {"sessions": len(walks), "steps": 800 * len(walks), "distance_m": 500.0, "seconds": 600.0, "kcal": 30.0}
    s: dict[str, Any] = {"version": "0.8.0", "units": "mph", "last_session": walks[0] if walks else None}
    s["totals"] = {"today": sums, "week": sums, "all": sums, "streak": 1, "days": []}
    if account is not None:
        s["walka_account"] = account
    return {"op": "history", "s": s}


def gets(seen: dict[str, Any]) -> list[str]:
    return [request["path"] for request in seen["asked"] if request["method"] == "GET"]


SENT = walk("20261116-120000", 1_794_830_400.0, in_walka_account=True, in_other_walka_account=False)
NOT_SENT = walk("20261115-120000", 1_794_744_000.0, in_walka_account=False, in_other_walka_account=False)
# A copy was sent to a Walka account that is not the one signed in now; and one that has a copy in both.
SENT_ELSEWHERE = walk("20261113-120000", 1_794_571_200.0, in_walka_account=False, in_other_walka_account=True)
SENT_TO_BOTH = walk("20261112-120000", 1_794_484_800.0, in_walka_account=True, in_other_walka_account=True)
WALKS_LINK = {
    "text": "walka.dev/account/walks/",
    "href": "https://walka.dev/account/walks/",
    "target": "_blank",
    "rel": "noopener noreferrer",
}
LIST = "/api/sessions?limit=200"


@needs_node
def test_the_question_before_a_sent_walk_is_deleted_says_where_its_copy_stays(tmp_path: Path):
    on = signed_in(sending=True, chosen="all", counts={"sent": 1, "waiting": 1})
    seen = play(
        tmp_path,
        {"op": "walks", "walks": [SENT, NOT_SENT]},
        history(on, [SENT, NOT_SENT]),
        {"op": "row", "at": 0},
        press("Delete walk..."),
        press("Delete from this computer"),
        {"op": "row", "at": 0},  # (the walk that is left)
        press("Delete walk..."),
        press("Delete permanently"),
        history=True,
    )
    assert gets(seen[1]) == [LIST] and seen[1]["rows"] == ["false", "false"] and seen[1]["confirms"] == []
    asked = seen[3]["confirms"]
    assert asked == [
        {
            "text": DELETE_SENT,
            "buttons": ["Keep it", "Delete from this computer"],
            "links": [
                {
                    "text": "walka.dev/account/walks/",
                    "href": "https://walka.dev/account/walks/",
                    "target": "_blank",
                    "rel": "noopener noreferrer",
                }
            ],
        }
    ]
    assert seen[3]["focus"] == "btn: Keep it"  # as before: the keyboard is on the way back
    # It is deleted here and nowhere else: one request, to the companion, for this walk.
    assert seen[4]["asked"][-1] == {
        "path": "/api/sessions/20261116-120000",
        "method": "DELETE",
        "type": "application/json",
        "body": {},
    }
    assert seen[4]["toasts"] == [{"role": "status", "kind": "ok", "text": "Walk deleted from this computer."}]
    assert seen[4]["rows"] == ["false"]
    # A walk that is not in the Walka account: the words there always were.
    assert seen[6]["confirms"] == [{"text": DELETE_PLAIN, "buttons": ["Keep it", "Delete permanently"], "links": []}]
    assert seen[7]["asked"][-1]["path"] == "/api/sessions/20261115-120000"
    assert seen[7]["toasts"][-1] == {"role": "status", "kind": "ok", "text": "Walk deleted."}

    # A Walka without that part says nothing of it anywhere on the view, and asks for the list once.
    plain = walk("20261114-120000", 1_794_657_600.0)
    seen = play(
        tmp_path,
        {"op": "walks", "walks": [plain]},
        history(None, [plain]),
        history(None, [plain]),
        {"op": "row", "at": 0},
        press("Delete walk..."),
        history=True,
    )
    assert gets(seen[2]) == [LIST]
    assert seen[4]["confirms"] == [{"text": DELETE_PLAIN, "buttons": ["Keep it", "Delete permanently"], "links": []}]
    assert "account" not in seen[4]["everything"] and "walka.dev" not in seen[4]["everything"]
    assert (seen[4]["railFoot"], seen[4]["footStays"]) == (RAIL_FOOT, PAGE_STAYS)


@needs_node
def test_after_signing_out_the_question_still_says_that_a_copy_was_sent_to_a_walka_account(tmp_path: Path):
    """Decision 8 has no "while signed in": the copy stays where it was sent, whoever is signed in now."""
    walks = [SENT_ELSEWHERE, NOT_SENT]
    seen = play(
        tmp_path,
        {"op": "walks", "walks": walks},
        history(part(), walks),  # nobody is signed in
        {"op": "row", "at": 0},
        press("Delete walk..."),
        press("Delete from this computer"),
        {"op": "row", "at": 0},  # (the walk that is left: it was never sent anywhere)
        press("Delete walk..."),
        press("Delete permanently"),
        history=True,
    )
    assert seen[3]["confirms"] == [
        {"text": DELETE_SENT_ELSEWHERE, "buttons": ["Keep it", "Delete from this computer"], "links": [WALKS_LINK]}
    ]
    assert seen[3]["focus"] == "btn: Keep it"
    # Deleted here and nowhere else, and the toast says what is true.
    assert seen[4]["asked"][-1]["path"] == "/api/sessions/20261113-120000"
    assert seen[4]["asked"][-1]["method"] == "DELETE"
    assert seen[4]["toasts"] == [{"role": "status", "kind": "ok", "text": "Walk deleted from this computer."}]
    assert seen[6]["confirms"] == [{"text": DELETE_PLAIN, "buttons": ["Keep it", "Delete permanently"], "links": []}]
    assert seen[7]["toasts"][-1] == {"role": "status", "kind": "ok", "text": "Walk deleted."}

    # With another Walka account signed in: the same words for the copy in the first one, the approved
    # words for a walk that is in the one signed in now - and both for a walk that is in both.
    walks = [SENT, SENT_ELSEWHERE, SENT_TO_BOTH]
    on = signed_in(sending=True, chosen="all", counts={"sent": 2})
    seen = play(
        tmp_path,
        {"op": "walks", "walks": walks},
        history(on, walks),
        {"op": "row", "at": 1},
        press("Delete walk..."),
        {"op": "row", "at": 2},
        press("Delete walk..."),
        {"op": "row", "at": 0},
        press("Delete walk..."),
        history=True,
    )
    assert seen[3]["confirms"][0]["text"] == DELETE_SENT_ELSEWHERE
    assert seen[5]["confirms"] == [
        {
            "text": DELETE_SENT + ANOTHER_COPY,
            "buttons": ["Keep it", "Delete from this computer"],
            "links": [WALKS_LINK],
        }
    ]
    assert seen[7]["confirms"][0]["text"] == DELETE_SENT

    # Somebody who never signed in (the part is there, the file is not): every walk comes with both
    # words false, and the question is the plain one, word for word - and says nothing of a Walka account.
    never = walk("20261114-120000", 1_794_657_600.0, in_walka_account=False, in_other_walka_account=False)
    seen = play(
        tmp_path,
        {"op": "walks", "walks": [never]},
        history(part(), [never]),
        {"op": "row", "at": 0},
        press("Delete walk..."),
        history=True,
    )
    assert seen[3]["confirms"] == [{"text": DELETE_PLAIN, "buttons": ["Keep it", "Delete permanently"], "links": []}]
    assert "Walka account" not in seen[3]["confirms"][0]["text"] and seen[3]["toasts"] == []
    # Anything but a plain "true" is not that word (a companion that is older, or one that is broken).
    for junk in ("true", 1, None, {"yes": 1}):
        odd = {**never, "in_other_walka_account": junk}
        seen = play(
            tmp_path,
            {"op": "walks", "walks": [odd]},
            history(part(), [odd]),
            {"op": "row", "at": 0},
            press("Delete walk..."),
            history=True,
        )
        assert seen[3]["confirms"][0]["text"] == DELETE_PLAIN, junk


@needs_node
def test_the_list_of_walks_is_fetched_again_when_a_walk_may_have_been_sent(tmp_path: Path):
    waiting = {**SENT, "in_walka_account": False}
    on = {"sending": True, "chosen": "all"}
    seen = play(
        tmp_path,
        {"op": "walks", "walks": [waiting]},
        history(signed_in(**on, rev=1), [waiting]),
        history(signed_in(**on, rev=1), [waiting]),
        {"op": "row", "at": 0},
        press("Delete walk..."),  # the question is open, in the words for a walk that was not sent ...
        {"op": "walks", "walks": [SENT]},
        history(signed_in(**on, rev=2, counts={"sent": 1}), [SENT]),  # ... and now it has been
        press("Delete walk..."),
        history(signed_in(**on, rev=3, counts={"sent": 1}), [SENT]),
        history=True,
    )
    assert gets(seen[2]) == [LIST]  # a snapshot that says nothing new asks for nothing
    assert seen[4]["confirms"][0]["text"] == DELETE_PLAIN
    # The list is asked for again, and the question in the old words is not left standing.
    assert gets(seen[6]).count(LIST) == 2 and seen[6]["confirms"] == [] and seen[6]["rows"] == ["true"]
    assert seen[7]["confirms"][0]["text"] == DELETE_SENT
    assert seen[7]["confirms"][0]["buttons"] == ["Keep it", "Delete from this computer"]
    # Asked again, and nothing in the list has changed: the view is left as the person has it.
    assert gets(seen[8]).count(LIST) == 3 and seen[8]["confirms"] == seen[7]["confirms"]
    assert all(step["toasts"] == [] for step in seen)


@needs_node
def test_a_walk_saved_while_the_list_is_on_its_way_is_not_missed(tmp_path: Path):
    on = {"sending": True, "chosen": "all"}
    seen = play(
        tmp_path,
        {"op": "walks", "walks": [NOT_SENT]},
        history(signed_in(**on, rev=1), [NOT_SENT]),
        {"op": "hold"},
        history(signed_in(**on, rev=2), [NOT_SENT]),  # asks for the list, and the answer is on its way ...
        {"op": "walks", "walks": [SENT, NOT_SENT]},
        history(signed_in(**on, rev=2), [SENT, NOT_SENT]),  # ... when a walk is saved
        {"op": "release"},  # the list from before the walk arrives
        history(signed_in(**on, rev=2), [SENT, NOT_SENT]),
        history(signed_in(**on, rev=2), [SENT, NOT_SENT]),
        history=True,
    )
    assert gets(seen[5]).count(LIST) == 2 and len(seen[6]["rows"]) == 1
    assert gets(seen[7]).count(LIST) == 3 and len(seen[7]["rows"]) == 2
    assert gets(seen[8]).count(LIST) == 3
