// The settings view.
//
// Forms are built from the field lists below. Values are shown in the user's
// units and converted back to the backend's metric units on save; only fields
// that were actually edited are sent (POST /api/config takes partial updates),
// so an untouched value never drifts through a unit round-trip. The server
// clamps everything to its own bounds; the ranges here mirror config._BOUNDS
// so mistakes are caught before they are sent.

import { $, CM_PER_INCH, U, api, h, post, run, setShown, setText, setTone, toast } from "./util.js";
import { installAddon, scanInto, syncAddon } from "./live.js";

let config = null; // the last config seen
let configKey = "";
let profileSet = true;
const forms = [];
let onUnitsChosen = () => {};

// ---- helpers ---------------------------------------------------------------------------

let nextId = 0;
const uid = () => `f${(nextId += 1)}`;

function getPath(object, path) {
  return path.split(".").reduce((value, key) => (value == null ? undefined : value[key]), object);
}

function setPath(object, path, value) {
  const keys = path.split(".");
  let target = object;
  for (const key of keys.slice(0, -1)) {
    if (typeof target[key] !== "object" || target[key] === null) target[key] = {};
    target = target[key];
  }
  target[keys[keys.length - 1]] = value;
}

function parseNumber(text) {
  const clean = text.trim().replace(",", ".");
  if (!/^\d+(\.\d*)?$|^\.\d+$/.test(clean)) return NaN;
  return Number(clean);
}

const trim = (value, digits) => String(+value.toFixed(digits));
/** Two texts that mean the same number ("3" and "3.0") are not a change. */
const sameText = (a, b) => a === b || (a !== "" && b !== "" && parseNumber(a) === parseNumber(b));
const ceilTo = (value, digits) => Math.ceil(value * 10 ** digits - 1e-9) / 10 ** digits;
const floorTo = (value, digits) => Math.floor(value * 10 ** digits + 1e-9) / 10 ** digits;

const same = (value) => value;
const KINDS = {
  speed: {
    unit: () => U.speedUnit,
    digits: () => 1,
    fixed: true,
    shown: (kmh) => U.speed(kmh),
    metric: (v) => U.toKmh(v),
  },
  rate: {
    unit: () => `${U.speedUnit} per second`,
    digits: () => 2,
    fixed: true,
    shown: (kmh) => U.speed(kmh),
    metric: (v) => U.toKmh(v),
  },
  seconds: { unit: () => "s", digits: () => 1, shown: same, metric: same },
  minutes: { unit: () => "min", digits: () => 1, shown: (s) => s / 60, metric: (m) => m * 60 },
  weight: {
    unit: () => U.weightUnit,
    digits: () => (U.mph ? 0 : 1),
    shown: (kg) => U.weight(kg),
    metric: (v) => U.toKg(v),
  },
  years: { unit: () => "years", digits: () => 0, shown: same, metric: same },
};

// ---- fields ----------------------------------------------------------------------------

class Field {
  constructor(spec) {
    this.spec = spec;
    this.id = uid();
    this.errorEl = h("p", { class: "error", id: `${this.id}-err`, "aria-live": "polite" });
    this.helpEl = h("p", { class: "help", id: `${this.id}-help`, text: spec.help || "" });
    this.sent = undefined;
  }

  describedBy() {
    return `${this.id}-help ${this.id}-err`;
  }

  setError(message) {
    setText(this.errorEl, message);
    for (const input of this.inputs || []) input.setAttribute("aria-invalid", message ? "true" : "false");
  }

  error() {
    return "";
  }

  /** Did the server store something else than what was sent (it clamps)? */
  differs() {
    return false;
  }
}

class NumberField extends Field {
  constructor(spec) {
    super(spec);
    this.kind = KINDS[spec.kind];
    this.input = h("input", {
      id: this.id,
      class: "short",
      type: "text",
      inputmode: "decimal",
      autocomplete: "off",
      "aria-describedby": this.describedBy(),
    });
    this.inputs = [this.input];
    this.unitEl = h("span", { class: "unit" });
    this.rangeEl = h("span");
    this.helpEl.append(" ", this.rangeEl);
    const label = h("label", { for: this.id, text: spec.label });
    if (spec.optional) label.append(" ", h("span", { class: "opt", text: "(optional)" }));
    this.el = h(
      "div",
      { class: "field" },
      label,
      h("div", { class: "control" }, this.input, this.unitEl),
      this.helpEl,
      this.errorEl,
    );
    this.base = "";
  }

