// The Walka-account card in Settings, and the few words elsewhere on the page that follow it.
//
// Walka can send a person's walks to their Walka account (on walka.dev). All of
// that is Python's work: this page talks to nobody but the companion. It shows
// the state words of the snapshot (snapshot.walka_account) as sentences, and
// each button is a POST to the companion's own API, which answers at once -
// what follows shows in the next snapshots.
//
// The rules this file keeps:
//   - Without that part of the snapshot there is no card, and the page is as it
//     always was. While nobody is signed in the card is quiet - no lamp, no
//     badge - and nothing outside it says a word about a Walka account. One
//     thing does, for somebody who signed in before: the question before a walk
//     is deleted, if a copy of that walk was sent from this computer.
//   - Every sentence is one of this file's own. Of the snapshot only the e-mail
//     address, the address Walka asks, the numbers, and what the Walka account
//     said of a walk are shown, and always as text: nothing from it is ever read
//     as markup or built into a link.
//   - Nothing is kept in the browser: no storage of any kind.
//   - Outside pages are plain links that open in the web browser, as the link to
//     the disclaimer does. There are four, all on walka.dev. The address for
//     signing in is never here: Python opens that one.
//   - A snapshot comes twice a second. The card is made of parts, and a part is
//     built again only when what it shows has changed - so the place of the
//     keyboard and the choice in the question stay where the person left them.

import { $, fmt, h, post, run, setAttr, setShown, setText, state } from "./util.js";

const PAGES = {
  terms: ["https://walka.dev/terms/", "walka.dev/terms/"],
  privacy: ["https://walka.dev/privacy/", "walka.dev/privacy/"],
  account: ["https://walka.dev/account/", "walka.dev/account/"],
  walks: ["https://walka.dev/account/walks/", "walka.dev/account/walks/"],
};

/** A link to one of the four pages. It opens in the web browser, like the link to the disclaimer. */
function outside(page, { text, button } = {}) {
  const [href, shown] = PAGES[page];
  return h("a", {
    class: button ? "btn" : null,
    href,
    target: "_blank",
    rel: "noopener noreferrer",
    text: text || shown,
  });
}

// ---- the words -------------------------------------------------------------------------------

const WAYS = [
  ["discord", "Discord"],
  ["github", "GitHub"],
  ["google", "Google"],
];
const WAY_NAME = Object.fromEntries(WAYS);

const RAIL_FOOT = "Runs on this computer. Nothing is uploaded.";
const RAIL_FOOT_SENDING = "Runs on this computer. Your walks are sent to your Walka account.";
const PAGE_STAYS = "Everything on this page stays on this computer.";
const PAGE_STAYS_SENDING =
  "Your walks are sent to your Walka account. Everything else on this page stays on this computer.";

// When the sign-in ends. Said once, in the card, until "OK" is pressed - and it asks for nothing.
const SIGN_IN_ENDED =
  "Walka is no longer signed in to your Walka account, and has stopped sending. You signed out elsewhere, or the account was deleted. Your walks on this computer are untouched.";

// The last try to sign in, or the stored sign-in: snapshot.walka_account.problem.
const PROBLEM = {
  refused: "Nothing was signed in.",
  failed: "Walka could not finish signing in. Try again.",
  timed_out: "Nobody came back from the browser. Try again.",
  no_store: "Walka could not keep the sign-in on this computer, so nothing was signed in.",
  certificate: "Walka could not check that it is talking to your Walka account's server, so it asked nothing.",
  no_answer: "There was no answer. Try again later.",
  no_stored_sign_in: "The sign-in is not on this computer any more. Nothing is sent.",
  // (store_refused: see problemSentence - the sentence names the system.)
};

