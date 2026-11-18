// The words and numbers of the journey card.
//
// Pure functions, no DOM and no imports: share.js turns what the companion
// knows (a character's journey, the all-time totals, the choices made on the
// Share page) into the `card` object that card.js draws. Keeping this apart
// means the texts can be tested without a browser (tests/test_share_js.py).

export const M_PER_MILE = 1609.344;
export const M_PER_YARD = 0.9144;
export const KMH_PER_MPH = 1.609344;
export const LB_PER_KG = 2.2046226218;

/** Picture sizes, in pixels. The first is the default. */
export const SIZES = [
  { id: "portrait", name: "Portrait", width: 1080, height: 1350, hint: "4:5 - most feeds" },
  { id: "square", name: "Square", width: 1080, height: 1080, hint: "1:1" },
  { id: "story", name: "Story", width: 1080, height: 1920, hint: "9:16 - stories" },
  { id: "wide", name: "Wide", width: 1200, height: 675, hint: "16:9 - posts and chats" },
];

export const YARDS_PER_MILE = 1760;

/**
 * What can be shown. `character` tiles need numbers from the game; the
 * `road` tile also needs the road the game has saved (GET /api/road).
 */
export const TILES = [
  { id: "steps", name: "My steps", hint: "Your real-world steps and the distance you walked." },
  {
    id: "route",
    name: "My road",
    character: true,
    road: true,
    hint: "Your character's path as a line, the stretches you walked on the treadmill bright.",
  },
  { id: "level", name: "Character level", character: true },
  { id: "played", name: "Time played", character: true },
  { id: "charSteps", name: "Character steps", character: true, hint: "How far your character went on foot." },
  { id: "calories", name: "Calories" },
];

export const TEXT_LIMITS = { overline: 40, title: 36, subtitle: 48, tagline: 64 };

export const DEFAULT_TEXTS = {
  overline: "Walking while I play",
  title: "One step at a time",
  tagline: "My character runs. I walk.",
};

/** The subtitle names the game and the road ahead: "WoW Forever · Level 1-60". */
export function defaultSubtitle(maxLevel) {
  return Number.isFinite(maxLevel) && maxLevel > 1 ? `WoW Forever · Level 1-${maxLevel}` : "WoW Forever";
}

/** A text of null means "the default"; an empty string means "leave it out". */
export const DEFAULT_OPTIONS = {
  character: "", // id of the chosen character; "" = whoever was seen last
  source: "character", // "character" (numbers from the game) | "all" (the companion's own totals)
  overline: null,
  title: null,
  subtitle: null,
  tagline: null,
  tiles: { steps: true, route: true, level: true, played: true, charSteps: true, calories: true },
  units: null, // "mi" | "km"; null = follow the dashboard's settings
  theme: "",
  size: "portrait",
  // Privacy: both stay off until asked for.
  showName: false,
  showFootnote: false,
};

function cleanText(value, limit) {
  if (value === null || value === undefined) return null;
  if (typeof value !== "string") return null;
  // One line, no control characters: this is drawn, not interpreted.
  const flat = value.replace(/[\u0000-\u001f\u007f-\u009f\u2028\u2029]+/g, " ");
  return Array.from(flat).slice(0, limit).join("");
}

/** Whatever was stored (or nothing at all) as a complete, valid set of choices. */
export function normalizeOptions(raw, themeIds = []) {
  const input = raw && typeof raw === "object" ? raw : {};
  const options = { ...DEFAULT_OPTIONS, tiles: { ...DEFAULT_OPTIONS.tiles } };
  if (typeof input.character === "string" && /^[0-9a-f]{1,16}$/.test(input.character))
    options.character = input.character;
  if (input.source === "all") options.source = "all";
  for (const key of Object.keys(TEXT_LIMITS)) options[key] = cleanText(input[key], TEXT_LIMITS[key]);
  if (input.tiles && typeof input.tiles === "object")
    for (const tile of TILES)
      if (typeof input.tiles[tile.id] === "boolean") options.tiles[tile.id] = input.tiles[tile.id];
  // An empty picture helps nobody (and the road alone may not be there to show).
  if (!TILES.some((tile) => !tile.road && options.tiles[tile.id])) options.tiles.steps = true;
  if (input.units === "mi" || input.units === "km") options.units = input.units;
  options.theme = themeIds.includes(input.theme) ? input.theme : themeIds[0] || "";
  if (SIZES.some((size) => size.id === input.size)) options.size = input.size;
  options.showName = input.showName === true;
  options.showFootnote = input.showFootnote === true;
  return options;
}

