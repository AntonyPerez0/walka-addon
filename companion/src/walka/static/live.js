// The live view: belt state and speed, the session's numbers, manual control,
// the treadmill, the other links, the first-run checklist and the event log.

import {
  $,
  U,
  fmt,
  h,
  post,
  run,
  setAttr,
  setDisabled,
  setShown,
  setText,
  setTone,
  setTransform,
  toast,
} from "./util.js";
import { setupSteps, sourceLabel } from "./status.js";

const EPS = 1e-6;
const SETUP_KEY = "walka.setup.hidden";

let heroAction = null; // what the hero button does right now
let manualOverride = null; // {kmh, until}: an optimistic speed while the server catches up
let last = null; // last snapshot, for click handlers

function remember(key, value) {
  try {
    if (value === undefined) return localStorage.getItem(key);
    localStorage.setItem(key, value);
  } catch {
    /* private mode: the preference just does not stick */
  }
  return null;
}

// ---- hero -------------------------------------------------------------------------------

// Shown instead of a number when the belt's speed is not known (no treadmill,
// no companion). Never a zero: an unseen belt may well be moving.
const NO_SPEED = "--";

const BELT_WORDS = {
  unknown: "state unknown",
  stopped: "stopped",
  starting: "counting down",
  running: "running",
  paused: "paused",
};

function updateHero(s, view, online) {
  const hero = $("hero");
  setTone(hero, view.tone, `#i-${view.icon}`);
  setText($("hero-title"), view.title);
  setText($("hero-detail"), view.detail);

  const action = online ? view.action : null;
  heroAction = action ? action.kind : null;
  const button = $("hero-action");
  setShown(button, !!action);
  setShown($("hero-action-note"), !!(action && action.note));
  if (action) {
    setText(button, action.label);
    setText($("hero-action-note"), action.note || "");
  }

  setText($("speed-unit"), U.speedUnit);
  if (!online || !s) {
    setText($("speed-num"), NO_SPEED);
    setAttr($("speed-num"), "data-void", "1");
    setText($("hero-meta"), "");
    return;
  }

  const t = s.treadmill;
  const g = s.governor;
  const frame = s.source.frame;
  const manual = s.source.manual;
  const speedText = t.connected ? fmt.speed(t.speed_kmh) : NO_SPEED;
  setText($("speed-num"), speedText);
  setAttr($("speed-num"), "data-void", t.connected ? null : "1");
  setAttr($("speed-num"), "data-wide", speedText.length > 3 ? "1" : null); // "12.0": shrink to fit a phone
  setText($("hero-meta"), sourceLabel(s));

  // Readout.
  const steering = t.connected && g.driving && g.wanted_kmh > 0;
  setText($("ro-target"), steering ? fmt.speedU(g.wanted_kmh) : "-");
  let askLabel = "Asked for";
  let ask = "-";
  if (manual.active) {
    askLabel = "You set";
    ask = fmt.speedU(manual.speed_kmh);
  } else if (s.source.kind === "wow" && frame) {
    askLabel = "Game asks";
    ask = fmt.speedU(frame.target_kmh);
  }
  setText($("ro-ask-k"), askLabel);
  setText($("ro-ask"), ask);
  const beaconLive = s.source.beacon && s.source.beacon.state === "ok" && frame;
  setText($("ro-char"), beaconLive ? `${frame.char_speed_yps.toFixed(1)} yd/s` : "-");
  setText($("ro-belt"), t.connected ? BELT_WORDS[t.state] || t.state : "not connected");

  // Meter: 0 .. the pad's top speed. The tinted span is what you may use
  // (pad minimum .. your max speed), the fill is the belt, the tick the target.
  const scale = t.max_kmh > 0 ? t.max_kmh : 6;
  const top = Math.min(t.cap_kmh, scale);
  const minAt = Math.min(1, t.min_kmh / scale);
  const capAt = Math.min(1, top / scale);
  const speedAt = t.connected ? Math.max(0, Math.min(1, t.speed_kmh / scale)) : 0;
  setTransform(
    $("meter-range"),
    `translateX(${(minAt * 100).toFixed(2)}%) scaleX(${Math.max(0, capAt - minAt).toFixed(4)})`,
  );
  setTransform($("meter-fill"), `scaleX(${speedAt.toFixed(4)})`);
  const target = $("meter-target");
  setShown(target, steering);
  if (steering) setTransform(target, `translateX(${(Math.min(1, g.wanted_kmh / scale) * 100).toFixed(2)}%)`);

  const capLine = $("meter-cap-line");
  setShown(capLine, capAt < 0.995);
  setTransform(capLine, `translateX(${(capAt * 100).toFixed(2)}%)`);
  const capIsEnd = capAt > 0.88;
  setTransform($("meter-min-at"), `translateX(${(minAt * 100).toFixed(2)}%)`);
  setText($("meter-min"), fmt.speed(t.min_kmh));
  setShown($("meter-cap-at"), !capIsEnd);
  setTransform($("meter-cap-at"), `translateX(${(capAt * 100).toFixed(2)}%)`);
  setText($("meter-cap"), `${fmt.speed(top)} max`);
  setText($("meter-max"), capIsEnd ? `${fmt.speed(top)} ${U.speedUnit} max` : `${fmt.speed(scale)} ${U.speedUnit}`);
  setAttr(
    $("meter"),
    "aria-label",
    `Belt at ${fmt.speedU(t.connected ? t.speed_kmh : 0)}; your maximum is ${fmt.speedU(top)}` +
      (steering ? `; heading for ${fmt.speedU(g.wanted_kmh)}` : ""),
  );
}

