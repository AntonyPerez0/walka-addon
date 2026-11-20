// The Share view: make a picture of a character's journey to post somewhere.
//
// The numbers come from the game (GET /api/journey: one entry per character
// the companion has seen, remembered while the game is closed) or from the
// companion's own all-time totals (in every state snapshot). The picture is
// drawn on a canvas in this page by card.js; nothing leaves the computer
// until the user saves or copies it. The choices made here are kept in this
// browser only.

import { $, api, h, run, setAttr, setDisabled, setShown, setText, state, toast } from "./util.js";
import {
  DEFAULT_TEXTS,
  SIZES,
  TEXT_LIMITS,
  TILES,
  buildCard,
  defaultSubtitle,
  fileName,
  hasRoad,
  normalizeOptions,
} from "./cardmodel.js";
import { THEMES, drawCard } from "./card.js";
import { LANDS } from "./lands.js";

const STORE_KEY = "walka.share.v1";
const THEME_IDS = THEMES.map((theme) => theme.id);
const CLASSES = [
  "",
  "Warrior",
  "Paladin",
  "Hunter",
  "Rogue",
  "Priest",
  "Death Knight",
  "Shaman",
  "Mage",
  "Warlock",
  "Monk",
  "Druid",
  "Demon Hunter",
  "Evoker",
];
const TEXT_FIELDS = [
  { key: "overline", label: "Small line on top" },
  { key: "title", label: "Title" },
  { key: "subtitle", label: "Line under the title" },
  { key: "tagline", label: "Closing line" },
];

let options = loadOptions();
let snapshot = null; // the last state snapshot seen while this view was open
let journey = null; // the last answer of /api/journey
let fetchedRev = null; // the journey revision that answer was fetched for
let fetching = false;
// The road of the chosen character (GET /api/road): the companion reads it
// from the file the game writes at logout or /reload, so it is asked for
// again now and then while this view is open.
let road = null; // the last answer
let roadFor = ""; // the character id it is the answer for
let roadAsked = ""; // the character id last asked about (answered or not)
let roadAt = 0; // when it was asked (ms)
let roadFetching = false;
const ROAD_EVERY_MS = 15000;
let paintedKey = "";
let paintQueued = false;
const inputs = {}; // text inputs by key

// ---- choices, kept in this browser -----------------------------------------------------------

function loadOptions() {
  try {
    return normalizeOptions(JSON.parse(localStorage.getItem(STORE_KEY) || "null"), THEME_IDS);
  } catch {
    return normalizeOptions(null, THEME_IDS); // storage switched off, or something else was stored there
  }
}

function saveOptions() {
  try {
    localStorage.setItem(STORE_KEY, JSON.stringify(options));
  } catch {
    // Private windows and full or disabled storage: the choices then last until the page is closed.
  }
}

function change(patch) {
  options = normalizeOptions({ ...options, ...patch }, THEME_IDS);
  saveOptions();
  announce(""); // "Saved" and "Copied" were about the picture as it was
  refreshRoad(); // (another character may have been picked)
  refresh();
}

// ---- what is known ---------------------------------------------------------------------------

const characters = () => (journey ? journey.characters : []);

/** The character the picture is about: the one picked here, else whoever is playing, else the last seen. */
function chosen() {
  const list = characters();
  return (
    list.find((entry) => entry.id === options.character) ||
    list.find((entry) => journey && entry.id === journey.current) ||
    list[0] ||
    null
  );
}

function describe(entry) {
  const name = entry.name || "Unnamed character";
  const kind = CLASSES[entry.class_id] || "";
  const level = entry.level ? `level ${entry.level}${kind ? ` ${kind}` : ""}` : kind;
  return level ? `${name} - ${level}` : name;
}

