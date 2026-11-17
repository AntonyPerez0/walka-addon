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

// Fonts that ship with Windows first (the companion is a Windows app), then
// the usual ones elsewhere. Sizes are measured, never assumed, so a fallback
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

/** Where everything goes, for a picture of W x H. */
function layout(W, H, card) {
  const hero = card.tiles.find((item) => item.hero) || null;
  const small = card.tiles.filter((item) => !item.hero);
  const landscape = W / H >= 1.3;
  const panels = [];

  if (landscape && small.length) {
    // Two columns: words and the hero on the left, the small tiles on the right.
    const unit = (H / 1080) * 1.12;
    const margin = 62 * unit;
    const gap = 22 * unit;
    const top = 58 * unit;
    const foot = footerHeight(card, unit);
    const bottom = H - 50 * unit - foot - 14 * unit;
    const leftW = (W - 2 * margin - gap) * 0.45;
    const rightX = margin + leftW + gap;
    const head = { x: margin, y: top, w: leftW, h: headerHeight(card, unit) };
    if (hero) {
      const y = head.y + head.h + 22 * unit;
      placeRows([[hero]], margin, y, leftW, bottom - y, gap, panels, [1]);
    } else {
      head.y = top + (bottom - top - head.h) / 2;
    }
    const rows = small.length <= 2 ? rowsOf(small, 1) : rowsOf(small, 2);
    placeRows(
      rows,
      rightX,
      top + 6 * unit,
      W - margin - rightX,
      bottom - top - 6 * unit,
      gap,
      panels,
      rows.map(() => 1),
    );
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

  const rows = [...(hero ? [[hero]] : []), ...rowsOf(small, 2)];
  // A hero is a little taller than a row of two; a lone small tile a little lower.
  const natural = rows.map((row) => (row[0].hero ? 300 : row.length === 1 ? 215 : 250) * unit);
  const y0 = head.y + head.h + 24 * unit;
  const room = bottom - 16 * unit - y0;
  const wanted = natural.reduce((a, b) => a + b, 0);
  const spare = room - gap * (rows.length - 1);
  const scale = Math.min(1.45, spare / wanted);
  const used = wanted * scale + gap * (rows.length - 1);
  // Space left over (a story, or few tiles) is shared out: some above the
  // words, some either side of the panels, the rest below the closing line.
  const slack = Math.max(0, room - used);
  head.y += slack * 0.3;
  const panelsTop = y0 + slack * 0.5;
  placeRows(rows, margin, panelsTop, W - 2 * margin, used, gap, panels, natural);
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
    tile(ctx, report, theme, box, item, plan.unit, hero, slots);
    report.panels.push({ id: item.id, ...box });
  }
  footer(ctx, report, theme, card, plan.foot, plan.unit);
  ctx.restore();
  return report;
}
