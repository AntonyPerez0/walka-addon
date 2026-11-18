// The journey card: a picture of a character's numbers, drawn on a canvas.
//
// Everything is drawn here, by hand: gradients, frames, gems and type. No
// image files, no web fonts, nothing fetched - the picture is Walka's own
// design and works offline. `drawCard` takes a 2D context and the `card`
// object from cardmodel.js; it has no imports and touches no DOM, so a test
// can run it against a recording context (tests/test_share_js.py).
//
// One rule holds everywhere: text never leaves its box. Every line goes
// through `fitLine` / `fitRuns`, which shrink it until it fits and, for
// free text, cut it with an ellipsis as the last resort. `drawCard` returns
// where each text ended up, so that this can be checked.

// Fonts that ship with Windows first (where most people run the companion),
// then the Mac's and the usual ones elsewhere. Sizes are measured, never assumed, so a fallback
// only changes the look.
const SANS =
  'Bahnschrift, "DIN Alternate", "Segoe UI Variable Display", "Segoe UI", Inter, "Helvetica Neue", Roboto, "Liberation Sans", Arial, sans-serif';
const SERIF =
  '"Palatino Linotype", Palatino, "Book Antiqua", "Iowan Old Style", "URW Palladio L", P052, Georgia, Caladea, "Bitstream Charter", "DejaVu Serif", serif';

const LABEL = { family: SANS, weight: 600 };
const NUMBER = { family: SANS, weight: 700 };
const TITLE = { family: SERIF, weight: 700 };
const TAGLINE = { family: SERIF, weight: 400, italic: true };

/**
 * Themes: a palette and a frame shape each.
 * bg: top, middle, bottom of the backdrop; glows: soft lights on it;
 * metal: the frame, light to dark and back; ink: "r,g,b" of the fine lines.
 */
export const THEMES = [
  {
    id: "sapphire",
    name: "Sapphire",
    shape: "cut",
    bg: ["#14205a", "#0a1036", "#04051a"],
    glows: [
      { x: 0.5, y: 0.1, r: 0.62, color: "70,120,255", alpha: 0.42 },
      { x: 0.02, y: 0.62, r: 0.5, color: "132,70,235", alpha: 0.3 },
      { x: 1.0, y: 0.92, r: 0.52, color: "30,160,255", alpha: 0.22 },
    ],
    ink: "214,228,255",
    metal: ["#fff4c7", "#f1c75a", "#a8721b", "#f8de8a", "#7a4d0f"],
    metalEdge: "#170e03",
    panel: ["rgba(24,34,92,0.9)", "rgba(8,10,36,0.95)"],
    panelGlow: "96,150,255",
    label: "#7ed4ff",
    value: "#ffffff",
    sub: "#f6cf6a",
    muted: "#a3b0e2",
    title: ["#ffffff", "#ffe6a3"],
    gem: ["#eaffff", "#69d2ff", "#1e73e8", "#0b2f8f"],
    glow: "90,165,255",
    shade: "0,0,0",
    // The road's map: the sea, the land, its coast, and a road not walked for real.
    map: { sea: "rgba(2,5,26,0.5)", land: "#3b4a86", coast: "rgba(176,202,255,0.8)", dim: "rgba(126,212,255,0.9)" },
  },
  {
    id: "ember",
    name: "Ember",
    shape: "notch",
    bg: ["#451912", "#210b0c", "#090405"],
    glows: [
      { x: 0.5, y: 0.08, r: 0.6, color: "255,120,40", alpha: 0.36 },
      { x: 0.0, y: 0.6, r: 0.5, color: "210,36,56", alpha: 0.28 },
      { x: 1.0, y: 0.95, r: 0.5, color: "255,170,60", alpha: 0.2 },
    ],
    ink: "255,222,196",
    metal: ["#ffe6cc", "#e59a5e", "#8f4722", "#f4ba86", "#5a2910"],
    metalEdge: "#160703",
    panel: ["rgba(66,25,20,0.9)", "rgba(20,8,8,0.95)"],
    panelGlow: "255,140,60",
    label: "#ffb783",
    value: "#fff8f1",
    sub: "#ffd680",
    muted: "#d3a996",
    title: ["#fff6ea", "#ffc98a"],
    gem: ["#fff3da", "#ffb04f", "#e4571d", "#7c1d0a"],
    glow: "255,130,50",
    shade: "0,0,0",
    // The road's map: the sea, the land, its coast, and a road not walked for real.
    map: { sea: "rgba(10,3,3,0.5)", land: "#70402f", coast: "rgba(255,206,170,0.75)", dim: "rgba(255,183,131,0.9)" },
  },
  {
    id: "wildwood",
    name: "Wildwood",
    shape: "scallop",
    bg: ["#11413a", "#082520", "#020c0a"],
    glows: [
      { x: 0.5, y: 0.08, r: 0.6, color: "60,225,165", alpha: 0.3 },
      { x: 0.0, y: 0.62, r: 0.5, color: "24,150,175", alpha: 0.28 },
      { x: 1.0, y: 0.95, r: 0.5, color: "196,214,96", alpha: 0.16 },
    ],
    ink: "216,255,236",
    metal: ["#f8f1c8", "#cfb766", "#7f6c2b", "#e8d998", "#4d4119"],
    metalEdge: "#0d0b03",
    panel: ["rgba(15,58,51,0.9)", "rgba(4,20,18,0.95)"],
    panelGlow: "80,230,170",
    label: "#8af2c8",
    value: "#f8fffa",
    sub: "#ecd98e",
    muted: "#9cc0b1",
    title: ["#ffffff", "#eaf4bb"],
    gem: ["#ecfff5", "#64e8a8", "#17a26b", "#064e3b"],
    glow: "80,230,170",
    shade: "0,0,0",
    // The road's map: the sea, the land, its coast, and a road not walked for real.
    map: { sea: "rgba(1,12,10,0.5)", land: "#2c6553", coast: "rgba(176,244,214,0.75)", dim: "rgba(138,242,200,0.88)" },
  },
  {
    id: "frost",
    name: "Frost",
    shape: "crest",
    bg: ["#28405f", "#131d31", "#070b15"],
    glows: [
      { x: 0.5, y: 0.08, r: 0.62, color: "150,210,255", alpha: 0.34 },
      { x: 0.0, y: 0.6, r: 0.5, color: "124,134,226", alpha: 0.22 },
      { x: 1.0, y: 0.95, r: 0.5, color: "90,220,240", alpha: 0.18 },
    ],
    ink: "236,246,255",
    metal: ["#ffffff", "#cbd5e2", "#707e94", "#eaf0f7", "#465268"],
    metalEdge: "#05070c",
    panel: ["rgba(34,50,80,0.9)", "rgba(9,14,27,0.95)"],
    panelGlow: "170,215,255",
    label: "#aadfff",
    value: "#ffffff",
    sub: "#dbe7f6",
    muted: "#9dadc6",
    title: ["#ffffff", "#cfe6ff"],
    gem: ["#ffffff", "#c2ecff", "#5cabe8", "#2b509c"],
    glow: "150,205,255",
    shade: "0,0,0",
    // The road's map: the sea, the land, its coast, and a road not walked for real.
    map: { sea: "rgba(4,9,20,0.5)", land: "#465a7a", coast: "rgba(206,228,255,0.78)", dim: "rgba(170,223,255,0.88)" },
  },
  {
    id: "parchment",
    name: "Parchment",
    shape: "round",
    light: true,
    bg: ["#f8efd8", "#efe1bf", "#dfcb9d"],
    glows: [
      { x: 0.5, y: 0.1, r: 0.62, color: "255,255,244", alpha: 0.75 },
      { x: 0.0, y: 0.65, r: 0.45, color: "176,128,60", alpha: 0.16 },
      { x: 1.0, y: 0.95, r: 0.5, color: "150,96,40", alpha: 0.18 },
    ],
    ink: "104,72,34",
    metal: ["#d9b56a", "#96702c", "#5d4014", "#b8904a", "#3e290a"],
    metalEdge: "#2c1d08",
    panel: ["rgba(255,251,238,0.88)", "rgba(246,235,207,0.9)"],
    panelGlow: "255,255,255",
    label: "#8f2a2a",
    value: "#2a1c0f",
    sub: "#85560f",
    muted: "#74634b",
    title: ["#3a2412", "#6e2a1b"],
    gem: ["#ffeae2", "#e36d5e", "#a5262b", "#5b1019"],
    glow: "255,255,255",
    shade: "96,62,20",
    // The road's map: the sea, the land, its coast, and a road not walked for real.
    map: { sea: "rgba(140,104,52,0.13)", land: "#d0bc9b", coast: "rgba(96,66,30,0.75)", dim: "rgba(122,90,56,0.85)" },
  },
];

// ---- small helpers ----------------------------------------------------------------------

const clamp = (value, lo, hi) => Math.min(hi, Math.max(lo, value));
const rgba = (rgb, alpha) => `rgba(${rgb},${alpha})`;

