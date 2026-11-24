// The history view: totals, the 7-day steps chart, and the list of walks with
// per-walk detail (speed / heart-rate chart, TCX download, delete).

import { $, U, api, del, fmt, h, icon, run, setShown, setText, toast } from "./util.js";
import { SessionChart, WeekChart } from "./charts.js";
import { SOURCE_LABEL } from "./status.js";
import { otherAccountWalkWords, sentWalkWords } from "./walkaaccount.js";

const FIRST_PAGE = 200;
const ALL = 2000;

let weekChart = null;
let tableKey = "";
let sessions = [];
let limit = FIRST_PAGE;
let listTrigger = null; // changes when the set of stored walks may have changed
let sentTrigger = null; // changes when a walk may have gone to the Walka account
let loading = false;
let openId = null;
let openChart = null;
const details = new Map(); // id -> full record with samples

function plural(n, word) {
  return `${fmt.int(n)} ${word}${n === 1 ? "" : "s"}`;
}

// ---- totals and the week ------------------------------------------------------------------

function bucketLine(bucket) {
  if (!bucket.sessions) return "no walks";
  const parts = [
    fmt.distU(bucket.distance_m),
    fmt.span(bucket.seconds),
    `${fmt.int(bucket.kcal)} kcal`,
    plural(bucket.sessions, "walk"),
  ];
  // No-break spaces inside each part: a line may end after a part, never between a number and its unit.
  return parts.map((part) => part.replace(/ /g, "\u00a0")).join(" \u00b7 ");
}

function updateTotals(s) {
  const totals = s.totals;
  for (const [name, bucket] of [
    ["today", totals.today],
    ["week", totals.week],
    ["all", totals.all],
  ]) {
    setText($(`tot-${name}-steps`), fmt.int(bucket.steps));
    setText($(`tot-${name}-sub`), bucketLine(bucket));
  }
  setText($("tot-streak"), fmt.int(totals.streak));
  setText($("tot-streak-unit"), totals.streak === 1 ? "day" : "days");
  let note = "Walk today to start a streak.";
  if (totals.streak > 0)
    note = totals.today.sessions ? "in a row, including today" : "in a row - walk today to keep it going";
  setText($("tot-streak-sub"), note);
}

function parseDay(day) {
  const [y, m, d] = day.split("-").map(Number);
  return new Date(y, m - 1, d);
}

function updateWeek(s) {
  const days = s.totals.days || [];
  const items = days.map((day, i) => {
    const date = parseDay(day.day);
    const today = i === days.length - 1;
    return {
      label: today ? "Today" : date.toLocaleDateString([], { weekday: "short" }),
      name: date.toLocaleDateString([], { weekday: "long", day: "numeric", month: "long" }),
      value: day.steps,
      today,
      readout: [
        { value: fmt.int(day.steps), label: "steps" },
        { value: fmt.distU(day.distance_m), label: "walked" },
        { value: fmt.span(day.seconds), label: "on the belt" },
        { value: fmt.int(day.kcal), label: "kcal" },
      ],
    };
  });
  weekChart.setData(items);

  const key = JSON.stringify(items);
  if (key === tableKey) return;
  tableKey = key;
  $("week-table")
    .querySelector("tbody")
    .replaceChildren(
      ...items.map((item, i) =>
        h(
          "tr",
          null,
          h("th", { scope: "row", text: item.name }),
          h("td", { text: fmt.int(days[i].steps) }),
          h("td", { text: fmt.distU(days[i].distance_m) }),
          h("td", { text: fmt.span(days[i].seconds) }),
          h("td", { text: fmt.int(days[i].kcal) }),
        ),
      ),
    );
}

// ---- the list of walks ----------------------------------------------------------------------

function cell(value, unit, unitClass = "unit") {
  return h("span", { class: "r" }, value, unit ? h("span", { class: unitClass, text: unit }) : null);
}

function rowLabel(walk) {
  const parts = [
    fmt.dateTime(walk.start),
    SOURCE_LABEL[walk.source] || walk.source,
    fmt.span(walk.seconds),
    fmt.distU(walk.distance_m),
    `${fmt.int(walk.steps)} steps`,
    `${fmt.int(walk.kcal)} kcal`,
  ];
  return `${parts.join(", ")}. Show details.`;
}

function fact(label, value) {
  return h("div", null, h("dt", { text: label }), h("dd", { text: value }));
}

