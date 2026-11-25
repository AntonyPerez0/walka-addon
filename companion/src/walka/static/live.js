// The live view: what the belt is doing and its speed, the walk's numbers,
// the treadmill, the other links, the first-run checklist and the event log.

import { $, U, fmt, h, post, run, setAttr, setShown, setText, setTone, setTransform, toast } from "./util.js";
import { SOURCE_LABEL, setupSteps, sourceLabel } from "./status.js";
import { t, tr } from "./i18n.js";

const SETUP_KEY = "walka.setup.hidden";

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
const NO_SPEED = "No reading"; // said in words: a dash at this size reads as a glitch

// The character in the game right now, as the addon's journey numbers say:
// "Name · level 42". (Its speed in the game is no business of the belt's.)
function characterText(character) {
  if (!character) return "-";
  const name = character.name || "Your character";
  return character.level ? `${name} · level ${character.level}` : name;
}

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

  // The one thing to press: have the companion look for the treadmill.
  const action = online ? view.action : null;
  const button = $("hero-action");
  setShown(button, !!action);
  if (action) setText(button, action.label);

  setText($("speed-unit"), U.speedUnit);
  if (!online || !s) {
    // Nothing here may look live: the last readings are old, so they go.
    setText($("speed-num"), NO_SPEED);
    setAttr($("speed-num"), "data-void", "1");
    setAttr($("speed-num"), "data-wide", null);
    setText($("hero-meta"), "");
    for (const id of ["ro-set", "ro-game", "ro-char"]) setText($(id), "-");
    setText($("ro-belt"), s ? "unknown" : "-");
    setTransform($("meter-fill"), "scaleX(0)");
    setText($("meter-max"), "");
    return;
  }

  const t = s.treadmill;
  const frame = s.source.frame;
  const speedText = t.connected ? fmt.speed(t.speed_kmh) : NO_SPEED;
  setText($("speed-num"), speedText);
  setAttr($("speed-num"), "data-void", t.connected ? null : "1");
  setAttr($("speed-num"), "data-wide", t.connected && speedText.length > 3 ? "1" : null); // "12.0": shrink to fit
  setText($("hero-meta"), sourceLabel(s));

  // Readout: what the pad says it is set to, and what the game says.
  setText($("ro-set"), t.connected && t.target_kmh != null ? fmt.speedU(t.target_kmh) : "-");
  const beaconLive = s.source.beacon && s.source.beacon.state === "ok" && frame;
  let game = "-";
  if (beaconLive) game = !frame.active ? "none" : frame.paused ? "paused in game" : "marked";
  setText($("ro-game"), game);
  setText($("ro-char"), characterText(s.character));
  setText($("ro-belt"), t.connected ? BELT_WORDS[t.state] || t.state : "not connected");

  // Meter: the belt's speed between nothing and the pad's own top speed.
  const scale = t.max_kmh > 0 ? t.max_kmh : 6;
  const speedAt = t.connected ? Math.max(0, Math.min(1, t.speed_kmh / scale)) : 0;
  setTransform($("meter-fill"), `scaleX(${speedAt.toFixed(4)})`);
  const padTop = `${fmt.speed(scale)} ${U.speedUnit}`;
  setText($("meter-max"), t.name ? `${padTop} - the pad's top speed` : "");
  setAttr(
    $("meter"),
    "aria-label",
    t.connected ? `Belt at ${fmt.speedU(t.speed_kmh)}; the pad's top speed is ${padTop}` : "No reading from the belt",
  );
}

// ---- session ------------------------------------------------------------------------------

const WALK_TITLE = { wow: "This walk - World of Warcraft", free: "This walk" };

function tile(n, label, value, unit, source) {
  setText($(`t${n}-k`), label);
  setText($(`t${n}-v`), value);
  const unitEl = $(`t${n}-u`);
  if (unitEl) setText(unitEl, unit || "");
  setText($(`t${n}-s`), source || "");
}