/** A small repeatable random source: the same card always gets the same stars. */
function randomSource(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function setFont(ctx, spec, size) {
  ctx.font = `${spec.italic ? "italic " : ""}${spec.weight} ${size.toFixed(2)}px ${spec.family}`;
}

// Letter-spacing is done by hand (one glyph at a time) so that every browser
// spaces and measures alike. That only works for scripts whose letters stand
// alone; anything else is drawn as one run, unspaced.
const STANDS_ALONE = /^[\u0020-\u024f\u0370-\u03ff\u0400-\u052f\u2010-\u2027\u2030-\u205e]*$/;

function glyphs(text) {
  if (typeof Intl !== "undefined" && Intl.Segmenter)
    return Array.from(new Intl.Segmenter().segment(text), (part) => part.segment);
  return Array.from(text);
}

function textWidth(ctx, text, spec, size, tracking) {
  setFont(ctx, spec, size);
  if (!tracking || !STANDS_ALONE.test(text)) return ctx.measureText(text).width;
  const parts = glyphs(text);
  let width = tracking * size * Math.max(0, parts.length - 1);
  for (const part of parts) width += ctx.measureText(part).width;
  return width;
}

/** Draw `text` with its left edge at x; returns its width. */
function drawText(ctx, text, spec, size, tracking, x, y) {
  setFont(ctx, spec, size);
  ctx.textAlign = "left";
  ctx.textBaseline = "alphabetic";
  if (!tracking || !STANDS_ALONE.test(text)) {
    ctx.fillText(text, x, y);
    return ctx.measureText(text).width;
  }
  let pen = x;
  const parts = glyphs(text);
  parts.forEach((part, i) => {
    ctx.fillText(part, pen, y);
    pen += ctx.measureText(part).width + (i < parts.length - 1 ? tracking * size : 0);
  });
  return pen - x;
}

/**
 * The size at which `text` fits `width`: `size` if it can, smaller down to
 * `min` if it must, and at `min` the text itself is cut ("Long titl...").
 */
function fitLine(ctx, text, spec, { size, min, width, tracking = 0 }) {
  let fitted = size;
  let measured = textWidth(ctx, text, spec, fitted, tracking);
  while (measured > width && fitted > min) {
    fitted = Math.max(min, Math.min(fitted * 0.97, (fitted * width) / measured));
    measured = textWidth(ctx, text, spec, fitted, tracking);
  }
  let shown = text;
  if (measured > width) {
    const parts = glyphs(text);
    while (parts.length > 1 && measured > width) {
      parts.pop();
      shown = `${parts.join("").trimEnd()}\u2026`;
      measured = textWidth(ctx, shown, spec, fitted, tracking);
    }
  }
  return { text: shown, size: fitted, width: measured };
}

/** How tall `text` really is around its baseline (a guess where the browser gives none). */
function extent(ctx, text, spec, size) {
  setFont(ctx, spec, size);
  const m = ctx.measureText(text);
  return {
    ascent: Number.isFinite(m.actualBoundingBoxAscent) ? m.actualBoundingBoxAscent : size * 0.74,
    descent: Number.isFinite(m.actualBoundingBoxDescent) ? m.actualBoundingBoxDescent : size * 0.2,
  };
}

function note(report, id, text, left, baseline, width, size, ext, box) {
  report.texts.push({
    id,
    text,
    size,
    left,
    right: left + width,
    top: baseline - ext.ascent,
    bottom: baseline + ext.descent,
    box,
  });
}

/** One centred line, fitted to `width`. `paint` sets the fill (colour, shadow) just before drawing. */
function line(ctx, report, id, text, spec, at, paint, box) {
  if (!text) return null;
  const fit = fitLine(ctx, text, spec, at);
  const left = at.x - fit.width / 2;
  ctx.save();
  paint(ctx, fit, left);
  drawText(ctx, fit.text, spec, fit.size, at.tracking || 0, left, at.y);
  ctx.restore();
  note(report, id, fit.text, left, at.y, fit.width, fit.size, extent(ctx, fit.text, spec, fit.size), box);
  return { ...fit, left };
}

// ---- shapes ---------------------------------------------------------------------------------

/** The outline of a panel, `inset` pixels inside its box. c: corner size. */
function outline(ctx, shape, x, y, w, h, c, inset = 0) {
  const X = x + inset;
  const Y = y + inset;
  const R = x + w - inset;
  const B = y + h - inset;
  ctx.beginPath();
  if (shape === "round") {
    const r = Math.max(1, c - inset);
    ctx.moveTo(X + r, Y);
    ctx.arcTo(R, Y, R, B, r);
    ctx.arcTo(R, B, X, B, r);
    ctx.arcTo(X, B, X, Y, r);
    ctx.arcTo(X, Y, R, Y, r);
  } else if (shape === "notch") {
    ctx.moveTo(X + c, Y);
    ctx.lineTo(R - c, Y);
    ctx.lineTo(R - c, Y + c);
    ctx.lineTo(R, Y + c);
    ctx.lineTo(R, B - c);
    ctx.lineTo(R - c, B - c);
    ctx.lineTo(R - c, B);
    ctx.lineTo(X + c, B);
    ctx.lineTo(X + c, B - c);
    ctx.lineTo(X, B - c);
    ctx.lineTo(X, Y + c);
    ctx.lineTo(X + c, Y + c);
  } else if (shape === "scallop") {
    // Corners bitten out by a circle around each corner of the box.
    const r = c + inset;
    const a = Math.asin(inset / r);
    const turn = Math.PI / 2;
    ctx.arc(x, y, r, turn - a, a, true);
    ctx.arc(x + w, y, r, Math.PI - a, turn + a, true);
    ctx.arc(x + w, y + h, r, 3 * turn - a, Math.PI + a, true);
    ctx.arc(x, y + h, r, 4 * turn - a, 3 * turn + a, true);
  } else {
    // "cut": corners cut off at 45 degrees; "crest" adds a nick in the middle
    // of the top and bottom edges.
    const k = Math.max(1, c - 0.414 * inset);
    const mid = (X + R) / 2;
    const nick = shape === "crest" ? c * 0.62 : 0;
    ctx.moveTo(X + k, Y);
    if (nick) {
      ctx.lineTo(mid - nick * 1.5, Y);
      ctx.lineTo(mid, Y + nick);
      ctx.lineTo(mid + nick * 1.5, Y);
    }
    ctx.lineTo(R - k, Y);
    ctx.lineTo(R, Y + k);
    ctx.lineTo(R, B - k);
    ctx.lineTo(R - k, B);
    if (nick) {
      ctx.lineTo(mid + nick * 1.5, B);
      ctx.lineTo(mid, B - nick);
      ctx.lineTo(mid - nick * 1.5, B);
    }
    ctx.lineTo(X + k, B);
    ctx.lineTo(X, B - k);
    ctx.lineTo(X, Y + k);
  }
  ctx.closePath();
}

function diamond(ctx, cx, cy, halfWidth, halfHeight) {
  ctx.beginPath();
  ctx.moveTo(cx, cy - halfHeight);
  ctx.lineTo(cx + halfWidth, cy);
  ctx.lineTo(cx, cy + halfHeight);
  ctx.lineTo(cx - halfWidth, cy);
  ctx.closePath();
}

function metal(ctx, theme, x0, y0, x1, y1) {
  const gradient = ctx.createLinearGradient(x0, y0, x1, y1);
  theme.metal.forEach((color, i) => gradient.addColorStop(i / (theme.metal.length - 1), color));
  return gradient;
}

/** A cut gem in a metal setting. r: half its height. */
function gem(ctx, theme, cx, cy, r) {
  const hw = r * 0.74;
  ctx.save();
  ctx.shadowColor = rgba(theme.glow, theme.light ? 0.0 : 0.85);
  ctx.shadowBlur = r * 1.8;
  diamond(ctx, cx, cy, hw + r * 0.3, r * 1.3);
  ctx.fillStyle = theme.metalEdge;
  ctx.fill();
  ctx.shadowBlur = 0;
  diamond(ctx, cx, cy, hw + r * 0.22, r * 1.22);
  ctx.fillStyle = metal(ctx, theme, cx - hw, cy - r, cx + hw, cy + r);
  ctx.fill();
  // Four facets around the centre, lit from the top right.
  const facets = [
    [-1, -1, theme.gem[1]],
    [1, -1, theme.gem[0]],
    [-1, 1, theme.gem[3]],
    [1, 1, theme.gem[2]],
  ];
  for (const [sx, sy, color] of facets) {
    ctx.beginPath();
    ctx.moveTo(cx, cy);
    ctx.lineTo(cx + sx * hw, cy);
    ctx.lineTo(cx, cy + sy * r);
    ctx.closePath();
    ctx.fillStyle = color;
    ctx.fill();
  }
  diamond(ctx, cx, cy, hw * 0.42, r * 0.42);
  ctx.fillStyle = "rgba(255,255,255,0.3)";
  ctx.fill();
  ctx.restore();
}

/** A fine line that fades out towards `x1`, with a small stud part of the way along. */
function taper(ctx, theme, x0, x1, y, weight) {
  const gradient = ctx.createLinearGradient(x0, y, x1, y);
  gradient.addColorStop(0, theme.metal[1]);
  gradient.addColorStop(1, rgba(theme.ink, 0));
  ctx.save();
  ctx.strokeStyle = gradient;
  ctx.lineWidth = weight;
  ctx.beginPath();
  ctx.moveTo(x0, y);
  ctx.lineTo(x1, y);
  ctx.stroke();
  const stud = x0 + (x1 - x0) * 0.34;
  diamond(ctx, stud, y, weight * 2.4, weight * 2.4);
  ctx.fillStyle = theme.metal[1];
  ctx.fill();
  ctx.restore();
}

/** line - gem - line, with the gem on a small four-point star: the mark at the head of the card. */
function flourish(ctx, theme, cx, y, reach, r) {
  const weight = Math.max(1, r * 0.14);
  taper(ctx, theme, cx - r * 2.6, cx - reach, y, weight);
  taper(ctx, theme, cx + r * 2.6, cx + reach, y, weight);
  ctx.save();
  ctx.fillStyle = metal(ctx, theme, cx - r * 2, y - r * 2, cx + r * 2, y + r * 2);
  diamond(ctx, cx, y, r * 2.15, r * 0.3);
  ctx.fill();
  diamond(ctx, cx, y, r * 0.3, r * 2.15);
  ctx.fill();
  ctx.restore();
  gem(ctx, theme, cx, y, r);
}

// ---- the backdrop -----------------------------------------------------------------------------

function backdrop(ctx, W, H, theme, focus) {
  const base = ctx.createLinearGradient(0, 0, 0, H);
  base.addColorStop(0, theme.bg[0]);
  base.addColorStop(0.5, theme.bg[1]);
  base.addColorStop(1, theme.bg[2]);
  ctx.fillStyle = base;
  ctx.fillRect(0, 0, W, H);

  const reach = Math.max(W, H);
  for (const light of theme.glows) {
    const cx = light.x * W;
    const cy = light.y * H;
    const glow = ctx.createRadialGradient(cx, cy, 0, cx, cy, light.r * reach);
    glow.addColorStop(0, rgba(light.color, light.alpha));
    glow.addColorStop(1, rgba(light.color, 0));
    ctx.fillStyle = glow;
    ctx.fillRect(0, 0, W, H);
  }

  const unit = Math.min(W, H) / 1080;
  if (H / W > 1.5) {
    // A tall picture has room at its foot: a second, quieter dial balances the first.
    ctx.save();
    ctx.strokeStyle = rgba(theme.ink, theme.light ? 0.08 : 0.06);
    for (const [radius, weight] of [
      [150, 1],
      [262, 2],
      [274, 0.8],
      [430, 1],
    ]) {
      ctx.lineWidth = weight * unit;
      ctx.beginPath();
      ctx.arc(W / 2, H - 150 * unit, radius * unit, 0, Math.PI * 2);
      ctx.stroke();
    }
    ctx.restore();
  }

  // A wayfinder's dial behind the title: rings, tick marks and eight rays.
  ctx.save();
  ctx.translate(focus.x, focus.y);
  ctx.strokeStyle = rgba(theme.ink, theme.light ? 0.1 : 0.075);
  for (const [radius, weight] of [
    [190, 1.2],
    [330, 2.2],
    [344, 0.8],
    [520, 1.2],
    [760, 0.8],
  ]) {
    ctx.lineWidth = weight * unit;
    ctx.beginPath();
    ctx.arc(0, 0, radius * unit, 0, Math.PI * 2);
    ctx.stroke();
  }
  ctx.lineWidth = 1.2 * unit;
  for (let i = 0; i < 72; i += 1) {
    const angle = (i / 72) * Math.PI * 2;
    const inner = (i % 9 === 0 ? 300 : 316) * unit;
    ctx.beginPath();
    ctx.moveTo(Math.cos(angle) * inner, Math.sin(angle) * inner);
    ctx.lineTo(Math.cos(angle) * 330 * unit, Math.sin(angle) * 330 * unit);
    ctx.stroke();
  }
  for (let i = 0; i < 8; i += 1) {
    const angle = (i / 8) * Math.PI * 2 - Math.PI / 2;
    const length = (i % 2 === 0 ? 900 : 560) * unit;
    const ray = ctx.createLinearGradient(0, 0, Math.cos(angle) * length, Math.sin(angle) * length);
    ray.addColorStop(0, rgba(theme.ink, theme.light ? 0.07 : 0.09));
    ray.addColorStop(1, rgba(theme.ink, 0));
    ctx.fillStyle = ray;
    const spread = 0.016;
    ctx.beginPath();
    ctx.moveTo(0, 0);
    ctx.lineTo(Math.cos(angle - spread) * length * 0.4, Math.sin(angle - spread) * length * 0.4);
    ctx.lineTo(Math.cos(angle) * length, Math.sin(angle) * length);
    ctx.lineTo(Math.cos(angle + spread) * length * 0.4, Math.sin(angle + spread) * length * 0.4);
    ctx.closePath();
    ctx.fill();
  }
  ctx.restore();

  // Stars (specks of paper grain on the light theme).
  const random = randomSource(W * 31 + H * 17 + theme.id.length * 7919);
  const count = Math.round((W * H) / 5200);
  for (let i = 0; i < count; i += 1) {
    const x = random() * W;
    const y = random() * H;
    const big = random() > 0.93;
    const size = (big ? 1.5 + random() * 1.3 : 0.5 + random() * 0.9) * unit;
    ctx.fillStyle = rgba(theme.ink, (theme.light ? 0.1 : 0.16) + random() * (big ? 0.5 : 0.3));
    ctx.beginPath();
    ctx.arc(x, y, size, 0, Math.PI * 2);
    ctx.fill();
    if (big && !theme.light) {
      // a four-point glint
      ctx.fillStyle = rgba(theme.ink, 0.2);
      diamond(ctx, x, y, size * 0.5, size * 4.5);
      ctx.fill();
      diamond(ctx, x, y, size * 4.5, size * 0.5);
      ctx.fill();
    }
  }

  // Darker towards the edges, so the middle glows.
  const vignette = ctx.createRadialGradient(W / 2, H / 2, Math.min(W, H) * 0.35, W / 2, H / 2, reach * 0.78);
  vignette.addColorStop(0, rgba(theme.shade, 0));
  vignette.addColorStop(1, rgba(theme.shade, theme.light ? 0.3 : 0.62));
  ctx.fillStyle = vignette;
  ctx.fillRect(0, 0, W, H);
}

/** The fine frame around the whole picture, open at the bottom for the credit. */
function border(ctx, report, W, H, theme, unit, credit) {
  const inset = 24 * unit;
  const cut = 30 * unit;
  const size = 13.5 * unit;
  const tracking = 0.3;
  const y = H - inset;
  const text = credit.toUpperCase();
  const width = text ? textWidth(ctx, text, LABEL, size, tracking) : 0;
  const gap = text ? width / 2 + 22 * unit : 0;

  const trace = (d, k) => {
    ctx.beginPath();
    ctx.moveTo(W / 2 + gap, H - d);
    ctx.lineTo(W - d - k, H - d);
    ctx.lineTo(W - d, H - d - k);
    ctx.lineTo(W - d, d + k);
    ctx.lineTo(W - d - k, d);
    ctx.lineTo(d + k, d);
    ctx.lineTo(d, d + k);
    ctx.lineTo(d, H - d - k);
    ctx.lineTo(d + k, H - d);
    ctx.lineTo(W / 2 - gap, H - d);
  };
  ctx.save();
  ctx.lineJoin = "miter";
  trace(inset, cut);
  ctx.strokeStyle = metal(ctx, theme, 0, 0, W, H);
  ctx.globalAlpha = 0.9;
  ctx.lineWidth = 2.4 * unit;
  ctx.stroke();
  ctx.globalAlpha = 1;
  const inner = inset + 9 * unit;
  ctx.beginPath();
  outline(ctx, "cut", inner, inner, W - 2 * inner, H - 2 * inner, cut - 4 * unit);
  ctx.strokeStyle = rgba(theme.ink, theme.light ? 0.32 : 0.2);
  ctx.lineWidth = 1 * unit;
  ctx.stroke();
  // A stud on each cut corner.
  for (const [sx, sy] of [
    [0, 0],
    [1, 0],
    [0, 1],
    [1, 1],
  ]) {
    const px = sx ? W - inset - cut / 2 : inset + cut / 2;
    const py = sy ? H - inset - cut / 2 : inset + cut / 2;
    diamond(ctx, px, py, 5 * unit, 5 * unit);
    ctx.fillStyle = theme.metal[1];
    ctx.fill();
  }
  if (text) {
    ctx.fillStyle = theme.muted;
    const left = W / 2 - width / 2;
    const baseline = y + size * 0.36;
    drawText(ctx, text, LABEL, size, tracking, left, baseline);
    note(report, "credit", text, left, baseline, width, size, extent(ctx, text, LABEL, size), null);
  }
  ctx.restore();
}

// ---- panels and what is in them -------------------------------------------------------------------

const CORNER = { cut: 26, crest: 24, notch: 13, scallop: 22, round: 22 };
/** Width of a panel's metal frame. */
const bandOf = (unit, hero) => (hero ? 8 : 6.5) * unit;

function panel(ctx, theme, box, unit, hero) {
  const { x, y, w, h } = box;
  const c = CORNER[theme.shape] * unit * (hero ? 1.1 : 1);
  const band = bandOf(unit, hero);
  const trace = (inset = 0) => outline(ctx, theme.shape, x, y, w, h, c, inset);

  ctx.save();
  ctx.lineJoin = "miter";
  trace();
  ctx.shadowColor = rgba(theme.shade, theme.light ? 0.3 : 0.6);
  ctx.shadowBlur = 34 * unit;
  ctx.shadowOffsetY = 12 * unit;
  ctx.fillStyle = theme.panel[1];
  ctx.fill();
  ctx.restore();

  ctx.save();
  trace();
  ctx.clip();
  const fill = ctx.createLinearGradient(0, y, 0, y + h);
  fill.addColorStop(0, theme.panel[0]);
  fill.addColorStop(1, theme.panel[1]);
  ctx.fillStyle = fill;
  ctx.fillRect(x, y, w, h);
  // Light falling in from the top edge.
  const sheen = ctx.createRadialGradient(x + w / 2, y, 0, x + w / 2, y, Math.max(w * 0.62, h));
  sheen.addColorStop(0, rgba(theme.panelGlow, theme.light ? 0.55 : hero ? 0.3 : 0.22));
  sheen.addColorStop(1, rgba(theme.panelGlow, 0));
  ctx.fillStyle = sheen;
  ctx.fillRect(x, y, w, h);
  ctx.restore();

  // The frame: a dark edge, a band of metal, a bright ridge along its middle,
  // and a hairline inside - which is what makes it read as bevelled.
  ctx.save();
  ctx.lineJoin = "miter";
  ctx.miterLimit = 4;
  trace();
  ctx.strokeStyle = theme.metalEdge;
  ctx.lineWidth = band + 3 * unit;
  ctx.stroke();
  ctx.strokeStyle = metal(ctx, theme, x, y, x + w, y + h);
  ctx.lineWidth = band;
  if (hero && !theme.light) {
    // The hero's frame catches the light.
    ctx.shadowColor = rgba(theme.glow, 0.5);
    ctx.shadowBlur = 22 * unit;
  }
  ctx.stroke();
  ctx.shadowBlur = 0;
  ctx.strokeStyle = rgba(theme.light ? "255,244,214" : "255,255,255", theme.light ? 0.5 : 0.42);
  ctx.lineWidth = Math.max(1, band * 0.2);
  ctx.stroke();
  trace(band / 2 + 6.5 * unit);
  ctx.strokeStyle = rgba(theme.light ? theme.ink : "255,255,255", theme.light ? 0.3 : 0.16);
  ctx.lineWidth = Math.max(1, 1.1 * unit);
  ctx.stroke();
  ctx.restore();
}

function toneOf(theme, tone) {
  return tone === "sub" ? theme.sub : theme.value;
}

/** A value made of runs ("23" big, "/ 60" small) as widths at a base size. */
function measureRuns(ctx, runs, size) {
  let width = 0;
  const widths = runs.map((run, i) => {
    const w = textWidth(ctx, run.text, NUMBER, size * (run.scale || 1), 0);
    width += w + (i ? (run.gap || 0) * size : 0);
    return w;
  });
  return { width, widths };
}

/** Numbers are never cut: they shrink until they fit. */
function fitRuns(ctx, runs, size, width) {
  let fitted = size;
  let measured = measureRuns(ctx, runs, fitted);
  let guard = 0;
  while (measured.width > width && fitted > 4 && guard < 60) {
    fitted = Math.max(4, Math.min(fitted * 0.97, (fitted * width) / measured.width));
    measured = measureRuns(ctx, runs, fitted);
    guard += 1;
  }
  return { size: fitted, ...measured };
}

function drawRuns(ctx, report, id, runs, fit, cx, baseline, theme, glow, box) {
  let pen = cx - fit.width / 2;
  const left = pen;
  ctx.save();
  ctx.shadowColor = rgba(theme.light ? theme.shade : theme.glow, theme.light ? 0.18 : glow);
  ctx.shadowBlur = fit.size * (theme.light ? 0.08 : 0.24);
  ctx.shadowOffsetY = theme.light ? fit.size * 0.02 : 0;
  runs.forEach((run, i) => {
    pen += i ? (run.gap || 0) * fit.size : 0;
    ctx.fillStyle = toneOf(theme, run.tone);
    drawText(ctx, run.text, NUMBER, fit.size * (run.scale || 1), 0, pen, baseline);
    pen += fit.widths[i];
  });
  ctx.restore();
  const text = runs.map((run) => run.text).join(" ");
  note(report, id, text, left, baseline, fit.width, fit.size, extent(ctx, runs[0].text, NUMBER, fit.size), box);
}

/** "MY STEPS  <>  REAL-WORLD WALKING": label parts with a small stud between them. */
function drawLabel(ctx, report, id, parts, at, theme, box) {
  const texts = parts.map((part) => part.toUpperCase());
  let size = at.size;
  const measure = () => {
    const stud = size * 1.5;
    const widths = texts.map((text) => textWidth(ctx, text, LABEL, size, at.tracking));
    return { stud, widths, width: widths.reduce((a, b) => a + b, 0) + stud * (texts.length - 1) };
  };
  let m = measure();
  let guard = 0;
  while (m.width > at.width && size > 4 && guard < 60) {
    size = Math.max(4, Math.min(size * 0.97, (size * at.width) / m.width));
    m = measure();
    guard += 1;
  }
  let pen = at.x - m.width / 2;
  const left = pen;
  ctx.save();
  texts.forEach((text, i) => {
    if (i) {
      diamond(ctx, pen + m.stud / 2, at.y - size * 0.36, size * 0.2, size * 0.2);
      ctx.fillStyle = theme.sub;
      ctx.fill();
      pen += m.stud;
    }
    ctx.fillStyle = theme.label;
    drawText(ctx, text, LABEL, size, at.tracking, pen, at.y);
    pen += m.widths[i];
  });
  ctx.restore();
  note(report, id, texts.join(" "), left, at.y, m.width, size, extent(ctx, texts[0], LABEL, size), box);
}

function progressBar(ctx, theme, cx, y, width, height, fraction) {
  const x = cx - width / 2;
  const r = height / 2;
  const bar = (w) => {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + height, r);
    ctx.arcTo(x + w, y + height, x, y + height, r);
    ctx.arcTo(x, y + height, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
  };
  ctx.save();
  bar(width);
  ctx.fillStyle = rgba(theme.shade, theme.light ? 0.16 : 0.5);
  ctx.fill();
  ctx.strokeStyle = rgba(theme.ink, theme.light ? 0.35 : 0.22);
  ctx.lineWidth = Math.max(1, height * 0.12);
  ctx.stroke();
  const filled = Math.max(height, width * clamp(fraction, 0, 1));
  bar(filled);
  ctx.fillStyle = metal(ctx, theme, x, y, x + filled, y + height);
  ctx.fill();
  ctx.restore();
  gem(ctx, theme, x + filled, y + height / 2, height * 0.95);
}

/**
 * One tile: a column of label, value, (level bar), sub-line and (note),
 * centred in its panel. The sizes scale with the panel, and if the column
 * would be taller than the panel it shrinks as a whole.
 *
 * `slots` says which of the optional lines any tile in the same row has:
 * every tile of a row leaves room for them, so that labels, values and
 * sub-lines stand on the same lines right across the row.
 */
function tile(ctx, report, theme, box, item, unit, hero, slots) {
  const frame = bandOf(unit, hero) + 10 * unit;
  let s = hero ? Math.min(box.h / 300, box.w / 640) : Math.min(box.h / 250, box.w / 430);
  const padX = (hero ? 46 : 30) * s + frame;
  const width = box.w - 2 * padX;
  const cx = box.x + box.w / 2;
  const sizes = hero
    ? { label: 25, value: 146, sub: 26, note: 17, gapLabel: 36, gapValue: 52, gapBar: 0, gapSub: 18 }
    : { label: 21, value: 86, sub: 20, note: 14.5, gapLabel: 28, gapValue: 26, gapBar: 20, gapSub: 15 };
  const CAP = 0.72; // height of capitals and digits, as a share of the font size
  let air = 1; // how far the gaps between the lines are opened up
  const column = () => {
    let total = sizes.label * CAP + sizes.gapLabel * air + sizes.value * CAP;
    if (slots.bar) total += sizes.gapBar * air + 9;
    if (slots.sub) total += sizes.gapValue * air + sizes.sub * CAP;
    if (slots.note) total += sizes.gapSub * air + sizes.note * CAP;
    return total * s;
  };
  const room = box.h - 2 * (frame + 12 * s);
  if (column() > room) s *= room / column();
  // A tall panel (a story, the wide picture) would leave its lines huddled
  // in the middle: let them breathe instead.
  while (air < 1.7 && column() < box.h * 0.56) air += 0.05;
  const inner = { x: box.x + frame, y: box.y + frame, w: box.w - 2 * frame, h: box.h - 2 * frame };

  let y = box.y + (box.h - column()) / 2;
  y += sizes.label * CAP * s;
  drawLabel(
    ctx,
    report,
    `${item.id}.label`,
    item.label,
    { x: cx, y, size: sizes.label * s, width, tracking: 0.17 },
    theme,
    inner,
  );

  y += (sizes.gapLabel * air + sizes.value * CAP) * s;
  const fit = fitRuns(ctx, item.value, sizes.value * s, width);
  // A value that had to shrink sits a little higher, to stay centred on its line.
  const lift = ((sizes.value * s - fit.size) * CAP) / 2;
  drawRuns(ctx, report, `${item.id}.value`, item.value, fit, cx, y - lift, theme, hero ? 0.6 : 0.4, inner);

  if (slots.bar) {
    y += sizes.gapBar * air * s;
    if (item.progress !== undefined) progressBar(ctx, theme, cx, y, Math.min(width, 250 * s), 9 * s, item.progress);
    y += 9 * s;
  }
  if (slots.sub) y += (sizes.gapValue * air + sizes.sub * CAP) * s;
  if (item.sub) {
    const at = {
      x: cx,
      y,
      size: sizes.sub * s,
      min: sizes.sub * s * 0.55,
      width: width - (hero ? 150 * s : 0),
      tracking: 0.14,
    };
    const drawn = line(
      ctx,
      report,
      `${item.id}.sub`,
      item.sub.toUpperCase(),
      LABEL,
      at,
      (c) => (c.fillStyle = theme.sub),
      inner,
    );
    if (hero && drawn) {
      // Short fading lines either side of the hero's sub-line.
      const mid = y - drawn.size * CAP * 0.5;
      const reach = Math.min(150 * s, (box.w - 2 * frame - drawn.width) / 2 - 26 * s);
      if (reach > 30 * s) {
        const weight = Math.max(1, 2 * s);
        taper(ctx, theme, drawn.left - 18 * s, drawn.left - 18 * s - reach, mid, weight);
        taper(ctx, theme, drawn.left + drawn.width + 18 * s, drawn.left + drawn.width + 18 * s + reach, mid, weight);
      }
    }
  }
  if (slots.note) y += (sizes.gapSub * air + sizes.note * CAP) * s;
  if (item.note) {
    const at = { x: cx, y, size: sizes.note * s, min: sizes.note * s * 0.6, width, tracking: 0.12 };
    line(ctx, report, `${item.id}.note`, item.note.toUpperCase(), LABEL, at, (c) => (c.fillStyle = theme.muted), inner);
  }
}

// ---- the road -----------------------------------------------------------------------------------

// ---- the road, on a map ------------------------------------------------------------------------
//
// The road tile: the character's path on a low-poly map of each continent
// (lands.js, handed in by the page; a continent without one gets an empty
// chart). The world's yards run north (x) and west (y); on the picture north
// is up and west is left.
//
// * Two continents with maps share one sea and one scale; a single
//   continent with a short road is shown close up, with the whole continent
//   small in a corner.
// * Three kinds of line: walked on the treadmill (bright), travelled with
//   the belt still (the same line, faded), flights and passages between
//   continents (fine dashes). A key under the map says which is which.
// * START where the road begins, a gem with the level where the character
//   is now, a numbered stud at every tenth level reached. Words are put
//   where they cover neither a mark nor the road.

/**
 * A continent as a low-poly map: its outline filled, facets in ten shades
 * over it (a made-up relief, lit from the north-west), and the coast as a
 * fine line. `at` turns world yards into a place on the picture. Returns
 * how far the land reaches there.
 */
function drawLand(ctx, theme, land, at, s) {
  const corners = land.points.length / 2;
  const px = new Array(corners);
  const py = new Array(corners);
  const reach = { left: Infinity, top: Infinity, right: -Infinity, bottom: -Infinity, faces: land.faces.length / 4 };
  for (let i = 0; i < corners; i += 1) {
    [px[i], py[i]] = at(land.points[2 * i], land.points[2 * i + 1]);
    reach.left = Math.min(reach.left, px[i]);
    reach.right = Math.max(reach.right, px[i]);
    reach.top = Math.min(reach.top, py[i]);
    reach.bottom = Math.max(reach.bottom, py[i]);
  }
  coastPath(ctx, land, px, py);
  ctx.save();
  ctx.lineJoin = "round";
  // The ground: one flat tone, so that no seam shows between the facets,
  // lifted off the sea by a soft glow (a shadow, on paper).
  ctx.save();
  ctx.shadowColor = theme.light ? rgba(theme.shade, 0.4) : rgba(theme.glow, 0.32);
  ctx.shadowBlur = 14 * s;
  ctx.shadowOffsetY = theme.light ? 2 * s : 0;
  ctx.fillStyle = theme.map.land;
  ctx.fill();
  ctx.restore();
  // The facets: the lit ones a little lighter, the ones in shadow a little
  // darker. All of a shade are filled at once.
  const steps = 10;
  for (let shade = 0; shade < steps; shade += 1) {
    const lit = (shade - (steps - 1) / 2) / ((steps - 1) / 2); // -1 (in shadow) to 1 (lit)
    if (Math.abs(lit) < 0.05) continue;
    ctx.beginPath();
    let any = false;
    for (let i = 0; i < land.faces.length; i += 4) {
      if (land.faces[i + 3] !== shade) continue;
      const [a, b, c] = [land.faces[i], land.faces[i + 1], land.faces[i + 2]];
      ctx.moveTo(px[a], py[a]);
      ctx.lineTo(px[b], py[b]);
      ctx.lineTo(px[c], py[c]);
      ctx.closePath();
      any = true;
    }
    if (!any) continue;
    if (theme.light) ctx.fillStyle = lit > 0 ? rgba("255,252,242", 0.3 * lit) : rgba(theme.shade, -0.16 * lit);
    else ctx.fillStyle = lit > 0 ? rgba("255,255,255", 0.11 * lit) : rgba("0,0,0", -0.16 * lit);
    ctx.fill();
  }
  // The coast.
  coastPath(ctx, land, px, py);
  ctx.strokeStyle = theme.map.coast;
  ctx.lineWidth = Math.max(1, 1.3 * s);
  ctx.stroke();
  ctx.restore();
  return reach;
}

function coastPath(ctx, land, px, py) {
  ctx.beginPath();
  for (const ring of land.coast) {
    ring.forEach((corner, i) => (i === 0 ? ctx.moveTo(px[corner], py[corner]) : ctx.lineTo(px[corner], py[corner])));
    ctx.closePath();
  }
}

/**
 * What a plate shows of a continent, as {x, y, scale, zoomed}: the world
 * place (yards: x north, y west) at the plate's middle and the pixels a
 * yard.
 *
 * With a map of the continent all of it is shown - unless `alone` (it is
 * the only continent on the picture) and a closer look would show the road
 * much larger: then the plate closes in on the road, never to less than a
 * fifth of the continent's length, so that the coast still says where this
 * is. Without a map the plate shows where the road goes.
 */
function mapView(land, w, h, s, alone) {
  const road = land.bounds;
  const shape = w / h;
  // The window of the plate's shape around the bounds `b`, at least `least` yards tall.
  const fit = (b, least = 1) => {
    const tall = Math.max(b[2] - b[0], (b[3] - b[1]) / shape, least);
    const x = (b[0] + b[2]) / 2;
    const y = (b[1] + b[3]) / 2;
    return [x - tall / 2, y - (tall * shape) / 2, x + tall / 2, y + (tall * shape) / 2];
  };
  const view = (window, zoomed) => ({
    x: (window[0] + window[2]) / 2,
    y: (window[1] + window[3]) / 2,
    scale: h / (window[2] - window[0]),
    zoomed,
  });
  const both = (a, b) => [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[2], b[2]), Math.max(a[3], b[3])];
  const margin = Math.max(300, 0.2 * Math.max(road[2] - road[0], road[3] - road[1]));
  const near = [road[0] - margin, road[1] - margin, road[2] + margin, road[3] + margin];
  if (!land.land) {
    // (The first few yards of a new character are not blown up into a
    // journey: a road fills its plate from about a hundred yards on.)
    const around = [road[0] - margin / 3, road[1] - margin / 3, road[2] + margin / 3, road[3] + margin / 3];
    return view(fit(around, h / (2 * s)), false);
  }
  const map = land.land.bounds;
  // All of the continent (and the road, should it lead off the map: a boat far out at sea).
  const all = fit(both(road, map));
  if (!alone) return view(all, false);
  const close = fit(near, 0.2 * (map[2] - map[0]));
  // (A close-up that would show most of the continent anyway is not worth
  // losing its outline for. So the picture never jumps by much as a road grows.)
  if (close[2] - close[0] >= 0.7 * (all[2] - all[0])) return view(all, false);
  // Closed in on the road - but not looking out over open sea where there
  // is land to show instead: the window stays over the land (and the road
  // with its margin) wherever it is the smaller of the two.
  const keep = both(near, map);
  const settle = (lo, hi, least, most) =>
    hi - lo >= most - least ? (least + most) / 2 - (lo + hi) / 2 : lo < least ? least - lo : hi > most ? most - hi : 0;
  const dx = settle(close[0], close[2], keep[0], keep[2]);
  const dy = settle(close[1], close[3], keep[1], keep[3]);
  return view([close[0] + dx, close[1] + dy, close[2] + dx, close[3] + dy], true);
}