function closeDetail() {
  if (openChart) openChart.destroy();
  openChart = null;
  openId = null;
}

function renderDetail(panel, walk, record) {
  const chartHost = h("div", { class: "chart" });
  const from = (source, counter, estimate) => (source === counter ? "treadmill counter" : estimate);
  const facts = h(
    "dl",
    { class: "facts num" },
    fact("Average speed", fmt.speedU(walk.avg_kmh)),
    fact("Top speed", fmt.speedU(walk.max_kmh)),
    fact("Average heart rate", walk.avg_hr ? `${Math.round(walk.avg_hr)} bpm` : "-"),
    fact("Highest heart rate", walk.max_hr ? `${walk.max_hr} bpm` : "-"),
    fact("Steps", from(walk.steps_source, "treadmill", "estimate from speed")),
    fact("Distance", from(walk.distance_source, "treadmill", "from belt speed")),
    fact("Calories", walk.calories_source === "heart_rate" ? "from heart rate" : "estimate from speed"),
    fact("Treadmill", walk.treadmill || "-"),
  );

  const confirm = h("div", { class: "confirm", hidden: true });
  const removeButton = h("button", { class: "btn danger", type: "button", text: "Delete walk..." });
  const keep = h("button", { class: "btn", type: "button", text: "Keep it" });
  // A walk that was sent to the Walka account stays there: deleting it here says so, and where.
  // So does a copy that was sent from this computer to a Walka account that is not the one signed
  // in now (after signing out, say). Without either word the question is the one it always was.
  const elsewhere = walk.in_other_walka_account === true;
  let sent = null;
  if (walk.in_walka_account === true) sent = sentWalkWords(elsewhere);
  else if (elsewhere) sent = otherAccountWalkWords();
  const reallyRemove = h("button", {
    class: "btn danger solid",
    type: "button",
    text: sent ? sent.button : "Delete permanently",
  });
  const question = sent
    ? h("p", null, sent.question)
    : h("p", {
        text: "Delete this walk? It disappears from your totals here and from the addon's history. This cannot be undone.",
      });
  confirm.append(question, keep, reallyRemove);
  removeButton.addEventListener("click", () => {
    confirm.hidden = false;
    removeButton.hidden = true;
    keep.focus();
  });
  keep.addEventListener("click", () => {
    confirm.hidden = true;
    removeButton.hidden = false;
    removeButton.focus();
  });
  reallyRemove.addEventListener("click", () =>
    run(reallyRemove, async () => {
      await del(`/api/sessions/${encodeURIComponent(walk.id)}`);
      details.delete(walk.id);
      sessions = sessions.filter((item) => item.id !== walk.id);
      closeDetail();
      renderList();
      toast(sent ? sent.toast : "Walk deleted.", "ok");
    }),
  );

  const tcx = h("a", {
    class: "btn",
    href: `/api/sessions/${encodeURIComponent(walk.id)}/tcx`,
    download: `walka-${walk.id}.tcx`,
    title: "A TCX file imports into Garmin Connect, Strava and most fitness sites",
    text: "Download TCX",
  });

  panel.replaceChildren(facts, chartHost, h("div", { class: "btn-row" }, tcx, removeButton), confirm);
  if (openChart) openChart.destroy();
  openChart = new SessionChart(chartHost, record.samples || [], {
    speed: (kmh) => U.speed(kmh),
    speedUnit: U.speedUnit,
    label:
      `Speed${walk.avg_hr ? " and heart rate" : ""} over the walk: average ${fmt.speedU(walk.avg_kmh)}, top ${fmt.speedU(walk.max_kmh)}` +
      (walk.avg_hr ? `, average heart rate ${Math.round(walk.avg_hr)} bpm` : "") +
      ". Use the arrow keys to read values.",
  });
}

async function openDetail(walk, button, panel) {
  openId = walk.id;
  button.setAttribute("aria-expanded", "true");
  panel.hidden = false;
  let record = details.get(walk.id);
  if (!record) {
    panel.replaceChildren(h("p", { class: "muted", text: "Loading the walk..." }));
    try {
      record = await api(`/api/sessions/${encodeURIComponent(walk.id)}`);
      details.set(walk.id, record);
    } catch (error) {
      panel.replaceChildren(h("p", { class: "error", role: "alert", text: error.message }));
      return;
    }
    if (openId !== walk.id) return; // closed or switched while loading
  }
  renderDetail(panel, walk, record);
}