// The last piece of work, while sending is on: snapshot.walka_account.last.word.
const LAST_ON = {
  sent: "Sent.",
  later: "No answer - Walka tries again later.",
  no_answer: "There was no answer. Try again later.",
  too_old: "This Walka is too old for your Walka account as it is now: get the newest Walka.",
  full: "Your Walka account is full: it has room for 20,000 walks.",
  stopped: "Walka stopped for now and tries again when it is next started.",
  not_open: "Walka stopped for now and tries again when it is next started.",
  certificate: "Walka could not check that it is talking to your Walka account's server, so it sent nothing.",
};
// ... and while it is off: then the word is about a button of this card. It is said until such a
// button is pressed again - the companion keeps its last word, and what was true of one try
// must not stand beside the next. (Nor does a word about sending stand once sending was stopped.)
const LAST_ASKED = {
  no_answer: "There was no answer. Try again later.",
  stopped: "That did not work just now. Try again later.",
  certificate: "Walka could not check that it is talking to your Walka account's server, so it asked nothing.",
};
// What stays true of the Walka account, whoever asked and whenever.
const LAST_STANDS = {
  too_old: LAST_ON.too_old,
  full: LAST_ON.full,
};

// What the companion answers when it does not do what a button asked, in this page's words.
const REFUSAL = {
  "Busy.": "Walka is still asking your Walka account. Try again in a moment.",
  "Closing.": "Walka is closing.",
  "Not available.": "The Walka account is not available in this Walka.",
  "Not signed in.": "Walka is not signed in to a Walka account.",
  "Signed in already.": "Walka is signed in already.",
  "No e-mail address.": "The sign-in gave Walka no e-mail address, so sending cannot be switched on.",
  "Not an answer to the question.": "The question ran out. Nothing was switched on.",
  "Failed.": "That did not work. Try again.",
};

// The question that stands before sending. Every switching-on passes it.
const QUESTION = {
  title: "Send my walks to my Walka account?",
  goes: "What goes, of each walk: when it started and ended, the day, and how far this computer's clock was from world time; how long the belt moved; distance, steps and calories (estimates), and how many of the calories were on top of sitting; how each number was come by (for the calories: from speed or from heart rate - the word, not the heart rate); whether a walking session was running in the game; and the walk's name on this computer, which is its start time.",
  stays:
    "What stays here: the samples, the heart rate, the top speed, the treadmill's name, the character, your weight, height, age and sex.",
  where: (host) => `Where: your Walka account, in the database the site's owner runs (${host}).`,
  all: "All of them",
  fromNowOn: "Only walks from now on",
  then: "From then on each walk goes when it is saved, without asking again.",
  public:
    'Your page on walka.dev is public. Walks you send count on it as soon as they are sent, so anyone who reloads it can tell when you were on the pad. With "All of them" your whole history shows there at once.',
  whoever: "Every walk recorded on this computer goes to this account, whoever walked.",
  stop: "To stop: switch it off here, or sign out. Stopping deletes nothing in the account. There a walk is deleted one at a time:",
  send: "Send my walks",
  notNow: "Not now",
};

const count = (value) => (Number.isFinite(value) && value > 0 ? Math.floor(value) : 0);
const walks = (n) => `${fmt.int(n)} ${n === 1 ? "walk" : "walks"}`;
const wouldGo = (n) => (n ? `${walks(n)} would go now.` : "No walk would go now.");
const unnamedLine = (n) =>
  n === 1
    ? "1 walk does not say which treadmill made it, and is not sent."
    : `${walks(n)} do not say which treadmill made them, and are not sent.`;
const heldLine = (n) =>
  n === 1
    ? "1 walk is outside what your Walka account takes, and stays here."
    : `${walks(n)} are outside what your Walka account takes, and stay here.`;
const asideLine = (n) =>
  n === 1
    ? "Your Walka account did not take 1 walk. It stays here."
    : `Your Walka account did not take ${walks(n)}. They stay here.`;
const unreadableLine = (n) =>
  n === 1 ? "1 walk could not be read, and is not sent." : `${walks(n)} could not be read, and are not sent.`;

/** Sending is off: what Walka has done with the walks on this computer - not what the Walka account
    holds. Walka has not asked it (nothing is asked while sending is off), and a walk that was put
    in on the website is there all the same. "To your Walka account": a walk that Walka sent to
    another Walka account, signed in here before, counts as not sent to this one. "Were ...
    already" is also true of a walk that was deleted there since. */
