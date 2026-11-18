// Runs the Share page's two DOM-free modules under Node, for test_share_page.py.
//
//   node share_harness.mjs <cardmodel.js> <card.js>   < requests.json   > answers.json
//
// The request is a JSON list of {op, ...}; the answer is the list of results.
// card.js draws on a canvas 2D context; here it gets a stand-in that records
// what is drawn and measures text by a simple rule (every character is
// `charWidth` em wide), so the layout can be checked without a browser.

import { readFileSync } from "node:fs";

// The static files are plain browser modules (no package.json says so), so
// they are loaded as data: URLs, which Node always treats as modules.
const load = (path) => import(`data:text/javascript;base64,${readFileSync(path).toString("base64")}`);
const model = await load(process.argv[2]);
const card = await load(process.argv[3]);
// The continents' maps (lands.js), handed to the model as the page does.
const lands = process.argv[4] ? (await load(process.argv[4])).LANDS : null;
const themeIds = card.THEMES.map((theme) => theme.id);

const DRAWING = new Set([
  "save",
  "restore",
  "beginPath",
  "closePath",
  "moveTo",
  "lineTo",
  "arc",
  "arcTo",
  "fill",
  "stroke",
  "clip",
  "fillRect",
  "translate",
]);

/** A stand-in 2D context: records calls, refuses anything that is not plain drawing. */
function fakeContext(charWidth) {
  const calls = [];
  const state = { font: "10px sans-serif" };
  const fontSize = () => Number(/([\d.]+)px/.exec(state.font)[1]);
  const gradient = () => ({ addColorStop() {} });
  const api = {
    measureText(text) {
      const size = fontSize();
      return {
        width: Array.from(String(text)).length * size * charWidth,
        actualBoundingBoxAscent: size * 0.74,
        actualBoundingBoxDescent: size * 0.2,
      };
    },
    fillText(text, x, y) {
      calls.push(["fillText", text, Math.round(x * 100) / 100, Math.round(y * 100) / 100, state.font]);
    },
    createLinearGradient: gradient,
    createRadialGradient: gradient,
    setLineDash(segments) {
      calls.push(["setLineDash", ...segments.map((value) => Math.round(value * 100) / 100)]);
    },
  };
  const ctx = new Proxy(state, {
    get(target, name) {
      if (name in api) return api[name];
      if (DRAWING.has(name)) return (...args) => calls.push([name, ...args.map((arg) => Math.round(arg * 100) / 100)]);
      if (name in target) return target[name];
      throw new Error(`card.js used canvas API "${String(name)}", which the picture must not need`);
    },
    set(target, name, value) {
      target[name] = value;
      return true;
    },
  });
  return { ctx, calls };
}

function build(request) {
  const options = model.normalizeOptions(request.options, themeIds);
  return model.buildCard({
    character: request.character ?? null,
    totals: request.totals ?? null,
    options,
    units: request.units ?? "mph",
    weightKg: request.weightKg ?? null,
    road: request.road ?? null,
    // (`lands: false` in a request: a road on an empty panel; an object: those maps.)
    lands: request.lands === undefined ? lands : request.lands || null,
    now: request.now ?? 0,
  });
}

const ops = {
  about: () => ({
    defaults: model.DEFAULT_OPTIONS,
    normalized: model.normalizeOptions(null, themeIds),
    sizes: model.SIZES,
    tiles: model.TILES,
    limits: model.TEXT_LIMITS,
    texts: model.DEFAULT_TEXTS,
    themes: card.THEMES,
  }),
  normalize: (request) => model.normalizeOptions(request.raw, request.themes ?? themeIds),
  build,
  subtitle: (request) => model.defaultSubtitle(request.maxLevel),
  fileName: (request) => model.fileName(request.now),
  draw(request) {
    const size = model.SIZES.find((entry) => entry.id === request.size);
    const { ctx, calls } = fakeContext(request.charWidth ?? 0.6);
    const report = card.drawCard(ctx, size.width, size.height, build(request), request.theme);
    return { report, texts: calls.filter((call) => call[0] === "fillText").length, calls: request.calls ? calls : undefined };
  },
};

const requests = JSON.parse(readFileSync(0, "utf-8"));
process.stdout.write(JSON.stringify(requests.map((request) => ops[request.op](request))));
