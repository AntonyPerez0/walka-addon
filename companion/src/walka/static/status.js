// Plain words for machine state.
//
// Pure functions from a snapshot to what the page should say: what the belt
// is doing and what Walka makes of it, as a headline with the next thing to
// do, and the health of each link (treadmill, game signal, heart-rate sensor,
// addon). No DOM in here.
//
// Walka only listens to the treadmill, so nothing here is something Walka
// does to the belt: the belt is started, stopped and set at the treadmill.

import { fmt, sentence } from "./util.js";

const PAD_ADVICE =
  "Switch the treadmill on and close its own phone app - most treadmills accept only one Bluetooth connection. No pairing in the computer's Bluetooth settings is needed.";
const WINDOW_ADVICE = "Bring the game window to the front; nothing may cover its top-left corner.";
const NOT_FILED = "Screen reading is off, so walks are not filed under a game session.";

/** What a walk is filed under. ("manual": walks recorded by a version that had dashboard sessions.) */
export const SOURCE_LABEL = { wow: "World of Warcraft", free: "No game session", manual: "Manual session" };

/** What the walk being recorded is filed under, as shown next to the state. */
export function sourceLabel(s) {
  if (s.session) return SOURCE_LABEL[s.session.source] || "";
  return s.source.kind === "wow" ? SOURCE_LABEL.wow : "";
}

// ---- links -----------------------------------------------------------------------

export function treadmillHealth(s) {
  const t = s.treadmill;
  if (t.connected) {
    if (t.age_s != null && t.age_s > 5) {
      return {
        tone: "warn",
        pill: "no data",
        state: "no data",
        detail: `Connected, but the pad has sent nothing for ${Math.round(t.age_s)} s. Walka counts the belt as standing still until data returns.`,
      };
    }
    return { tone: "ok", pill: "connected", state: "connected", detail: "" };
  }
  if (!t.want_connected) {
    return {
      tone: "idle",
      pill: "off",
      state: "disconnected",
      detail: "Not connected. Press Connect when the pad is switched on.",
    };
  }
  if (/reconnects when/i.test(t.error)) {
    // The link dropped while nothing was being recorded: the companion says
    // when it looks for the pad again, and is not searching meanwhile.
    return { tone: "idle", pill: "off", state: "not connected", detail: sentence(t.error) };
  }
  if (t.error) {
    let detail = sentence(t.error);
    if (/bluetooth scan failed/i.test(t.error)) detail += " Check that Bluetooth is switched on on this computer.";
    else if (!/phone app/i.test(t.error)) detail += ` ${PAD_ADVICE}`;
    return { tone: "bad", pill: "not found", state: "not connected", detail: `${detail} Walka keeps trying.` };
  }
  return {
    tone: "warn",
    pill: "searching",
    state: "searching",
    detail: `${sentence(t.detail || "scanning")} ${PAD_ADVICE}`,
  };
}