function notSentLine(waiting, sent) {
  if (waiting === 1) return "Nothing is sent. Walka has not sent 1 walk on this computer to your Walka account.";
  if (waiting) return `Nothing is sent. Walka has not sent ${walks(waiting)} on this computer to your Walka account.`;
  if (sent === 1)
    return "Nothing is sent. 1 walk on this computer was sent before, or was in your Walka account already.";
  if (sent)
    return `Nothing is sent. ${walks(sent)} on this computer were sent before, or were in your Walka account already.`;
  return "Nothing is sent.";
}

/** Which system's words to use: the one this page is shown on - but only while that is the
    computer that runs Walka (address 127.0.0.1). The snapshot does not name the system, and a
    page that is open on another device must not name its own. */
function system() {
  const here = typeof location === "object" && location ? location.hostname : "";
  if (!["127.0.0.1", "localhost", "[::1]"].includes(here)) return "";
  const nav = typeof navigator === "object" && navigator ? navigator : {};
  const told = `${nav.platform || ""} ${nav.userAgent || ""}`;
  if (/Win/.test(told)) return "windows";
  if (/Mac/.test(told)) return "mac";
  return "";
}

function storeSentence(store, where) {
  if (store !== "lasting") return "The sign-in is kept in memory, until Walka is closed.";
  if (where === "windows") return "The sign-in is kept in Windows' Credential Manager.";
  if (where === "mac") return "The sign-in is kept in your Mac's keychain.";
  return "The sign-in is kept in the password store of the computer that runs Walka.";
}

function problemSentence(problem, where) {
  if (problem === "store_refused") {
    if (where === "mac") return "macOS did not let this version of Walka read the stored sign-in. Sign in again.";
    if (where === "windows") return "Windows did not let Walka read the stored sign-in. Sign in again.";
    return "The computer that runs Walka did not let it read the stored sign-in. Sign in again.";
  }
  return PROBLEM[problem] || "";
}

/** Signing out took the sign-in out of the system's store - and the store did not say that it is
    gone (snapshot.walka_account.left_in_store). Said in the card, with a button that tries once more.
    The entry is named after the address Walka asks; "Walka account" is how its name begins on both
    systems. It invites nobody to sign in: the Walka account may be gone. */
function leftSentence(where) {
  const named = 'its name begins "Walka account".';
  if (where === "windows")
    return `Walka could not make sure that your sign-in was taken out of Windows' Credential Manager. Try again, or delete the entry there by hand: ${named}`;
  if (where === "mac")
    return `Walka could not make sure that your sign-in was taken out of your Mac's keychain. Try again, or delete the entry by hand in Keychain Access: ${named}`;
  return `Walka could not make sure that your sign-in was taken out of the password store of the computer that runs Walka. Try again, or delete the entry there by hand: ${named}`;
}

// ---- the page's own memory (gone when the page is closed: nothing is stored) -----------------

const local = {
  open: "", // the id of the question that is shown
  dismissed: "", // the id of a question this page has answered, or said "Not now" to
  mine: false, // this page asked for the question: the keyboard may be moved into it
  ranOut: false, // the question that was open went away unanswered
  switchingUntil: 0, // a moment after "Send my walks": the next snapshots say the rest
  focus: "", // where the keyboard goes once the card is drawn again
  heard: "", // the companion's last word as it stood when a button was pressed: that one is old
};

function forget() {
  Object.assign(local, { open: "", dismissed: "", mine: false, ranOut: false, switchingUntil: 0, focus: "" });
  local.heard = "";
}

/** The companion's last word and when it was said: to tell a new one from the one that was there. */
const saidOf = (a) => (a && a.last && typeof a.last === "object" ? `${a.last.word}|${a.last.at}` : "");

/** A button of the card is pressed: whatever the card said of the last try is not said of this one. */
function pressedNow() {
  local.heard = saidOf(partOf(state.snapshot));
}

// ---- pressing a button ------------------------------------------------------------------------

/** One button of the card: a POST to the companion, the house's busy state and its error display. */
function press(button, path, body, then) {
  return run(button, async () => {
    try {
      await post(path, body);
    } catch (error) {
      if (then && then.refused) then.refused(error);
      throw new Error(REFUSAL[error.message] || error.message);
    }
    if (then && then.done) then.done();
  });
}