/** A stretch of road as a path. `p`: [x0, y0, x1, y1, ...] in world yards. */
function tracePath(ctx, at, p) {
  ctx.beginPath();
  for (let i = 0; i + 1 < p.length; i += 2) {
    const [px, py] = at(p[i], p[i + 1]);
    if (i === 0) ctx.moveTo(px, py);
    else ctx.lineTo(px, py);
  }
}

/**
 * The three kinds of road, each with a look of its own. `fine` (0 to 1):
 * how much finer than usual to draw it - a long road, wound tight around
 * every village, would otherwise run together into blots.
 */
function strokeRoad(ctx, theme, kind, s, fine = 0) {
  const casing = theme.light ? "rgba(255,250,236,0.9)" : rgba(theme.shade, 0.55);
  if (kind === "flight") {
    // A flight, or the passage to another continent: fine dashes.
    ctx.setLineDash([4 * s, 3.5 * s]);
    ctx.strokeStyle = theme.map.dim;
    ctx.lineWidth = Math.max(1, 1.7 * s);
    ctx.stroke();
    ctx.setLineDash([]);
    return;
  }
  const bright = kind === "walked";
  const slim = 1 - 0.55 * fine;
  const width = (bright ? 4.2 : 3.2) * s * slim;
  // A dark edge (a light one on paper) keeps the road apart from the land.
  ctx.strokeStyle = casing;
  ctx.lineWidth = width + (bright ? 2.6 : 2.2) * s * slim;
  ctx.stroke();
  if (bright && !theme.light) {
    ctx.strokeStyle = rgba(theme.glow, 0.3);
    ctx.lineWidth = width + 5 * s * slim;
    ctx.stroke();
  }
  // Walked for real: bright. Travelled with the belt still: the same road, in the map's quieter colour.
  ctx.strokeStyle = bright ? theme.value : theme.map.dim;
  ctx.lineWidth = Math.max(bright ? 1.6 : 1.2, width);
  ctx.stroke();
}

