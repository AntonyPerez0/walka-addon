// The dashboard's two charts, as plain inline SVG.
//
// * WeekChart    - steps per day, last 7 days: thin columns on one baseline.
// * SessionChart - one walk: speed and (if recorded) heart rate as two small
//                  panels sharing the time axis. Never two scales on one plot.
//
// Both redraw only when their data or their width changes, carry a hover /
// keyboard readout, and keep text in ink colours - only marks wear the series
// colour.

import { fmt, tooltip } from "./util.js";

const NS = "http://www.w3.org/2000/svg";

function el(name, attrs, ...children) {
  const node = document.createElementNS(NS, name);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value != null) node.setAttribute(key, value);
  }
  for (const child of children) {
    if (child != null) node.append(child.nodeType ? child : document.createTextNode(String(child)));
  }
  return node;
}

/** A clean axis: steps of 1, 2, 2.5 or 5 x 10^n, about `target` ticks. */
export function niceScale(max, target = 4) {
  if (!(max > 0)) return { top: 1, ticks: [0, 1] };
  const raw = max / target;
  const pow = 10 ** Math.floor(Math.log10(raw));
  const norm = raw / pow;
  const step = (norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 2.5 ? 2.5 : norm <= 5 ? 5 : 10) * pow;
  const count = Math.max(1, Math.ceil(max / step - 1e-9));
  const ticks = [];
  for (let i = 0; i <= count; i += 1) ticks.push(+(i * step).toFixed(6));
  return { top: count * step, ticks };
}

function tickLabel(value, top) {
  if (top >= 10000) return value === 0 ? "0" : `${+(value / 1000).toFixed(1)}K`;
  if (top < 10 && !Number.isInteger(top)) return value.toFixed(1);
  return fmt.int(value);
}

/** Redraw on width changes, at most once per frame. */
function watchWidth(host, redraw) {
  let frame = 0;
  let lastWidth = -1;
  const observer = new ResizeObserver(() => {
    const width = host.clientWidth;
    if (width === lastWidth) return;
    lastWidth = width;
    cancelAnimationFrame(frame);
    frame = requestAnimationFrame(redraw);
  });
  observer.observe(host);
  return observer;
}

// ---- steps per day -------------------------------------------------------------------

export class WeekChart {
  /** host: an empty block element; the chart fills its width. */
  constructor(host) {
    this.host = host;
    this.items = [];
    this.key = "";
    this.drawnKey = "";
    watchWidth(host, () => this.render(true));
  }

  /** items: [{label, name, value, today, readout}] oldest first. */
  setData(items) {
    const key = JSON.stringify(items);
    if (key === this.key) return;
    this.key = key;
    this.items = items;
    this.render(false);
  }

  render(force) {
    const width = this.host.clientWidth;
    if (!width || !this.items.length) return; // hidden view: drawn when it gets a size
    const drawnKey = `${width}|${this.key}`;
    if (!force && drawnKey === this.drawnKey) return;
    this.drawnKey = drawnKey;

    const items = this.items;
    const height = 232;
    const m = { l: 44, r: 8, t: 24, b: 28 };
    const plotW = width - m.l - m.r;
    const plotH = height - m.t - m.b;
    const y0 = m.t + plotH;
    const max = Math.max(...items.map((item) => item.value));
    const { top, ticks } = niceScale(max, 4);
    const band = plotW / items.length;
    const barW = Math.min(24, band * 0.6);
    const yOf = (value) => y0 - (value / top) * plotH;

    const svg = el("svg", {
      viewBox: `0 0 ${width} ${height}`,
      height,
      role: "group",
      "aria-label": "Steps per day for the last seven days",
    });

    if (max > 0) {
      for (const tick of ticks) {
        const y = Math.round(yOf(tick)) + 0.5;
        if (tick > 0) svg.append(el("line", { class: "grid", x1: m.l, x2: width - m.r, y1: y, y2: y }));
        svg.append(el("text", { x: m.l - 8, y: y + 4, "text-anchor": "end" }, tickLabel(tick, top)));
      }
    }
    svg.append(el("line", { class: "axis", x1: m.l, x2: width - m.r, y1: y0 + 0.5, y2: y0 + 0.5 }));

    // Label only the bars the eye is looking for: today and the best day.
    const best = items.reduce((a, b) => (b.value > a.value ? b : a), items[0]);
    items.forEach((item, i) => {
      const x = m.l + i * band + (band - barW) / 2;
      const y = yOf(item.value);
      const group = el("g", { class: "col" });
      if (item.value > 0) {
        const r = Math.min(4, barW / 2, y0 - y);
        group.append(
          el("path", {
            class: "bar",
            d: `M${x},${y0} V${y + r} Q${x},${y} ${x + r},${y} H${x + barW - r} Q${x + barW},${y} ${x + barW},${y + r} V${y0} Z`,
          }),
        );
        if (item.today || item === best) {
          group.append(
            el(
              "text",
              { class: "label-strong", x: x + barW / 2, y: y - 7, "text-anchor": "middle" },
              fmt.int(item.value),
            ),
          );
        }
      }
      group.append(
        el(
          "text",
          {
            class: item.today ? "label-strong" : null,
            x: m.l + i * band + band / 2,
            y: height - 8,
            "text-anchor": "middle",
          },
          item.label,
        ),
      );
      // The hit target is the whole column, not the painted bar.
      const hit = el("rect", {
        class: "hit",
        x: m.l + i * band + 1,
        y: m.t - 16,
        width: band - 2,
        height: plotH + 16 + m.b - 2,
        rx: 6,
        tabindex: 0,
        role: "img",
        "aria-label": `${item.name}: ${item.readout.map((row) => `${row.value} ${row.label}`).join(", ")}`,
      });
      const show = (px, py) => tooltip.show(px, py, [{ value: item.name, label: "" }, ...item.readout]);
      hit.addEventListener("pointermove", (event) => show(event.clientX, event.clientY));
      hit.addEventListener("pointerleave", () => tooltip.hide());
      hit.addEventListener("focus", () => {
        const box = hit.getBoundingClientRect();
        show(box.left + box.width / 2, box.top + 24);
      });
      hit.addEventListener("blur", () => tooltip.hide());
      group.append(hit);
      svg.append(group);
    });

    if (!(max > 0)) {
      svg.append(
        el("text", { x: m.l + plotW / 2, y: m.t + plotH / 2, "text-anchor": "middle" }, "No steps in the last 7 days"),
      );
    }
    this.host.replaceChildren(svg);
  }
}