function again() {
  if (state.snapshot) updateWalkaAccount(state.snapshot);
}

// ---- what each part of the card shows ---------------------------------------------------------
// A model is plain data: the part is built again only when its model has changed.

const KNOWN = new Set(["signed_out", "signing_in", "signed_in"]);

function modelOf(a) {
  const signedIn = a.state === "signed_in";
  const sending = signedIn && a.sending === true;
  const asking = a.busy === "asking";
  const counts = a.counts || {};
  const way = WAY_NAME[a.way] || "";
  const host = typeof a.host === "string" ? a.host : "";
  const where = system();
  const word = a.last && typeof a.last.word === "string" ? a.last.word : "";
  const notTaken = signedIn && a.first_step === "not_taken";
  const canSend = signedIn && a.can_send === true;

  // The question, unless this page has answered it or put it off.
  const asked = signedIn && !sending && a.question && typeof a.question === "object" ? a.question : null;
  // (Never one that does not say which Walka account: nobody could tell where the walks would go.)
  const named = asked && typeof asked.id === "string" && typeof asked.email === "string" && asked.email !== "";
  const question = named && asked.id !== local.dismissed ? asked : null;
  if (question) {
    if (question.id !== local.open) {
      local.open = question.id;
      local.ranOut = false;
      if (local.mine || card.contains(document.activeElement)) local.focus = "question";
      local.mine = false;
    }
  } else {
    // It was there and is gone, and nobody here answered it: it ran out. It is not asked again by itself.
    if (local.open && !asked && signedIn && !sending && !asking && local.open !== local.dismissed) local.ranOut = true;
    local.open = "";
  }
  if (!signedIn || sending) local.ranOut = false;
  if (sending) local.switchingUntil = 0;
  const heard = saidOf(a);
  if (sending) local.heard = heard;
  const switching = signedIn && !sending && performance.now() < local.switchingUntil;

  const model = { head: "", tone: "idle" };
  model.notice = a.notice === "ended" ? { kind: "ended" } : null;
  // Beside that sentence where it stands, not instead of it. (Not while a new sign-in waits for
  // the browser: its key is written over the entry.)
  model.left = a.left_in_store === "sign_in" && a.state === "signed_out" ? { where } : null;
  model.intro = signedIn ? null : { quiet: true };

  if (a.state === "signing_in") {
    model.told = { line: "Waiting for your web browser. Sign in there, then come back." };
    model.who = { state: "signing_in" };
  } else if (!signedIn) {
    const problem = problemSentence(a.problem, where);
    model.told = problem ? { line: problem } : null;
    model.who = { state: "signed_out", host };
  } else {
    model.told = null;
    model.who = { state: "signed_in", email: typeof a.email === "string" ? a.email : "", way, store: a.store, where };
    model.head = sending ? "sending on" : "sending off";
    model.tone = sending ? "ok" : "idle";
  }

  model.step = notTaken ? { way, asking } : null;

  model.sending = null;
  model.said = null;
  if (signedIn) {
    const lines = [];
    if (!canSend) {
      model.sending = { kind: "no_email" };
    } else if (sending) {
      model.sending = {
        kind: "on",
        sent: count(counts.sent),
        waiting: count(counts.waiting),
        from: a.chosen === "from" && Number.isFinite(a.from) ? a.from : null,
      };
      if (a.busy === "sending") lines.push("Sending now...");
      else if (asking) lines.push("Asking your Walka account...");
      if (LAST_ON[word]) lines.push(LAST_ON[word]);
    } else {
      model.sending = {
        kind: "off",
        // No count while the first step is not taken: the card says just above that this sign-in
        // has no Walka account yet, and a number of walks beside that would speak of one.
        waiting: notTaken ? 0 : count(counts.waiting),
        sent: notTaken ? 0 : count(counts.sent),
        // No button while the first step is not taken, while the question is open, or just after it was answered.
        button: !notTaken && !question && !switching,
        asking,
      };
      if (switching) lines.push("Switching sending on...");
      else if (asking) lines.push("Asking your Walka account...");
      if (local.ranOut) lines.push("The question ran out after ten minutes. Nothing was switched on.");
      if (!asking && !switching) {
        if (LAST_STANDS[word]) lines.push(LAST_STANDS[word]);
        else if (LAST_ASKED[word] && heard !== local.heard) lines.push(LAST_ASKED[word]);
      }
    }
    model.said = lines.length ? { lines } : null;
  }

  model.question = question
    ? {
        id: question.id,
        email: typeof question.email === "string" ? question.email : "",
        way: WAY_NAME[question.way] || "",
        waiting: count(question.waiting),
        unnamed: count(question.unnamed),
        held: count(question.held),
        public: question.public === true,
        host,
      }
    : null;

  model.lists = null;
  // (Nor are walks listed while the first step is not taken: "your Walka account did not take ..."
  // beside "no Walka account yet". The companion may still know such walks from the question of a
  // Walka account that was signed in before.)
  if (signedIn && !notTaken) {
    const list = (items, last) =>
      (Array.isArray(items) ? items : [])
        .filter((item) => item && typeof item === "object")
        .map((item) => [Number.isFinite(item.start) ? item.start : null, String(item[last] ?? "")]);
    const lists = {
      aside: count(counts.aside),
      asideWalks: list(a.aside, "message"),
      held: count(counts.held),
      heldWalks: list(a.held, "why"),
      // (While the question is open it says this itself, and there is no more to say of it.)
      unnamed: question ? 0 : count(counts.unnamed),
      unreadable: count(counts.unreadable),
    };
    if (lists.aside || lists.held || lists.unnamed || lists.unreadable) model.lists = lists;
  }
  return model;
}