// ---- session ------------------------------------------------------------------------------

const SESSION_TITLE = { wow: "World of Warcraft session", manual: "Manual session", free: "Free walk" };

function tile(n, label, value, unit, source) {
  setText($(`t${n}-k`), label);
  setText($(`t${n}-v`), value);
  const unitEl = $(`t${n}-u`);
  if (unitEl) setText(unitEl, unit || "");
  setText($(`t${n}-s`), source || "");
}

function updateSession(s) {
  const live = s.session;
  setShown($("howto"), !live);
  if (live) {
    setText($("session-title"), SESSION_TITLE[live.source] || "Session");
    setText($("session-sub"), `since ${fmt.timeOfDay(live.started_at)}`);
    tile(1, "Time", fmt.clock(live.seconds), "", "belt moving");
    tile(
      2,
      "Distance",
      fmt.dist(live.distance_m),
      U.distUnit,
      live.distance_source === "treadmill" ? "treadmill counter" : "from belt speed",
    );
    tile(
      3,
      "Steps",
      fmt.int(live.steps),
      "",
      live.steps_source === "treadmill" ? "treadmill counter" : "estimate: speed and height",
    );
    tile(
      4,
      "Calories",
      fmt.int(live.kcal),
      "",
      (live.calories_source === "heart_rate" ? "from heart rate" : "estimate: speed and weight") +
        (live.kcal_extra >= 1 ? ` · ${fmt.int(live.kcal_extra)} more than sitting` : ""),
    );
    const hrNote = live.avg_hr
      ? `avg ${Math.round(live.avg_hr)}, max ${live.max_hr || "-"}`
      : live.hr
        ? "heart-rate sensor"
        : "no sensor";
    tile(5, "Heart rate", live.hr ? String(live.hr) : "-", live.hr ? "bpm" : "", hrNote);
    const avg = live.seconds > 0 ? (live.distance_m / live.seconds) * 3.6 : null;
    const avgUnit = avg == null ? "" : U.speedUnit;
    tile(6, "Average speed", avg == null ? "-" : fmt.speed(avg), avgUnit, `max ${fmt.speedU(live.max_kmh)}`);
    return;
  }
  const today = s.totals.today;
  setText($("session-title"), "Today");
  setText($("session-sub"), "no session running");
  tile(1, "Time", fmt.clock(today.seconds), "", "");
  tile(2, "Distance", fmt.dist(today.distance_m), U.distUnit, "");
  tile(3, "Steps", fmt.int(today.steps), "", "");
  tile(4, "Calories", fmt.int(today.kcal), "", "");
  tile(5, "Walks", fmt.int(today.sessions), "", "today");
  tile(6, "Streak", fmt.int(s.totals.streak), s.totals.streak === 1 ? "day" : "days", "days in a row with a walk");
  const lastWalk = s.last_session;
  setShown($("last-walk"), !!lastWalk);
  if (lastWalk) {
    setText(
      $("last-walk"),
      `Last walk: ${fmt.span(lastWalk.seconds)}, ${fmt.distU(lastWalk.distance_m)}, ${fmt.int(lastWalk.steps)} steps, ${fmt.int(lastWalk.kcal)} kcal.`,
    );
  }
}