function updateSession(s, online) {
  const live = s.session;
  setShown($("howto"), !live);
  if (live) {
    setText($("session-title"), WALK_TITLE[live.source] || SOURCE_LABEL[live.source] || "This walk");
    // Without the companion these are the last numbers heard, not a running clock.
    setText($("session-sub"), online ? `since ${fmt.timeOfDay(live.started_at)}` : "last known");
    tile(1, "Time", fmt.clock(live.seconds), "", online ? "belt moving" : "when the companion was last heard");
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
    // No sensor at all (and one could be used): the way to add one, instead of "no sensor".
    const addSensor = !live.hr && !live.avg_hr && !!s.heart_rate && !s.heart_rate.enabled;
    setShown($("t5-add"), addSensor);
    setShown($("t5-s"), !addSensor);
    const avg = live.seconds > 0 ? (live.distance_m / live.seconds) * 3.6 : null;
    const avgUnit = avg == null ? "" : U.speedUnit;
    tile(6, "Average speed", avg == null ? "-" : fmt.speed(avg), avgUnit, `max ${fmt.speedU(live.max_kmh)}`);
    return;
  }
  const today = s.totals.today;
  setShown($("t5-add"), false);
  setShown($("t5-s"), true);
  setText($("session-title"), "Today");
  setText($("session-sub"), online ? "no walk being recorded" : "last known");
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
  // (Only FTMS says its speed range: for any other reader the default one is not the pad's.)
  const ranged = known && (t.kind === "ftms" || t.kind === "simulated");
  setText($("tm-range"), ranged ? `${fmt.speed(t.min_kmh)}-${fmt.speedU(t.max_kmh)}` : known ? "not sent" : "-");
  setText($("tm-step"), known ? `${trimNumber(U.speed(t.step_kmh), 2)} ${U.speedUnit}` : "-");
  setText($("tm-grid"), known ? (t.mph_grid ? "mph grid" : "km/h grid") : "-");
  setText($("tm-address"), t.address || "-");

  const searching = !t.connected && t.want_connected;
  const connect = $("tm-connect");
  setShown(connect, !t.connected);
  setText(connect, searching ? "Retry now" : "Connect");
  const disconnect = $("tm-disconnect");
  setShown(disconnect, t.connected || searching);
  setText(disconnect, t.connected ? "Disconnect" : "Stop searching");
  // Walka is tied to the first treadmill it connected to (so that a second
  // pad in range is never listened to by mistake). Offer the way out.
  setShown($("tm-forget"), !!t.pinned);
  // (A recording being played back has no remote: what it does is recorded.)
  setShown($("tm-sim"), t.kind === "simulated" && !t.replay);
}

function signalWord(rssi) {
  if (rssi == null) return "";
  return rssi >= -60 ? "strong signal" : rssi >= -75 ? "good signal" : "weak signal";
}

