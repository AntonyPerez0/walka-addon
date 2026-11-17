// Plain words for machine state.
//
// Pure functions from a snapshot to what the page should say: the governor's
// phase as a headline with the next thing to do, and the health of each link
// (treadmill, game signal, heart-rate sensor, addon). No DOM in here.

import { capitalise, fmt, sentence } from "./util.js";

const PAD_ADVICE =
  "Switch the pad on and close the UREVO phone app - the pad accepts only one Bluetooth connection. No pairing in Windows is needed.";
const WINDOW_ADVICE = "Bring the game window to the front; nothing may cover its top-left corner.";

/** Seconds since the game signal was declared lost (0 while it is fresh). */
function lostFor(s) {
  const age = s.source.signal_age_s;
  if (age == null) return 0;
  return Math.max(0, age - s.config.limits.signal_timeout_s);
}

function aboutSeconds(seconds) {
  const n = Math.max(0, Math.ceil(seconds));
  if (n >= 90) return `about ${Math.round(n / 60)} min`;
  return n <= 1 ? "a moment" : `about ${n} s`;
}

export const SOURCE_LABEL = { wow: "World of Warcraft", manual: "Manual mode", free: "Pad remote", none: "" };

/** Where the current belt target comes from, as shown next to the state. */
export function sourceLabel(s) {
  if (s.source.kind !== "none") return SOURCE_LABEL[s.source.kind] || "";
  if (s.session && s.session.source === "free") return SOURCE_LABEL.free;
  return "";
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
        detail: `Connected, but the pad has sent nothing for ${Math.round(t.age_s)} s. Walka treats the belt as stopped until data returns.`,
      };
    }
    return { tone: "ok", pill: "connected", state: "connected", detail: "" };
  }
  if (!t.want_connected) {
    return { tone: "idle", pill: "off", state: "disconnected", detail: "Not connected. Press Connect when the pad is switched on." };
  }
  if (t.error) {
    let detail = sentence(t.error);
    if (/bluetooth scan failed/i.test(t.error)) detail += " Check that Bluetooth is switched on on this PC.";
    else if (!/urevo/i.test(t.error)) detail += ` ${PAD_ADVICE}`;
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
      detail: "Screen reading is switched off (the companion was started with --no-capture). Use manual control.",
    };
  }
  const age = beacon.age_s;
  const ageText = age == null ? "" : age < 10 ? `${age.toFixed(1)} s` : age < 120 ? `${Math.round(age)} s` : `${Math.round(age / 60)} min`;
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
      health = { tone: "idle", pill: "closed", state: "game closed", detail: "The game was closed, so the session has ended." };
      break;
    case "error":
      health = {
        tone: "bad",
        pill: "error",
        state: "error",
        detail: `${sentence(beacon.detail || "screen capture is unavailable")} Restart the companion on the same desktop as the game.`,
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
  if (s.source.manual.active) {
    health.detail = `Manual mode is on, so the game signal is ignored until you end the manual session. ${health.detail}`.trim();
    health.ignored = true;
  }
  return health;
}