/** Does the line from (x0, y0) to (x1, y1) touch the box {l, t, r, b}? */
function crossesBox(x0, y0, x1, y1, box) {
  if (Math.max(x0, x1) < box.l || Math.min(x0, x1) > box.r || Math.max(y0, y1) < box.t || Math.min(y0, y1) > box.b) return false;
  const inside = (x, y) => x >= box.l && x <= box.r && y >= box.t && y <= box.b;
  if (inside(x0, y0) || inside(x1, y1)) return true;
  // Which side of the line each corner lies on: all on one side means no touch.
  const side = (x, y) => (x1 - x0) * (y - y0) - (y1 - y0) * (x - x0);
  const sides = [side(box.l, box.t), side(box.r, box.t), side(box.r, box.b), side(box.l, box.b)];
  return !(sides.every((value) => value > 0) || sides.every((value) => value < 0));
}

/**
 * The whole continent in a corner of a plate that has closed in, with a
 * frame around what the plate shows. `avoid`: places on the plate (the
 * road's points) it should keep away from. Returns its box, and the frame
 * in it (null if the plate shows nothing of the land).
 */
function drawInset(ctx, theme, land, view, plate, avoid, s) {
  const map = land.bounds;
  const tall = clamp(0.33 * plate.h, 38 * s, 100 * s);
  const wide = Math.min(0.42 * plate.w, (tall * (map[3] - map[1])) / (map[2] - map[0]));
  const high = (wide * (map[2] - map[0])) / (map[3] - map[1]);
  const pad = 5 * s;
  const box = { w: wide + 2 * pad, h: high + 2 * pad };
  // The corner whose nearest piece of road is farthest away.
  const corners = [
    [plate.x + 4 * s, plate.y + 4 * s],
    [plate.x + plate.w - box.w - 4 * s, plate.y + 4 * s],
    [plate.x + 4 * s, plate.y + plate.h - box.h - 4 * s],
    [plate.x + plate.w - box.w - 4 * s, plate.y + plate.h - box.h - 4 * s],
  ];
  const clear = (corner) => {
    const cx = corner[0] + box.w / 2;
    const cy = corner[1] + box.h / 2;
    let nearest = Infinity;
    for (const [px, py] of avoid) {
      // (How far outside the box the point is: 0 if it is under it.)
      const dx = Math.max(Math.abs(px - cx) - box.w / 2, 0);
      const dy = Math.max(Math.abs(py - cy) - box.h / 2, 0);
      nearest = Math.min(nearest, Math.hypot(dx, dy));
    }
    return nearest;
  };
  [box.x, box.y] = corners.reduce((best, corner) => (clear(corner) > clear(best) ? corner : best));
  const scale = wide / (map[3] - map[1]);
  const at = (x, y) => [box.x + pad + (map[3] - y) * scale, box.y + pad + (map[2] - x) * scale];
  ctx.save();
  outline(ctx, "round", box.x, box.y, box.w, box.h, 5 * s);
  ctx.fillStyle = theme.light ? "rgba(255,251,238,0.9)" : rgba(theme.shade, 0.62);
  ctx.fill();
  ctx.strokeStyle = rgba(theme.ink, 0.4);
  ctx.lineWidth = Math.max(1, s);
  ctx.stroke();
  const count = land.points.length / 2;
  const px = new Array(count);
  const py = new Array(count);
  for (let i = 0; i < count; i += 1) [px[i], py[i]] = at(land.points[2 * i], land.points[2 * i + 1]);
  coastPath(ctx, land, px, py);
  ctx.fillStyle = theme.map.land;
  ctx.fill();
  ctx.strokeStyle = theme.map.coast;
  ctx.lineWidth = Math.max(0.8, 0.8 * s);
  ctx.stroke();
  // What the plate shows: the part of its window that lies over the map.
  const halfX = plate.h / 2 / view.scale;
  const halfY = plate.w / 2 / view.scale;
  const north = Math.min(map[2], view.x + halfX);
  const south = Math.max(map[0], view.x - halfX);
  const west = Math.min(map[3], view.y + halfY);
  const east = Math.max(map[1], view.y - halfY);
  box.frame = null;
  if (north > south && west > east) {
    const [l, t] = at(north, west);
    const [r, b] = at(south, east);
    ctx.beginPath();
    ctx.moveTo(l, t);
    ctx.lineTo(r, t);
    ctx.lineTo(r, b);
    ctx.lineTo(l, b);
    ctx.closePath();
    ctx.fillStyle = rgba(theme.light ? theme.shade : "255,255,255", 0.2);
    ctx.fill();
    ctx.strokeStyle = theme.sub;
    ctx.lineWidth = Math.max(1.4, 2 * s);
    ctx.stroke();
    box.frame = { x: l, y: t, w: r - l, h: b - t };
  }
  ctx.restore();
  return box;
}