// ---- manual control ---------------------------------------------------------------------------

function manualBounds(t) {
  const hi = Math.min(t.cap_kmh, t.max_kmh);
  return { lo: Math.min(t.min_kmh, hi), hi };
}

function manualSpeed(s) {
  const { lo, hi } = manualBounds(s.treadmill);
  const now = performance.now();
  if (manualOverride && now > manualOverride.until) manualOverride = null;
  const kmh = manualOverride ? manualOverride.kmh : s.source.manual.speed_kmh;
  return Math.max(lo, Math.min(hi, kmh));
}

function updateManual(s) {
  const m = s.source.manual;
  const t = s.treadmill;
  const { lo, hi } = manualBounds(t);
  const kmh = manualSpeed(s);
  const unit = U.speedUnit;

  setAttr($("manual"), "data-active", m.active ? "true" : "false");
  setShown($("manual-badge"), m.active);
  setText(
    $("manual-intro"),
    m.active
      ? "Manual mode is on: the belt holds this speed, and the game signal is ignored until you end the session."
      : "For any other game, or no game at all: you set the speed here.",
  );
  setText($("manual-speed-num"), fmt.speed(kmh));
  setText($("manual-speed-unit"), unit);
  setDisabled($("manual-minus"), kmh <= lo + EPS);
  setDisabled($("manual-plus"), kmh >= hi - EPS);
  setAttr($("manual-minus"), "aria-label", `Slower by 0.1 ${unit}`);
  setAttr($("manual-plus"), "aria-label", `Faster by 0.1 ${unit}`);

  let note;
  if (!t.connected) note = "Connect the treadmill to start a manual session.";
  else if (!m.active)
    note = `Starts the belt after the pad's 3-2-1 countdown, then holds ${fmt.speedU(kmh)}. Overrides the game while it runs.`;
  else if (m.paused) note = "Paused. Resume to carry on, or end the session.";
  else note = `Allowed range ${fmt.speed(lo)}-${fmt.speedU(hi)}. End session, or STOP, stops the belt.`;
  setText($("manual-note"), note);

  setShown($("manual-start"), !m.active);
  setDisabled($("manual-start"), !t.connected);
  setShown($("manual-pause"), m.active && !m.paused);
  setShown($("manual-resume"), m.active && m.paused);
  setShown($("manual-stop"), m.active);
}

function nudgeManual(direction) {
  if (!last) return;
  const { lo, hi } = manualBounds(last.treadmill);
  const current = manualSpeed(last);
  // Step on the display grid (0.1 mph or 0.1 km/h); the governor then snaps
  // to whatever grid the pad's firmware really has.
  const shown = Math.round(U.speed(current) * 10) / 10;
  const next = Math.max(lo, Math.min(hi, U.toKmh(shown + direction * 0.1)));
  if (Math.abs(next - current) < EPS) return;
  manualOverride = { kmh: next, until: performance.now() + 1500 };
  updateManual(last);
  post("/api/manual", { action: "speed", speed_kmh: +next.toFixed(6) }).catch((error) => {
    manualOverride = null;
    toast(error.message, "error");
  });
}