// ---- one walk: speed and heart rate over time ---------------------------------------------

function linePath(points) {
  let d = "";
  let pen = false;
  for (const point of points) {
    if (point == null) {
      pen = false;
      continue;
    }
    d += `${pen ? "L" : "M"}${point[0].toFixed(1)},${point[1].toFixed(1)}`;
    pen = true;
  }
  return d;
}

function timeStep(total, maxTicks) {
  const steps = [5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200];
  return steps.find((step) => total / step <= maxTicks) || 14400;
}

export class SessionChart {
  /**
   * samples: [[seconds, kmh, bpm|null], ...]
   * options: {speed: kmh => display value, speedUnit, label}
   */
  constructor(host, samples, options) {
    this.host = host;
    this.options = options;
    this.samples = samples.filter((row) => Array.isArray(row) && Number.isFinite(row[0]));
    this.index = -1;
    this.observer = watchWidth(host, () => this.render());
    this.render();
  }

  destroy() {
    this.observer.disconnect();
    tooltip.hide();
  }

  render() {
    const width = this.host.clientWidth;
    if (!width) return;
    const samples = this.samples;
    if (samples.length < 2) {
      const note = document.createElement("p");
      note.className = "chart-empty";
      note.textContent = "This walk was too short to draw.";
      this.host.replaceChildren(note);
      return;
    }
    const { speed, speedUnit, label } = this.options;
    const times = samples.map((row) => row[0]);
    const tMax = Math.max(times[times.length - 1], 1);
    const speeds = samples.map((row) => speed(row[1] || 0));
    const beats = samples.map((row) => (row[2] ? row[2] : null));
    const hasHr = beats.some((bpm) => bpm != null);

    const m = { l: 44, r: 12, t: 26, b: 28 };
    const plotW = width - m.l - m.r;
    const xOf = (t) => m.l + (t / tMax) * plotW;

    const panels = [];
    const speedScale = niceScale(Math.max(...speeds, 0.5), 3);
    panels.push({
      cls: "s1",
      title: `Speed (${speedUnit})`,
      values: speeds,
      lo: 0,
      hi: speedScale.top,
      ticks: speedScale.ticks,
      tick: (value) => (speedScale.top < 10 ? value.toFixed(1) : String(value)),
      height: 150,
      area: true,
      read: (value) => ({ value: `${value.toFixed(1)} ${speedUnit}`, label: "speed", key: "s1" }),
    });
    if (hasHr) {
      const known = beats.filter((bpm) => bpm != null);
      const lo = Math.floor((Math.min(...known) - 5) / 10) * 10;
      const hi = Math.max(lo + 20, Math.ceil((Math.max(...known) + 5) / 10) * 10);
      const step = (hi - lo) / 10 > 4 ? 20 : 10;
      const ticks = [];
      for (let value = lo; value <= hi + 1e-9; value += step) ticks.push(value);
      panels.push({
        cls: "s2",
        title: "Heart rate (bpm)",
        values: beats,
        lo,
        hi: ticks[ticks.length - 1] >= hi ? ticks[ticks.length - 1] : hi,
        ticks,
        tick: (value) => String(value),
        height: 110,
        area: false,
        read: (value) => ({ value: `${Math.round(value)} bpm`, label: "heart rate", key: "s2" }),
      });
    }

    const gap = 38;
    let cursorY = m.t;
    for (const panel of panels) {
      panel.top = cursorY;
      panel.bottom = cursorY + panel.height;
      panel.yOf = (value) => panel.bottom - ((value - panel.lo) / (panel.hi - panel.lo)) * panel.height;
      cursorY = panel.bottom + gap;
    }
    const height = cursorY - gap + m.b;
    const plotTop = panels[0].top;
    const plotBottom = panels[panels.length - 1].bottom;

    const svg = el("svg", { viewBox: `0 0 ${width} ${height}`, height, role: "img", tabindex: 0, "aria-label": label });

    for (const panel of panels) {
      // Panel title doubles as the legend: a short stroke of the series colour.
      svg.append(
        el("line", { class: `key ${panel.cls}`, x1: m.l, x2: m.l + 14, y1: panel.top - 12, y2: panel.top - 12 }),
      );
      svg.append(el("text", { class: "label-strong", x: m.l + 20, y: panel.top - 8 }, panel.title));
      for (const tick of panel.ticks) {
        const y = Math.round(panel.yOf(tick)) + 0.5;
        const isBase = tick === panel.ticks[0];
        svg.append(el("line", { class: isBase ? "axis" : "grid", x1: m.l, x2: width - m.r, y1: y, y2: y }));
        svg.append(el("text", { x: m.l - 8, y: y + 4, "text-anchor": "end" }, panel.tick(tick)));
      }
      const points = panel.values.map((value, i) => (value == null ? null : [xOf(times[i]), panel.yOf(value)]));
      if (panel.area) {
        const base = panel.yOf(panel.lo).toFixed(1);
        svg.append(
          el("path", {
            class: "area",
            d: `${linePath(points)}L${xOf(times[times.length - 1]).toFixed(1)},${base}L${xOf(times[0]).toFixed(1)},${base}Z`,
          }),
        );
      }
      svg.append(el("path", { class: `line ${panel.cls}`, d: linePath(points) }));
    }

    // Time axis under the last panel.
    const step = timeStep(tMax, Math.max(2, Math.floor(plotW / 80)));
    for (let t = 0; t <= tMax + 1e-9; t += step) {
      const anchor = t === 0 ? "start" : xOf(t) > width - m.r - 24 ? "end" : "middle";
      svg.append(el("text", { x: xOf(t), y: plotBottom + 19, "text-anchor": anchor }, fmt.clock(t)));
    }

    // The readout: a crosshair finds the time; one tooltip lists every series.
    const cross = el("line", { class: "cross", y1: plotTop, y2: plotBottom, visibility: "hidden" });
    const dots = panels.map((panel) => el("circle", { class: `dot ${panel.cls}`, r: 5, visibility: "hidden" }));
    svg.append(cross, ...dots);
    const overlay = el("rect", { class: "hit", x: m.l, y: plotTop, width: plotW, height: plotBottom - plotTop });
    svg.append(overlay);

    const nearest = (t) => {
      let lo = 0;
      let hi = times.length - 1;
      while (hi - lo > 1) {
        const mid = (lo + hi) >> 1;
        if (times[mid] < t) lo = mid;
        else hi = mid;
      }
      return t - times[lo] <= times[hi] - t ? lo : hi;
    };

    const select = (index, px, py) => {
      this.index = index;
      const x = xOf(times[index]);
      cross.setAttribute("x1", x);
      cross.setAttribute("x2", x);
      cross.setAttribute("visibility", "visible");
      const rows = [];
      panels.forEach((panel, p) => {
        const value = panel.values[index];
        if (value == null) {
          dots[p].setAttribute("visibility", "hidden");
          return;
        }
        dots[p].setAttribute("cx", x);
        dots[p].setAttribute("cy", panel.yOf(value));
        dots[p].setAttribute("visibility", "visible");
        rows.push(panel.read(value));
      });
      rows.push({ value: fmt.clock(times[index]), label: "into the walk" });
      if (px == null) {
        const box = svg.getBoundingClientRect();
        px = box.left + (x / width) * box.width;
        py = box.top + plotTop + 30;
      }
      tooltip.show(px, py, rows);
    };

    const clear = () => {
      this.index = -1;
      cross.setAttribute("visibility", "hidden");
      for (const dot of dots) dot.setAttribute("visibility", "hidden");
      tooltip.hide();
    };

    overlay.addEventListener("pointermove", (event) => {
      const box = svg.getBoundingClientRect();
      const x = ((event.clientX - box.left) / box.width) * width;
      select(nearest(((x - m.l) / plotW) * tMax), event.clientX, event.clientY);
    });
    overlay.addEventListener("pointerleave", clear);
    svg.addEventListener("keydown", (event) => {
      const jump = event.shiftKey ? 10 : 1;
      let index = this.index < 0 ? 0 : this.index;
      if (event.key === "ArrowRight") index += jump;
      else if (event.key === "ArrowLeft") index -= jump;
      else if (event.key === "Home") index = 0;
      else if (event.key === "End") index = times.length - 1;
      else if (event.key === "Escape") {
        clear();
        return;
      } else return;
      event.preventDefault();
      select(Math.min(times.length - 1, Math.max(0, index)));
    });
    svg.addEventListener("blur", clear);

    this.host.replaceChildren(svg);
  }
}