function renderList() {
  const list = $("session-list");
  if (openChart) openChart.destroy();
  openChart = null;
  const reopen = openId;
  openId = null;

  setShown($("sessions-empty"), !sessions.length && !loading);
  setShown($("sessions-more"), sessions.length >= limit && limit < ALL);
  setText($("sessions-count"), sessions.length ? plural(sessions.length, "walk") : "");

  list.replaceChildren(
    ...sessions.map((walk) => {
      const panelId = `walk-${walk.id}`;
      const panel = h("div", { class: "sdetail", id: panelId, hidden: true });
      const button = h(
        "button",
        {
          class: "srow srow-btn",
          type: "button",
          "aria-expanded": "false",
          "aria-controls": panelId,
          "aria-label": rowLabel(walk),
        },
        h("span", { class: "when", text: fmt.dateTime(walk.start) }),
        h("span", { class: "src", text: SOURCE_LABEL[walk.source] || walk.source }),
        cell(fmt.clock(walk.seconds)),
        cell(fmt.dist(walk.distance_m), U.distUnit),
        cell(fmt.int(walk.steps), "steps", "unit narrow-only"),
        cell(fmt.int(walk.kcal), "kcal"),
        cell(fmt.speed(walk.avg_kmh), U.speedUnit),
        cell(walk.avg_hr ? String(Math.round(walk.avg_hr)) : "-", walk.avg_hr ? "bpm" : ""),
        h("span", { class: "chev" }, icon("chevron", 20)),
      );
      button.addEventListener("click", () => {
        const wasOpen = openId === walk.id;
        // One walk open at a time.
        for (const other of list.querySelectorAll('.srow-btn[aria-expanded="true"]')) {
          other.setAttribute("aria-expanded", "false");
          other.nextElementSibling.hidden = true;
        }
        closeDetail();
        if (!wasOpen) openDetail(walk, button, panel);
      });
      const item = h("li", null, button, panel);
      if (walk.id === reopen) openDetail(walk, button, panel);
      return item;
    }),
  );
}

/** Fetch the list. `quietly`: only to learn which walks are in the Walka account by now - the
    list is drawn again only if something in it changed, and a failure is tried again later. */
async function loadSessions(quietly = false) {
  if (loading) return;
  loading = true;
  let same = false;
  try {
    const data = await api(`/api/sessions?limit=${limit}`);
    const found = data.sessions || [];
    same = quietly && JSON.stringify(found) === JSON.stringify(sessions);
    sessions = found;
    setShown($("sessions-error"), false);
  } catch (error) {
    if (quietly) {
      sentTrigger = null;
      same = true;
    } else {
      setText($("sessions-error"), `Could not load your walks. ${error.message}`);
      setShown($("sessions-error"), true);
      listTrigger = null; // try again on the next snapshot
    }
  } finally {
    loading = false;
  }
  if (!same) renderList();
}

// ---- wiring --------------------------------------------------------------------------------

export function initHistory() {
  weekChart = new WeekChart($("week-chart"));
  $("week-toggle").addEventListener("click", (event) => {
    const button = event.currentTarget;
    const asTable = button.getAttribute("aria-pressed") !== "true";
    button.setAttribute("aria-pressed", String(asTable));
    button.textContent = asTable ? "Show as chart" : "Show as table";
    $("week-table").hidden = !asTable;
    $("week-chart").hidden = asTable;
  });
  $("sessions-more").addEventListener("click", () => {
    limit = ALL;
    loadSessions();
  });
}

export function updateHistory(s) {
  updateTotals(s);
  updateWeek(s);
  // A walk was saved, discarded or deleted somewhere: refresh the list.
  const trigger = `${s.totals.all.sessions}|${s.last_session ? s.last_session.id : ""}`;
  // The Walka account's part of the snapshot changed (only there when Walka has that part): a
  // walk may have been sent, and the question before deleting it then says where its copy is.
  const sent = s.walka_account ? s.walka_account.rev : null;
  if (trigger !== listTrigger) {
    // (Not while a list is on its way: that one may be from before the change. With the next snapshot.)
    if (loading) return;
    listTrigger = trigger;
    sentTrigger = sent;
    loadSessions();
  } else if (sent !== sentTrigger && !loading) {
    sentTrigger = sent;
    loadSessions(true);
  }
}

/** Display units changed: every number in the list is stale. */
export function historyUnitsChanged() {
  if (sessions.length) renderList();
}