// ---- numbers as text ---------------------------------------------------------------------
// The card is in English, so its numbers are too: 105,937 everywhere.

const grouped = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });
const oneDecimal = new Intl.NumberFormat("en-US", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const DASH = "\u2013"; // an en dash stands for "not known"

const known = (value) => typeof value === "number" && Number.isFinite(value) && value >= 0;

export function groupInt(value) {
  return known(value) ? grouped.format(Math.round(value)) : DASH;
}

/** "53.0", "143.2", and "1,204" once the decimals stop mattering. */
export function distanceNumber(value) {
  if (!known(value)) return DASH;
  return value >= 1000 ? grouped.format(Math.round(value)) : oneDecimal.format(value);
}

/** Calories are an estimate: past a thousand, the last digit would be a pretence. */
export function calorieNumber(kcal) {
  if (!known(kcal)) return DASH;
  return grouped.format(kcal >= 1000 ? Math.round(kcal / 10) * 10 : Math.round(kcal));
}

/** Seconds of /played as [hours, minutes], or null when not known. */
export function playedParts(seconds) {
  if (!known(seconds) || seconds <= 0) return null;
  const minutes = Math.floor(seconds / 60);
  return [Math.floor(minutes / 60), minutes % 60];
}

export function playedText(seconds) {
  const parts = playedParts(seconds);
  if (!parts) return DASH;
  const [hours, minutes] = parts;
  return hours ? `${grouped.format(hours)}h ${String(minutes).padStart(2, "0")}m` : `${minutes}m`;
}

export function dateText(epochSeconds) {
  const date = new Date(epochSeconds * 1000);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

// ---- the tiles ---------------------------------------------------------------------------

/** "~" marks an estimate - but nothing needs estimating about zero. */
const about = (value) => (value > 0 ? "~" : "");
/** The same, in words, for the description read out by screen readers. */
const aboutWord = (value) => (value > 0 ? "about " : "");

function realNumbers(character, totals, source) {
  if (source === "character" && character)
    return {
      steps: character.steps,
      distance_m: character.distance_m,
      kcal: character.kcal,
      seconds: character.seconds,
      estimated: !!character.estimated,
    };
  const all = totals || {};
  return { steps: all.steps, distance_m: all.distance_m, kcal: all.kcal, seconds: all.seconds, estimated: false };
}

function stepsTile(real, km) {
  const mark = (value) => (real.estimated ? about(value) : "");
  const distance = known(real.distance_m) ? real.distance_m / (km ? 1000 : M_PER_MILE) : null;
  const length = distance === null ? "" : `${distanceNumber(distance)} ${km ? "km" : "miles"} walked`;
  const say = (value) => (real.estimated ? aboutWord(value) : "");
  return {
    id: "steps",
    hero: true,
    label: ["My steps", "Real-world walking"],
    value: [{ text: known(real.steps) ? mark(real.steps) + groupInt(real.steps) : DASH }],
    sub: length && mark(distance) + length,
    plain: `My steps: ${say(real.steps)}${groupInt(real.steps)}${length ? ` (${say(distance)}${length})` : ""}`,
  };
}

function levelTile(character, showName) {
  const { level, max_level: cap } = character;
  const tile = { id: "level", label: ["Character level"], value: [{ text: DASH }], sub: "Not known yet" };
  tile.plain = "Character level: not known";
  if (!known(level) || level < 1) return tile;
  tile.value = [{ text: String(level) }];
  tile.sub = "";
  tile.plain = `Character level ${level}`;
  if (known(cap) && cap >= level) {
    tile.value.push({ text: `/ ${cap}`, scale: 0.5, tone: "sub", gap: 0.16 });
    tile.progress = level / cap;
    const left = cap - level;
    tile.sub = left === 0 ? "Level cap reached" : `${left} ${left === 1 ? "level" : "levels"} to go`;
    tile.plain += ` of ${cap}`;
  }
  if (showName && character.name) tile.sub = character.name;
  return tile;
}

function playedTile(character) {
  const parts = playedParts(character.played_s);
  const tile = { id: "played", label: ["Time played"], sub: "In-game time" };
  tile.plain = `Time played: ${parts ? playedText(character.played_s) : "not known yet"}`;
  if (!parts) return { ...tile, value: [{ text: DASH }], sub: "Not known yet" };
  const [hours, minutes] = parts;
  const unit = { scale: 0.52, tone: "sub", gap: 0.03 };
  tile.value = hours
    ? [
        { text: grouped.format(hours) },
        { text: "h", ...unit },
        { text: String(minutes).padStart(2, "0"), gap: 0.2 },
        { text: "m", ...unit },
      ]
    : [{ text: String(minutes) }, { text: "m", ...unit }];
  return tile;
}

function charStepsTile(character, km) {
  const steps = character.character_steps;
  const tile = { id: "charSteps", label: ["Character steps"], value: [{ text: DASH }], sub: "Not known yet" };
  tile.plain = "Character steps: not known";
  if (!known(steps)) return tile;
  // Steps are a convention (2,000 to an in-game mile), hence "about".
  tile.value = [{ text: about(steps) + groupInt(steps) }];
  // The in-game window words it the same way: miles, or km for those who chose km.
  const yards = character.foot_yd;
  tile.sub =
    km && known(yards)
      ? `${distanceNumber((yards * M_PER_YARD) / 1000)} in-game km`
      : `${distanceNumber(character.character_miles)} in-game miles`;
  tile.note = known(character.since) ? `On foot since ${dateText(character.since)}` : "Travelled on foot";
  tile.plain = `Character steps: ${aboutWord(steps)}${groupInt(steps)} (${tile.sub})`;
  return tile;
}

function footnote(real, km, weightKg) {
  const parts = [];
  if (known(weightKg) && weightKg > 0)
    parts.push(km ? `${Math.round(weightKg)} kg` : `${Math.round(weightKg * LB_PER_KG)} lb`);
  if (known(real.distance_m) && known(real.seconds) && real.seconds >= 60 && real.distance_m > 0) {
    const kmh = real.distance_m / 1000 / (real.seconds / 3600);
    parts.push(km ? `${oneDecimal.format(kmh)} km/h average` : `${oneDecimal.format(kmh / KMH_PER_MPH)} mph average`);
  }
  return parts.join(" · ");
}

function caloriesTile(real, km, weightKg, showFootnote) {
  const tile = { id: "calories", label: ["Calories burned"], value: [{ text: DASH }], sub: "kcal · approximate" };
  tile.plain = `Calories: ${known(real.kcal) ? aboutWord(real.kcal) + calorieNumber(real.kcal) : "not known"}`;
  if (known(real.kcal)) tile.value = [{ text: about(real.kcal) + calorieNumber(real.kcal) }];
  const note = showFootnote ? footnote(real, km, weightKg) : "";
  if (note) tile.note = note;
  return tile;
}

/** Does `road` (an answer of /api/road) hold a road of this character? */
export function hasRoad(road, character) {
  return !!(
    road &&
    character &&
    road.id === character.id &&
    Array.isArray(road.continents) &&
    road.continents.some((continent) => Array.isArray(continent.paths) && continent.paths.length > 0)
  );
}

/**
 * A continent's map (an entry of lands.js) if it is whole enough to draw:
 * corners, an outline of them and triangles between them.
 */
function usableLand(land) {
  if (!land || !Array.isArray(land.points) || !Array.isArray(land.coast) || !Array.isArray(land.faces)) return null;
  const corners = land.points.length / 2;
  const corner = (index) => Number.isInteger(index) && index >= 0 && index < corners;
  const bounds = land.bounds;
  if (!Array.isArray(bounds) || bounds.length !== 4 || !bounds.every(Number.isFinite)) return null;
  if (!(bounds[2] > bounds[0] && bounds[3] > bounds[1])) return null;
  if (corners < 3 || !Number.isInteger(corners) || !land.points.every(Number.isFinite)) return null;
  if (!land.coast.length || !land.coast.every((ring) => Array.isArray(ring) && ring.length >= 3 && ring.every(corner)))
    return null;
  if (land.faces.length % 4 !== 0) return null;
  for (let i = 0; i < land.faces.length; i += 4) {
    if (!corner(land.faces[i]) || !corner(land.faces[i + 1]) || !corner(land.faces[i + 2])) return null;
    if (!Number.isFinite(land.faces[i + 3])) return null;
  }
  return { bounds, points: land.points, coast: land.coast, faces: land.faces };
}

/**
 * The road as a tile: up to two continents (the ones with the most road),
 * placed west to east as on the world map. `lands`: the continents' maps by
 * id (lands.js), drawn behind the road where there is one.
 */
function routeTile(road, km, lands, character) {
  const shown = road.continents.filter((continent) => continent.paths.length > 0).slice(0, 2);
  // Kalimdor (continent 1) lies west of the Eastern Kingdoms (0).
  if (shown.length === 2 && shown[1].id === 1) shown.reverse();
  const names = shown.map((continent) => continent.name).filter(Boolean);
  const yards = known(road.yards) ? road.yards : 0;
  const distance = km ? (yards * M_PER_YARD) / 1000 : yards / YARDS_PER_MILE;
  // (Said to be the road's, so that it is not taken for the distance on
  // foot in the tile beside it. A road too short to measure has none.)
  const measured = distanceNumber(distance) !== "0.0";
  const length = measured ? `${distanceNumber(distance)} in-game ${km ? "km" : "miles"} of road` : "My road starts here";
  const spot = (place) =>
    place && Number.isFinite(place.c) && Number.isFinite(place.x) && Number.isFinite(place.y)
      ? { c: place.c, x: place.x, y: place.y }
      : null;
  const level = character && known(character.level) && character.level > 0 ? character.level : road.level;
  // (No percentage of a road too short to show as more than "0.0".)
  const share = yards >= 100 && known(road.belt_yards) ? Math.min(100, Math.round((road.belt_yards / yards) * 100)) : 0;
  return {
    id: "route",
    wide: true,
    label: ["My road"],
    route: {
      continents: shown.map((continent) => ({
        id: continent.id,
        name: continent.name || "",
        bounds: continent.bounds,
        paths: continent.paths,
        land: usableLand(lands ? lands[continent.id] : null),
      })),
      start: spot(road.start),
      end: spot(road.end),
      // The character's level, written beside the gem.
      level: known(level) && level > 0 ? Math.round(level) : null,
      milestones: (Array.isArray(road.milestones) ? road.milestones : []).filter(
        (mark) => spot(mark) && Number.isFinite(mark.level),
      ),
      // Passages from one continent to another, drawn as arcs.
      crossings: (Array.isArray(road.crossings) ? road.crossings : [])
        .filter((crossing) => crossing && spot(crossing.from) && spot(crossing.to))
        .slice(0, 3),
    },
    sub: length,
    note: share > 0 ? `${share}% of it walked for real` : "",
    plain: measured
      ? `My road${names.length ? ` on ${names.join(" and ")}` : ""}: ${distanceNumber(distance)} in-game ${
          km ? "km" : "miles"
        }${share > 0 ? `, ${share}% of it walked on the treadmill` : ""}`
      : `My road${names.length ? ` on ${names.join(" and ")}` : ""} starts here`,
  };
}

/**
 * Everything card.js needs to draw one picture.
 *
 * character: an entry of /api/journey's `characters`, or null (no game numbers);
 * totals: the state snapshot's `totals.all`; units: the dashboard's "mph" | "kmh";
 * weightKg: from the body profile (only used for the opt-in footnote);
 * road: the `road` of /api/road for that character, or null;
 * lands: lands.js' LANDS (the continents' maps), or null for a road on an
 *   empty panel;
 * now: epoch seconds.
 */
export function buildCard({
  character = null,
  totals = null,
  options,
  units = "mph",
  weightKg = null,
  road = null,
  lands = null,
  now = 0,
}) {
  const source = character && options.source !== "all" ? "character" : "all";
  const km = (options.units || (units === "kmh" ? "km" : "mi")) === "km";
  const real = realNumbers(character, totals, source);
  const text = (key, fallback) => (options[key] === null ? fallback : options[key]).trim();

  const roadKnown = hasRoad(road, character);
  const make = {
    steps: () => stepsTile(real, km),
    route: () => routeTile(road, km, lands, character),
    level: () => levelTile(character, options.showName),
    played: () => playedTile(character),
    charSteps: () => charStepsTile(character, km),
    calories: () => caloriesTile(real, km, weightKg, options.showFootnote),
  };
  const tiles = TILES.filter(
    (tile) => options.tiles[tile.id] && (character || !tile.character) && (roadKnown || !tile.road),
  ).map((tile) => make[tile.id]());

  const through = source === "character" ? character.seen_at : now;
  const title = text("title", DEFAULT_TEXTS.title);
  return {
    overline: text("overline", DEFAULT_TEXTS.overline),
    title,
    subtitle: text("subtitle", defaultSubtitle(character ? character.max_level : null)),
    tagline: text("tagline", DEFAULT_TEXTS.tagline),
    footer: dateText(through) ? `Totals through ${dateText(through)}` : "",
    credit: "Made with Walka",
    tiles,
    source,
    alt: [`Walka journey picture${title ? `: ${title}` : ""}`, ...tiles.map((tile) => tile.plain)].join(". ") + ".",
  };
}

/** The file name of a downloaded picture. Never the character's name: files get passed around. */
export function fileName(now) {
  const date = new Date(now * 1000);
  const stamp = Number.isNaN(date.getTime())
    ? "card"
    : [date.getFullYear(), date.getMonth() + 1, date.getDate()].map((part) => String(part).padStart(2, "0")).join("-");
  return `walka-journey-${stamp}.png`;
}