  bounds() {
    const digits = this.kind.digits();
    return {
      lo: ceilTo(this.kind.shown(this.spec.min), digits),
      hi: floorTo(this.kind.shown(this.spec.max), digits),
      digits,
    };
  }

  fill(source) {
    const metric = getPath(source, this.spec.path);
    const { lo, hi, digits } = this.bounds();
    const shown = metric == null ? null : this.kind.shown(metric);
    this.base = shown == null ? "" : this.kind.fixed ? shown.toFixed(digits) : trim(shown, digits);
    this.input.value = this.base;
    setText(this.unitEl, this.kind.unit());
    setText(this.rangeEl, `(${trim(lo, digits)} to ${trim(hi, digits)})`);
    this.setError("");
  }

  changed() {
    return !sameText(this.input.value.trim(), this.base);
  }

  error() {
    const text = this.input.value.trim();
    if (!text) return this.spec.optional ? "" : "Enter a number.";
    const value = parseNumber(text);
    const { lo, hi, digits } = this.bounds();
    if (Number.isNaN(value)) return "Enter a number, for example " + trim((lo + hi) / 2, digits) + ".";
    if (value < lo - 1e-9 || value > hi + 1e-9)
      return `Between ${trim(lo, digits)} and ${trim(hi, digits)} ${this.kind.unit()}.`;
    return "";
  }

  write(patch) {
    const text = this.input.value.trim();
    if (!text) {
      this.sent = null;
    } else {
      const metric = this.kind.metric(parseNumber(text));
      this.sent = this.spec.kind === "years" ? Math.round(metric) : +metric.toFixed(6);
    }
    setPath(patch, this.spec.path, this.sent);
  }

  differs(source) {
    const stored = getPath(source, this.spec.path);
    if (this.sent == null || stored == null) return this.sent !== (stored ?? null);
    return Math.abs(stored - this.sent) > 1e-4 * Math.max(1, Math.abs(this.sent));
  }
}

/** Height: feet and inches, or centimetres. Stored in cm (120-230). */
class HeightField extends Field {
  constructor(spec) {
    super(spec);
    const input = (label) =>
      h("input", {
        class: "short",
        type: "text",
        inputmode: "decimal",
        autocomplete: "off",
        "aria-label": label,
        "aria-describedby": this.describedBy(),
      });
    this.major = input("Height");
    this.minor = input("Height, inches");
    this.majorUnit = h("span", { class: "unit" });
    this.minorUnit = h("span", { class: "unit", text: "in" });
    this.inputs = [this.major, this.minor];
    this.rangeEl = h("span");
    this.helpEl.append(" ", this.rangeEl);
    this.el = h(
      "div",
      { class: "field", role: "group", "aria-labelledby": `${this.id}-label` },
      h("span", { class: "legend", id: `${this.id}-label`, text: spec.label }),
      h("div", { class: "control" }, this.major, this.majorUnit, this.minor, this.minorUnit),
      this.helpEl,
      this.errorEl,
    );
    this.base = ["", ""];
  }

  fill(source) {
    const cm = getPath(source, this.spec.path);
    const imperial = U.mph;
    this.minor.hidden = !imperial;
    this.minorUnit.hidden = !imperial;
    this.major.setAttribute("aria-label", imperial ? "Height, feet" : "Height in centimetres");
    setText(this.majorUnit, imperial ? "ft" : "cm");
    if (cm == null) this.base = ["", ""];
    else if (imperial) {
      const inches = Math.round((cm / CM_PER_INCH) * 2) / 2;
      this.base = [String(Math.floor(inches / 12)), trim(inches % 12, 1)];
    } else this.base = [trim(cm, 0), ""];
    this.major.value = this.base[0];
    this.minor.value = this.base[1];
    setText(this.rangeEl, imperial ? "(3 ft 11.5 in to 7 ft 6.5 in)" : "(120 to 230)");
    this.setError("");
  }

  changed() {
    const minor = this.minor.value.trim() || "0";
    return !sameText(this.major.value.trim(), this.base[0]) || (U.mph && !sameText(minor, this.base[1] || "0"));
  }

