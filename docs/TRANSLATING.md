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

What is not translated by this: the words on the Share picture (they can be
edited on the Share page), the files Walka writes, the command-line tools,
and the game addon. Words the companion sends to the dashboard (a
treadmill's state, the activity log) can be translated by adding them to a
translation, the same way.

Check a translation at both narrow (about 420 px) and wide (about 1280 px)
windows: some labels have little room, and longer words may need shorter
wording.