// ---- building the parts -----------------------------------------------------------------------
// Each builder gives the elements of its part, and names the ones the keyboard can be on
// (`acts`), so that it can be put back on the same button after the part was built again.

const line = (text, cls) => h("p", { class: cls || null, text });

function button(acts, name, text, onPress, props) {
  const el = h("button", { class: "btn", type: "button", text, ...props });
  el.addEventListener("click", () => onPress(el));
  acts.set(name, el);
  return el;
}

function buildNotice(model, acts) {
  const ok = button(acts, "ok", "OK", (el) => press(el, "/api/walka-account/notice/ok"));
  return [h("div", { class: "walka-account-panel" }, line(SIGN_IN_ENDED), h("div", { class: "btn-row" }, ok))];
}

function buildLeft(model, acts) {
  // The same route as "Sign out": the companion then asks the system's store once more to take
  // the entry out. It reads nothing there and asks nobody else.
  const again = button(acts, "try-again", "Try again", (el) => press(el, "/api/walka-account/sign-out"));
  return [
    h("div", { class: "walka-account-panel" }, line(leftSentence(model.where)), h("div", { class: "btn-row" }, again)),
  ];
}

function buildIntro() {
  return [line("Optional. Walka works in full without it.", "muted")];
}

function buildSaid(model) {
  return model.lines ? model.lines.map((text) => line(text)) : [line(model.line)];
}

function buildWho(model, acts) {
  if (model.state === "signing_in") {
    const stop = button(acts, "stop-waiting", "Stop waiting", (el) => press(el, "/api/walka-account/sign-out"));
    return [h("div", { class: "btn-row" }, stop)];
  }
  if (model.state === "signed_out") {
    const ways = WAYS.map(([way, name]) =>
      button(acts, `sign-in-${way}`, `Sign in with ${name}`, (el) => press(el, "/api/walka-account/sign-in", { way })),
    );
    return [
      h("div", { class: "btn-row" }, ways),
      h(
        "p",
        { class: "muted small" },
        "Signing in makes a Walka account if you have none: terms (",
        outside("terms"),
        "), privacy (",
        outside("privacy"),
        "). Nothing is sent until you switch that on. Your web browser opens for this, and Walka asks ",
        h("span", { class: "walka-account-host", text: model.host }),
        ".",
      ),
    ];
  }
  const who = h("p", { class: "walka-account-signed" });
  if (model.email)
    who.append("Signed in as ", h("strong", { text: model.email }), model.way ? `, with ${model.way}.` : ".");
  else who.append(model.way ? `Signed in with ${model.way}.` : "Signed in.");
  const signOut = button(acts, "sign-out", "Sign out", (el) => press(el, "/api/walka-account/sign-out"));
  return [
    who,
    h("div", { class: "btn-row" }, signOut),
    line(
      `"Sign out" on walka.dev does not sign this computer out: sign out here. Signing out here may ask your Walka account one last time, to tell it so. ${storeSentence(model.store, model.where)}`,
      "muted small",
    ),
  ];
}

