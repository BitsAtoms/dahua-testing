// Space editor, part 2b-1: floors and rooms on the grid.
//
// The plan (space_map.v2) comes from and goes back to /api/space-map; the
// server validates it again on save. Every change goes through `change()`,
// which keeps the undo history; drags only preview until the pointer is
// released. Cameras, doors and floor links stay in the plan untouched (they
// are edited in part 2b-2), except that deleting a room or a floor removes
// what referred to it.

import {
  bounds, edges, largestRectangle, moveCorner, moveEdge, orthogonalStep, rectangle, roomProblem,
  simplify, translate,
} from "./geometry.js";

const MAX_FLOORS = 3;
const HISTORY = 200;

const $ = id => document.getElementById(id);
const app = $("app");
const svg = $("plan");
const layers = {
  grid: $("layer-grid"), ghost: $("layer-ghost"), rooms: $("layer-rooms"),
  handles: $("layer-handles"), preview: $("layer-preview"), cursor: $("layer-cursor"),
};

const state = {
  plan: null, revision: null, savedJson: null,
  floorId: null, tool: "select", selected: null,
  pointer: null, preview: null, drawing: null, hover: null,
  history: [], future: [], saving: false,
};

// ---------------------------------------------------------------- helpers

const clamp = (value, low, high) => Math.min(high, Math.max(low, value));
const same = (a, b) => a.x === b.x && a.y === b.y;
const floor = () => state.plan.floors.find(item => item.id === state.floorId);
const room = id => floor().rooms.find(item => item.id === id);
const others = id => floor().rooms.filter(item => item.id !== id);
const pointsAttr = points => points.map(point => `${point.x},${point.y}`).join(" ");
const escapeText = text => String(text).replace(/[&<>"']/g,
  char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
const pixelsPerStep = () => svg.getScreenCTM()?.a || 20;
const dirty = () => state.plan !== null && JSON.stringify(state.plan) !== state.savedJson;

function newId(prefix, taken) {
  let id;
  do { id = `${prefix}_${Math.random().toString(36).slice(2, 8)}`; } while (taken.has(id));
  return id;
}

function nextName(base, used, first = 1) {
  for (let number = first; ; number += 1) {
    if (!used.has(`${base} ${number}`)) return `${base} ${number}`;
  }
}

function gridPoint(event, snap = true) {
  const point = new DOMPoint(event.clientX, event.clientY).matrixTransform(svg.getScreenCTM().inverse());
  if (!snap) return { x: point.x, y: point.y };
  const { columns, rows } = state.plan.grid;
  return { x: clamp(Math.round(point.x), 0, columns), y: clamp(Math.round(point.y), 0, rows) };
}

function size(points) {
  const box = bounds(points);
  return `${box.x1 - box.x0} × ${box.y1 - box.y0}`;
}

// --------------------------------------------------------------- history

function remember(before) {
  state.history.push(before);
  if (state.history.length > HISTORY) state.history.shift();
  state.future = [];
}

function change(mutate) {
  const before = JSON.stringify(state.plan);
  mutate(state.plan);
  if (JSON.stringify(state.plan) !== before) remember(before);
  render();
}

function restore(json) {
  state.plan = JSON.parse(json);
  if (!floor()) state.floorId = state.plan.floors[0].id;
  if (state.selected && !room(state.selected)) state.selected = null;
  render();
}

function undo() {
  if (!state.history.length) return;
  cancelInteraction();
  state.future.push(JSON.stringify(state.plan));
  restore(state.history.pop());
}

function redo() {
  if (!state.future.length) return;
  cancelInteraction();
  state.history.push(JSON.stringify(state.plan));
  restore(state.future.pop());
}

// --------------------------------------------------------------- actions

function cancelInteraction() {
  state.pointer = null;
  state.preview = null;
  state.drawing = null;
}

function setTool(tool) {
  cancelInteraction();
  state.tool = tool;
  render();
}

function setFloor(id) {
  cancelInteraction();
  state.floorId = id;
  state.selected = null;
  render();
}

function select(id) {
  state.selected = id;
  render();
}

function createRoom(polygon) {
  const problem = roomProblem(polygon, floor().rooms, state.plan.grid);
  if (problem) {
    say(`No se puede crear la sala: ${problem}.`);
    return;
  }
  const rooms = state.plan.floors.flatMap(item => item.rooms);
  const id = newId("room", new Set(rooms.map(item => item.id)));
  const name = nextName("Sala", new Set(rooms.map(item => item.name)));
  change(() => { floor().rooms.push({ id, name, polygon }); });
  state.selected = id;
  render();
  const input = document.querySelector("#inspector input");
  input?.focus();
  input?.select();
}

function deleteRoom(id) {
  const target = room(id);
  if (!target) return;
  change(plan => {
    const current = floor();
    current.rooms = current.rooms.filter(item => item.id !== id);
    current.doors = current.doors.filter(door => !door.rooms.includes(id));
    for (const camera of current.cameras) if (camera.room_id === id) camera.room_id = null;
    plan.floor_links = plan.floor_links.filter(link => !link.rooms.includes(id));
  });
  state.selected = null;
  render();
  say(`Sala «${target.name}» borrada. Ctrl+Z la recupera.`, "done");
}

function addFloor() {
  if (state.plan.floors.length >= MAX_FLOORS) return;
  const id = newId("floor", new Set(state.plan.floors.map(item => item.id)));
  const name = nextName("Planta", new Set(state.plan.floors.map(item => item.name)), 0);
  change(plan => { plan.floors.push({ id, name, rooms: [], cameras: [], doors: [] }); });
  setFloor(id);
}

function deleteFloor(id) {
  const index = state.plan.floors.findIndex(item => item.id === id);
  if (index < 0 || state.plan.floors.length === 1) return;
  const name = state.plan.floors[index].name;
  cancelInteraction();
  change(plan => {
    const gone = new Set(plan.floors[index].rooms.map(item => item.id));
    plan.floors.splice(index, 1);
    plan.floor_links = plan.floor_links.filter(link => !link.rooms.some(roomId => gone.has(roomId)));
    state.floorId = plan.floors[Math.max(0, index - 1)].id;
    state.selected = null;
  });
  say(`${name} borrada. Ctrl+Z la recupera.`, "done");
}

// -------------------------------------------------------------- drawing

// A new wall may not touch an earlier wall, nor turn back over the last one.
function crossesOpen(points, next) {
  const last = points[points.length - 1];
  const wall = [last, next];
  for (let index = 0; index + 2 < points.length; index += 1) {
    if (touches([points[index], points[index + 1]], wall)) return true;
  }
  if (points.length >= 2) {
    const before = points[points.length - 2];
    const sameLine = (before.x === last.x && last.x === next.x) || (before.y === last.y && last.y === next.y);
    const back = Math.sign(next.x - last.x) === -Math.sign(last.x - before.x)
      && Math.sign(next.y - last.y) === -Math.sign(last.y - before.y);
    if (sameLine && back) return true;
  }
  return false;
}

function touches([a, b], [c, d]) {
  return Math.min(a.x, b.x) <= Math.max(c.x, d.x) && Math.min(c.x, d.x) <= Math.max(a.x, b.x)
    && Math.min(a.y, b.y) <= Math.max(c.y, d.y) && Math.min(c.y, d.y) <= Math.max(a.y, b.y);
}

function addCorner(point) {
  const points = state.drawing.points;
  const next = orthogonalStep(points[points.length - 1], point);
  if (same(next, points[points.length - 1])) return;
  if (points.length >= 3 && same(next, points[0])) {
    closeOutline();
    return;
  }
  if (crossesOpen(points, next)) {
    say("Las paredes no pueden cruzarse ni volver sobre sí mismas.");
    return;
  }
  points.push(next);
  render();
}

function tryClose() {
  const points = state.drawing.points;
  const first = points[0];
  const last = points[points.length - 1];
  if (points.length >= 3 && (first.x === last.x || first.y === last.y)) closeOutline();
  else say("Para cerrar, la última pared tiene que llegar recta a la primera esquina.");
}

function closeOutline() {
  const outline = simplify(state.drawing.points);
  const problem = roomProblem(outline, floor().rooms, state.plan.grid);
  if (problem) {
    say(`No se puede cerrar la sala: ${problem}.`);
    return;
  }
  state.drawing = null;
  createRoom(outline);
}

function removeLastCorner() {
  state.drawing.points.pop();
  if (!state.drawing.points.length) state.drawing = null;
  render();
}

function dragPreview(pointer, point, raw) {
  const { original, index } = pointer;
  let polygon;
  if (pointer.kind === "corner") {
    polygon = moveCorner(original, index, point);
  } else if (pointer.kind === "edge") {
    const next = original[(index + 1) % original.length];
    const offset = original[index].y === next.y
      ? Math.round(raw.y - pointer.raw.y) : Math.round(raw.x - pointer.raw.x);
    polygon = moveEdge(original, index, offset);
  } else {
    polygon = translate(original, Math.round(raw.x - pointer.raw.x), Math.round(raw.y - pointer.raw.y));
  }
  const clean = simplify(polygon);
  return {
    roomId: pointer.roomId, polygon, clean,
    problem: roomProblem(clean, others(pointer.roomId), state.plan.grid),
    changed: JSON.stringify(clean) !== JSON.stringify(original),
  };
}

// --------------------------------------------------------------- pointer

svg.addEventListener("pointerdown", event => {
  if (event.button !== 0 || !state.plan) return;
  const point = gridPoint(event);
  if (state.tool === "room") {
    if (state.drawing) {
      addCorner(point);
    } else {
      state.pointer = { kind: "new", start: point };
      svg.setPointerCapture(event.pointerId);
      renderCanvas();
    }
    return;
  }
  const hit = event.target.closest("[data-hit]");
  if (!hit) {
    select(null);
    return;
  }
  const target = room(hit.dataset.room);
  if (!target) return;
  if (state.selected !== target.id) select(target.id);
  state.pointer = {
    kind: hit.dataset.hit, roomId: target.id, index: Number(hit.dataset.index),
    original: target.polygon, raw: gridPoint(event, false),
  };
  svg.setPointerCapture(event.pointerId);
});

svg.addEventListener("pointermove", event => {
  if (!state.plan) return;
  const point = gridPoint(event);
  const moved = !state.hover || !same(point, state.hover);
  state.hover = point;
  if (state.pointer && state.pointer.kind !== "new") {
    state.preview = dragPreview(state.pointer, point, gridPoint(event, false));
    renderCanvas();
  } else if (moved) {
    renderCanvas();
  }
});

svg.addEventListener("pointerup", event => {
  const pointer = state.pointer;
  if (!pointer) return;
  state.pointer = null;
  if (pointer.kind === "new") {
    const end = gridPoint(event);
    if (pointer.start.x !== end.x && pointer.start.y !== end.y) {
      createRoom(rectangle(pointer.start, end));
    } else {
      // A click (or a straight drag, the first wall) starts an outline drawn corner by corner.
      state.drawing = { points: [pointer.start] };
      if (!same(pointer.start, end)) addCorner(end);
      render();
    }
    return;
  }
  const preview = state.preview;
  state.preview = null;
  if (preview?.changed) {
    if (preview.problem) say(`No se puede: ${preview.problem}.`);
    else change(() => { room(pointer.roomId).polygon = preview.clean; });
  }
  render();
});

svg.addEventListener("pointercancel", () => {
  state.pointer = null;
  state.preview = null;
  render();
});

svg.addEventListener("pointerleave", () => {
  if (state.pointer) return;
  state.hover = null;
  renderCanvas();
});

svg.addEventListener("dblclick", event => {
  if (state.tool !== "select" || !event.target.closest("[data-hit]")) return;
  const input = document.querySelector("#inspector input");
  input?.focus();
  input?.select();
});

// -------------------------------------------------------------- keyboard

addEventListener("keydown", event => {
  if (!state.plan) return;
  const key = event.key.toLowerCase();
  const typing = event.target.closest?.("input");
  if ((event.ctrlKey || event.metaKey) && key === "s") {
    event.preventDefault();
    typing?.blur();
    save();
    return;
  }
  if (typing) {
    if (key === "enter") typing.blur();
    if (key === "escape") { typing.revert?.(); typing.blur(); }
    return;
  }
  if (event.ctrlKey || event.metaKey) {
    if (key === "z" && !event.shiftKey) { event.preventDefault(); undo(); }
    if (key === "y" || (key === "z" && event.shiftKey)) { event.preventDefault(); redo(); }
    return;
  }
  if (key === "escape") {
    if (state.drawing || state.pointer) cancelInteraction();
    else state.selected = null;
    render();
  } else if (key === "enter" && state.drawing) {
    tryClose();
  } else if (key === "backspace" && state.drawing) {
    event.preventDefault();
    removeLastCorner();
  } else if ((key === "delete" || key === "backspace") && state.selected) {
    event.preventDefault();
    deleteRoom(state.selected);
  } else if (key === "v") {
    setTool("select");
  } else if (key === "s") {
    setTool("room");
  }
});

addEventListener("beforeunload", event => {
  if (dirty()) {
    event.preventDefault();
    event.returnValue = "";
  }
});

addEventListener("resize", () => { if (state.plan) renderCanvas(); });

// ---------------------------------------------------------------- render

function render() {
  if (!state.plan) return;
  app.classList.toggle("mode-select", state.tool === "select");
  app.classList.toggle("mode-room", state.tool === "room");
  for (const button of document.querySelectorAll("[data-tool]")) {
    button.classList.toggle("on", button.dataset.tool === state.tool);
  }
  renderCanvas();
  renderFloors();
  renderInspector();
  renderHeader();
}

// Everything but the inspector, so a name field keeps its focus while typing.
function renderLive() {
  renderCanvas();
  renderFloors();
  renderHeader();
}

function renderGrid() {
  const { columns, rows } = state.plan.grid;
  svg.setAttribute("viewBox", `-1 -1 ${columns + 2} ${rows + 2}`);
  layers.grid.innerHTML = `
    <defs><pattern id="dots" x="-0.5" y="-0.5" width="1" height="1" patternUnits="userSpaceOnUse">
      <circle class="grid-dot" cx="0.5" cy="0.5" r="0.07"/></pattern></defs>
    <rect x="-0.5" y="-0.5" width="${columns + 1}" height="${rows + 1}" fill="url(#dots)"/>
    <rect class="grid-edge" x="0" y="0" width="${columns}" height="${rows}"/>`;
}

function renderCanvas() {
  const current = floor();
  const index = state.plan.floors.indexOf(current);
  const below = index > 0 ? state.plan.floors[index - 1] : null;
  layers.ghost.innerHTML = below
    ? below.rooms.map(item => `<polygon class="ghost" points="${pointsAttr(item.polygon)}"/>`).join("")
    : "";
  const previewed = state.preview?.changed ? state.preview.roomId : null;
  layers.rooms.innerHTML = current.rooms
    .filter(item => item.id !== previewed)
    .map(item => `<g class="room${item.id === state.selected ? " selected" : ""}" data-hit="room" data-room="${item.id}">
      <polygon points="${pointsAttr(item.polygon)}"/>${labelMarkup(item.polygon, item.name)}</g>`)
    .join("");
  layers.handles.innerHTML = handlesMarkup();
  layers.preview.innerHTML = previewMarkup();
  layers.cursor.innerHTML = cursorMarkup();
  const empty = !current.rooms.length && !state.drawing && !state.pointer;
  $("empty-hint").hidden = !empty;
  $("empty-hint").textContent = state.tool === "room"
    ? "Arrastra sobre la rejilla para dibujar la primera sala"
    : "Planta vacía · elige ▭ Sala (S) y arrastra sobre la rejilla";
  $("floor-title").textContent = below ? `${current.name} · en gris: ${below.name}` : current.name;
  $("hint").textContent = hintText();
}

function labelMarkup(polygon, name) {
  const space = largestRectangle(polygon);
  if (!space) return "";
  const { x0, x1, y0, y1 } = space;
  const fontSize = clamp(Math.min(1.1, (x1 - x0 - 0.4) / (name.length * 0.62 + 0.3), (y1 - y0) * 0.5), 0.3, 1.1);
  return `<text x="${(x0 + x1) / 2}" y="${(y0 + y1) / 2}" font-size="${fontSize}"
    text-anchor="middle" dominant-baseline="central">${escapeText(name)}</text>`;
}

function handlesMarkup() {
  if (state.tool !== "select" || !state.selected || !room(state.selected)) return "";
  const polygon = state.preview?.changed ? state.preview.polygon : room(state.selected).polygon;
  const half = 6 / pixelsPerStep();
  const id = state.selected;
  const walls = edges(polygon).map(([a, b], index) => `<line class="edge-hit ${a.y === b.y ? "across" : "along"}"
    data-hit="edge" data-room="${id}" data-index="${index}" x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}"/>`);
  const corners = polygon.map((point, index) => `<rect class="corner" data-hit="corner" data-room="${id}"
    data-index="${index}" x="${point.x - half}" y="${point.y - half}" width="${2 * half}" height="${2 * half}"/>`);
  return walls.join("") + corners.join("");
}

function previewMarkup() {
  const step = 1 / pixelsPerStep();
  if (state.preview?.changed) {
    const { polygon, problem } = state.preview;
    const target = room(state.preview.roomId);
    return `<polygon class="preview${problem ? " invalid" : ""}" points="${pointsAttr(polygon)}"/>`
      + (target ? labelMarkup(polygon, target.name).replace("<text", '<text class="preview-label"') : "")
      + tagMarkup(polygon, problem ? `✕ ${problem}` : size(polygon), problem, step);
  }
  if (state.pointer?.kind === "new" && state.hover
      && state.pointer.start.x !== state.hover.x && state.pointer.start.y !== state.hover.y) {
    const polygon = rectangle(state.pointer.start, state.hover);
    const problem = roomProblem(polygon, floor().rooms, state.plan.grid);
    return `<polygon class="preview${problem ? " invalid" : ""}" points="${pointsAttr(polygon)}"/>`
      + tagMarkup(polygon, problem ? `✕ ${problem}` : size(polygon), problem, step);
  }
  if (!state.drawing) return "";
  const points = state.drawing.points;
  const last = points[points.length - 1];
  const next = state.hover ? orthogonalStep(last, state.hover) : null;
  const closing = next && points.length >= 3 && same(next, points[0]);
  const invalid = next && !closing && !same(next, last) && crossesOpen(points, next);
  return `<polyline class="drawing-line" points="${pointsAttr(points)}"/>`
    + (next ? `<line class="drawing-next${invalid ? " invalid" : ""}" x1="${last.x}" y1="${last.y}" x2="${next.x}" y2="${next.y}"/>` : "")
    + points.map(point => `<circle class="drawing-corner" cx="${point.x}" cy="${point.y}" r="${4 * step}"/>`).join("")
    + `<circle class="drawing-start${closing ? " closing" : ""}" cx="${points[0].x}" cy="${points[0].y}" r="${9 * step}"/>`;
}

// A short text just above a shape: its size while drawing, or why it cannot go there.
function tagMarkup(polygon, text, problem, step) {
  const box = bounds(polygon);
  const fontSize = 14 * step;
  const y = box.y0 > 1 ? box.y0 - 6 * step : box.y1 + 18 * step;
  const width = (text.length * 0.6 + 1) * fontSize;
  return `<rect class="label-plate" x="${box.x0}" y="${y - fontSize}" width="${width}" height="${fontSize * 1.4}"/>
    <text class="preview-label${problem ? " invalid" : ""}" x="${box.x0 + fontSize * 0.5}" y="${y}"
      font-size="${fontSize}">${escapeText(text)}</text>`;
}

function cursorMarkup() {
  const hover = state.hover;
  if (!hover || (state.tool !== "room" && !state.pointer)) return "";
  const { columns, rows } = state.plan.grid;
  return `<line class="cursor-line" x1="0" y1="${hover.y}" x2="${columns}" y2="${hover.y}"/>
    <line class="cursor-line" x1="${hover.x}" y1="0" x2="${hover.x}" y2="${rows}"/>
    <circle class="cursor-point" cx="${hover.x}" cy="${hover.y}" r="${5 / pixelsPerStep()}"/>`;
}

function hintText() {
  if (state.drawing) {
    return "Clic: siguiente esquina · clic en la primera esquina o Intro: cerrar · Retroceso: borrar la última · Esc: cancelar";
  }
  if (state.tool === "room") {
    return "Arrastra para dibujar un rectángulo · o haz clic en cada esquina para una forma en L · V: seleccionar";
  }
  if (state.selected) {
    return "Arrastra una esquina o una pared para cambiar la forma · arrastra el interior para mover la sala · Supr: borrar · Esc: soltar";
  }
  return "Haz clic en una sala para elegirla · S: dibujar una sala nueva · Ctrl+Z: deshacer";
}

function renderFloors() {
  const list = $("floor-list");
  list.replaceChildren(...state.plan.floors.map(item => {
    const entry = document.createElement("li");
    const button = document.createElement("button");
    button.className = item.id === state.floorId ? "on" : "";
    const name = document.createElement("span");
    name.textContent = item.name;
    const count = document.createElement("span");
    count.className = "count";
    count.textContent = `${item.rooms.length} ${item.rooms.length === 1 ? "sala" : "salas"}`;
    button.append(name, count);
    button.addEventListener("click", () => setFloor(item.id));
    entry.append(button);
    return entry;
  }));
  const full = state.plan.floors.length >= MAX_FLOORS;
  $("add-floor").disabled = full;
  $("add-floor").textContent = full ? `Máximo ${MAX_FLOORS} plantas` : "+ Nueva planta";
}

function nameField(labelText, value, maxLength, apply) {
  const label = document.createElement("label");
  const caption = document.createElement("span");
  caption.className = "bc-label";
  caption.textContent = labelText;
  const input = document.createElement("input");
  input.value = value;
  input.maxLength = maxLength;
  input.spellcheck = false;
  let original = value;
  let snapshot = null;
  input.addEventListener("focus", () => {
    original = input.value;
    snapshot = JSON.stringify(state.plan);
  });
  input.addEventListener("input", () => {
    if (input.value.trim()) {
      apply(input.value.trim());
      renderLive();
    }
  });
  input.addEventListener("change", () => {
    if (!input.value.trim()) {
      input.value = original;
      apply(original);
    }
    if (snapshot && JSON.stringify(state.plan) !== snapshot) remember(snapshot);
    snapshot = null;
    renderLive();
  });
  input.revert = () => {
    input.value = original;
    apply(original);
    renderLive();
  };
  label.append(caption, input);
  return label;
}

function note(text) {
  const element = document.createElement("div");
  element.className = "note";
  element.textContent = text;
  return element;
}

function section(...children) {
  const element = document.createElement("div");
  element.className = "section";
  element.append(...children);
  return element;
}

function actionButton(text, onClick, className = "") {
  const button = document.createElement("button");
  button.textContent = text;
  button.className = className;
  button.addEventListener("click", onClick);
  return button;
}

function renderInspector() {
  const inspector = $("inspector");
  const target = state.selected ? room(state.selected) : null;
  if (target) {
    inspector.replaceChildren(section(
      nameField("Sala", target.name, 60, name => { room(target.id).name = name; }),
      note(`Tamaño: ${size(target.polygon)} casillas · ${target.polygon.length} esquinas`),
      note("Arrastra una esquina o una pared para cambiar la forma, o el interior para moverla."),
      actionButton("Borrar sala · Supr", () => deleteRoom(target.id), "danger"),
    ));
    return;
  }
  const current = floor();
  const count = current.rooms.length;
  const remove = actionButton("Borrar planta", () => {
    if (count && !remove.classList.contains("armed")) {
      remove.classList.add("armed");
      remove.textContent = `¿Seguro? Borra ${count} ${count === 1 ? "sala" : "salas"}`;
      setTimeout(() => { remove.classList.remove("armed"); remove.textContent = "Borrar planta"; }, 4000);
      return;
    }
    deleteFloor(current.id);
  }, "danger");
  remove.disabled = state.plan.floors.length === 1;
  inspector.replaceChildren(
    section(
      nameField("Planta", current.name, 40, name => { floor().name = name; }),
      note(`${count} ${count === 1 ? "sala" : "salas"}. Las plantas se muestran en el mapa una al lado de otra, en este orden.`),
      remove,
    ),
    section(
      nameField("Espacio de trabajo", state.plan.workspace.name, 120,
        name => { state.plan.workspace.name = name; }),
      note("El edificio y sus salas son datos del sitio: el plano se guarda solo en este PC."),
    ),
  );
}

function renderHeader() {
  $("workspace-name").textContent = state.plan.workspace.name;
  const pending = dirty();
  const saveState = $("save-state");
  saveState.className = `save-state${pending ? " dirty" : ""}`;
  saveState.textContent = state.saving ? "Guardando…"
    : pending ? "● Cambios sin guardar"
      : state.revision ? "✓ Guardado" : "Plano nuevo";
  $("save").disabled = !pending || state.saving;
  $("undo").disabled = !state.history.length;
  $("redo").disabled = !state.future.length;
}

// -------------------------------------------------------------- messages

let messageTimer = null;

function say(text, kind = "error", action = null) {
  const message = $("message");
  clearTimeout(messageTimer);
  message.className = `message ${kind}`;
  message.textContent = kind === "error" ? `▲ ${text}` : text;
  if (action) {
    message.append(actionButton(action.text, action.run));
  } else {
    messageTimer = setTimeout(() => { message.textContent = ""; message.className = "message"; }, 8000);
  }
}

// ------------------------------------------------------------ load, save

async function save() {
  if (state.saving || !dirty()) return;
  state.saving = true;
  renderHeader();
  const sent = JSON.stringify(state.plan);
  try {
    const response = await fetch("/api/space-map", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: `{"plan":${sent},"revision":${JSON.stringify(state.revision)}}`,
    });
    const answer = await response.json();
    if (response.ok) {
      state.revision = answer.revision;
      state.savedJson = sent;
      say(`✓ Guardado a las ${new Date().toLocaleTimeString("es-ES", { hour12: false })}`, "done");
    } else if (response.status === 409) {
      say("Otra ventana guardó el plano mientras editabas: no se ha guardado.", "error", {
        text: "Recargar (se pierden los cambios de esta ventana)",
        run: () => { state.savedJson = JSON.stringify(state.plan); location.reload(); },
      });
    } else {
      say(`No se ha guardado: ${answer.error || `error ${response.status}`}`);
    }
  } catch {
    say("El servicio de pantallas no responde: no se ha guardado.");
  } finally {
    state.saving = false;
    renderHeader();
  }
}

async function load() {
  try {
    const response = await fetch("/api/space-map", { cache: "no-store" });
    const answer = await response.json();
    if (!response.ok) throw new Error(answer.error || `error ${response.status}`);
    state.plan = answer.plan;
    state.revision = answer.revision;
    state.savedJson = JSON.stringify(state.plan);
    state.floorId = state.plan.floors[0].id;
    renderGrid();
    render();
  } catch (error) {
    const fatal = document.createElement("div");
    fatal.className = "fatal";
    fatal.append(note(`No se puede abrir el plano: ${error.message}`),
      actionButton("Reintentar", () => location.reload(), "primary"));
    $("layout").replaceChildren(fatal);
    $("save-state").textContent = "Sin plano";
  }
}

for (const button of document.querySelectorAll("[data-tool]")) {
  button.addEventListener("click", () => setTool(button.dataset.tool));
}
$("add-floor").addEventListener("click", addFloor);
$("undo").addEventListener("click", undo);
$("redo").addEventListener("click", redo);
$("save").addEventListener("click", save);

load();