  cm() {
    const major = parseNumber(this.major.value);
    if (!U.mph) return major;
    const minor = this.minor.value.trim() ? parseNumber(this.minor.value) : 0;
    if (Number.isNaN(major) || Number.isNaN(minor) || minor >= 12) return NaN;
    return (major * 12 + minor) * CM_PER_INCH;
  }

  error() {
    const cm = this.cm();
    if (Number.isNaN(cm))
      return U.mph ? "Enter feet and inches, for example 5 ft 9 in." : "Enter a number, for example 175.";
    if (cm < 120 - 0.7 || cm > 230 + 0.7)
      return U.mph ? "Between 3 ft 11.5 in and 7 ft 6.5 in." : "Between 120 and 230 cm.";
    return "";
  }

  write(patch) {
    this.sent = +Math.min(230, Math.max(120, this.cm())).toFixed(1);
    setPath(patch, this.spec.path, this.sent);
  }
}

class TextField extends Field {
  constructor(spec) {
    super(spec);
    this.input = h("input", {
      id: this.id,
      type: "text",
      autocomplete: "off",
      spellcheck: "false",
      placeholder: spec.placeholder || null,
      "aria-describedby": this.describedBy(),
    });
    this.inputs = [this.input];
    const label = h("label", { for: this.id, text: spec.label });
    if (spec.optional) label.append(" ", h("span", { class: "opt", text: "(optional)" }));
    this.el = h("div", { class: "field" }, label, this.input, this.helpEl, this.errorEl);
    this.base = "";
  }

  fill(source) {
    this.base = String(getPath(source, this.spec.path) ?? "");
    this.input.value = this.base;
    this.setError("");
  }

  changed() {
    return this.input.value.trim() !== this.base;
  }

  error() {
    const text = this.input.value.trim();
    return this.spec.pattern && text && !this.spec.pattern.test(text) ? this.spec.patternHelp : "";
  }

  write(patch) {
    setPath(patch, this.spec.path, this.input.value.trim());
  }
}

class SelectField extends Field {
  constructor(spec) {
    super(spec);
    this.input = h(
      "select",
      { id: this.id, "aria-describedby": this.describedBy() },
      spec.options.map(([value, label]) => h("option", { value, text: label })),
    );
    this.inputs = [this.input];
    const label = h("label", { for: this.id, text: spec.label });
    if (spec.optional) label.append(" ", h("span", { class: "opt", text: "(optional)" }));
    this.el = h("div", { class: "field" }, label, this.input, this.helpEl, this.errorEl);
    this.base = "";
  }

  fill(source) {
    this.base = String(getPath(source, this.spec.path) ?? "");
    this.input.value = this.base;
  }

  changed() {
    return this.input.value !== this.base;
  }

  write(patch) {
    setPath(patch, this.spec.path, this.input.value || (this.spec.optional ? null : ""));
  }
}

class CheckField extends Field {
  constructor(spec) {
    super(spec);
    this.input = h("input", { type: "checkbox", id: this.id, "aria-describedby": `${this.id}-help` });
    this.inputs = [];
    this.helpEl = h("span", { class: "help", id: `${this.id}-help`, text: spec.help || "" });
    this.el = h(
      "label",
      { class: "check full", for: this.id },
      this.input,
      h("strong", { text: spec.label }),
      this.helpEl,
    );
    this.base = false;
  }

  fill(source) {
    this.base = !!getPath(source, this.spec.path);
    this.input.checked = this.base;
  }

  changed() {
    return this.input.checked !== this.base;
  }

  write(patch) {
    setPath(patch, this.spec.path, this.input.checked);
  }
}

const TYPES = { number: NumberField, height: HeightField, text: TextField, select: SelectField, check: CheckField };

// ---- a form card --------------------------------------------------------------------------

