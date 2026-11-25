// Walka dashboard - entry point.
//
// One EventSource delivers a full snapshot of the companion every half second.
// Each snapshot is turned into a few view models (status.js) and handed to the
// visible view, which writes only what changed (see the setters in util.js).
// Everything that changes state goes out as a JSON POST/DELETE (util.js: api).

import { $, U, fmt, setAttr, setShown, setText, setTone, state } from "./util.js";
import { OFFLINE, describe, health, staleHealth } from "./status.js";
import { initLive, updateLive } from "./live.js";
import { historyUnitsChanged, initHistory, updateHistory } from "./history.js";
import { initSettings, settingsUnitsChanged, updateSettings } from "./settings.js";
import { initShare, shareUnitsChanged, updateShare } from "./share.js";
import { updateFeet } from "./walkaaccount.js";
import { initWelcome, updateWelcome } from "./welcome.js";

const VIEWS = ["live", "history", "share", "settings"];
const STALE_AFTER_MS = 3000; // snapshots arrive every 500 ms
const CONNECTING = {
  tone: "idle",
  icon: "search",
  title: "Connecting to the companion",
  short: "Connecting",
  detail: "Waiting for the first data.",
};

let view = "";
let lastMessageAt = 0;
let offlineSince = performance.now();
let everOnline = false;
let unitsLockedUntil = 0; // after choosing units here, ignore a snapshot that was already in flight

const pills = ["treadmill", "game", "hr", "addon"].map((name) => {
  const el = $(`pill-${name}`);
  return { name, el, stateEl: el.querySelector(".pill-state"), label: el.querySelector(".pill-name").textContent };
});

// ---- rendering ---------------------------------------------------------------------------

// What cannot work without the companion is switched off while it is silent (and looks it),
// instead of failing one press at a time.
const NEEDS_COMPANION = "#treadmill, #links, #setup, #view-settings .card";
let companionHeard = null;
let focusToReturn = null; // what had focus when its card was switched off
function gateControls(online) {
  if (online === companionHeard) return;
  companionHeard = online;
  if (!online) {
    const active = document.activeElement;
    focusToReturn = active && active.closest && active.closest(NEEDS_COMPANION) ? active : null;
  }
  for (const el of document.querySelectorAll(NEEDS_COMPANION)) el.inert = !online;
  if (online && focusToReturn) {
    // Only if focus is still nowhere: never take it from where the user has moved since.
    if (document.activeElement === document.body && focusToReturn.isConnected) {
      focusToReturn.focus({ preventScroll: true });
    }
    focusToReturn = null;
  }
}

function updateChrome(s, summary, links, online) {
  gateControls(online);
  if (links) {
    for (const pill of pills) {
      const link = links[pill.name];
      setTone(pill.el, link.tone);
      setText(pill.stateEl, link.pill);
      setAttr(pill.el, "aria-label", `${pill.label}: ${link.state}. Show details.`);
    }
  }
  // What the belt is doing, on every view (the Live view says it in full).
  const belt = $("belt");
  setTone(belt, summary.tone, `#i-${summary.icon}`);
  setText($("belt-title"), summary.short);
  const beltSpeed = online && s && s.treadmill.connected ? fmt.speedU(s.treadmill.speed_kmh) : "";
  setText($("belt-speed"), beltSpeed);
  // The name starts with what is shown, so it is what a voice user would say.
  setAttr(
    belt,
    "aria-label",
    `${summary.short}${beltSpeed ? `, ${beltSpeed}` : ""} - belt: ${summary.title}. Show the live view.`,
  );
  let title = "Walka";
  if (online && s) {
    const speed = s.treadmill.connected ? `${fmt.speedU(s.treadmill.speed_kmh)} · ` : "";
    title = `${speed}${summary.short} - Walka`;
  } else if (everOnline) {
    title = "Offline - Walka";
  }
  if (document.title !== title) document.title = title;
  // "Nothing is uploaded", on every view - unless walks are being sent to a Walka account.
  updateFeet(s);
  // A newer Walka, when the update notice is switched on.
  const update = online && s && s.update;
  const newer = !!(update && update.enabled && update.newer);
  setShown($("update-note"), newer);
  if (newer) {
    setText($("update-title"), `Walka ${update.latest.replace(/^v/, "")} is out.`);
    setText($("update-detail"), `You have ${update.current}. Walka does not update itself: download the new version from its page.`);
    setAttr($("update-link"), "href", update.url);
  }
}

