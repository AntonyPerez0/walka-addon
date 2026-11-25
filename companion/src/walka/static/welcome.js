// The first-run guide: on a new install it is the whole screen, one step at a
// time, until it is finished or skipped (config.welcome_done). "Open the setup
// guide again" in Settings brings it back (#welcome).
//
// It holds nothing of its own: every step is the same thing the dashboard does
// elsewhere (the same requests), with fewer words around it.

import { $, h, post, run, sentence, setAttr, setShown, setText, toast } from "./util.js";
import { installAddon, scanTreadmills } from "./live.js";
import { adoptConfig, formFor, scanHeartRate } from "./settings.js";

const STEPS = ["hello", "units", "treadmill", "profile", "game", "hr", "done"];
const COUNTED = STEPS.filter((step) => step !== "hello" && step !== "done");

let index = 0;
let shown = false;
let last = null; // the last snapshot
let links = null;
let unitsTouched = false;
let onUnitsChosen = () => {};

const stepEl = (name) => document.querySelector(`.wz-step[data-step="${name}"]`);
const current = () => STEPS[index];

/** The units the computer's region uses, else the browser's language, else what is set. */
function suggestedUnits(s) {
  if (s.setup && s.setup.system_units) return s.setup.system_units;
  const match = /[-_]([A-Za-z]{2})$/.exec(navigator.language || "");
  if (match) return ["US", "GB", "LR", "MM"].includes(match[1].toUpperCase()) ? "mph" : "kmh";
  return s.units;
}

function wanted(s) {
  if (location.hash === "#welcome") return true;
  return !!(s && s.config && s.config.welcome_done === false);
}

// ---- the steps -----------------------------------------------------------------------

function statusLine(el, tone, text) {
  setAttr(el, "data-tone", tone);
  setText(el, text);
}

function treadmillLine(s) {
  const t = s.treadmill;
  if (t.connected) return ["ok", `Connected to ${t.name || "the treadmill"}. You can go on.`];
  if (t.want_connected) {
    return ["warn", t.error ? `Still looking: ${t.error}` : "Looking for the treadmill..."];
  }
  return ["idle", "Not connected yet. Press \"Look for it\" once it is switched on."];
}

function nextLabel(s) {
  switch (current()) {
    case "hello":
      return "Get started";
    case "treadmill":
      return s.treadmill.connected ? "Next" : "Skip for now";
    case "game":
      return s.addon && s.addon.dir ? "Next" : "Skip - no game";
    case "hr":
      return s.heart_rate && s.heart_rate.enabled ? "Next" : "Skip";
    case "done":
      return "Open the dashboard";
    default:
      return "Next";
  }
}

function paint() {
  const s = last;
  if (!s) return;
  const name = current();
  for (const step of STEPS) setShown(stepEl(step), step === name);
  const counted = name === "done" ? COUNTED.length : COUNTED.indexOf(name);
  setText($("wz-count"), counted >= 0 && counted < COUNTED.length ? `Step ${counted + 1} of ${COUNTED.length}` : "");
  for (const [i, dot] of [...$("wz-dots").children].entries()) {
    setAttr(dot, "data-state", i < counted ? "done" : i === counted ? "now" : null);
  }
  setShown($("wz-back"), index > 0);
  setShown($("wz-skip"), name !== "done");
  setText($("wz-next"), nextLabel(s));

  if (name === "units" && !unitsTouched) {
    const pick = suggestedUnits(s);
    for (const radio of document.querySelectorAll('input[name="wz-units"]')) radio.checked = radio.value === pick;
    setText(
      $("wz-units-hint"),
      s.setup && s.setup.system_units
        ? "Chosen to match your computer's region. You can change it later in Settings."
        : "You can change it later in Settings.",
    );
  }
  if (name === "treadmill") statusLine($("wz-tm-status"), ...treadmillLine(s));
  if (name === "game" && links) {
    statusLine($("wz-addon-status"), links.addon.tone, sentence(links.addon.detail || links.addon.state));
    setText($("wz-addon-install"), s.addon && s.addon.dir ? "Install again" : "Install the addon");
  }
  if (name === "hr" && links) {
    setShown($("wz-hr-status"), !!(s.heart_rate && s.heart_rate.enabled));
    statusLine($("wz-hr-status"), links.hr.tone, sentence(links.hr.detail || links.hr.state));
    $("wz-hr-scan").disabled = !links.hr.available;
  }
  if (name === "done") {
    const left = [];
    if (!s.treadmill.connected) left.push("connect the treadmill");
    if (!(s.addon && s.addon.dir)) left.push("install the game addon");
    setText(
      $("wz-left"),
      left.length ? `Still to do, whenever you like: ${left.join(" and ")}. "Getting started" on the Live screen keeps track.` : "",
    );
  }
}