// ---- treadmill ----------------------------------------------------------------------------------

function trimNumber(value, digits) {
  return String(+value.toFixed(digits));
}

function updateTreadmill(s, link) {
  const t = s.treadmill;
  setTone($("treadmill"), link.tone);
  setText($("tm-state"), link.state);
  setText($("tm-detail"), link.detail);

  const known = !!t.name;
  setText($("tm-name"), known ? t.name : "-");
  setAttr($("tm-name"), "title", known && t.address ? `${t.name} (${t.address})` : null);
  setText($("tm-range"), known ? `${fmt.speed(t.min_kmh)}-${fmt.speedU(t.max_kmh)}` : "-");
  setText($("tm-step"), known ? `${trimNumber(U.speed(t.step_kmh), 2)} ${U.speedUnit}` : "-");
  setText($("tm-grid"), known ? (t.mph_grid ? "mph grid" : "km/h grid") : "-");

  const searching = !t.connected && t.want_connected;
  const connect = $("tm-connect");
  setShown(connect, !t.connected);
  setText(connect, searching ? "Retry now" : "Connect");
  const disconnect = $("tm-disconnect");
  setShown(disconnect, t.connected || searching);
  setText(disconnect, t.connected ? "Disconnect" : "Stop searching");
  setShown($("tm-sim"), t.kind === "simulated");
}

function signalWord(rssi) {
  if (rssi == null) return "";
  return rssi >= -60 ? "strong signal" : rssi >= -75 ? "good signal" : "weak signal";
}

function deviceRow(device, onPick, pickLabel) {
  const details = [device.address, signalWord(device.rssi)];
  if (device.ftms === true) details.push("treadmill service");
  else if (device.ftms === false) details.push("matched by name");
  const button = h("button", { class: "btn small", type: "button", text: pickLabel });
  button.addEventListener("click", () => onPick(device, button));
  return h(
    "div",
    { class: "device" },
    h(
      "div",
      { class: "device-text" },
      h("strong", { text: device.name || "(no name)" }),
      h("span", { text: details.filter(Boolean).join(" · ") }),
    ),
    button,
  );
}

/** Shared by the treadmill card and the heart-rate settings. */
export async function scanInto(out, button, { path, empty, onPick, pickLabel }) {
  if (button.dataset.busy === "1") return;
  button.dataset.busy = "1";
  out.replaceChildren(h("p", { class: "muted", text: "Scanning for about 6 seconds..." }));
  try {
    const data = await post(path, {}, { timeoutMs: 40000 });
    const devices = data.devices || [];
    if (!devices.length) out.replaceChildren(h("p", { class: "muted", text: empty() }));
    else out.replaceChildren(...devices.map((device) => deviceRow(device, onPick, pickLabel)));
  } catch (error) {
    out.replaceChildren(h("p", { class: "error", role: "alert", text: error.message }));
  } finally {
    delete button.dataset.busy;
  }
}

function scanTreadmills() {
  scanInto($("tm-scan-out"), $("tm-scan"), {
    path: "/api/treadmill/scan",
    pickLabel: "Connect",
    empty: () =>
      last && last.treadmill.kind === "simulated"
        ? "Nothing to scan for: this is the simulated treadmill. Start the companion without --simulate or --demo to look for a real pad."
        : "No treadmill found. Is the pad switched on and in range, and is the UREVO phone app closed? A pad that is already connected - here or to a phone - does not show up.",
    onPick: (device, button) =>
      run(button, () => post("/api/treadmill/connect", { address: device.address }), {
        success: `Connecting to ${device.name || device.address}.`,
      }),
  });
}

