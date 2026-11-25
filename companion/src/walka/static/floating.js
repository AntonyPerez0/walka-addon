// The floating window: the walk at a glance, on top of the game.
//
// The same snapshot the dashboard gets (one EventSource, twice a second),
// shown the way the addon's in-game panel used to show its estimates: the
// speed, steps, calories, distance and time of the walk being recorded -
// or today's, with no walk - and today's steps against the daily goal.
// Everything here is measured by the treadmill.

import { $, U, fmt, post, setAttr, setShown, setText, setTransform, state } from "./util.js";

// A snapshot older than this: the companion is gone (it is closing).
const STALE_AFTER_MS = 3000;

let lastMessageAt = 0;
let snapshot = null;

/** What the panel's header says: [state, words]. From the treadmill alone. */
function headline(s) {
  const t = s.treadmill;
  if (!t.connected) return ["off", t.want_connected ? "Looking for the treadmill" : "No treadmill"];
  if (t.moving) return ["walking", "Walking"];
  if (t.state === "starting") return ["off", "Counting down"];
  if (t.state === "paused") return ["paused", "Paused at the treadmill"];
  return ["off", "Belt stopped"];
}

function render() {
  const s = snapshot;
  const panel = $("panel");
  if (!s || performance.now() - lastMessageAt > STALE_AFTER_MS) {
    setAttr(panel, "data-state", "offline");
    setText($("state"), "Walka is not running");
    setText($("speed"), "-");
    return;
  }
  state.units = s.units || "mph";

  const [tone, words] = headline(s);
  setAttr(panel, "data-state", tone);
  setText($("state"), words);

  const t = s.treadmill;
  setText($("speed"), t.connected ? fmt.speed(t.speed_kmh) : "-");
  setText($("unit"), U.speedUnit);

  // The walk being recorded, or today's totals without one.
  const walk = s.session;
  const today = (s.totals && s.totals.today) || {};
  const numbers = walk || today;
  setText($("caption"), walk ? "This walk" : "Today");
  setText($("steps"), fmt.int(numbers.steps || 0));
  setText($("kcal"), fmt.int(numbers.kcal || 0));
  setText($("dist"), fmt.dist(numbers.distance_m || 0));
  setText($("dist-unit"), ` ${U.distUnit}`);
  setText($("time"), fmt.clock(numbers.seconds || 0));

  // Today's steps against the goal (0: no bar).
  const goal = (s.config && s.config.overlay && s.config.overlay.step_goal) || 0;
  setShown($("goal"), goal > 0);
  if (goal > 0) {
    const fraction = Math.max(0, Math.min(1, (today.steps || 0) / goal));
    setTransform($("goal-fill"), `scaleX(${fraction.toFixed(4)})`);
    setAttr($("goal"), "data-done", fraction >= 1 ? "1" : null);
    setAttr($("goal"), "title", `Today: ${fmt.int(today.steps || 0)} of ${fmt.int(goal)} steps`);
  }
}

function connect() {
  const stream = new EventSource("/api/events");
  stream.onmessage = (event) => {
    try {
      snapshot = JSON.parse(event.data);
    } catch {
      return;
    }
    lastMessageAt = performance.now();
    render();
  };
  stream.onerror = () => {
    render();
    if (stream.readyState === EventSource.CLOSED) setTimeout(connect, 2000);
  };
}

function init() {
  const close = $("close");
  // A press on the button must not start a drag of the window.
  close.addEventListener("mousedown", (event) => event.stopPropagation());
  close.addEventListener("click", async () => {
    try {
      await post("/api/config", { overlay: { enabled: false } });
    } catch {
      /* the companion is closing: the window goes with it */
    }
  });
  connect();
  setInterval(render, 1000); // notices a companion that went quiet
}

init();
