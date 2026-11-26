# Translating the dashboard

[← Back to the README](../README.md)

The dashboard can be translated; there are no translations yet.

* `companion/src/walka/static/locales/strings.json` lists the dashboard's
  English strings. It is made by `python tools/dashboard_strings.py`, and a
  test fails when it is out of date.
* A translation is `companion/src/walka/static/locales/<language>.json`:

  ```json
  {"language": "de", "strings": {"Settings": "Einstellungen", "Walka {1} is out.": "Walka {1} ist da."}}
  ```

  Keys are the English text. `{1}`, `{2}` ... are the parts that change
  (a version, a number, a name); they may move in the sentence. Strings
  left out stay in English.
* Add the language code to `AVAILABLE` in `static/i18n.js`. The dashboard
  picks the first of the browser's (or the system's) languages that has a
  translation.

A language written from the right (Arabic, Hebrew, Persian, Urdu) sets
the page's direction too; for another one, add `"dir": "rtl"` to its file.

## What can be translated today, and what cannot

`strings.json` is what can be translated. A text that is not in it stays
in English, even in a translation file. Today that is so for:

* **Texts with a number or a name at the very end**, such as
  `` `Walk saved: ${minutes}` ``, and **texts with `%` in them**: the list
  maker leaves them out.
* **Single words in small letters** (`"connected"`, `"paused"`): left out
  so that names in the code are not taken for words. Capitalised single
  words (`"Connect"`) are in.
* **A text inside another text** (a template within a template): only
  the inner parts are listed, each on its own.
* **Words put straight into an element** as a child, `h("p", {}, "...")`:
  only the `text` and labelling attributes of `h()` are translated.
  Words passed as `{ text: "..." }` are.
* **The charts** (`charts.js`): their axis labels and keys are never
  translated.
* **The floating window** (`floating.html`, `floating.js`): it loads no
  language at all.
* The words on the Share picture (they can be edited on the Share page),
  the files Walka writes, the command-line tools and the game addon.

Words the companion sends to the dashboard (a treadmill's state, the
activity log) can be translated by adding them to a translation, the same
way, as long as they reach the page through `text`.

Check a translation at both narrow (about 420 px) and wide (about 1280 px)
windows: some labels have little room, and longer words may need shorter
wording.