function routeTile(ctx, report, theme, box, item, unit) {
  const frame = bandOf(unit, false) + 10 * unit;
  // Type is sized like a small tile's: the panel is about as wide as one
  // when it stands beside the tiles, and wider when it has a row to itself.
  const s = Math.min(1.25, box.h / 300, box.w / 430);
  const CAP = 0.72;
  const inner = { x: box.x + frame, y: box.y + frame, w: box.w - 2 * frame, h: box.h - 2 * frame };
  const cx = box.x + box.w / 2;
  const padX = 26 * s;
  const width = inner.w - 2 * padX;
  const labelSize = 21 * s;
  const subSize = 18 * s;
  const keySize = 13.5 * s;

  const labelY = inner.y + 17 * s + labelSize * CAP;
  drawLabel(ctx, report, "route.label", item.label, { x: cx, y: labelY, size: labelSize, width, tracking: 0.17 }, theme, inner);

  // Under the map: how long the road is, and the key to its lines - a
  // piece of each kind of line with what it means (and how much of the
  // road was walked for real). One row where the panel is wide enough,
  // else two.
  const sample = 20 * s;
  const sampleGap = 6 * s;
  const between = 14 * s;
  const write = (id, text, size, left, y, colour) =>
    line(
      ctx,
      report,
      `route.${id}`,
      text,
      LABEL,
      { x: left + textWidth(ctx, text, LABEL, size, 0.14) / 2, y, size, min: size, width: 4000, tracking: 0.14 },
      (c) => (c.fillStyle = colour),
      inner,
    );
  const keyWidth = (texts, size) =>
    texts.reduce((sum, text) => sum + sample + sampleGap + textWidth(ctx, text, LABEL, size, 0.14), 0) + between * Math.max(0, texts.length - 1);
  // The key as it fits `room`: in full, else in brief, else smaller, else without its last entries.
  const fitKey = (room) => {
    let entries = item.key;
    const full = entries.map((entry) => entry.text.toUpperCase());
    if (keyWidth(full, keySize) <= room) return { entries, texts: full, size: keySize };
    while (entries.length) {
      const brief = entries.map((entry) => entry.brief.toUpperCase());
      const wide = keyWidth(brief, keySize);
      if (wide <= room) return { entries, texts: brief, size: keySize };
      const size = keySize * Math.max(0.68, (room - entries.length * (sample + sampleGap) - between * (entries.length - 1)) / Math.max(1, wide - entries.length * (sample + sampleGap) - between * (entries.length - 1)));
      if (keyWidth(brief, size) <= room) return { entries, texts: brief, size };
      entries = entries.slice(0, -1);
    }
    return { entries: [], texts: [], size: keySize };
  };
  const drawKey = (key, left, y) => {
    let pen = left;
    key.entries.forEach((entry, i) => {
      ctx.save();
      ctx.lineCap = "round";
      ctx.beginPath();
      ctx.moveTo(pen + 2 * s, y - key.size * 0.36);
      ctx.lineTo(pen + sample - 2 * s, y - key.size * 0.36);
      strokeRoad(ctx, theme, entry.style === "flight" ? "flight" : entry.style, s * 0.85);
      ctx.restore();
      pen += sample + sampleGap;
      write(`key-${entry.style}`, key.texts[i], key.size, pen, y, entry.style === "walked" ? theme.sub : theme.muted);
      pen += textWidth(ctx, key.texts[i], LABEL, key.size, 0.14) + between;
    });
  };
  let textTop = inner.y + inner.h - 17 * s; // the baseline of the lowest line, then of the one above it, ...
  const subFit = fitLine(ctx, item.sub.toUpperCase(), LABEL, { size: subSize, min: subSize * 0.55, width, tracking: 0.14 });
  const stud = subSize * 1.5;
  const lone = item.key.length ? fitKey(width - subFit.width - stud) : null;
  if (lone && lone.entries.length === item.key.length && lone.size === keySize && width >= 600 * s) {
    // One row: the length, a stud, the key.
    const together = subFit.width + stud + keyWidth(lone.texts, lone.size);
    let pen = cx - together / 2;
    write("sub", subFit.text, subFit.size, pen, textTop, theme.sub);
    pen += subFit.width;
    diamond(ctx, pen + stud / 2, textTop - subSize * 0.36, subSize * 0.17, subSize * 0.17);
    ctx.fillStyle = theme.muted;
    ctx.fill();
    drawKey(lone, pen + stud, textTop);
    textTop -= subSize * CAP + 11 * s;
  } else {
    if (item.key.length) {
      const key = fitKey(width);
      if (key.entries.length) {
        drawKey(key, cx - keyWidth(key.texts, key.size) / 2, textTop);
        textTop -= key.size * CAP + 10 * s;
      }
    }
    write("sub", subFit.text, subFit.size, cx - subFit.width / 2, textTop, theme.sub);
    textTop -= subSize * CAP + 11 * s;
  }

  const route = item.route;
  const lands = route.continents;
  if (!lands.length) return;
  const named = lands.some((land) => land.name);
  const nameSize = 12.5 * s;
  const namesY = textTop - 3 * s; // the baseline of the continents' names
  // (The map may come closer to the panel's edge than the words do.)
  const edge = 9 * s;
  const top = labelY + 13 * s;
  const area = {
    x: inner.x + edge,
    y: top,
    w: inner.w - 2 * edge,
    h: (named ? namesY - nameSize * CAP - 9 * s : textTop + 2 * s) - top,
  };
  if (area.h < 24 * s || area.w < 40 * s) return;

  // A plate for each continent, side by side: Kalimdor to the west. Two
  // continents that both have a map share one sea, as on a map of the
  // world: each gets the width its land needs, and both are drawn to one
  // scale.
  const pad = 12 * s; // room inside a plate, so that no mark hangs over its edge
  const world = lands.length > 1 && lands.every((land) => land.land);
  const gapX = world ? 0 : 12 * s;
  const wide = lands.map((land) => {
    if (!world) return 1;
    const road = land.bounds;
    const map = land.land.bounds;
    return Math.max(road[3], map[3]) - Math.min(road[1], map[1]);
  });
  const shares = wide.reduce((sum, value) => sum + value, 0);
  let left = area.x;
  const plates = lands.map((land, i) => {
    const plate = { x: left, y: area.y, w: ((area.w - gapX * (lands.length - 1)) * wide[i]) / shares, h: area.h };
    left += plate.w + gapX;
    return { land, plate, view: mapView(land, plate.w - 2 * pad, plate.h - 2 * pad, s, lands.length === 1) };
  });
  if (world) {
    const scale = Math.min(...plates.map((entry) => entry.view.scale));
    for (const entry of plates) entry.view.scale = scale;
  }
  // The sea: one sheet of it under a map of the world, else one under each plate.
  const seas = world ? [area] : plates.map((entry) => entry.plate);
  const place = {};
  for (const entry of plates) {
    const { plate, view } = entry;
    const midX = plate.x + plate.w / 2;
    const midY = plate.y + plate.h / 2;
    // West is left, north is up.
    entry.at = (x, y) => [midX - (y - view.y) * view.scale, midY - (x - view.x) * view.scale];
    place[entry.land.id] = entry;
  }

  const reach = {
    left: Infinity,
    top: Infinity,
    right: -Infinity,
    bottom: -Infinity,
    points: 0,
    box: inner,
    plates: [],
    marks: [],
    arcs: [],
    key: item.key.map((entry) => entry.style),
    onRoad: [], // words that found no place off the road
  };
  report.route = reach;
  const radius = 9 * s;
  // The sea, with a chart's faint grid over it.
  for (const sea of seas) {
    ctx.save();
    outline(ctx, "round", sea.x, sea.y, sea.w, sea.h, radius);
    ctx.fillStyle = theme.map.sea;
    ctx.fill();
    ctx.clip();
    const step = 46 * s;
    ctx.strokeStyle = rgba(theme.ink, theme.light ? 0.09 : 0.06);
    ctx.lineWidth = Math.max(1, s);
    ctx.beginPath();
    for (let x = sea.x + sea.w / 2 - Math.floor(sea.w / 2 / step) * step; x < sea.x + sea.w; x += step) {
      ctx.moveTo(x, sea.y);
      ctx.lineTo(x, sea.y + sea.h);
    }
    for (let y = sea.y + sea.h / 2 - Math.floor(sea.h / 2 / step) * step; y < sea.y + sea.h; y += step) {
      ctx.moveTo(sea.x, y);
      ctx.lineTo(sea.x + sea.w, y);
    }
    ctx.stroke();
    ctx.restore();
  }
  // What is drawn where, so that nothing is put on top of something else:
  // `taken` are boxes (marks, words, the small map), `ink` the lines of
  // the road and of the arcs between continents.
  const taken = [];
  const ink = [];
  // How much road there is on the picture, in pixels: a long one is drawn finer.
  let drawnLength = 0;
  for (const { land, at } of plates) {
    for (const path of land.paths) {
      let before = null;
      for (let i = 0; i + 1 < path.p.length; i += 2) {
        const spot = at(path.p[i], path.p[i + 1]);
        if (before) drawnLength += Math.hypot(spot[0] - before[0], spot[1] - before[1]);
        before = spot;
      }
    }
  }
  const fine = clamp((drawnLength / s - 700) / 2600, 0, 1);
  reach.fine = fine;
  const mark = (kind, b) => {
    taken.push(b);
    reach.marks.push({ kind, left: b.l, top: b.t, right: b.r, bottom: b.b });
  };
  for (const entry of plates) {
    const { land, plate, view, at } = entry;
    const seen = { id: land.id, ...plate, zoomed: view.zoomed, mapped: !!land.land, shared: world, land: null, inset: null };
    const road = { left: Infinity, top: Infinity, right: -Infinity, bottom: -Infinity, points: 0 };
    const dots = [];
    for (const path of land.paths) {
      let before = null;
      for (let i = 0; i + 1 < path.p.length; i += 2) {
        const [px, py] = at(path.p[i], path.p[i + 1]);
        road.left = Math.min(road.left, px);
        road.right = Math.max(road.right, px);
        road.top = Math.min(road.top, py);
        road.bottom = Math.max(road.bottom, py);
        road.points += 1;
        dots.push([px, py]);
        if (before) ink.push([before[0], before[1], px, py]);
        before = [px, py];
      }
    }
    seen.road = road;
    reach.left = Math.min(reach.left, road.left);
    reach.right = Math.max(reach.right, road.right);
    reach.top = Math.min(reach.top, road.top);
    reach.bottom = Math.max(reach.bottom, road.bottom);
    reach.points += road.points;
    reach.plates.push(seen);

    // Nothing of a continent is drawn outside its sea.
    const sea = world ? area : plate;
    ctx.save();
    outline(ctx, "round", sea.x, sea.y, sea.w, sea.h, radius);
    ctx.clip();
    // The land.
    if (land.land) seen.land = drawLand(ctx, theme, land.land, at, s);
    // The road: flights first, then what was travelled with the belt
    // still, and over everything what was walked for real.
    ctx.lineJoin = "round";
    ctx.lineCap = "round";
    for (const kind of ["flight", "road", "walked"]) {
      for (const path of land.paths) {
        if (path.style !== kind || path.p.length < 4) continue;
        tracePath(ctx, at, path.p);
        strokeRoad(ctx, theme, kind, s, fine);
      }
    }
    if (view.zoomed) {
      seen.inset = drawInset(ctx, theme, land.land, view, plate, dots, s);
      mark("inset", { l: seen.inset.x, t: seen.inset.y, r: seen.inset.x + seen.inset.w, b: seen.inset.y + seen.inset.h });
    }
    ctx.restore();
  }
  // The edge of each sea, over whatever reaches it.
  for (const sea of seas) {
    outline(ctx, "round", sea.x, sea.y, sea.w, sea.h, radius);
    ctx.strokeStyle = rgba(theme.ink, theme.light ? 0.3 : 0.2);
    ctx.lineWidth = Math.max(1, s);
    ctx.stroke();
  }

  // From one continent to the other (a boat, a zeppelin): a dashed arc.
  const shown = (spot) => {
    const entry = spot && place[spot.c];
    if (!entry) return null;
    const [px, py] = entry.at(spot.x, spot.y);
    const { plate } = entry;
    const inside = px >= plate.x && px <= plate.x + plate.w && py >= plate.y && py <= plate.y + plate.h;
    return inside ? [px, py] : null;
  };
  for (const crossing of route.crossings) {
    const a = shown(crossing.from);
    const b = shown(crossing.to);
    if (!a || !b) continue;
    // (Bowed upwards, but never above the map.)
    const lift = Math.min(0.24 * Math.hypot(b[0] - a[0], b[1] - a[1]) + 14 * s, Math.min(a[1], b[1]) - area.y - 6 * s);
    const mid = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2 - 2 * Math.max(0, lift)];
    ctx.save();
    ctx.lineCap = "round";
    ctx.beginPath();
    let before = null;
    let highest = Infinity;
    for (let i = 0; i <= 24; i += 1) {
      const t = i / 24;
      const px = (1 - t) * (1 - t) * a[0] + 2 * t * (1 - t) * mid[0] + t * t * b[0];
      const py = (1 - t) * (1 - t) * a[1] + 2 * t * (1 - t) * mid[1] + t * t * b[1];
      if (i === 0) ctx.moveTo(px, py);
      else ctx.lineTo(px, py);
      if (before) ink.push([before[0], before[1], px, py]);
      before = [px, py];
      highest = Math.min(highest, py);
    }
    strokeRoad(ctx, theme, "flight", s);
    ctx.restore();
    reach.arcs.push({ from: a, to: b, top: highest });
  }

  // The marks and their words. Where the road is short, the ones that
  // matter less are left out: the gem and START come before the studs.
  const free = (b) => taken.every((o) => b.r < o.l || b.l > o.r || b.b < o.t || b.t > o.b);
  const onRoad = (b) => {
    const wider = { l: b.l - 2 * s, t: b.t - 2 * s, r: b.r + 2 * s, b: b.b + 2 * s };
    return ink.some((segment) => crossesBox(segment[0], segment[1], segment[2], segment[3], wider));
  };
  const around = (px, py, r) => ({ l: px - r, r: px + r, t: py - r, b: py + r });
  const halo = (c) => {
    c.shadowColor = theme.light ? "rgba(255,250,236,0.95)" : rgba(theme.shade, 0.85);
    c.shadowBlur = 4 * s;
  };
  // A word beside a mark: to its right, else to its left, above or below -
  // inside the plate, on no other mark or word, and off the road if there
  // is such a place. `texts`: the word, and shorter ways of saying it.
  const tag = (id, texts, spec, size, colour, px, py, r, plate) => {
    const up = size * CAP;
    for (const clean of [true, false]) {
      for (const text of texts) {
        const wide = textWidth(ctx, text, spec, size, 0.08);
        const half = wide / 2;
        const spots = [
          [px + r + 4 * s + half, py + up / 2],
          [px - r - 4 * s - half, py + up / 2],
          [px, py - r - 6 * s],
          [px, py + r + 5 * s + up],
        ];
        for (const [x, y] of spots) {
          const b = { l: x - half - 2 * s, r: x + half + 2 * s, t: y - up - 2 * s, b: y + 3 * s };
          if (b.l < plate.x + 2 || b.r > plate.x + plate.w - 2 || b.t < plate.y + 2 || b.b > plate.y + plate.h - 2) continue;
          if (!free(b) || (clean && onRoad(b))) continue;
          if (!clean) {
            // No place off the road (it is wound tight all around): the
            // word gets a small plate of its own, so that it can be read.
            outline(ctx, "round", b.l, b.t, b.r - b.l, b.b - b.t, 3 * s);
            ctx.fillStyle = theme.light ? "rgba(255,250,236,0.88)" : rgba(theme.shade, 0.72);
            ctx.fill();
          }
          line(
            ctx,
            report,
            `route.${id}`,
            text,
            spec,
            { x, y, size, min: size, width: wide + 1, tracking: 0.08 },
            (c) => {
              c.fillStyle = colour;
              halo(c);
            },
            inner,
          );
          taken.push(b);
          if (!clean) reach.onRoad.push(id);
          return true;
        }
      }
    }
    return false;
  };

  // Where the character is now: a gem. Where the road begins: a ring
  // (none if that is the same place).
  const end = shown(route.end);
  const gemR = 10.5 * s;
  if (end) mark("gem", around(end[0], end[1], gemR * 1.15));
  let start = shown(route.start);
  const ringR = 6.2 * s;
  if (start && end && Math.hypot(start[0] - end[0], start[1] - end[1]) < gemR + ringR + 4 * s) start = null;
  if (start) mark("ring", around(start[0], start[1], ringR + 1.5 * s));
  const tagSize = 11.5 * s;
  if (end) {
    const now = route.level ? [`NOW · LV ${route.level}`, `LV ${route.level}`] : ["NOW"];
    tag("now", now, NUMBER, tagSize * 1.08, theme.value, end[0], end[1], gemR, place[route.end.c].plate);
  }
  if (start) tag("start", ["START"], LABEL, tagSize, theme.label, start[0], start[1], ringR, place[route.start.c].plate);
  // Every tenth level reached on the way: a stud with its number. (Not for
  // the level the character has now: the gem says that.)
  const numberSize = 13.5 * s;
  const studR = 5.6 * s;
  for (const milestone of route.milestones) {
    const spot = shown(milestone);
    if (!spot || milestone.level === route.level) continue;
    const b = around(spot[0], spot[1], studR + 1.5 * s);
    if (!free(b)) continue;
    ctx.save();
    diamond(ctx, spot[0], spot[1], studR, studR);
    ctx.fillStyle = theme.sub;
    ctx.fill();
    ctx.strokeStyle = theme.metalEdge;
    ctx.lineWidth = Math.max(1, 1.2 * s);
    ctx.stroke();
    ctx.restore();
    mark("stud", b);
    tag(`level${milestone.level}`, [String(milestone.level)], NUMBER, numberSize, theme.sub, spot[0], spot[1], studR, place[milestone.c].plate);
  }
  if (start) {
    ctx.save();
    ctx.beginPath();
    ctx.arc(start[0], start[1], ringR, 0, Math.PI * 2);
    ctx.fillStyle = theme.light ? "#fffaf0" : "#ffffff";
    ctx.fill();
    ctx.strokeStyle = theme.label;
    ctx.lineWidth = Math.max(1.4, 2.6 * s);
    ctx.stroke();
    ctx.restore();
  }
  if (end) gem(ctx, theme, end[0], end[1], gemR);

  // Which continent is which: its name under each plate.
  if (named) {
    for (const { land, plate } of plates) {
      if (!land.name) continue;
      line(
        ctx,
        report,
        `route.land${land.id}`,
        land.name.toUpperCase(),
        LABEL,
        { x: plate.x + plate.w / 2, y: namesY, size: nameSize, min: nameSize * 0.7, width: plate.w - 8 * s, tracking: 0.14 },
        (c) => (c.fillStyle = theme.muted),
        inner,
      );
    }
  }
}