function deviceRow(device, onPick, pickLabel) {
  const details = [device.address, signalWord(device.rssi)];
  if (device.ftms === true) details.push("treadmill service");
  else if (device.candidate === false) details.push("not a treadmill by its name or service");
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
export async function scanInto(out, button, { path, empty, onPick, pickLabel, body = {}, after = null }) {
  if (button.dataset.busy === "1") return;
  button.dataset.busy = "1";
  out.replaceChildren(h("p", { class: "muted", text: "Scanning for about 6 seconds..." }));
  try {
    const data = await post(path, body, { timeoutMs: 40000 });
    const devices = data.devices || [];
    // pickLabel may depend on the device (a function), as may what picking it does.
    const label = (device) => (typeof pickLabel === "function" ? pickLabel(device) : pickLabel);
    const rows = devices.map((device) => deviceRow(device, onPick, label(device)));
    if (!devices.length) rows.push(h("p", { class: "muted", text: empty() }));
    if (after) rows.push(after());
    out.replaceChildren(...rows);
  } catch (error) {
    out.replaceChildren(h("p", { class: "error", role: "alert", text: error.message }));
  } finally {
    delete button.dataset.busy;
  }
}

/** A device from "all Bluetooth devices": connect to it and see whether Walka can read it. */
async function tryDevice(device, button) {
  const row = button.closest(".device");
  const note = h("p", { class: "muted small device-note", text: "Connecting to it, to see what it offers..." });
  row.after(note);
  let result;
  button.disabled = true;
  try {
    result = await post("/api/treadmill/identify", { address: device.address }, { timeoutMs: 60000 });
  } catch (error) {
    note.className = "error small device-note";
    note.textContent = t("Could not look at it: {1}", error.message);
    return;
  } finally {
    button.disabled = false;
  }
  const found = result.device;
  note.textContent = found.readable
    ? t("Walka can read it: it sends {1}. Connecting.", tr(found.how))
    : t("Walka cannot read it as a treadmill. It offers: {1}.", found.services.map((name) => tr(name)).join(", ") || tr("nothing it names"));
  if (found.readable) note.className = "small device-note ok";
}

/** Shared by the treadmill card and the first-run guide. */
export function scanTreadmills(out = $("tm-scan-out"), button = $("tm-scan"), { all = false } = {}) {
  scanInto(out, button, {
    path: "/api/treadmill/scan",
    body: all ? { all: true } : {},
    pickLabel: (device) => (device.candidate === false ? "Check" : "Connect"),
    after: () => {
      const toggle = h("button", {
        class: "btn quiet small",
        type: "button",
        text: all ? "Show treadmills only" : "Show all Bluetooth devices",
      });
      toggle.addEventListener("click", () => scanTreadmills(out, button, { all: !all }));
      return h("div", { class: "btn-row" }, toggle);
    },
    empty: () =>
      last && last.treadmill.kind === "simulated"
        ? "Nothing to scan for: this is the simulated treadmill. Start the companion without --simulate or --demo to look for a real pad."
        : "No treadmill found. Is it switched on and in range, and is its own phone app closed? A treadmill that is already connected - here or to a phone - does not show up.",
    onPick: (device, button) =>
      // Not a treadmill by its name or service, or one read another way than now: look at it first.
      device.candidate === false || (device.protocol && last && device.protocol !== last.treadmill.kind)
        ? tryDevice(device, button)
        : run(button, () => post("/api/treadmill/connect", { address: device.address }), {
            success: `Connecting to ${device.name || device.address}.`,
          }),
  });
}

// ---- "Check my treadmill" ---------------------------------------------------------------------------

const ISSUE_FORM = "https://github.com/AntonyPerez0/walka-addon/issues/new?template=my-treadmill.yml";
let checkKey = null;

function downloadLink(name, label) {
  return h("a", {
    class: "btn small",
    href: `/api/treadmill/check/${encodeURIComponent(name)}`,
    download: name,
    text: label,
  });
}

function updateCheck(s) {
  const check = s.check;
  const simulated = s.treadmill.kind === "simulated";
  setShown($("tm-check-box"), !simulated);
  if (!check || simulated) return;
  $("tm-check").disabled = check.running;
  setText($("tm-check"), check.running ? "Checking..." : "Check my treadmill");
  const key = JSON.stringify([check.running, check.seconds, check.line, check.report, check.error]);
  if (key === checkKey) return;
  checkKey = key;
  const out = $("tm-check-out");
  if (check.running) {
    out.replaceChildren(
      h("p", {}, h("strong", { text: `Checking - ${check.seconds || 0} s of about ${check.expected_s} s.` })),
      h("p", {
        text: "While it listens: start the belt with the treadmill's own remote, change the speed, pause, and stop. Walka is not connected to the treadmill until the check is done.",
      }),
      check.line ? h("p", { class: "muted small mono", text: check.line }) : null,
    );
    setShown(out, true);
    return;
  }
  if (!check.report && !check.error) {
    setShown(out, false);
    return;
  }
  const rows = [];
  if (check.error) rows.push(h("p", { class: "error", text: check.error }));
  if (check.report) {
    rows.push(
      h("p", {
        text: check.error
          ? "What was heard is in the report."
          : `Done. The report${check.recording ? ` and a recording of ${check.notifications} messages from the treadmill are` : " is"} saved. Both are made to be posted: no other devices, Bluetooth addresses cut short, no serial number.`,
      }),
      h(
        "div",
        { class: "btn-row" },
        downloadLink(check.report, "Save the report"),
        check.recording ? downloadLink(check.recording, "Save the recording") : null,
        h("a", {
          class: "btn small quiet",
          href: ISSUE_FORM,
          target: "_blank",
          rel: "noopener noreferrer",
          text: "Post it on GitHub",
        }),
      ),
    );
  }
  out.replaceChildren(...rows);
  setShown(out, true);
}

// ---- other links ------------------------------------------------------------------------------------

const FLAG_HELP = {
  ACTIVE: "A walking session is marked in game",
  PAUSED: "The session is paused in game",
  MOVING: "The character is moving",
  MOUNTED: "On a mount",
  TAXI: "On a flight path",
  SWIMMING: "Swimming",
  FLYING: "Flying",
  AFK: "Away from keyboard",
  ESTIMATED: "The game hides the character's movement from addons in combat: the addon is estimating it",
};

let flagsKey = null;

function updateLinks(s, links, online) {
  // Game signal. Offline, the last frame is old: no sentence about it, no flags.
  const game = links.game;
  const beacon = online ? s.source.beacon : null;
  const frame = online ? s.source.frame : null;
  setTone($("link-game"), game.tone);
  setText($("game-state"), game.state);
  let detail = game.detail;
  if (beacon && beacon.state === "ok" && frame) {
    detail = frame.active
      ? `Session ${frame.session}${frame.paused ? " (paused in game)" : ""}${s.character && s.character.name ? ` for ${s.character.name}` : ""}. Walks are filed under this session.`
      : "The addon is visible, but no session is marked in game, so a walk would not count for your character. The session starts by itself when you log in; if it was paused or stopped, click Walka in the game's AddOns menu, by the minimap.";
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

/** stay: do not go to the addon settings when it fails (the first-run guide). */
export function installAddon(button, addonsDir, { stay = false } = {}) {
  return run(button, async () => {
    try {
      const data = await post("/api/addon/install", addonsDir ? { addons_dir: addonsDir } : {});
      toast(
        `Addon installed to ${data.path}. Restart the game (or type /reload) and tick Walka in the AddOns list.`,
        "ok",
      );
      return data;
    } catch (error) {
      if (!addonsDir && !stay && !location.hash.startsWith("#settings")) location.hash = "#settings/set-addon";
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
  // An error that happened while you were looking elsewhere (a walk that
  // could not be saved, say) must not sit quietly in a list: raise it.
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
  setTransform($("setup-bar"), `scaleX(${done / names.length})`);
}

// ---- wiring ----------------------------------------------------------------------------------------------

export function initLive() {
  $("hero-action").addEventListener("click", (event) =>
    run(event.currentTarget, () => post("/api/treadmill/connect", {})),
  );

  $("tm-scan").addEventListener("click", () => scanTreadmills());
  $("tm-check").addEventListener("click", (event) =>
    run(event.currentTarget, () => post("/api/treadmill/check", {}, { timeoutMs: 15000 })),
  );
  $("tm-connect").addEventListener("click", (event) =>
    run(event.currentTarget, () => post("/api/treadmill/connect", {})),
  );
  $("tm-disconnect").addEventListener("click", (event) =>
    run(event.currentTarget, () => post("/api/treadmill/disconnect")),
  );
  $("tm-forget").addEventListener("click", (event) =>
    run(event.currentTarget, () => post("/api/treadmill/forget")),
  );
  // The simulated pad's remote (the fieldset is hidden for a real treadmill).
  for (const button of document.querySelectorAll("#tm-sim [data-press]")) {
    button.addEventListener("click", () =>
      run(button, () => post("/api/treadmill/press", { button: button.dataset.press })),
    );
  }

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
  updateSession(s, online);
  updateTreadmill(s, links.treadmill);
  updateCheck(s);
  updateLinks(s, links, online);
  updateLog(s);
  updateSetup(s);
}