export function gameHealth(s) {
  const beacon = s.source.beacon;
  if (!beacon) {
    return {
      tone: "idle",
      pill: "off",
      state: "off",
      detail:
        "Screen reading is switched off (the companion was started with --no-capture). Walks are still recorded, just not filed under a game session.",
    };
  }
  const age = beacon.age_s;
  const ageText =
    age == null
      ? ""
      : age < 10
        ? `${age.toFixed(1)} s`
        : age < 120
          ? `${Math.round(age)} s`
          : `${Math.round(age / 60)} min`;
  let health;
  switch (beacon.state) {
    case "ok":
      health = { tone: "ok", pill: "live", state: `live · ${ageText}`, detail: "" };
      break;
    case "frozen":
      health = {
        tone: "warn",
        pill: "frozen",
        state: `frozen · ${ageText}`,
        detail: "The game picture is visible but not updating - usually a loading screen. It recovers by itself.",
      };
      break;
    case "game_closed":
      health = {
        tone: "idle",
        pill: "closed",
        state: "game closed",
        detail: "The game was closed, so its session has ended.",
      };
      break;
    case "error":
      health = {
        tone: "bad",
        pill: "error",
        state: "error",
        // (On a Mac the reason says which switch in System Settings it is.)
        detail: /System Settings/.test(beacon.detail || "")
          ? sentence(beacon.detail)
          : `${sentence(beacon.detail || "screen capture is unavailable")} Restart the companion on the same desktop as the game.`,
      };
      break;
    case "starting":
      health = { tone: "idle", pill: "starting", state: "starting", detail: "Starting to watch the screen." };
      break;
    default:
      if (!beacon.frames) {
        health = {
          tone: "idle",
          pill: "waiting",
          state: "not seen yet",
          detail:
            "No signal yet. Start World of Warcraft, enable the Walka addon (AddOns button on the character screen), and run the game windowed or borderless with the top-left corner of its window visible.",
        };
      } else {
        health = {
          tone: "warn",
          pill: "lost",
          state: ageText ? `lost · ${ageText}` : "lost",
          detail: `The signal is not visible: the game is minimised, covered, or on a loading screen. ${WINDOW_ADVICE}`,
        };
      }
  }
  return health;
}

export function heartHealth(s) {
  const hr = s.heart_rate;
  if (!hr) {
    return {
      tone: "idle",
      pill: "N/A",
      state: "not available",
      available: false,
      detail: "Heart-rate sensors are not used with the simulated treadmill.",
    };
  }
  if (!hr.enabled) {
    return {
      tone: "idle",
      pill: "off",
      state: "off",
      available: true,
      detail: "Optional. A Bluetooth heart-rate sensor adds heart rate to your walks and sharper calorie numbers.",
    };
  }
  if (hr.connected && hr.bpm != null) {
    return { tone: "ok", pill: "live", state: `${hr.bpm} bpm`, available: true, detail: hr.name || "" };
  }
  if (hr.connected) {
    return {
      tone: "warn",
      pill: "no reading",
      state: "no reading",
      available: true,
      detail: `${hr.name || "The sensor"} is connected but is not sending a heart rate. Check that it is worn snugly.`,
    };
  }
  return {
    tone: "warn",
    pill: "searching",
    state: "searching",
    available: true,
    detail: `${sentence(hr.detail || "searching")} Wear the sensor so it wakes up; on a watch, switch on heart-rate broadcasting.`,
  };
}

export function addonHealth(s) {
  const addon = s.addon;
  if (addon.dir && !addon.error) {
    const ago = addon.last_sync == null ? "" : ` ${fmt.ago(s.time - addon.last_sync)}`;
    return { tone: "ok", pill: "synced", state: `synced${ago}`, installed: true, detail: addon.dir };
  }
  if (addon.dir) {
    return {
      tone: "bad",
      pill: "error",
      state: "sync failed",
      installed: true,
      detail: `${sentence(addon.error)} Check that the folder still exists and that the game is not blocking it. (${addon.dir})`,
    };
  }
  return {
    tone: "warn",
    pill: "missing",
    state: "not installed",
    installed: false,
    detail: "The Walka addon was not found in the game's AddOns folder, so Walka cannot tell which character your walks are for.",
  };
}

export function health(s) {
  return { treadmill: treadmillHealth(s), game: gameHealth(s), hr: heartHealth(s), addon: addonHealth(s) };
}

/**
 * The same links while the page cannot hear the companion: the last answers are
 * old, so none of them is shown as if it were still true.
 */
export function staleHealth(s) {
  const links = health(s);
  for (const link of Object.values(links)) {
    link.tone = "idle";
    link.pill = "unknown";
    link.state = "unknown";
    link.detail = "Not known while the companion is not running.";
  }
  return links;
}

// ---- the headline -------------------------------------------------------------------

/**
 * What the belt is doing, and what Walka makes of it, in plain words.
 * Returns {tone, icon, title, short, detail, action?: {label}} - the one
 * action there is asks the companion to look for the treadmill.
 * tone: "ok" a walk is being recorded, "warn" waiting or degraded,
 *       "bad" broken, "idle" at rest.
 */
