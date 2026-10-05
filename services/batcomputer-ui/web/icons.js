// Decorative icons for the yellow header block (owner, 2026-10-01: no letters).
// Simple black strokes drawn for this project, 24x24. A classic script, so the
// static style sample can load it from disk too.
(function () {
  const PATHS = {
    // supervisor console: a terminal prompt
    terminal: '<rect x="3" y="4" width="18" height="16" rx="1"/><path d="M7 9l3 3-3 3M12.5 15H17"/>',
    // health of the parts: a pulse line
    pulse: '<path d="M2 12h4.5l2-5 4 10 2.5-6 1.5 1H22"/>',
    // health of the cameras: a video camera
    camera: '<rect x="2.5" y="7" width="12.5" height="10" rx="1"/><path d="M15 11l6.5-3.2v8.4L15 13z"/>',
    // video wall: four screens
    videowall: '<rect x="3" y="4" width="8" height="7"/><rect x="13" y="4" width="8" height="7"/><rect x="3" y="13" width="8" height="7"/><rect x="13" y="13" width="8" height="7"/>',
    // live map: a folded map
    floorplan: '<path d="M3 6.5l6-2.5 6 2.5 6-2.5v13.5l-6 2.5-6-2.5-6 2.5zM9 4v13.5M15 6.5V20"/>',
    // events: a photo
    photo: '<rect x="3" y="5" width="18" height="14"/><circle cx="9" cy="10" r="2"/><path d="M21 16l-5-5-10 8"/>',
    // journeys: a route between two points
    route: '<circle cx="5" cy="18" r="2"/><circle cx="19" cy="6" r="2"/><path d="M7 18h5.5a3 3 0 0 0 0-6h-2a3 3 0 0 1 0-6H17"/>',
    // space editor: a room outline and a pencil
    editor: '<path d="M11 4H3v16h16v-8"/><path d="M9 15l1-4 8.5-8.5 3 3L13 14z"/>',
  };

  function svg(name) {
    const paths = PATHS[name];
    if (!paths) return "";
    return `<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">${paths}</svg>`;
  }

  function decorate(root = document) {
    for (const chip of root.querySelectorAll(".bc-code[data-icon]")) {
      chip.innerHTML = svg(chip.dataset.icon);
    }
  }

  window.bcIcons = { svg, decorate, names: Object.keys(PATHS) };
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => decorate());
  } else {
    decorate();
  }
})();
