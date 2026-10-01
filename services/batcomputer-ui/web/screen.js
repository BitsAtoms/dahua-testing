// Shared behaviour of every screen page.
//
// Each page is designed on a fixed canvas (CSS pixels at device scale factor
// 2). On the Batcomputer the window is exactly that size, so the scale is 1.
// Anywhere else (this PC, the preview) the canvas is scaled down to fit and
// centred, never enlarged.

export function fitCanvas(element, width, height) {
  function fit() {
    const scale = Math.min(innerWidth / width, innerHeight / height, 1);
    element.style.width = `${width}px`;
    element.style.height = `${height}px`;
    element.style.transform = `translate(${(innerWidth - width * scale) / 2}px, ${(innerHeight - height * scale) / 2}px) scale(${scale})`;
  }
  addEventListener("resize", fit);
  fit();
}

export function localTime(iso) {
  const date = iso ? new Date(iso) : new Date();
  return Number.isNaN(date.getTime()) ? "--:--:--" : date.toLocaleTimeString("es-ES", { hour12: false });
}

export function startClock(element) {
  const tick = () => { element.textContent = localTime(); };
  tick();
  setInterval(tick, 1000);
}

// Calls `load` now and every `seconds`, never overlapping; errors are passed
// to `onError` so a screen can show that its source is down.
export function poll(seconds, load, onError = () => {}) {
  let running = false;
  async function run() {
    if (running) return;
    running = true;
    try { await load(); } catch (error) { onError(error); } finally { running = false; }
  }
  run();
  return setInterval(run, seconds * 1000);
}
