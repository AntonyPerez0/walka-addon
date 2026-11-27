// Translating the dashboard. There are no translations yet: this is the
// machinery, so that adding one is a file and nothing else.
//
// A catalog is keyed by the English text itself, as the page shows it.
// Text that is put together while the page runs ("Walka 1.0.0 is out.") is
// matched by a key with numbered gaps: "Walka {1} is out." -> "Walka {1}
// ist da." The words the companion itself sends (a treadmill's state, the
// activity log) are matched the same way. Every piece of text the page
// shows goes through tr(): setText, setAttr, h() and toast() call it (see
// util.js), and the page's own text is translated once, at the start.
//
// The English strings, for a translator to start from:
// locales/strings.json (made by tools/dashboard_strings.py). A translation is
// locales/<language>.json - {"language": "de", "strings": {English: German}} -
// and its language code added to AVAILABLE below.

export const AVAILABLE = []; // no translations yet

let exact = null; // Map: English -> translation
let patterns = []; // [{re, out}] for keys with gaps

const ATTRIBUTES = ["aria-label", "title", "placeholder", "alt"];
const SKIP = new Set(["SCRIPT", "STYLE", "svg", "CODE", "KBD", "TEXTAREA"]);

function escapeRegExp(text) {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/** Use a catalog: {"English": "translation"}, keys may have gaps {1}, {2} ... */
export function useCatalog(strings) {
  exact = new Map();
  patterns = [];
  for (const [source, target] of Object.entries(strings || {})) {
    if (typeof target !== "string" || !target) continue;
    if (/\{\d+\}/.test(source)) {
      const order = [];
      const re = new RegExp(
        `^${escapeRegExp(source).replace(/\\\{(\d+)\\\}/g, (_m, n) => {
          order.push(Number(n));
          return "([\\s\\S]+?)";
        })}$`,
      );
      patterns.push({ re, order, out: target });
    } else {
      exact.set(source, target);
    }
  }
}

/** The page's language for this text (the text itself when there is no translation). */
export function tr(text) {
  if (!exact || typeof text !== "string" || !text) return text;
  const hit = exact.get(text);
  if (hit !== undefined) return hit;
  for (const { re, order, out } of patterns) {
    const match = re.exec(text);
    if (match) {
      const values = {};
      order.forEach((n, i) => {
        values[n] = tr(match[i + 1]); // (a gap can hold words of its own)
      });
      return out.replace(/\{(\d+)\}/g, (_m, n) => values[n] ?? "");
    }
  }
  return text;
}

/** Text from a template, for code that builds a sentence: t("Walka {1} is out.", version). */
export function t(source, ...values) {
  const filled = source.replace(/\{(\d+)\}/g, (_m, n) => String(values[Number(n) - 1] ?? ""));
  return tr(filled);
}

/** Translate the text and labels already in the page (once, at the start). */
export function translateTree(root = document.body) {
  if (!exact) return;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT);
  for (let node = walker.currentNode; node; node = walker.nextNode()) {
    if (node.nodeType === Node.ELEMENT_NODE) {
      if (SKIP.has(node.nodeName)) continue;
      for (const name of ATTRIBUTES) {
        const value = node.getAttribute(name);
        if (value) node.setAttribute(name, tr(value));
      }
    } else if (node.parentElement && !SKIP.has(node.parentElement.nodeName)) {
      const text = node.nodeValue;
      const trimmed = text.replace(/\s+/g, " ").trim();
      if (!trimmed) continue;
      const done = tr(trimmed);
      if (done !== trimmed) node.nodeValue = text.replace(/\S[\s\S]*\S|\S/, done);
    }
  }
}

const RIGHT_TO_LEFT = ["ar", "fa", "he", "ps", "ur", "yi"];

/** Pick and load a translation for the browser's languages. Without one, nothing happens. */
export async function loadLanguage(wanted = navigator.languages || [navigator.language]) {
  const lang = wanted
    .map((tag) => String(tag || "").toLowerCase())
    .flatMap((tag) => [tag, tag.split("-")[0]])
    .find((tag) => AVAILABLE.includes(tag));
  if (!lang) return null;
  try {
    const response = await fetch(`/static/locales/${lang}.json`);
    const catalog = await response.json();
    useCatalog(catalog.strings);
    document.documentElement.lang = lang;
    // Arabic, Hebrew, Persian, Urdu ... are written from the right.
    document.documentElement.dir = catalog.dir === "rtl" || RIGHT_TO_LEFT.includes(lang.split("-")[0]) ? "rtl" : "ltr";
    translateTree();
    return lang;
  } catch {
    return null; // English, then
  }
}
