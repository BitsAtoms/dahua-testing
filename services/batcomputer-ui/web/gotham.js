// The GOTHAM row: fictional live notifications from Batman's universe that
// keep a mostly healthy screen alive (owner, 2026-10-01). They never count as
// system alarms. Messages live in /web/data/gotham-alerts.json.

const ICONS = { ok: "●", warning: "▲", error: "■", critical: "✕", unknown: "○", riddle: "?" };
// ?enigma forces the easter egg, to review it without waiting for the 1 %.
const FORCE_RIDDLE = new URLSearchParams(location.search).has("enigma");
const REDUCED_MOTION = matchMedia("(prefers-reduced-motion: reduce)").matches;

// "{a-b}" becomes a random whole number between a and b, "{x|y}" a random choice.
export function fill(template, random = Math.random) {
  return template.replace(/\{([^{}]+)\}/g, (match, body) => {
    const range = body.match(/^(-?\d+)-(-?\d+)$/);
    if (range) {
      const [low, high] = [Number(range[1]), Number(range[2])];
      return String(low + Math.floor(random() * (high - low + 1)));
    }
    const options = body.split("|");
    return options.length > 1 ? options[Math.floor(random() * options.length)] : match;
  });
}

export function pick(data, previous, random = Math.random, forceRiddle = false) {
  const egg = data.easter_egg;
  if (egg && egg.messages.length && (forceRiddle || random() < egg.odds)) {
    const fresh = egg.messages.filter(m => m !== previous);
    const choices = fresh.length ? fresh : egg.messages;
    const riddle = choices[Math.floor(random() * choices.length)];
    return { ...riddle, state: "riddle", source: riddle };
  }
  const states = Object.keys(data.weights);
  let roll = random() * states.reduce((sum, state) => sum + data.weights[state], 0);
  let state = states[states.length - 1];
  for (const candidate of states) {
    roll -= data.weights[candidate];
    if (roll < 0) { state = candidate; break; }
  }
  const pool = data.messages.filter(m => m.state === state && m !== previous);
  const choices = pool.length ? pool : data.messages.filter(m => m !== previous);
  return choices[Math.floor(random() * choices.length)];
}

function typeInto(element, text, perCharacterMs) {
  if (REDUCED_MOTION) { element.textContent = text; return Promise.resolve(); }
  element.textContent = "";
  return new Promise(resolve => {
    let index = 0;
    const timer = setInterval(() => {
      index += 1;
      element.textContent = text.slice(0, index);
      if (index >= text.length) { clearInterval(timer); resolve(); }
    }, perCharacterMs);
  });
}

export async function startGothamTicker(row, { minSeconds = 6, maxSeconds = 11 } = {}) {
  const data = await (await fetch("/web/data/gotham-alerts.json", { cache: "no-store" })).json();
  const icon = row.querySelector(".bc-icon");
  const label = row.querySelector(".gotham-label");
  const value = row.querySelector(".bc-value");
  const bar = row.querySelector(".gotham-bar");
  const tag = row.querySelector(".gotham-tag");
  const text = row.querySelector(".gotham-text");
  let previous = null;

  // Riddles are longer and use two lines (question, answer): shrink their
  // letters until both fit whole.
  function fitWhole(labelText, valueText) {
    let size = 1;
    const apply = () => { text.style.fontSize = value.style.fontSize = `${size}em`; };
    label.textContent = labelText;
    value.textContent = valueText;
    apply();
    const overflows = () => text.scrollWidth > text.clientWidth + 1 || value.scrollWidth > text.clientWidth + 1
      || row.scrollHeight > row.clientHeight + 1;
    while (size > 0.55 && overflows()) { size -= 0.05; apply(); }
    label.textContent = "";
    value.textContent = "";
  }

  async function next() {
    const message = pick(data, previous, Math.random, FORCE_RIDDLE);
    previous = message.source || message;
    const seconds = minSeconds + Math.random() * (maxSeconds - minSeconds);
    row.className = `bc-status gotham ${message.state}`;
    // Interference flicker as a new transmission comes in.
    row.classList.remove("arriving");
    void row.offsetWidth;
    row.classList.add("arriving");
    icon.textContent = ICONS[message.state];
    tag.textContent = message.state === "riddle" ? "ENIGMA" : "GOTHAM";
    const labelText = fill(message.label);
    const valueText = fill(message.value);
    text.style.fontSize = value.style.fontSize = "";
    if (message.state === "riddle") fitWhole(labelText, valueText);
    value.textContent = "";
    bar.style.transition = "none";
    bar.style.transform = "scaleX(1)";
    await typeInto(label, labelText, 28);
    await typeInto(value, valueText, 22);
    void bar.offsetWidth;
    bar.style.transition = `transform ${seconds}s linear`;
    bar.style.transform = "scaleX(0)";
    setTimeout(next, seconds * 1000);
  }
  next();
}