// ---- other links ------------------------------------------------------------------------------------

const FLAG_HELP = {
  ACTIVE: "A walking session is running in game",
  PAUSED: "The session is paused in game",
  MOVING: "The character is moving",
  MOUNTED: "On a mount",
  TAXI: "On a flight path",
  SWIMMING: "Swimming",
  FLYING: "Flying",
  DEAD: "Dead or a ghost",
  COMBAT: "In combat",
  AFK: "Away from keyboard",
  FALLING: "Falling",
  RESTING: "Resting (inn or city)",
  ESTIMATED: "The character's speed is an estimate - the game hides it from addons in combat",
};

let flagsKey = null;

function updateLinks(s, links) {
  // Game signal.
  const game = links.game;
  const beacon = s.source.beacon;
  const frame = s.source.frame;
  setTone($("link-game"), game.ignored && game.tone === "ok" ? "idle" : game.tone);
  setText($("game-state"), game.ignored ? `${game.pill}, ignored` : game.state);
  let detail = game.detail;
  if (beacon && beacon.state === "ok" && frame && !game.ignored) {
    detail = frame.active
      ? `Session ${frame.session}${frame.paused ? " (paused)" : ""}: character at ${frame.char_speed_yps.toFixed(1)} yd/s, asking the belt for ${fmt.speedU(frame.target_kmh)}.`
      : "The addon is visible. No session is running in game - type /walka start there.";
  }
  setText($("game-detail"), detail);
  const showFlags = beacon && frame && (beacon.state === "ok" || beacon.state === "frozen");
  const key = showFlags ? frame.flags.join(",") : "";
  if (key !== flagsKey) {
    flagsKey = key;
    $("game-flags").replaceChildren(
      ...(showFlags ? frame.flags : []).map((flag) =>
        h("span", { class: flag === "ESTIMATED" ? "chip note" : "chip", title: FLAG_HELP[flag] || null, text: flag }),
      ),
    );
  }

  // Heart rate.
  const hr = links.hr;
  setTone($("link-hr"), hr.tone);
  setText($("hr-state"), hr.state);
  setText($("hr-detail"), hr.detail);
  setShown($("hr-setup"), hr.available);
  setText($("hr-setup"), s.heart_rate && s.heart_rate.enabled ? "Change sensor" : "Set up");

  // Addon.
  const addon = links.addon;
  setTone($("link-addon"), addon.tone);
  setText($("addon-state"), addon.state);
  setText($("addon-detail"), addon.detail);
  setShown($("addon-sync"), addon.installed);
  setShown($("addon-install"), !addon.installed);
}

export function installAddon(button, addonsDir) {
  return run(button, async () => {
    try {
      const data = await post("/api/addon/install", addonsDir ? { addons_dir: addonsDir } : {});
      toast(
        `Addon installed to ${data.path}. Restart the game (or type /reload) and tick Walka in the AddOns list.`,
        "ok",
      );
      return data;
    } catch (error) {
      if (!addonsDir && !location.hash.startsWith("#settings")) location.hash = "#settings/set-addon";
      throw error;
    }
  });
}

export function syncAddon(button) {
  return run(button, () => post("/api/addon/sync"), {
    success: "History synced to the addon. Type /reload in game to load it.",
  });
}

// ---- activity log -----------------------------------------------------------------------------------

let logKey = null;
let newestEvent = null; // time of the newest event already seen

function updateLog(s) {
  const events = s.events || [];
  const key = events.length ? `${events.length}|${events[0].time}` : "";
  if (key === logKey) return;
  logKey = key;
  // An error that happened while you were looking elsewhere (a stop command
  // that failed, say) must not sit quietly in a list: raise it.
  if (newestEvent !== null) {
    for (const event of events) {
      if (event.time <= newestEvent) break;
      if (event.level === "error") toast(event.text, "error", { sticky: true });
    }
  }
  newestEvent = events.length ? events[0].time : 0;
  setShown($("log-empty"), !events.length);
  setShown($("log"), !!events.length);
  $("log").replaceChildren(
    ...events.map((event) =>
      h(
        "li",
        { "data-level": event.level },
        h("time", { text: fmt.timeOfDaySec(event.time) }),
        h("span", { text: event.text }),
      ),
    ),
  );
}