// ---- header and footer ------------------------------------------------------------------------

function headerHeight(card, unit) {
  let height = 50 * unit; // the gem
  if (card.overline) height += 42 * unit;
  if (card.title) height += 86 * unit;
  if (card.subtitle) height += 44 * unit;
  return height;
}

function header(ctx, report, theme, card, area, unit) {
  const cx = area.x + area.w / 2;
  const width = area.w - 40 * unit;
  let y = area.y + 22 * unit;
  flourish(ctx, theme, cx, y, Math.min(250 * unit, area.w * 0.38), 16 * unit);
  y += 28 * unit;
  if (card.overline) {
    y += 42 * unit;
    const at = { x: cx, y: y - 10 * unit, size: 25 * unit, min: 13 * unit, width, tracking: 0.24 };
    line(ctx, report, "overline", card.overline.toUpperCase(), LABEL, at, (c) => (c.fillStyle = theme.label), null);
  }
  if (card.title) {
    y += 86 * unit;
    const at = { x: cx, y: y - 16 * unit, size: 78 * unit, min: 30 * unit, width, tracking: 0.03 };
    line(
      ctx,
      report,
      "title",
      card.title.toUpperCase(),
      TITLE,
      at,
      (c, fit) => {
        const fill = c.createLinearGradient(0, at.y - fit.size * 0.8, 0, at.y + fit.size * 0.1);
        fill.addColorStop(0, theme.title[0]);
        fill.addColorStop(1, theme.title[1]);
        c.fillStyle = fill;
        c.shadowColor = rgba(theme.shade, theme.light ? 0.22 : 0.7);
        c.shadowBlur = (theme.light ? 4 : 14) * unit;
        c.shadowOffsetY = (theme.light ? 1.5 : 4) * unit;
      },
      null,
    );
  }
  if (card.subtitle) {
    y += 44 * unit;
    const at = { x: cx, y: y - 8 * unit, size: 23 * unit, min: 12 * unit, width, tracking: 0.2 };
    line(ctx, report, "subtitle", card.subtitle.toUpperCase(), LABEL, at, (c) => (c.fillStyle = theme.sub), null);
  }
}