export function heartHealth(s) {
  const hr = s.heart_rate;
  if (!hr) {
    return {
      tone: "idle",
      pill: "n/a",
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
    detail: "The Walka addon was not found in the game's AddOns folder, so your history cannot be shown in game yet.",
  };
}

export function health(s) {
  return { treadmill: treadmillHealth(s), game: gameHealth(s), hr: heartHealth(s), addon: addonHealth(s) };
}

// ---- the headline -------------------------------------------------------------------

const RESUME_NOTE = "The belt starts after the pad's 3-2-1 countdown.";

/**
 * The governor's state in plain words.
 * Returns {tone, icon, title, short, detail, action?: {kind, label, note}}.
 * tone: "ok" belt driven / session live, "warn" waiting or degraded,
 *       "bad" stopped or broken, "idle" at rest.
 */
export function describe(s) {
  const g = s.governor;
  const t = s.treadmill;
  const lim = s.config.limits;
  const manual = s.source.manual;
  const reason = g.reason || "";

  switch (g.phase) {
    case "no_treadmill": {
      const link = treadmillHealth(s);
      const waiting = manual.active ? " Your manual session is waiting for the pad." : "";
      if (!t.want_connected) {
        return {
          tone: "idle",
          icon: "idle",
          title: "Treadmill disconnected",
          short: "No treadmill",
          detail: `Walka is not connected to the pad. Press Connect when you want to walk.${waiting}`,
          action: { kind: "connect", label: "Connect" },
        };
      }
      if (t.error) {
        return {
          tone: "bad",
          icon: "x",
          title: "Treadmill not connected",
          short: "No treadmill",
          detail: `${link.detail}${waiting}`,
          action: { kind: "connect", label: "Try again now" },
        };
      }
      return {
        tone: "warn",
        icon: "search",
        title: "Looking for the treadmill",
        short: "Searching",
        detail: `${link.detail}${waiting}`,
      };
    }

    case "starting":
      return {
        tone: "warn",
        icon: "go",
        title: "Starting the belt",
        short: "Starting",
        detail: "The pad counts down 3-2-1, then the belt starts at its slowest speed. Get ready to walk.",
      };

    case "following": {
      if (manual.active) {
        return {
          tone: "ok",
          icon: "go",
          title: "Manual session running",
          short: "Manual",
          detail: "The belt holds the speed set under Manual control. The game signal is ignored until you end the manual session.",
        };
      }
      const asked = s.source.frame ? s.source.frame.target_kmh : 0;
      const capped = asked - g.wanted_kmh > 0.08;
      return {
        tone: "ok",
        icon: "go",
        title: "Following your character",
        short: "Following",
        detail: capped
          ? `Your character asks for ${fmt.speedU(asked)}; the belt is held at ${fmt.speedU(g.wanted_kmh)}, the most your limits and the pad allow.`
          : "The belt follows your character's speed. Nothing to do - just walk.",
      };
    }

    case "holding":
      if (reason.includes("holding")) {
        const left = lim.hold_s - lostFor(s);
        const next = lim.safe_speed_kmh > 0 ? `slows to the safe speed (${fmt.speedU(lim.safe_speed_kmh)})` : "pauses";
        return {
          tone: "warn",
          icon: "warn",
          title: "Game signal lost - holding speed",
          short: "Signal lost",
          detail: `The belt keeps its speed and ${next} in ${aboutSeconds(left)} unless the signal returns. ${WINDOW_ADVICE}`,
        };
      }
      return {
        tone: "warn",
        icon: "warn",
        title: "Game signal lost",
        short: "Signal lost",
        detail: `The belt is stopped and carries on when the signal returns. ${WINDOW_ADVICE}`,
      };

    case "safe": {
      const left = lim.hold_s + lim.loss_pause_s - lostFor(s);
      return {
        tone: "warn",
        icon: "warn",
        title: "Slowed to safe speed",
        short: "Safe speed",
        detail: `The game signal has been gone for a while. The belt pauses in ${aboutSeconds(left)} unless it returns. ${WINDOW_ADVICE}`,
      };
    }

    case "standing":
      if (reason.includes("cap")) {
        return {
          tone: "warn",
          icon: "warn",
          title: "Max speed is below the pad's minimum",
          short: "Capped",
          detail: `Your max speed (${fmt.speedU(lim.max_speed_kmh)}) is lower than the slowest this pad can go (${fmt.speedU(t.min_kmh)}), so the belt stays paused. Raise Max speed under Settings, Safety limits.`,
          action: { kind: "limits", label: "Open safety limits" },
        };
      }
      return {
        tone: "ok",
        icon: "pause",
        title: "Standing - belt paused",
        short: "Standing",
        detail: "Your character is standing still, so the belt is paused. It starts again by itself when your character moves.",
      };

    case "paused":
      if (manual.active) {
        return {
          tone: "idle",
          icon: "pause",
          title: "Paused",
          short: "Paused",
          detail: `Your manual session is paused. Resume to carry on at ${fmt.speedU(manual.speed_kmh)}.`,
          action: { kind: "manual-resume", label: "Resume", note: RESUME_NOTE },
        };
      }
      return {
        tone: "idle",
        icon: "pause",
        title: "Paused",
        short: "Paused",
        detail: "The session is paused in game. Resume there with /walka resume or the HUD button.",
      };

    case "waiting":
      if (reason.startsWith("move your character")) {
        return {
          tone: "warn",
          icon: "warn",
          title: "Waiting for you to move",
          short: "Waiting",
          detail:
            "Walka did not watch this session begin, so it will not start the belt by itself. Stand still, then start moving your character - or press Resume.",
          action: { kind: "resume", label: "Resume", note: RESUME_NOTE },
        };
      }
      return {
        tone: "warn",
        icon: "warn",
        title: "Paused - game signal lost",
        short: "Waiting",
        detail: `The belt was paused because the game signal stayed away. ${WINDOW_ADVICE} Then move your character, or press Resume.`,
        action: { kind: "resume", label: "Resume", note: "The belt starts once the signal is back." },
      };

    case "user_stopped":
      if (reason.startsWith("belt is not moving")) {
        return {
          tone: "bad",
          icon: "stop",
          title: "Belt is not moving",
          short: "Stopped",
          detail:
            "The pad did not start, or it stopped by itself. Check the pad - power, safety key, an error on its display - then press Resume.",
          action: { kind: "resume", label: "Resume", note: RESUME_NOTE },
        };
      }
      return {
        tone: "bad",
        icon: "stop",
        title: "Stopped",
        short: "Stopped",
        detail:
          "The belt was stopped with STOP or at the treadmill, and it stays off until you say so: press Resume, press start on the pad, or pause and resume in game.",
        action: { kind: "resume", label: "Resume", note: RESUME_NOTE },
      };

    default: {
      if (g.phase !== "idle") {
        // A phase this page does not know yet: say what the companion says.
        return { tone: "idle", icon: "idle", title: capitalise(g.phase.replace(/_/g, " ")), short: capitalise(g.phase.replace(/_/g, " ")), detail: sentence(reason) };
      }
      // idle: no session - the governor keeps its hands off the belt.
      if (s.session && s.session.source === "free" && t.speed_kmh > 0.05) {
        return {
          tone: "ok",
          icon: "go",
          title: "Walking with the pad's remote",
          short: "Free walk",
          detail:
            "The belt is running from the pad's own controls. Walka records the walk but does not touch the speed. To let the game set the pace, type /walka start in game.",
        };
      }
      const how = s.source.beacon
        ? "In game, type /walka start or press Start on the Walka HUD. For any other game, start a manual session."
        : "Screen reading is off, so start a manual session to walk.";
      return {
        tone: "idle",
        icon: "idle",
        title: "Ready",
        short: "Ready",
        detail: `No session is running, so Walka leaves the belt alone. ${how}`,
      };
    }
  }
}

/** What to show when the event stream is down. */
export const OFFLINE = {
  tone: "bad",
  icon: "x",
  title: "Companion not running",
  short: "Offline",
  detail:
    "This page has lost the Walka companion and cannot see or control the belt. If the belt is moving, stop it with the pad's own remote. The page reconnects by itself.",
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