function lastSeen(entry, now) {
  if (entry.live) return "in the game now, numbers update as you play";
  const days = Math.floor((now - entry.seen_at) / 86400);
  const when = new Date(entry.seen_at * 1000).toLocaleDateString([], {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
  return days < 1 ? "last seen today" : `last seen ${when}`;
}

const allTotals = () => (snapshot && snapshot.totals ? snapshot.totals.all : null);
const pictureSize = () => SIZES.find((size) => size.id === options.size) || SIZES[0];
const usesKm = () => (options.units || (state.units === "kmh" ? "km" : "mi")) === "km";

/** The chosen character's road, if the companion has found one. */
function chosenRoad() {
  const character = chosen();
  return character && road && roadFor === character.id && hasRoad(road.road, character) ? road.road : null;
}

function currentCard() {
  return buildCard({
    character: chosen(),
    road: chosenRoad(),
    lands: LANDS,
    totals: allTotals(),
    options,
    units: state.units,
    weightKg: snapshot && snapshot.config ? snapshot.config.profile.weight_kg : null,
    now: snapshot ? snapshot.time : Date.now() / 1000,
  });
}

// ---- drawing ------------------------------------------------------------------------------------

function paint() {
  paintQueued = false;
  const canvas = $("share-canvas");
  const size = pictureSize();
  const card = currentCard();
  // A snapshot arrives twice a second; the picture is only redrawn when it would look different.
  const key = JSON.stringify([card, options.theme, size.id]);
  if (key === paintedKey) return card;
  paintedKey = key;
  if (canvas.width !== size.width) canvas.width = size.width;
  if (canvas.height !== size.height) canvas.height = size.height;
  canvas.dataset.size = size.id;
  drawCard(canvas.getContext("2d"), size.width, size.height, card, options.theme);
  setAttr(canvas, "aria-label", card.alt);
  return card;
}

function queuePaint() {
  if (paintQueued) return;
  paintQueued = true;
  requestAnimationFrame(paint);
}

// ---- the page around the picture ---------------------------------------------------------------

function fillCharacters(list, character) {
  const select = $("share-character");
  const key = JSON.stringify(list.map((entry) => [entry.id, describe(entry)]));
  if (select.dataset.key !== key) {
    select.dataset.key = key;
    // Names come off the screen: they are only ever written as text.
    select.replaceChildren(...list.map((entry) => h("option", { value: entry.id, text: describe(entry) })));
  }
  if (character) select.value = character.id;
  setShown($("share-character-field"), list.length > 1);
}

function refresh() {
  const list = characters();
  const character = chosen();
  const totals = allTotals();
  const walks = totals ? totals.sessions : 0;
  const loaded = journey !== null;
  const canDraw = loaded && (!!character || walks > 0);
  const now = snapshot ? snapshot.time : Date.now() / 1000;

  setShown($("share-loading"), !loaded);
  setShown($("share-empty"), loaded && !character);
  setShown($("share-empty-walks"), loaded && !character && walks > 0);
  setShown($("share-frame"), canDraw);
  setShown($("share-demo"), !!(journey && journey.demo));
  setDisabled($("share-download"), !canDraw);
  setDisabled($("share-copy"), !canDraw || !canCopy());

  fillCharacters(list, character);
  setText($("share-character-line"), character ? `${describe(character)}: ${lastSeen(character, now)}.` : "");
  setShown($("share-character-line"), !!character);

  const fromGame = !!character && options.source !== "all";
  for (const radio of document.querySelectorAll('input[name="share-source"]')) {
    radio.checked = radio.value === (fromGame ? "character" : "all");
    if (radio.value === "character") setDisabled(radio, !character);
  }
  let help = `Everything this companion has recorded: ${walks} ${walks === 1 ? "walk" : "walks"}, with any game or none.`;
  if (fromGame) {
    help = "Steps, distance and calories from the walks you did while playing this character, as the game counts them.";
    if (character.estimated)
      help += " Some are still the addon's estimate (marked ~) until the game has loaded the measured numbers.";
  }
  setText($("share-source-help"), help);
  for (const radio of document.querySelectorAll('input[name="share-units"]'))
    radio.checked = radio.value === (usesKm() ? "km" : "mi");

  for (const field of TEXT_FIELDS) {
    const input = inputs[field.key];
    const fallback =
      field.key === "subtitle" ? defaultSubtitle(character ? character.max_level : null) : DEFAULT_TEXTS[field.key];
    const value = options[field.key] === null ? fallback : options[field.key];
    // Never overwrite what is being typed.
    if (document.activeElement !== input && input.value !== value) input.value = value;
    setAttr(input, "placeholder", fallback);
  }

  const myRoad = chosenRoad();
  for (const tile of TILES) {
    const box = $(`share-tile-${tile.id}`);
    const possible = (!!character || !tile.character) && (!!myRoad || !tile.road);
    box.checked = options.tiles[tile.id] && possible;
    setDisabled(box, !possible);
  }
  setText($("share-road-help"), roadHelp(character, myRoad));
  setShown($("share-road-help"), !!character);
  $("share-name").checked = options.showName;
  setDisabled($("share-name"), !character);
  $("share-footnote").checked = options.showFootnote;
  for (const radio of document.querySelectorAll('input[name="share-theme"]'))
    radio.checked = radio.value === options.theme;
  for (const radio of document.querySelectorAll('input[name="share-size"]'))
    radio.checked = radio.value === options.size;

  const size = pictureSize();
  setText($("share-dim"), canDraw ? `${size.width} × ${size.height} px` : "");
  if (canDraw) queuePaint();
}

/** What the page says about the road: how fresh it is, or why there is none. */
function roadHelp(character, myRoad) {
  if (!character) return "";
  if (myRoad) {
    if (road.demo) return "My road: a made-up road for the demo character.";
    const when = new Date(myRoad.as_of * 1000).toLocaleString([], {
      day: "numeric",
      month: "short",
      hour: "2-digit",
      minute: "2-digit",
    });
    return `My road: as the game last saved it (${when}). The game writes it when you log out or type /reload - do that to bring the road on the picture up to date.`;
  }
  if (!road || roadFor !== character.id) return "My road: looking for it...";
  if (!road.game_found)
    return "My road: the game's folder was not found, so the road cannot be read. Install the addon, or say where it is, under Settings - Game addon.";
  return "My road: none found for this character yet. The addon records it as you play, and the game saves it when you log out or type /reload.";
}

async function fetchRoad(id) {
  if (roadFetching) return;
  roadFetching = true;
  roadAsked = id;
  try {
    road = await api(`/api/road?character=${encodeURIComponent(id)}`, { timeoutMs: 30000 });
    roadFor = id;
  } catch {
    // The picture is simply drawn without the road; it is asked for again soon.
    road = null;
    roadFor = "";
  } finally {
    roadAt = Date.now();
    roadFetching = false;
  }
  refresh();
}

function refreshRoad() {
  const character = chosen();
  // (A question that failed is not asked again at once, only when it is due.)
  if (character && (character.id !== roadAsked || Date.now() - roadAt > ROAD_EVERY_MS)) fetchRoad(character.id);
}

// ---- saving and copying ------------------------------------------------------------------------

function canCopy() {
  return !!(window.isSecureContext && navigator.clipboard && navigator.clipboard.write && window.ClipboardItem);
}

function pngBlob() {
  paint(); // make sure the canvas shows the latest choices
  return new Promise((resolve, reject) => {
    $("share-canvas").toBlob(
      (blob) => (blob ? resolve(blob) : reject(new Error("The picture could not be made."))),
      "image/png",
    );
  });
}

function announce(text) {
  setText($("share-status"), text);
}

async function download() {
  const blob = await pngBlob();
  const url = URL.createObjectURL(blob);
  const link = h("a", { href: url, download: fileName(Date.now() / 1000), hidden: true });
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 60000);
  // In Walka's own window a "Save as" box asks where to; a browser just saves.
  announce(window.pywebview ? "" : "Saved to your downloads folder.");
}

async function copy() {
  const put = (image) => navigator.clipboard.write([new ClipboardItem({ "image/png": image })]);
  try {
    try {
      // Handing over a promise keeps the click's permission while the PNG is encoded.
      await put(pngBlob());
    } catch {
      await put(await pngBlob()); // older browsers want the finished image
    }
  } catch {
    throw new Error("Copying the picture was not allowed here. Use Download PNG instead.");
  }
  announce("Copied. Paste it into a post or a chat.");
}

// ---- wiring ----------------------------------------------------------------------------------------

function radios(host, name, entries) {
  host.replaceChildren(
    ...entries.map((entry) =>
      h(
        "label",
        null,
        h("input", { type: "radio", name, value: entry.id }),
        h(
          "span",
          null,
          entry.swatch || null,
          h("strong", { text: entry.name }),
          entry.hint ? h("small", { text: entry.hint }) : null,
        ),
      ),
    ),
  );
}

function swatch(theme) {
  const chip = h("i", { class: "swatch", "aria-hidden": "true" });
  // Set from script: the page's content policy does not allow inline style attributes.
  chip.style.background = `linear-gradient(135deg, ${theme.bg[0]} 0 55%, ${theme.metal[1]} 55% 78%, ${theme.gem[1]} 78%)`;
  return chip;
}

function onRadio(name, apply) {
  for (const radio of document.querySelectorAll(`input[name="${name}"]`))
    radio.addEventListener("change", () => radio.checked && apply(radio.value));
}

export function initShare() {
  $("share-words").replaceChildren(
    ...TEXT_FIELDS.map((field) => {
      const id = `share-text-${field.key}`;
      const input = h("input", {
        id,
        type: "text",
        maxlength: TEXT_LIMITS[field.key],
        autocomplete: "off",
        spellcheck: true,
      });
      inputs[field.key] = input;
      input.addEventListener("input", () => change({ [field.key]: input.value }));
      return h("div", { class: "field" }, h("label", { for: id, text: field.label }), input);
    }),
  );
  $("share-words-reset").addEventListener("click", () => {
    change({ overline: null, title: null, subtitle: null, tagline: null });
    announce("The words are back to the suggestions.");
  });

  $("share-tiles").replaceChildren(
    ...TILES.map((tile) => {
      const box = h("input", { id: `share-tile-${tile.id}`, type: "checkbox" });
      box.addEventListener("change", () => {
        const tiles = { ...options.tiles, [tile.id]: box.checked };
        // (The road is no number, and may not be there to show.)
        if (!TILES.some((entry) => !entry.road && tiles[entry.id] && (chosen() || !entry.character))) {
          toast("Keep at least one number on the picture.", "info");
          refresh();
          return;
        }
        change({ tiles });
      });
      return h(
        "label",
        { class: "check" },
        box,
        h("strong", { text: tile.name }),
        tile.hint ? h("span", { class: "help", text: tile.hint }) : null,
      );
    }),
  );
  $("share-name").addEventListener("change", (event) => change({ showName: event.target.checked }));
  $("share-footnote").addEventListener("change", (event) => change({ showFootnote: event.target.checked }));

  radios(
    $("share-themes"),
    "share-theme",
    THEMES.map((theme) => ({ id: theme.id, name: theme.name, swatch: swatch(theme) })),
  );
  radios(
    $("share-sizes"),
    "share-size",
    SIZES.map((size) => ({ id: size.id, name: size.name, hint: `${size.width} × ${size.height}` })),
  );
  onRadio("share-theme", (theme) => change({ theme }));
  onRadio("share-size", (size) => change({ size }));
  onRadio("share-source", (source) => change({ source }));
  onRadio("share-units", (units) => change({ units }));
  $("share-character").addEventListener("change", (event) => change({ character: event.target.value }));

  $("share-download").addEventListener("click", (event) => run(event.currentTarget, download));
  $("share-copy").addEventListener("click", (event) => run(event.currentTarget, copy));
  setShown($("share-copy-note"), !canCopy());
  refresh();
}

async function fetchJourney(rev) {
  if (fetching) return;
  fetching = true;
  try {
    journey = await api("/api/journey");
    fetchedRev = rev;
    setShown($("share-error"), false);
  } catch (error) {
    setText($("share-error"), `Could not load your character's numbers. ${error.message}`);
    setShown($("share-error"), true);
    fetchedRev = null; // try again with the next snapshot
  } finally {
    fetching = false;
  }
  refresh();
}

/** Called with every state snapshot while the Share view is open. */
export function updateShare(s) {
  if (!s) return;
  snapshot = s;
  const rev = s.journey ? s.journey.rev : 0;
  if (rev !== fetchedRev) fetchJourney(rev);
  refreshRoad();
  refresh();
}

/** The dashboard's units changed: the distance on the picture follows unless it was chosen here. */
export function shareUnitsChanged() {
  refresh();
}
