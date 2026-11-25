// Plain words for machine state.
//
// Pure functions from a snapshot to what the page should say: what the belt
// is doing and what Walka makes of it, as a headline with the next thing to
// do, and the health of each link (treadmill, the game's saved file,
// heart-rate sensor, addon). No DOM in here.
//
// Walka only listens to the treadmill, so nothing here is something Walka
// does to the belt: the belt is started, stopped and set at the treadmill.
// Nothing about the game is live either: Walka reads what the game saved
// (at logout or /reload), and files walks under the game's sessions then.

import { fmt, sentence } from "./util.js";

const PAD_ADVICE =
  "Switch the treadmill on and close its own phone app - most treadmills accept only one Bluetooth connection. No pairing in the computer's Bluetooth settings is needed.";
const FILED_LATER =
  "Walks are filed under the game's sessions when the game saves its file: when you log out or type /reload.";

/** What a walk is filed under. ("manual": walks recorded by a version that had dashboard sessions.) */
export const SOURCE_LABEL = { wow: "In game", free: "No game session", manual: "Manual session" };

/** A walk of the history, in a few words: "In game · Thrall". */
export function walkLabel(walk) {
  const label = SOURCE_LABEL[walk.source] || walk.source || "";
  return walk.character ? `${label} · ${walk.character}` : label;
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

function plural(count, one, many) {
  return `${fmt.int(count)} ${count === 1 ? one : many}`;
}

/** The game's saved file: whether it was found, and when the game last wrote it. Never live. */
export function gameHealth(s) {
  const game = s.game;
  if (!game) {
    return { tone: "idle", pill: "unknown", state: "unknown", detail: FILED_LATER };
  }
  if (game.demo) {
    return {
      tone: "ok",
      pill: "demo",
      state: "demo",
      detail: "This is the demo: its game sessions and characters are made up, and no game is read.",
    };
  }
  if (game.files > 0) {
    const ago = game.saved_at == null ? "" : ` ${fmt.ago(s.time - game.saved_at)}`;
    const held = `${plural(game.sessions, "game session", "game sessions")} and ${plural(game.characters, "character", "characters")}`;
    return {
      tone: "ok",
      pill: "found",
      state: `saved${ago}`,
      detail: `The game last saved Walka's file${ago}, with ${held}. ${FILED_LATER}`,
    };
  }
  if (game.game_found) {
    return {
      tone: "idle",
      pill: "no file yet",
      state: "no saved file yet",
      detail:
        "World of Warcraft is on this computer, but it has not saved Walka's file yet. It does when you log out or type /reload with the Walka addon on. Walks are recorded all the same, and filed under the game's sessions once it has.",
    };
  }
  return {
    tone: "idle",
    pill: "not found",
    state: "game not found",
    detail:
      "World of Warcraft was not found on this computer, so walks are not filed under a game session. They are recorded all the same. If the game is somewhere unusual, set its AddOns folder in Settings.",
  };
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
  if (hr.source === "network" && hr.bpm != null) {
    return { tone: "ok", pill: "live", state: `${hr.bpm} bpm`, available: true, detail: "From your watch, over the local network." };
  }
  if (!hr.enabled) {
    if (hr.network && hr.network.listening) {
      return {
        tone: "warn",
        pill: "waiting",
        state: "waiting",
        available: true,
        detail: "Listening for heart rate over the local network: nothing has arrived yet.",
      };
    }
    return {
      tone: "idle",
      pill: "off",
      state: "off",
      available: true,
      detail: hr.name
        ? `Off. Walka remembers your sensor (${hr.name}): "Turn on" uses it again.`
        : "Optional. A Bluetooth heart-rate sensor adds heart rate to your walks and sharper calorie numbers.",
    };
  }
  const battery = hr.battery != null ? ` Battery ${hr.battery}%.` : "";
  if (hr.connected && hr.bpm != null) {
    return { tone: "ok", pill: "live", state: `${hr.bpm} bpm`, available: true, detail: `${hr.name || ""}.${battery}`.replace(/^\./, "").trim() };
  }
  if (hr.connected && hr.contact === false) {
    return {
      tone: "warn",
      pill: "no contact",
      state: "no skin contact",
      available: true,
      detail: `${hr.name || "The sensor"} is not touching your skin, so its readings are not used. Wear it snugly (a strap's pads work better a little damp).${battery}`,
    };
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

  if (t.moving) {
    return {
      tone: "ok",
      icon: "go",
      title: "Recording your walk",
      short: "Recording",
      detail: `The belt is moving, and Walka records the walk from the treadmill. ${FILED_LATER}`,
    };
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

  if (s.session) {
    // (A walk being recorded is always on its own: the game session it was in is known later.)
    return {
      tone: "idle",
      icon: "idle",
      title: "The belt is standing still",
      short: "Standing still",
      detail:
        "The walk stays open for about a minute: start the belt again at the treadmill and it carries on. Otherwise it is saved as it is.",
    };
  }
  return {
    tone: "idle",
    icon: "idle",
    title: "Ready",
    short: "Ready",
    detail: `Start the belt with the treadmill's own remote - Walka records the walk by itself. ${FILED_LATER}`,
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
  return {
    treadmill: !!s.treadmill.connected,
    addon: !!s.addon.dir,
    game: !!(s.game && s.game.files > 0), // the game's saved file was found (optional)
    profile: !!(s.setup && s.setup.profile_set),
  };
}
