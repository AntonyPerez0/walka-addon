// How to make a watch or strap send its heart rate as a standard Bluetooth
// sensor, maker by maker. From each maker's own help pages (linked), as they
// read in November 2026. Shown in Settings -> Heart-rate sensor and in the
// first-run guide.

import { h } from "./util.js";
import { tr } from "./i18n.js";

const BRANDS = [
  {
    name: "Garmin",
    steps:
      "Settings → Health & Wellness (on some models: Watch Sensors) → Wrist Heart Rate → Broadcast Heart Rate, or the broadcast control in the controls menu; then press Start. Many models can also broadcast during an activity (an option in the activity's settings).",
    notes:
      "Only models on Garmin's list broadcast over Bluetooth; others send ANT+ only, which Walka cannot read. Broadcasting uses more battery.",
    link: "https://support.garmin.com/en-US/?faq=Zj1947s6pqAHzBCAhLhrC9",
  },
  {
    name: "Polar watches",
    steps:
      "Start a training session's pre-training mode, then: Settings → Sport settings → Heart rate → Share heart rate (Vantage V3, Grit X2 Pro, Ignite 3), or the quick menu → Share HR with other devices (Pacer, Ignite 2, Unite). Then pick the watch in Walka's scan.",
    notes:
      "It only shares while the watch is in pre-training or training mode. If the watch is sharing with your phone, stop that first.",
    link: "https://support.polar.com/en/hr-sensor-mode",
  },
  {
    name: "Polar straps (H10, Verity Sense)",
    steps: "Wear it: it shows up in Walka's scan. A strap's electrodes work better a little damp.",
    notes: "It may be connected to one other app at a time (two with Polar's dual-Bluetooth setting).",
    link: "https://support.polar.com/en/support/can_i_pair_h10_with_several_devices",
  },
  {
    name: "Google Pixel Watch (2 and newer)",
    steps:
      "Swipe down → Connected Fitness → Connect (or Settings → Connectivity → Share Heart Rate), then pick the watch in Walka's scan and accept on the watch.",
    notes: "Disconnect it from other equipment or apps first. Sharing uses more battery.",
    link: "https://support.google.com/googlehealth/answer/14236705",
  },
  {
    name: "Fitbit (Charge 6, and other models Google lists)",
    steps:
      'Swipe down from the clock → "Broadcast HR" (or start it from an exercise), then pick the tracker in Walka\'s scan and tap Share → Start on the tracker.',
    notes: "The Charge 6 shares with one device at a time.",
    link: "https://support.google.com/googlehealth/answer/14236705",
  },
  {
    name: "COROS",
    steps: "On the watch face, hold BACK → Toolbox → Broadcast HR.",
    notes:
      "Only one Bluetooth connection is allowed while it broadcasts: switch off your phone's Bluetooth (or unpair the watch from it) first.",
    link: "https://support.coros.com/hc/en-us/articles/360040256991-Broadcasting-Heart-Rate",
  },
  {
    name: "Suunto (Vertical 2, Race 2, Race, Race S)",
    steps:
      'Switch Broadcast on in a sport mode\'s settings and start that sport mode; the watch shows up as "Suunto watch HR".',
    notes: "It broadcasts only while that sport mode is in use.",
    link: "https://us.suunto.com/pages/feature-feed",
  },
  {
    name: "Amazfit Helio Strap",
    steps: "Zepp app → Device → Amazfit Helio Strap → Health Monitoring → Heart Rate Push.",
    notes: "Amazfit's own pages describe this for the Helio Strap; for Amazfit watches they describe no such setting.",
    link: "https://support.amazfit.com/us/amazfit_helio_strap/docs/GyUIdtHLvoMqNUxOkuNcQB4hn4d",
  },
  {
    name: "Wahoo TICKR",
    steps: "Wear it: it wakes up when it feels your heartbeat, and shows up in Walka's scan.",
    notes:
      "Older TICKRs take one Bluetooth connection at a time. Wahoo recommends ANT+ on Windows PCs; Walka reads Bluetooth only, so if it drops out, close other apps that use it.",
    link: "https://support.wahoofitness.com/hc/en-us/articles/115000320290-Wahoo-TICKR-Product-Instructions",
  },
  {
    name: "Apple Watch, Samsung Galaxy Watch",
    steps:
      "Neither has a built-in standard Bluetooth heart-rate broadcast. A watch app can add one: see \"Heart rate from an Apple Watch or a Galaxy Watch\" in Walka's help, which lists apps and what their store pages say.",
    notes: "On an Apple Watch such apps send the heart rate through the iPhone; on Wear OS (Galaxy Watch4 and newer) from the watch itself.",
    link: "https://github.com/AntonyPerez0/walka-addon/blob/HEAD/docs/HEART-RATE.md",
  },
];

/** The help, as a folded section: put it in ``host``. */
export function renderHeartRateHelp(host) {
  const items = BRANDS.map((brand) =>
    h(
      "div",
      { class: "hr-brand" },
      h("dt", { text: brand.name }),
      h(
        "dd",
        {},
        h("p", { text: brand.steps }),
        h("p", { class: "muted small" }, tr(brand.notes), " ", h("a", { href: brand.link, target: "_blank", rel: "noopener noreferrer", text: tr("The maker's help") })),
      ),
    ),
  );
  host.replaceChildren(
    h(
      "details",
      { class: "advanced hr-help" },
      h("summary", { text: "How to switch on heart-rate broadcasting" }),
      h(
        "div",
        { class: "advanced-body" },
        h("p", {
          class: "muted small",
          text: "Walka reads any device that sends heart rate as a standard Bluetooth sensor. Many watches do it only once it is switched on:",
        }),
        h("dl", { class: "hr-brands" }, ...items),
      ),
    ),
  );
}