class Form {
  /** spec: {fields, advanced?, confirm?: section that is sent even when unchanged} */
  constructor(root, spec) {
    this.root = root;
    this.spec = spec;
    const make = (field) => new TYPES[field.type](field);
    this.main = spec.fields.map(make);
    this.advanced = (spec.advanced || []).map(make);
    this.fields = [...this.main, ...this.advanced];
    this.note = "";
    this.filled = false; // nothing to compare against until the first config arrives

    this.saveButton = h("button", { class: "btn primary", type: "submit", text: "Save", disabled: true });
    this.revertButton = h("button", { class: "btn quiet", type: "button", text: "Undo changes", hidden: true });
    this.statusEl = h("span", { class: "status", role: "status" });
    root.append(...this.main.map((field) => field.el));
    if (this.advanced.length) {
      root.append(
        h(
          "details",
          null,
          h("summary", { text: "Advanced timing" }),
          h(
            "div",
            { class: "details-grid" },
            this.advanced.map((field) => field.el),
          ),
        ),
      );
    }
    root.append(h("div", { class: "form-foot" }, this.saveButton, this.revertButton, this.statusEl));

    const refresh = () => {
      this.note = "";
      this.refresh();
    };
    root.addEventListener("input", refresh);
    root.addEventListener("change", refresh);
    root.addEventListener("submit", (event) => {
      event.preventDefault();
      this.save();
    });
    this.revertButton.addEventListener("click", () => {
      if (config) this.fill(config);
    });
  }

  get dirty() {
    return this.filled && this.fields.some((field) => field.changed());
  }

  fill(source) {
    for (const field of this.fields) field.fill(source);
    this.filled = true;
    this.refresh();
  }

  refresh() {
    let invalid = false;
    for (const field of this.fields) {
      const message = field.changed() ? field.error() : "";
      field.setError(message);
      if (message) invalid = true;
    }
    const dirty = this.dirty;
    const unconfirmed = this.spec.confirm === "profile" && !profileSet;
    this.saveButton.disabled = !this.filled || invalid || !(dirty || unconfirmed);
    this.saveButton.textContent = unconfirmed && !dirty ? "These are right - save" : "Save";
    this.revertButton.hidden = !dirty;
    setText(this.statusEl, invalid ? "Fix the marked fields to save." : dirty ? "Unsaved changes." : this.note);
  }

  async save() {
    if (this.saveButton.disabled) return;
    const patch = {};
    if (this.spec.confirm) patch[this.spec.confirm] = {};
    const sent = this.fields.filter((field) => field.changed());
    for (const field of sent) field.write(patch);
    await run(this.saveButton, async () => {
      const data = await post("/api/config", patch);
      const adjusted = sent.some((field) => field.differs(data.config));
      if (this.spec.confirm === "profile") {
        profileSet = true;
        setShown($("profile-badge"), false);
      }
      adopt(data.config, true);
      this.fill(data.config);
      this.note = adjusted ? "Saved - some values were adjusted to the allowed range." : "Saved.";
      this.refresh();
    });
  }
}

