// Shared helpers: cheap DOM writes, units, formatting, the JSON API, toasts.
//
// The backend is always metric (km/h, metres, kg, cm, seconds). Everything the
// user sees goes through `U`, which follows snapshot.units.

export const KMH_PER_MPH = 1.609344;
export const M_PER_MILE = 1609.344;
export const LB_PER_KG = 2.2046226218;
export const CM_PER_INCH = 2.54;

/** Page-wide state. `units` mirrors the companion's config. */
export const state = {
  units: "mph",
  snapshot: null,
  online: false,
};

export const $ = (id) => document.getElementById(id);

// ---- cheap DOM writes ----------------------------------------------------------
// The page gets a full snapshot twice a second. Every write goes through these
// setters, which remember the last value per element and touch the DOM only
// when it actually changed - so an unchanged snapshot costs no layout at all.

const memo = new WeakMap();

function changed(el, key, value) {
  let seen = memo.get(el);
  if (!seen) {
    seen = {};
    memo.set(el, seen);
  }
  if (seen[key] === value) return false;
  seen[key] = value;
  return true;
}

export function setText(el, value) {
  const text = value == null ? "" : String(value);
  if (changed(el, "text", text)) el.textContent = text;
}

export function setShown(el, shown) {
  if (changed(el, "shown", !!shown)) el.hidden = !shown;
}

export function setAttr(el, name, value) {
  const text = value == null ? null : String(value);
  if (!changed(el, `@${name}`, text)) return;
  if (text === null) el.removeAttribute(name);
  else el.setAttribute(name, text);
}

export function setDisabled(el, disabled) {
  if (changed(el, "disabled", !!disabled)) el.disabled = !!disabled;
}

export function setTransform(el, value) {
  if (changed(el, "transform", value)) el.style.transform = value;
}

/** Set the tone of a status container and swap its lamp icon to match. */
const TONE_ICON = { ok: "#i-check", warn: "#i-warn", bad: "#i-x", idle: "#i-off" };

export function setTone(container, tone, icon) {
  setAttr(container, "data-tone", tone);
  const use = container.querySelector(".lamp use");
  if (use) setAttr(use, "href", icon || TONE_ICON[tone] || TONE_ICON.idle);
}

/** Build an element: h("button", {class: "btn", onclick: fn}, "text", child). */
export function h(tag, props, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value == null || value === false) continue;
    if (key === "class") el.className = value;
    else if (key === "text") el.textContent = value;
    else if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
    else if (key in el && typeof value !== "string") el[key] = value;
    else el.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child == null || child === false) continue;
    el.append(child.nodeType ? child : document.createTextNode(String(child)));
  }
  return el;
}

export function icon(name, size = 16) {
  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("width", size);
  svg.setAttribute("height", size);
  const use = document.createElementNS(NS, "use");
  use.setAttribute("href", `#i-${name}`);
  svg.append(use);
  return svg;
}

// ---- units --------------------------------------------------------------------

export const U = {
  get mph() {
    return state.units === "mph";
  },
  get speedUnit() {
    return this.mph ? "mph" : "km/h";
  },
  get distUnit() {
    return this.mph ? "mi" : "km";
  },
  get weightUnit() {
    return this.mph ? "lb" : "kg";
  },
  /** km/h -> display speed (number). */
  speed(kmh) {
    return this.mph ? kmh / KMH_PER_MPH : kmh;
  },
  /** display speed -> km/h. */
  toKmh(value) {
    return this.mph ? value * KMH_PER_MPH : value;
  },
  /** metres -> display distance (number). */
  dist(m) {
    return this.mph ? m / M_PER_MILE : m / 1000;
  },
  weight(kg) {
    return this.mph ? kg * LB_PER_KG : kg;
  },
  toKg(value) {
    return this.mph ? value / LB_PER_KG : value;
  },
};