function buildStep(model, acts) {
  const same = model.way ? ` (with ${model.way})` : "";
  const look = button(
    acts,
    "look-again",
    "Look again (asks your Walka account)",
    (el) => {
      pressedNow();
      again();
      press(el, "/api/walka-account/look-again");
    },
    { disabled: model.asking },
  );
  return [
    h(
      "div",
      { class: "walka-account-panel" },
      line(
        `This sign-in has no Walka account yet. To open one - or to delete this sign-in - go to walka.dev/account/ and sign in there the same way${same}. If you already have an account, it is under another e-mail address: sign out here and sign in the way that uses that address.`,
      ),
      h("div", { class: "btn-row" }, outside("account", { text: "Open walka.dev/account/", button: true }), look),
    ),
  ];
}

function buildSending(model, acts) {
  if (model.kind === "no_email") {
    return [
      line(
        "The sign-in gave Walka no e-mail address, so nobody could tell which Walka account your walks would go to. Sending cannot be switched on.",
      ),
    ];
  }
  if (model.kind === "on") {
    const numbers = `${fmt.int(model.sent)} sent, ${fmt.int(model.waiting)} waiting.`;
    const since = model.from === null ? "" : ` Only walks from ${fmt.dateTime(model.from)} on are sent.`;
    const stop = button(acts, "stop", "Stop sending", (el) => press(el, "/api/walka-account/sending/off"));
    return [
      line("Sending is on. Each walk goes to your Walka account when it is saved."),
      line(numbers + since, "num"),
      h("div", { class: "btn-row" }, stop),
    ];
  }
  const nodes = [line(notSentLine(model.waiting, model.sent))];
  if (model.button) {
    const ask = button(
      acts,
      "ask",
      "Send my walks to my Walka account...",
      (el) => {
        local.mine = true;
        local.ranOut = false;
        pressedNow();
        again(); // (what was said of the last question is not said of this one)
        press(el, "/api/walka-account/question", undefined, {
          refused: () => {
            local.mine = false;
          },
        });
      },
      { disabled: model.asking },
    );
    nodes.push(
      h("div", { class: "btn-row" }, ask, h("span", { class: "muted small", text: "Asks your Walka account first." })),
    );
  }
  return nodes;
}

function buildQuestion(model, acts) {
  const to = h(
    "p",
    null,
    "To ",
    h("strong", { text: model.email }),
    model.way ? `, signed in with ${model.way}.` : ".",
  );
  // (The companion makes no question without an e-mail address: nobody could tell which Walka account it is.)
  const send = h("button", { class: "btn primary", type: "button", text: QUESTION.send, disabled: true });
  const notNow = h("button", { class: "btn", type: "button", text: QUESTION.notNow });
  const hint = h("span", { class: "muted small", text: "Choose which walks first." });
  // Neither is chosen at first: the person says which, in so many words.
  const radio = (value, text) => {
    const input = h("input", { type: "radio", name: "walka-account-send", value });
    input.addEventListener("change", () => {
      send.disabled = false;
      hint.hidden = true;
    });
    acts.set(`send-${value}`, input);
    return h("label", null, input, h("span", { text }));
  };
  const choice = h(
    "div",
    { class: "segmented", role: "radiogroup", "aria-label": "Which walks to send" },
    radio("all", QUESTION.all),
    radio("from_now_on", QUESTION.fromNowOn),
  );
  const chosen = () => ["all", "from_now_on"].find((value) => acts.get(`send-${value}`).checked);

  send.addEventListener("click", () => {
    const which = chosen();
    if (!which) return;
    pressedNow();
    press(
      send,
      "/api/walka-account/sending",
      { question: model.id, send: which },
      {
        done: () => {
          local.dismissed = model.id;
          local.switchingUntil = performance.now() + 3000;
          local.focus = "card";
          again();
        },
        refused: (error) => {
          if (error.status !== 400) return;
          // The question is not open any more (ten minutes, or it was answered elsewhere).
          local.dismissed = model.id;
          local.ranOut = true;
          local.focus = "ask";
          again();
        },
      },
    );
  });
  notNow.addEventListener("click", () => {
    local.dismissed = model.id;
    local.focus = "ask";
    again();
  });
  acts.set("send", send);
  acts.set("not-now", notNow);

  const panel = h(
    "div",
    {
      class: "walka-account-panel walka-account-question",
      role: "group",
      "aria-labelledby": "walka-account-question-h",
      tabindex: "-1",
    },
    h("h4", { id: "walka-account-question-h", text: QUESTION.title }),
    to,
    line(QUESTION.goes),
    line(QUESTION.stays),
    h("p", null, `${QUESTION.where(model.host)} `, outside("privacy")),
    line(wouldGo(model.waiting)),
    choice,
    line(QUESTION.then),
    model.unnamed ? line(unnamedLine(model.unnamed)) : null,
    model.held ? line(heldLine(model.held)) : null,
    model.public ? line(QUESTION.public) : null,
    line(QUESTION.whoever),
    h("p", null, `${QUESTION.stop} `, outside("walks")),
    h("div", { class: "btn-row" }, send, notNow, hint),
  );
  acts.set("question", panel);
  return [panel];
}