const FORMS = {
  profile: {
    confirm: "profile",
    fields: [
      {
        type: "number",
        kind: "weight",
        path: "profile.weight_kg",
        min: 30,
        max: 250,
        label: "Weight",
        help: "Calories burned scale with your weight.",
      },
      {
        type: "height",
        path: "profile.height_cm",
        label: "Height",
        help: "Sets your stride for the step estimate when the pad does not count steps itself.",
      },
      {
        type: "number",
        kind: "years",
        path: "profile.age",
        min: 5,
        max: 110,
        optional: true,
        label: "Age",
        help: "With sex, lets Walka work out calories from your heart rate when a sensor is connected.",
      },
      {
        type: "select",
        path: "profile.sex",
        optional: true,
        label: "Sex",
        options: [
          ["", "Not set"],
          ["female", "Female"],
          ["male", "Male"],
        ],
        help: "Only used by the heart-rate calorie formula.",
      },
    ],
  },
  limits: {
    fields: [
      {
        type: "number",
        kind: "speed",
        path: "limits.max_speed_kmh",
        min: 0.8,
        max: 6.5,
        label: "Max speed",
        help: "The hard cap. Nothing - the game, manual mode or a glitch - can drive the belt faster than this.",
      },
      {
        type: "number",
        kind: "rate",
        path: "limits.accel_kmh_per_s",
        min: 0.05,
        max: 1,
        label: "Speeding up",
        help: "The most speed the belt may gain each second.",
      },
      {
        type: "number",
        kind: "rate",
        path: "limits.decel_kmh_per_s",
        min: 0.1,
        max: 2,
        label: "Slowing down",
        help: "The most speed the belt may lose each second.",
      },
      {
        type: "number",
        kind: "seconds",
        path: "limits.hold_s",
        min: 0,
        max: 60,
        label: "Signal lost: hold speed for",
        help: "How long the belt keeps its speed after the game signal disappears.",
      },
      {
        type: "number",
        kind: "speed",
        path: "limits.safe_speed_kmh",
        min: 0,
        max: 4,
        label: "Then slow down to",
        help: "The safe speed it drops to next. 0 pauses the belt straight away instead.",
      },
      {
        type: "number",
        kind: "minutes",
        path: "limits.loss_pause_s",
        min: 0,
        max: 3600,
        label: "Then pause after",
        help: "How long it stays at the safe speed before the belt is paused.",
      },
      {
        type: "check",
        path: "limits.resume_requires_movement",
        label: "After a pause, the belt restarts only when the character moves",
        help: "On: a belt paused by a lost signal - or a session Walka did not watch begin - waits for your character to move, or for Resume. Off: it restarts as soon as the signal is back.",
      },
      {
        type: "text",
        path: "stop_hotkey",
        optional: true,
        label: "STOP hotkey",
        placeholder: "Off",
        help: "A key combination that stops the belt from anywhere on this PC, even while the game has focus (Windows only). Use ctrl, alt, shift or win plus a key, for example ctrl+alt+x. The key press is not passed on to the game. Empty turns it off.",
      },
    ],
    advanced: [
      {
        type: "number",
        kind: "seconds",
        path: "limits.signal_timeout_s",
        min: 1,
        max: 15,
        label: "Signal counts as lost after",
        help: "A game signal older than this is treated as lost.",
      },
      {
        type: "number",
        kind: "seconds",
        path: "limits.command_interval_s",
        min: 0.5,
        max: 5,
        label: "Time between speed commands",
        help: "How often Walka may send the pad a new speed.",
      },
      {
        type: "number",
        kind: "seconds",
        path: "limits.start_grace_s",
        min: 5,
        max: 60,
        label: "Belt start allowance",
        help: "How long the pad may take to get the belt moving (its countdown) before Walka decides it did not start.",
      },
      {
        type: "number",
        kind: "seconds",
        path: "limits.stall_s",
        min: 2,
        max: 30,
        label: "Stall detection",
        help: "A running belt that reports zero speed for this long counts as stopped at the machine.",
      },
    ],
  },
  treadmill: {
    fields: [
      {
        type: "text",
        path: "treadmill.address",
        optional: true,
        label: "Bluetooth address",
        placeholder: "AA:BB:CC:DD:EE:FF",
        pattern: /^[0-9A-Fa-f:-]{6,40}$/,
        patternHelp: "A Bluetooth address looks like AA:BB:CC:DD:EE:FF.",
        help: "Leave empty to use the first pad found. Picking a pad from a scan on the Live view fills this in. Applies the next time the pad connects.",
      },
      {
        type: "select",
        path: "treadmill.units",
        label: "Firmware speed grid",
        options: [
          ["auto", "Detect automatically"],
          ["mph", "mph - 0.1 mph steps (US pads)"],
          ["kmh", "km/h - 0.1 km/h steps"],
        ],
        help: "How the pad's own firmware counts speed. Walka rounds its commands to this grid. Applies the next time the pad connects.",
      },
      {
        type: "check",
        path: "treadmill.auto_connect",
        label: "Connect automatically",
        help: "Look for the pad as soon as the companion starts.",
      },
      {
        type: "check",
        path: "treadmill.stop_on_exit",
        label: "Stop the belt when the companion exits",
        help: "Recommended. If the companion closes while it is driving the belt, it stops the belt first.",
      },
    ],
  },
  addon: {
    fields: [
      {
        type: "text",
        path: "wow.addon_dir",
        optional: true,
        label: "Addon folder",
        placeholder: "Found automatically",
        help: "The Walka folder inside Interface\\AddOns. Leave empty to let Walka find it.",
      },
    ],
  },
};

// ---- config in, config out ------------------------------------------------------------------

function adopt(next, force) {
  const key = JSON.stringify(next);
  if (!force && key === configKey) return;
  config = next;
  configKey = key;
  for (const form of forms) {
    if (force || !form.dirty) form.fill(next);
  }
}

function updateUnitsControl(units) {
  for (const radio of document.querySelectorAll('input[name="units"]')) {
    if (document.activeElement !== radio || radio.value === units) radio.checked = radio.value === units;
  }
}

// ---- heart rate and addon cards ---------------------------------------------------------------