const intFormat = new Intl.NumberFormat();
const oneDecimal = new Intl.NumberFormat(undefined, { minimumFractionDigits: 1, maximumFractionDigits: 1 });

export const fmt = {
  /** "2.4" - belt speeds live on a 0.1 grid in either unit. */
  speed(kmh, digits = 1) {
    if (kmh == null || !Number.isFinite(kmh)) return "-";
    const value = U.speed(kmh);
    return (Math.abs(value) < 0.05 && digits === 1 ? 0 : value).toFixed(digits);
  },
  speedU(kmh, digits = 1) {
    return `${fmt.speed(kmh, digits)} ${U.speedUnit}`;
  },
  /** "0.92" (two decimals under 100, one above). */
  dist(m) {
    if (m == null || !Number.isFinite(m)) return "-";
    const value = U.dist(m);
    if (value >= 1000) return oneDecimal.format(value); // "4,756.2": grouped like the steps beside it
    return value >= 100 ? value.toFixed(1) : value.toFixed(2);
  },
  distU(m) {
    return `${fmt.dist(m)} ${U.distUnit}`;
  },
  int(n) {
    return n == null || !Number.isFinite(n) ? "-" : intFormat.format(Math.round(n));
  },
  /** Seconds -> "4:07" or "1:02:45". */
  clock(seconds) {
    if (seconds == null || !Number.isFinite(seconds)) return "-";
    const total = Math.max(0, Math.floor(seconds));
    const hrs = Math.floor(total / 3600);
    const min = Math.floor((total % 3600) / 60);
    const sec = total % 60;
    const two = (n) => String(n).padStart(2, "0");
    return hrs ? `${hrs}:${two(min)}:${two(sec)}` : `${min}:${two(sec)}`;
  },
  /** Seconds -> "42 min" or "1 h 05 min" (for totals, where seconds are noise). */
  span(seconds) {
    if (seconds == null || !Number.isFinite(seconds)) return "-";
    const minutes = Math.round(seconds / 60);
    if (minutes < 60) return `${minutes} min`;
    return `${intFormat.format(Math.floor(minutes / 60))} h ${String(minutes % 60).padStart(2, "0")} min`;
  },
  /** How long ago, coarse on purpose so the text changes rarely. */
  ago(seconds) {
    if (seconds == null || !Number.isFinite(seconds)) return "never";
    if (seconds < 50) return "just now";
    if (seconds < 3600) return `${Math.max(1, Math.round(seconds / 60))} min ago`;
    if (seconds < 86400) return `${Math.round(seconds / 3600)} h ago`;
    return `${Math.round(seconds / 86400)} d ago`;
  },
  timeOfDay(epoch) {
    return new Date(epoch * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  },
  timeOfDaySec(epoch) {
    return new Date(epoch * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  },
  dateTime(epoch) {
    return new Date(epoch * 1000).toLocaleString([], {
      weekday: "short",
      day: "numeric",
      month: "short",
      hour: "2-digit",
      minute: "2-digit",
    });
  },
};

export function capitalise(text) {
  const value = String(text || "").trim();
  return value ? value[0].toUpperCase() + value.slice(1) : "";
}

/** A sentence from a backend detail string: capitalised, with a full stop. */
export function sentence(text) {
  const value = capitalise(text);
  return value && !/[.!?]$/.test(value) ? `${value}.` : value;
}

// ---- the JSON API ----------------------------------------------------------------
// The companion refuses any state-changing request that is not
// application/json, so every POST/DELETE carries a JSON body.

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

export async function api(path, { method = "GET", body, timeoutMs = 10000 } = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  const init = { method, cache: "no-store", signal: controller.signal, headers: {} };
  if (method !== "GET") {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body ?? {});
  }
  let response;
  try {
    response = await fetch(path, init);
  } catch (error) {
    throw new ApiError(
      error.name === "AbortError" ? "The companion did not answer in time." : "Could not reach the companion.",
      0,
    );
  } finally {
    clearTimeout(timer);
  }
  const text = await response.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = null;
  }
  if (!response.ok || (data && data.ok === false)) {
    const message =
      (data && data.error) || (data ? "" : text.replace(/^\d{3}:\s*/, "")) || `Request failed (${response.status}).`;
    throw new ApiError(sentence(message), response.status);
  }
  return data ?? {};
}