function buildLists(model) {
  const MOST = 20;
  const list = (items, total) => {
    if (!items.length) return null;
    const rows = items
      .slice(0, MOST)
      .map(([start, said]) =>
        h(
          "li",
          null,
          h("span", { class: "num", text: start === null ? "-" : fmt.dateTime(start) }),
          h("span", { text: said }),
        ),
      );
    const more = total - rows.length;
    if (more > 0) rows.push(h("li", null, h("span"), h("span", { class: "muted", text: `and ${fmt.int(more)} more` })));
    return h("ul", { class: "walka-account-walks" }, rows);
  };
  return [
    model.aside ? line(asideLine(model.aside)) : null,
    model.aside ? list(model.asideWalks, model.aside) : null,
    model.held ? line(heldLine(model.held)) : null,
    model.held ? list(model.heldWalks, model.held) : null,
    model.unnamed ? line(unnamedLine(model.unnamed)) : null,
    model.unreadable ? line(unreadableLine(model.unreadable)) : null,
  ].filter(Boolean);
}

// The parts, top to bottom. `live`: a place for short sentences that are read out when they change.
const PARTS = [
  ["notice", buildNotice, true],
  ["left", buildLeft, true],
  ["intro", buildIntro, false],
  ["told", buildSaid, true],
  ["who", buildWho, false],
  ["step", buildStep, false],
  ["sending", buildSending, false],
  ["said", buildSaid, true],
  ["question", buildQuestion, false],
  ["lists", buildLists, false],
];

// ---- drawing ----------------------------------------------------------------------------------

let card = null;
let headState = null;
const parts = [];

/** Build one part again if its model changed. Says whether the keyboard was in it and has no place now. */
function sync(part, model) {
  const key = JSON.stringify(model ?? null);
  if (key === part.key) return false;
  part.key = key;
  const active = document.activeElement;
  const had = !!active && part.el.contains(active);
  let name = "";
  if (had) {
    for (const [act, el] of part.acts) if (el === active) name = act;
  }
  part.acts = new Map();
  const nodes = model ? part.build(model, part.acts) : [];
  part.el.replaceChildren(...nodes);
  part.el.hidden = !nodes.length;
  if (!had) return false;
  const same = part.acts.get(name);
  if (same && !same.disabled) {
    same.focus({ preventScroll: true });
    return false;
  }
  return true;
}

function act(partName, name) {
  const part = parts.find((candidate) => candidate.name === partName);
  const el = part ? part.acts.get(name) : null;
  return el && !el.disabled ? el : null;
}