function render() {
  const s = state.snapshot;
  const online = state.online && !!s;
  const offlineLong = performance.now() - offlineSince > 2000;
  const summary = online ? describe(s) : everOnline || offlineLong ? OFFLINE : CONNECTING;
  // A snapshot from before the companion went silent says nothing about now.
  const links = s ? (online ? health(s) : staleHealth(s)) : null;
  updateChrome(s, summary, links, online);
  updateWelcome(s, links, online);
  if (view === "live") updateLive(s, summary, links, online);
  else if (s && view === "history") updateHistory(s);
  else if (s && view === "settings") updateSettings(s, links);
  else if (s && view === "share") updateShare(s);
}

function unitsChanged(units) {
  if (units === state.units) return;
  state.units = units;
  historyUnitsChanged();
  settingsUnitsChanged();
  shareUnitsChanged();
  render();
}

// ---- the event stream ------------------------------------------------------------------------

function setOnline(online) {
  if (online === state.online) return;
  state.online = online;
  if (online) everOnline = true;
  else offlineSince = performance.now();
  document.body.dataset.offline = online ? "0" : "1";
  if (online) setShown($("offline"), false);
  render();
}

function connect() {
  const stream = new EventSource("/api/events");
  stream.onmessage = (event) => {
    let snapshot;
    try {
      snapshot = JSON.parse(event.data);
    } catch {
      return;
    }
    lastMessageAt = performance.now();
    if (snapshot.units && snapshot.units !== state.units && lastMessageAt > unitsLockedUntil) {
      state.snapshot = snapshot;
      unitsChanged(snapshot.units);
    }
    state.snapshot = snapshot;
    if (state.online) render();
    else setOnline(true);
  };
  stream.onerror = () => {
    setOnline(false);
    // The browser retries by itself unless the stream was refused outright.
    if (stream.readyState === EventSource.CLOSED) setTimeout(connect, 2000);
  };
}

function watchdog() {
  const now = performance.now();
  if (state.online && now - lastMessageAt > STALE_AFTER_MS) setOnline(false);
  if (!state.online) {
    const seconds = Math.round((now - offlineSince) / 1000);
    // A short gap (page load, a blip) is not worth an alarm.
    if (seconds >= 2) {
      const first = $("offline").hidden;
      setShown($("offline"), true);
      setText(
        $("offline-since"),
        everOnline ? `No data for ${seconds < 90 ? `${seconds} s` : `${Math.round(seconds / 60)} min`}.` : "",
      );
      if (first) render();
    }
  }
}

// ---- views -------------------------------------------------------------------------------------

function route() {
  const [name, anchor] = (location.hash.replace(/^#/, "") || "live").split("/");
  const next = VIEWS.includes(name) ? name : "live";
  if (next !== view) {
    view = next;
    for (const candidate of VIEWS) $(`view-${candidate}`).hidden = candidate !== view;
    for (const tab of document.querySelectorAll(".tabs a")) {
      if (tab.dataset.view === view) tab.setAttribute("aria-current", "page");
      else tab.removeAttribute("aria-current");
    }
    render();
    if (!anchor) window.scrollTo(0, 0);
  }
  if (anchor) {
    const target = document.getElementById(anchor);
    if (target && target.closest(".view") === $(`view-${view}`)) {
      target.scrollIntoView({ block: "start" });
      if (target.tabIndex === -1) target.focus({ preventScroll: true });
    }
  }
}

// ---- start ---------------------------------------------------------------------------------------

initLive();
initHistory();
initShare();
const onUnits = (units) => {
  unitsLockedUntil = performance.now() + 1500;
  unitsChanged(units);
};
initSettings({ onUnits });
initWelcome({ onUnits });

window.addEventListener("hashchange", route);
// Following a link to the place you already are does not fire hashchange.
document.addEventListener("click", (event) => {
  const link = event.target.closest && event.target.closest('a[href^="#"]');
  if (link && link.getAttribute("href") === location.hash) route();
});
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) render();
});

setText($("speed-unit"), U.speedUnit);
route();
connect();
setInterval(watchdog, 1000);