export const post = (path, body, options) => api(path, { ...options, method: "POST", body });
export const del = (path) => api(path, { method: "DELETE", body: {} });

/** Run an async action from a button: busy state, and errors as a toast. */
export async function run(button, action, { success, quiet } = {}) {
  if (button && button.dataset.busy === "1") return undefined;
  if (button) button.dataset.busy = "1";
  try {
    const result = await action();
    if (success) toast(success, "ok");
    return result;
  } catch (error) {
    if (!quiet) toast(error.message || "Something went wrong.", "error");
    return undefined;
  } finally {
    if (button) delete button.dataset.busy;
  }
}

// ---- toasts ------------------------------------------------------------------

// A notice says what kind it is with a shape as well as a colour.
const TOAST_ICON = { info: "note", ok: "check", warn: "warn", error: "x" };

export function toast(message, kind = "info", { sticky = false } = {}) {
  const host = $("toasts");
  const dismiss = h("button", { type: "button", "aria-label": "Dismiss" }, icon("x", 14));
  const lamp = h("span", { class: "lamp" }, icon(TOAST_ICON[kind] || TOAST_ICON.info, 14));
  const item = h(
    "div",
    { class: "toast", "data-kind": kind, "data-sticky": sticky ? "1" : null, role: kind === "error" ? "alert" : "status" },
    lamp,
    h("p", { text: message }),
    dismiss,
  );
  const remove = () => item.remove();
  dismiss.addEventListener("click", remove);
  // Keep the stack short: the newest three are enough. One that stays until dismissed
  // (a failed STOP) is the last to make room.
  while (host.children.length >= 3) {
    const passing = [...host.children].find((child) => child.dataset.sticky !== "1");
    (passing || host.firstElementChild).remove();
  }
  host.append(item);
  // If the stack is taller than its room: keep in view the one that stays until dismissed
  // (a failed STOP), else the newest.
  const pinned = host.querySelector('[data-sticky="1"]');
  host.scrollTop = pinned ? pinned.offsetTop - host.firstElementChild.offsetTop : host.scrollHeight;
  if (!sticky) setTimeout(remove, kind === "error" ? 9000 : 4500);
  return item;
}

// ---- shared tooltip (charts) ----------------------------------------------------

export const tooltip = {
  /** rows: [{value, label, key}] - values lead, labels follow. */
  show(x, y, rows) {
    const el = $("tooltip");
    el.replaceChildren(
      ...rows.map((row) =>
        h(
          "div",
          { class: "tt-row" },
          row.key ? h("span", { class: `tt-key ${row.key}` }) : null,
          h("strong", { text: row.value }),
          h("span", { class: "tt-label", text: row.label }),
        ),
      ),
    );
    el.hidden = false;
    const box = el.getBoundingClientRect();
    const left = Math.min(Math.max(8, x + 14), window.innerWidth - box.width - 8);
    // Above the pointer, unless that is off the window or over STOP and the belt's state
    // (fixed top right; a bar at the bottom on a phone): those are never covered.
    const dockEl = document.querySelector(".stopdock");
    const dock = dockEl ? dockEl.getBoundingClientRect() : null;
    const covers = (t) =>
      !!dock && left < dock.right && left + box.width > dock.left && t < dock.bottom && t + box.height > dock.top;
    let top = y - box.height - 12;
    if (top < 8 || covers(top)) top = y + 18;
    if (covers(top)) top = dock.top - box.height - 8;
    el.style.transform = `translate(${Math.round(left)}px, ${Math.round(top)}px)`;
  },
  hide() {
    $("tooltip").hidden = true;
  },
};