function footerHeight(card, unit) {
  return (card.footer ? 36 : 0) * unit + (card.tagline ? 50 : 0) * unit;
}

function footer(ctx, report, theme, card, area, unit) {
  const cx = area.x + area.w / 2;
  const width = area.w - 60 * unit;
  let y = area.y;
  if (card.footer) {
    y += 36 * unit;
    const at = { x: cx, y: y - 10 * unit, size: 18.5 * unit, min: 11 * unit, width: width - 80 * unit, tracking: 0.22 };
    const drawn = line(
      ctx,
      report,
      "footer",
      card.footer.toUpperCase(),
      LABEL,
      at,
      (c) => (c.fillStyle = theme.muted),
      null,
    );
    if (drawn) {
      const mid = at.y - drawn.size * 0.36;
      for (const side of [-1, 1]) {
        const x = cx + side * (drawn.width / 2 + 22 * unit);
        diamond(ctx, x, mid, 4.5 * unit, 4.5 * unit);
        ctx.fillStyle = theme.metal[1];
        ctx.fill();
      }
    }
  }
  if (card.tagline) {
    y += 50 * unit;
    const at = { x: cx, y: y - 12 * unit, size: 33 * unit, min: 16 * unit, width, tracking: 0 };
    line(
      ctx,
      report,
      "tagline",
      card.tagline,
      TAGLINE,
      at,
      (c) => {
        c.fillStyle = theme.light ? theme.value : theme.title[1];
        c.shadowColor = rgba(theme.shade, theme.light ? 0 : 0.6);
        c.shadowBlur = 8 * unit;
      },
      null,
    );
  }
}