function updateStatusCards(s, links) {
  const hr = links.hr;
  setTone($("set-hr"), hr.tone);
  setText($("set-hr-state"), hr.state);
  setText($("set-hr-detail"), hr.detail);
  $("hr-scan").disabled = !hr.available;
  setShown($("hr-off"), !!(s.heart_rate && s.heart_rate.enabled));

  const addon = links.addon;
  setTone($("set-addon"), addon.tone);
  setText($("set-addon-state"), addon.state);
  setText($("set-addon-detail"), addon.detail);
  setShown($("addon-sync-2"), addon.installed);
}

async function findGameFolders(button) {
  const out = $("addon-find-out");
  await run(button, async () => {
    const data = await api("/api/addon/locations");
    const dirs = data.addons_dirs || [];
    const rows = [];
    if (!data.bundled) {
      rows.push(
        h("p", {
          class: "error",
          text: "This build does not include the addon files. Copy the Walka folder into Interface\\AddOns yourself, then set the addon folder above.",
        }),
      );
    }
    if (!dirs.length) {
      rows.push(
        h("p", {
          class: "muted",
          text: "No World of Warcraft folder was found in the usual places. Type the game's Interface\\AddOns folder below.",
        }),
      );
    }
    for (const dir of dirs) {
      const install = h("button", {
        class: "btn small",
        type: "button",
        text: "Install here",
        disabled: !data.bundled,
      });
      install.addEventListener("click", () => installAddon(install, dir));
      rows.push(
        h(
          "div",
          { class: "device" },
          h("div", { class: "device-text" }, h("strong", { text: dir, title: dir })),
          install,
        ),
      );
    }
    out.replaceChildren(...rows);
  });
}

// ---- wiring ---------------------------------------------------------------------------------------

export function initSettings({ onUnits }) {
  onUnitsChosen = onUnits;
  for (const root of document.querySelectorAll("form[data-form]")) {
    forms.push(new Form(root, FORMS[root.dataset.form]));
  }

  for (const radio of document.querySelectorAll('input[name="units"]')) {
    radio.addEventListener("change", async () => {
      if (!radio.checked) return;
      try {
        const data = await post("/api/config", { units: radio.value });
        onUnitsChosen(data.config.units);
        adopt(data.config, true);
      } catch (error) {
        toast(error.message, "error");
        if (config) updateUnitsControl(config.units);
      }
    });
  }

  $("hr-scan").addEventListener("click", () =>
    scanInto($("hr-scan-out"), $("hr-scan"), {
      path: "/api/heart-rate/scan",
      pickLabel: "Use this sensor",
      empty: () =>
        "No heart-rate sensor found. Wear it so it wakes up; on a watch, switch on heart-rate broadcasting. A sensor already connected to another app may not show up.",
      onPick: (device, button) =>
        run(
          button,
          () => post("/api/heart-rate/connect", { address: device.address, name: device.name || "", enabled: true }),
          {
            success: `Connecting to ${device.name || device.address}.`,
          },
        ),
    }),
  );
  $("hr-off").addEventListener("click", (event) =>
    run(event.currentTarget, () => post("/api/heart-rate/connect", { address: "", name: "", enabled: false }), {
      success: "Heart-rate sensor turned off.",
    }),
  );

  $("addon-find").addEventListener("click", (event) => findGameFolders(event.currentTarget));
  $("addon-install-auto").addEventListener("click", (event) => installAddon(event.currentTarget));
  $("addon-sync-2").addEventListener("click", (event) => syncAddon(event.currentTarget));
  $("addon-install-manual").addEventListener("click", (event) => {
    const dir = $("addon-dir-manual").value.trim();
    if (!dir) {
      toast("Type the game's Interface\\AddOns folder first.", "warn");
      $("addon-dir-manual").focus();
      return;
    }
    installAddon(event.currentTarget, dir);
  });
}

export function updateSettings(s, links) {
  const wasSet = profileSet;
  profileSet = !!(s.setup && s.setup.profile_set);
  setShown($("profile-badge"), !profileSet);
  adopt(s.config, false);
  if (wasSet !== profileSet) for (const form of forms) form.refresh();
  updateUnitsControl(s.units);
  updateStatusCards(s, links);
  setText($("version"), s.version);
}

/** Display units changed: re-show every value (unsaved edits are in the old unit, so they go). */
export function settingsUnitsChanged() {
  if (config) for (const form of forms) form.fill(config);
}