// ---- first-run checklist ---------------------------------------------------------------------------

function updateSetup(s) {
  const steps = setupSteps(s);
  const names = Object.keys(steps);
  const done = names.filter((name) => steps[name]).length;
  if (done === names.length) remember(SETUP_KEY, "1"); // finished once: stay out of the way
  const hidden = remember(SETUP_KEY) === "1";
  setShown($("setup"), !hidden);
  if (hidden) return;
  for (const name of names) setAttr($(`step-${name}`), "data-done", steps[name] ? "true" : "false");
  setText($("setup-count"), `${done} of ${names.length} done`);
}

// ---- wiring ----------------------------------------------------------------------------------------------

export function initLive() {
  const manual = (action, extra) => post("/api/manual", { action, ...extra });

  $("hero-action").addEventListener("click", (event) => {
    const button = event.currentTarget;
    if (heroAction === "resume") run(button, () => post("/api/resume"));
    else if (heroAction === "manual-resume") run(button, () => manual("resume"));
    else if (heroAction === "connect") run(button, () => post("/api/treadmill/connect", {}));
    else if (heroAction === "limits") location.hash = "#settings/set-limits";
  });

  $("manual-minus").addEventListener("click", () => nudgeManual(-1));
  $("manual-plus").addEventListener("click", () => nudgeManual(1));
  $("manual-start").addEventListener("click", (event) =>
    run(event.currentTarget, () => manual("start", last ? { speed_kmh: +manualSpeed(last).toFixed(6) } : {})),
  );
  $("manual-pause").addEventListener("click", (event) => run(event.currentTarget, () => manual("pause")));
  $("manual-resume").addEventListener("click", (event) => run(event.currentTarget, () => manual("resume")));
  $("manual-stop").addEventListener("click", (event) => run(event.currentTarget, () => manual("stop")));

  $("tm-scan").addEventListener("click", scanTreadmills);
  $("tm-connect").addEventListener("click", (event) =>
    run(event.currentTarget, () => post("/api/treadmill/connect", {})),
  );
  $("tm-disconnect").addEventListener("click", (event) => {
    // Cutting the link to a moving belt would leave it running with nobody steering.
    if (last && last.treadmill.connected && last.treadmill.speed_kmh > 0.05) {
      toast("Stop the belt first: disconnecting now would leave it running without Walka.", "warn");
      return;
    }
    run(event.currentTarget, () => post("/api/treadmill/disconnect"));
  });
  $("sim-start").addEventListener("click", (event) =>
    run(event.currentTarget, () => post("/api/treadmill/press", { button: "start" })),
  );
  $("sim-stop").addEventListener("click", (event) =>
    run(event.currentTarget, () => post("/api/treadmill/press", { button: "stop" })),
  );

  $("addon-sync").addEventListener("click", (event) => syncAddon(event.currentTarget));
  $("addon-install").addEventListener("click", (event) => installAddon(event.currentTarget));
  $("setup-install").addEventListener("click", (event) => installAddon(event.currentTarget));
  $("setup-hide").addEventListener("click", () => {
    remember(SETUP_KEY, "1");
    $("setup").hidden = true;
  });
}

/** view: describe(s) or OFFLINE; links: health(s). */
export function updateLive(s, view, links, online) {
  last = s;
  updateHero(s, view, online);
  if (!s) return;
  updateSession(s);
  updateManual(s);
  updateTreadmill(s, links.treadmill);
  updateLinks(s, links);
  updateLog(s);
  updateSetup(s);
}