// ---- layout -------------------------------------------------------------------------------------

/** The small tiles in rows: two to a row, a lone one gets the whole row. */
function rowsOf(tiles, perRow) {
  const rows = [];
  for (let i = 0; i < tiles.length; i += perRow) rows.push(tiles.slice(i, i + perRow));
  return rows;
}

function placeRows(rows, x, y, w, h, gap, out, weights) {
  const total = weights.reduce((a, b) => a + b, 0);
  const each = (h - gap * (rows.length - 1)) / total;
  let top = y;
  rows.forEach((row, i) => {
    const height = each * weights[i];
    const cell = (w - gap * (row.length - 1)) / row.length;
    const slots = {
      bar: row.some((item) => item.progress !== undefined),
      sub: row.some((item) => !!item.sub),
      note: row.some((item) => !!item.note),
    };
    row.forEach((item, j) =>
      out.push({ item, slots, hero: !!item.hero, box: { x: x + j * (cell + gap), y: top, w: cell, h: height } }),
    );
    top += height + gap;
  });
}

/**
 * The road beside the small tiles: a road runs north to south far more than
 * east to west, so it gets a tall panel on the left and the tiles stand in
 * a grid on its right - two abreast where there are more than two.
 */
function placeBlock(road, small, columns, x, y, w, h, gap, out) {
  const share = columns === 2 ? 0.43 : 0.54;
  const roadW = (w - gap) * share;
  out.push({ item: road, slots: {}, hero: false, box: { x, y, w: roadW, h } });
  const rows = rowsOf(small, columns);
  placeRows(
    rows,
    x + roadW + gap,
    y,
    w - roadW - gap,
    h,
    gap,
    out,
    rows.map(() => 1),
  );
}

/** Where everything goes, for a picture of W x H. */
function layout(W, H, card) {
  const hero = card.tiles.find((item) => item.hero) || null;
  const road = card.tiles.find((item) => item.wide) || null;
  const small = card.tiles.filter((item) => !item.hero && !item.wide);
  const landscape = W / H >= 1.3;
  const panels = [];

  if (landscape && (small.length || road)) {
    // Two columns: words and the hero on the left, the rest on the right.
    const unit = (H / 1080) * 1.12;
    const margin = 62 * unit;
    const gap = 22 * unit;
    const top = 58 * unit;
    const foot = footerHeight(card, unit);
    const bottom = H - 50 * unit - foot - 14 * unit;
    const leftW = (W - 2 * margin - gap) * (road && small.length ? 0.4 : 0.45);
    const rightX = margin + leftW + gap;
    const head = { x: margin, y: top, w: leftW, h: headerHeight(card, unit) };
    if (hero) {
      const y = head.y + head.h + 22 * unit;
      placeRows([[hero]], margin, y, leftW, bottom - y, gap, panels, [1]);
    } else {
      head.y = top + (bottom - top - head.h) / 2;
    }
    const right = { x: rightX, y: top + 6 * unit, w: W - margin - rightX, h: bottom - top - 6 * unit };
    if (road && small.length) {
      // (Here the tiles go one under the other: two abreast would be slivers.)
      placeBlock(road, small, 1, right.x, right.y, right.w, right.h, gap, panels);
    } else if (road) {
      panels.push({ item: road, slots: {}, hero: false, box: right });
    } else {
      const rows = small.length <= 2 ? rowsOf(small, 1) : rowsOf(small, 2);
      placeRows(
        rows,
        right.x,
        right.y,
        right.w,
        right.h,
        gap,
        panels,
        rows.map(() => 1),
      );
    }
    return {
      unit,
      panels,
      head,
      foot: { x: margin, y: bottom + 14 * unit, w: W - 2 * margin },
      focus: { x: margin + leftW / 2, y: head.y + head.h * 0.55 },
    };
  }

  const unit = landscape ? (H / 1080) * 1.12 : W / 1080;
  // A story is covered at the top and bottom by the app's own bars: keep clear of them.
  const safe = H / W > 1.6 ? 150 * unit : 0;
  const squeeze = clamp(H / W / 1.25, 0.8, 1.15); // a square has less room for words, a story more
  const margin = 58 * unit;
  const gap = 24 * unit;
  const top = 56 * unit + safe;
  const headUnit = unit * squeeze;
  const head = { x: margin, y: top, w: W - 2 * margin, h: headerHeight(card, headUnit) };
  const foot = footerHeight(card, unit);
  const bottom = H - 52 * unit - safe - foot;

  // Bands, top to bottom: the hero, then either rows of small tiles or -
  // with a road - the road beside them (or alone, across the picture).
  // A hero is a little taller than a row of two; a lone small tile a little
  // lower. Each band says how tall it would like to be and how to fill
  // the room it gets.
  const width = W - 2 * margin;
  const tall = H / W > 1.6;
  const bands = [];
  const rowBand = (row, natural) =>
    bands.push({
      natural,
      place: (y, h) => placeRows([row], margin, y, width, h, gap, panels, [1]),
    });
  if (hero) rowBand([hero], (road && !tall ? 262 : 300) * unit);
  if (road && small.length) {
    // The road beside the tiles. A story is tall enough for the tiles to
    // stand one under the other, which leaves the road a wide, tall panel -
    // the shape of a continent.
    const columns = small.length > 2 && !tall ? 2 : 1;
    const tileRows = Math.ceil(small.length / columns);
    // Enough for the tiles beside it, and never a squat road.
    const natural = tall
      ? Math.max(660, tileRows * 196 + (tileRows - 1) * 24)
      : Math.max(400, tileRows * 222 + (tileRows - 1) * 24);
    bands.push({
      natural: natural * unit,
      place: (y, h) => placeBlock(road, small, columns, margin, y, width, h, gap, panels),
    });
  } else {
    // No tiles to stand beside it: the road lies right across the picture.
    if (road)
      bands.push({
        natural: (tall ? 620 : 470) * unit,
        place: (y, h) => panels.push({ item: road, slots: {}, hero: false, box: { x: margin, y, w: width, h } }),
      });
    for (const row of rowsOf(small, 2)) rowBand(row, (row.length === 1 ? 215 : 250) * unit);
  }

  const y0 = head.y + head.h + 24 * unit;
  const room = bottom - 16 * unit - y0;
  const wanted = bands.reduce((sum, band) => sum + band.natural, 0);
  const spare = room - gap * (bands.length - 1);
  const scale = Math.min(1.45, spare / wanted);
  const used = wanted * scale + gap * (bands.length - 1);
  // Space left over (a story, or few tiles) is shared out: some above the
  // words, some either side of the panels, the rest below the closing line.
  const slack = Math.max(0, room - used);
  head.y += slack * 0.3;
  const panelsTop = y0 + slack * 0.5;
  let y = panelsTop;
  for (const band of bands) {
    const h = band.natural * scale;
    band.place(y, h);
    y += h + gap;
  }
  return {
    unit,
    headUnit,
    panels,
    head,
    foot: { x: margin, y: panelsTop + used + 16 * unit + slack * 0.2, w: W - 2 * margin },
    focus: { x: W / 2, y: head.y + head.h * 0.55 },
  };
}

/**
 * Draw `card` (from cardmodel.js: buildCard) on `ctx`, W x H pixels.
 * Returns {texts, panels}: where every text and panel ended up.
 */
export function drawCard(ctx, W, H, card, themeId) {
  const theme = THEMES.find((entry) => entry.id === themeId) || THEMES[0];
  const report = { width: W, height: H, theme: theme.id, texts: [], panels: [] };
  const plan = layout(W, H, card);
  ctx.save();
  backdrop(ctx, W, H, theme, plan.focus);
  border(ctx, report, W, H, theme, plan.unit, card.credit || "");
  header(ctx, report, theme, card, plan.head, plan.headUnit || plan.unit);
  for (const { item, hero, box, slots } of plan.panels) {
    panel(ctx, theme, box, plan.unit, hero);
    if (item.route) routeTile(ctx, report, theme, box, item, plan.unit);
    else tile(ctx, report, theme, box, item, plan.unit, hero, slots);
    report.panels.push({ id: item.id, ...box });
  }
  footer(ctx, report, theme, card, plan.foot, plan.unit);
  ctx.restore();
  return report;
}