export function initWalkaAccount() {
  card = $("set-walka-account");
  headState = $("walka-account-state");
  const body = $("walka-account-body");
  for (const [name, build, live] of PARTS) {
    const el = h("div", {
      class: `walka-account-part walka-account-${name}`,
      role: live ? "status" : null,
      hidden: true,
    });
    body.append(el);
    parts.push({ name, el, build, key: "null", acts: new Map() });
  }
}

/** The Walka account's part of a snapshot - or null: Walka has no such part, or it is not available
    (the simulated treadmill is in use, say). Then there is no card at all. */
function partOf(s) {
  const a = s && s.walka_account;
  return a && typeof a === "object" && KNOWN.has(a.state) ? a : null;
}

/** Is the card there at all? Hidden, and out of the keyboard's reach, whenever it is not.
    (On every view: the page switches every card of Settings on again when the companion is heard.) */
function gate(a) {
  setShown(card, !!a);
  const inert = !a || !state.online;
  if (card.inert !== inert) card.inert = inert;
}

/** The card, from a snapshot. */
export function updateWalkaAccount(s) {
  if (!card) return;
  const a = partOf(s);
  const there = !!a;
  gate(a);
  if (!there) {
    // Nothing of a Walka account stays behind in the page - not its e-mail address either.
    forget();
    setText(headState, "");
    setAttr(card, "data-tone", null);
    for (const part of parts) sync(part, null);
    return;
  }
  const model = modelOf(a);
  setText(headState, model.head);
  setAttr(card, "data-tone", model.tone);
  let lost = false;
  for (const part of parts) lost = sync(part, model[part.name]) || lost;

  // Where the keyboard goes: into the question when it opens, back to the button that
  // opened it when it closes - and never out of the card because a button went away.
  const wanted = local.focus;
  local.focus = "";
  if (wanted === "question") {
    const panel = act("question", "question");
    if (panel) {
      // From its first line: the question can be longer than a small window is high.
      panel.scrollIntoView({ block: "start" });
      panel.focus({ preventScroll: true });
    }
  } else if (wanted === "ask") {
    (act("sending", "ask") || card).focus({ preventScroll: true });
  } else if (wanted === "card" || lost) {
    card.focus({ preventScroll: true });
  }
}

/** The two feet - of the rail, on every view, and of Settings: today's words, unless sending is on.
    Then the page no longer says that nothing is uploaded. (Called for every snapshot, on every view.) */
export function updateFeet(s) {
  const a = partOf(s);
  const on = !!a && a.state === "signed_in" && a.sending === true;
  setText($("rail-foot"), on ? RAIL_FOOT_SENDING : RAIL_FOOT);
  setText($("foot-stays"), on ? PAGE_STAYS_SENDING : PAGE_STAYS);
  if (!card) return;
  // Also while another view is shown: a card that is not there is hidden, and holds nothing.
  if (a) gate(a);
  else updateWalkaAccount(s);
}

/** Deleting a walk that is in the Walka account: its copy stays there, and the question says where.
    `elsewhereToo`: a copy was also sent to a Walka account that is not the one signed in now. */
export function sentWalkWords(elsewhereToo = false) {
  const question = [
    "Delete this walk from this computer? It disappears from your totals here and from the addon's history. This cannot be undone. It stays in your account, and on your public page if you have one, until you delete it there: ",
    outside("walks"),
  ];
  if (elsewhereToo)
    question.push(
      ". Another copy was sent to a different Walka account from this computer. Deleting the walk here does not delete that copy either.",
    );
  return { question, button: "Delete from this computer", toast: "Walk deleted from this computer." };
}

/** Deleting a walk of which a copy was sent, from this computer, to a Walka account that is not the
    one signed in now - after signing out, say, or with another Walka account signed in. The copy
    stays there too. The words promise nothing that Walka cannot know (whether that Walka account
    is still there), name nobody, and do not say "your". */
export function otherAccountWalkWords() {
  return {
    question: [
      "Delete this walk from this computer? It disappears from your totals here and from the addon's history. This cannot be undone. A copy of this walk was sent to a Walka account from this computer. Deleting it here does not delete that copy: that is done on ",
      outside("walks"),
      ", signed in to that Walka account.",
    ],
    button: "Delete from this computer",
    toast: "Walk deleted from this computer.",
  };
}