export function describe(s) {
  const t = s.treadmill;
  const link = treadmillHealth(s);

  if (!t.connected) {
    if (!t.want_connected || link.tone === "idle") {
      return {
        tone: "idle",
        icon: "idle",
        title: "Treadmill disconnected",
        short: "No treadmill",
        detail: t.want_connected
          ? link.detail
          : "Walka is not listening to the pad, so no walk is recorded. Press Connect when you want to walk.",
        action: { label: "Connect" },
      };
    }
    if (t.error) {
      return {
        tone: "bad",
        icon: "x",
        title: "Treadmill not connected",
        short: "No treadmill",
        detail: link.detail,
        action: { label: "Try again now" },
      };
    }
    return {
      tone: "warn",
      icon: "search",
      title: "Looking for the treadmill",
      short: "Searching",
      detail: link.detail,
    };
  }

  if (link.tone !== "ok") {
    return { tone: "warn", icon: "warn", title: "No data from the treadmill", short: "No data", detail: link.detail };
  }

  const inGame = s.source.kind === "wow"; // a walking session is marked in the game, and in view
  const frame = s.source.frame;

  if (t.moving) {
    let detail;
    if (s.session && s.session.source === "wow") {
      detail = "The belt is moving, and Walka counts the walk for the session marked in World of Warcraft.";
      if (inGame && frame && frame.paused) {
        detail += " That session is paused in game; the walk is recorded all the same.";
      }
    } else if (s.source.beacon) {
      detail =
        "The belt is moving. No session is marked in a game, so the walk is recorded on its own. In World of Warcraft the session starts by itself when you log in (or click Walka in the game's AddOns menu).";
    } else {
      detail = `The belt is moving. ${NOT_FILED}`;
    }
    return { tone: "ok", icon: "go", title: "Recording your walk", short: "Recording", detail };
  }

  if (t.state === "starting") {
    return {
      tone: "ok",
      icon: "go",
      title: "The pad is counting down",
      short: "Starting",
      detail: "The belt was started at the treadmill and moves when its countdown is over. Walka records the walk from then on.",
    };
  }

  if (t.state === "paused") {
    return {
      tone: "idle",
      icon: "pause",
      title: "Paused at the treadmill",
      short: "Paused",
      detail: "The belt was paused with the treadmill's own controls. Start it again there to carry on.",
    };
  }

  if (s.session && s.session.source === "free") {
    return {
      tone: "idle",
      icon: "idle",
      title: "The belt is standing still",
      short: "Standing still",
      detail:
        "The walk stays open for about a minute: start the belt again at the treadmill and it carries on. Otherwise it is saved as it is.",
    };
  }
  if (inGame || s.session) {
    return {
      tone: "idle",
      icon: "idle",
      title: "The belt is standing still",
      short: "Standing still",
      detail:
        "A walking session is marked in World of Warcraft. Start the belt with the treadmill's own remote - Walka records the walk and counts it for your character.",
    };
  }
  return {
    tone: "idle",
    icon: "idle",
    title: "Ready",
    short: "Ready",
    detail: `Start the belt with the treadmill's own remote - Walka records the walk by itself. ${
      s.source.beacon
        ? "In World of Warcraft the session starts by itself when you log in, and the walk counts for your character."
        : NOT_FILED
    }`,
  };
}

/** What to show when the event stream is down. */
export const OFFLINE = {
  tone: "bad",
  icon: "x",
  title: "Companion not running",
  short: "Offline",
  detail:
    "This page has lost the Walka companion and cannot see the belt. No walk is recorded until the companion runs again. The page reconnects by itself.",
};

/** The four first-run steps, ticked from the snapshot. */
export function setupSteps(s) {
  const beacon = s.source.beacon;
  return {
    treadmill: !!s.treadmill.connected,
    addon: !!s.addon.dir,
    game: !!(beacon && beacon.frames > 0),
    profile: !!(s.setup && s.setup.profile_set),
  };
}