function go(to) {
  index = Math.max(0, Math.min(STEPS.length - 1, to));
  paint();
  const heading = stepEl(current()).querySelector("h2");
  if (heading) heading.focus({ preventScroll: true });
}

async function finish(button) {
  await run(button, async () => {
    const data = await post("/api/config", { welcome_done: true });
    adoptConfig({ config: data.config });
    if (last) last.config = data.config;
    if (location.hash === "#welcome") location.hash = "#live";
    index = 0;
    close();
  });
}

async function next(button) {
  const name = current();
  if (name === "units") {
    const picked = document.querySelector('input[name="wz-units"]:checked');
    if (picked) {
      const done = await run(button, async () => {
        const data = await post("/api/config", { units: picked.value });
        onUnitsChosen(data.config.units);
        return true;
      });
      if (!done) return;
    }
  } else if (name === "profile") {
    const form = formFor(document.querySelector('form[data-form="welcome"]'));
    if (form.invalid) {
      toast("Fix the marked field first.", "warn");
      return;
    }
    if (form.dirty) {
      await form.save();
      if (form.dirty) return; // not saved: the toast said why
    } else if (!(last.setup && last.setup.profile_set)) {
      // The values shown are right as they are: saving them confirms them.
      if (!(await run(button, () => post("/api/config", { profile: {} })))) return;
    }
  } else if (name === "done") {
    await finish(button);
    return;
  }
  go(index + 1);
}

// ---- shown or not --------------------------------------------------------------------

function open() {
  shown = true;
  setShown($("welcome"), true);
  document.body.dataset.welcome = "1";
  paint();
  const heading = stepEl(current()).querySelector("h2");
  if (heading) heading.focus({ preventScroll: true });
}

function close() {
  shown = false;
  setShown($("welcome"), false);
  delete document.body.dataset.welcome;
}

/** Every snapshot. */
export function updateWelcome(s, healthLinks, online) {
  if (!online || !s) {
    if (shown && location.hash !== "#welcome") close();
    return;
  }
  last = s;
  links = healthLinks;
  adoptConfig(s);
  if (wanted(s) && !shown) open();
  else if (!wanted(s) && shown) close();
  else if (shown) paint();
}

export function initWelcome({ onUnits }) {
  onUnitsChosen = onUnits;
  $("wz-dots").replaceChildren(...COUNTED.map(() => h("li")));
  $("wz-next").addEventListener("click", (event) => next(event.currentTarget));
  $("wz-back").addEventListener("click", () => go(index - 1));
  $("wz-skip").addEventListener("click", (event) => finish(event.currentTarget));
  for (const radio of document.querySelectorAll('input[name="wz-units"]')) {
    radio.addEventListener("change", () => {
      unitsTouched = true;
    });
  }
  $("wz-tm-look").addEventListener("click", (event) =>
    run(event.currentTarget, () => post("/api/treadmill/connect", {})),
  );
  $("wz-tm-scan").addEventListener("click", (event) => scanTreadmills($("wz-tm-out"), event.currentTarget));
  $("wz-addon-install").addEventListener("click", (event) => installAddon(event.currentTarget, undefined, { stay: true }));
  $("wz-hr-scan").addEventListener("click", (event) => scanHeartRate($("wz-hr-out"), event.currentTarget));
  window.addEventListener("hashchange", () => {
    if (location.hash === "#welcome" && !shown && last) {
      index = 0;
      open();
    }
  });
}
