// Space editor: floors, rooms, cameras, doors and floor links on the grid.
//
// The plan (space_map.v2) comes from and goes back to /api/space-map; the
// server validates it again on save. Every change goes through `change()`,
// which keeps the undo history; drags only preview until the pointer is
// released. After any change to rooms, `reconcileFloor` keeps doors and
// cameras consistent with them, so what the editor saves is always valid.

import {
  bounds, cameraRoom, cells, doorRooms, edges, headingVector, largestRectangle, moveCorner,
  moveEdge, orthogonalStep, reconcileFloor, rectangle, roomProblem, segmentUnits, simplify,
  translate, unitKey, wallMap,
} from "./geometry.js";

const MAX_FLOORS = 3;
const HISTORY = 200;
const VIEW_RADIUS = 2.6;
const HANDLE_DISTANCE = 2.2;
const LINK_KINDS = { stairs: "Escalera", elevator: "Ascensor" };

const $ = id => document.getElementById(id);
const app = $("app");
const svg = $("plan");
const layers = {
  grid: $("layer-grid"), ghost: $("layer-ghost"), rooms: $("layer-rooms"), doors: $("layer-doors"),
  cameras: $("layer-cameras"), handles: $("layer-handles"), preview: $("layer-preview"),
  cursor: $("layer-cursor"),
};

const state = {
  plan: null, revision: null, savedJson: null,
  floorId: null, tool: "select",
  // {kind: "room" | "camera" | "door" | "link", id}
  selected: null,
  pointer: null, preview: null, drawing: null, hover: null, hoverRaw: null,
  linking: null,
  cameras: [],
  history: [], future: [], saving: false,
};

// ---------------------------------------------------------------- helpers

const clamp = (value, low, high) => Math.min(high, Math.max(low, value));
const same = (a, b) => a.x === b.x && a.y === b.y;
const floor = () => state.plan.floors.find(item => item.id === state.floorId);
const room = id => floor().rooms.find(item => item.id === id);
const camera = id => floor().cameras.find(item => item.camera_id === id);
const door = id => floor().doors.find(item => item.id === id);
const link = id => state.plan.floor_links.find(item => item.id === id);
const others = id => floor().rooms.filter(item => item.id !== id);
const isSelected = (kind, id) => state.selected?.kind === kind && state.selected.id === id;
const pointsAttr = points => points.map(point => `${point.x},${point.y}`).join(" ");
const escapeText = text => String(text).replace(/[&<>"']/g,
  char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
const pixelsPerStep = () => svg.getScreenCTM()?.a || 20;
const dirty = () => state.plan !== null && JSON.stringify(state.plan) !== state.savedJson;
const plural = (count, one, many) => `${count} ${count === 1 ? one : many}`;

function findRoom(id) {
  for (const item of state.plan.floors) {
    const found = item.rooms.find(candidate => candidate.id === id);
    if (found) return { room: found, floor: item };
  }
  return null;
}

function cameraInfo(id) {
  return state.cameras.find(item => item.camera_id === id) ?? { camera_id: id, source: "", counts: true, note: "" };
}

function newId(prefix, taken) {
  let id;
  do { id = `${prefix}_${Math.random().toString(36).slice(2, 8)}`; } while (taken.has(id));
  return id;
}

function allIds() {
  const ids = new Set(state.plan.floor_links.map(item => item.id));
  for (const item of state.plan.floors) {
    ids.add(item.id);
    for (const child of [...item.rooms, ...item.doors]) ids.add(child.id);
  }
  return ids;
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

function halfStep(raw) {
  const { columns, rows } = state.plan.grid;
  return { x: clamp(Math.round(raw.x * 2) / 2, 0, columns), y: clamp(Math.round(raw.y * 2) / 2, 0, rows) };
}

function size(points) {
  const box = bounds(points);
  return `${box.x1 - box.x0} × ${box.y1 - box.y0}`;
}

function roomCentre(target) {
  const space = largestRectangle(target.polygon);
  return { x: (space.x0 + space.x1) / 2, y: (space.y0 + space.y1) / 2 };
}

// Degrees clockwise from the top of the plan, in 15° steps.
function headingTowards(from, to) {
  const degrees = (Math.atan2(to.x - from.x, -(to.y - from.y)) * 180) / Math.PI;
  return ((Math.round(degrees / 15) * 15) % 360 + 360) % 360;
}

function removedDoorsNotice(removed) {
  if (removed) {
    say(removed === 1 ? "Se quitó 1 puerta que ya no estaba en su pared."
      : `Se quitaron ${removed} puertas que ya no estaban en su pared.`, "warn");
  }
}

// --------------------------------------------------------------- history

function remember(before) {
  state.history.push(before);
  if (state.history.length > HISTORY) state.history.shift();
  state.future = [];
}

function change(mutate) {
  const before = JSON.stringify(state.plan);
  const result = mutate(state.plan);
  if (JSON.stringify(state.plan) !== before) remember(before);
  render();
  return result;
}

function restore(json) {
  state.plan = JSON.parse(json);
  if (!floor()) state.floorId = state.plan.floors[0].id;
  if (!selectionExists()) state.selected = null;
  render();
}

function selectionExists() {
  const selected = state.selected;
  if (!selected) return true;
  return Boolean({ room, camera, door, link }[selected.kind](selected.id));
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
  state.linking = null;
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

function select(kind, id) {
  state.selected = kind ? { kind, id } : null;
  if (kind !== "room") state.linking = null;
  render();
}

function focusName() {
  const input = document.querySelector("#inspector input");
  input?.focus();
  input?.select();
}

function createRoom(polygon) {
  const problem = roomProblem(polygon, floor().rooms, state.plan.grid);
  if (problem) {
    say(`No se puede crear la sala: ${problem}.`);
    return;
  }
  const rooms = state.plan.floors.flatMap(item => item.rooms);
  const id = newId("room", allIds());
  const name = nextName("Sala", new Set(rooms.map(item => item.name)));
  const removed = change(() => {
    floor().rooms.push({ id, name, polygon });
    return reconcileFloor(floor());
  });
  state.selected = { kind: "room", id };
  render();
  removedDoorsNotice(removed);
  focusName();
}

// Applies a dragged room shape. A moved room takes its cameras and its
// entrances along; doors to other rooms stay only if the wall still matches.
function applyRoomShape(roomId, polygon, offset) {
  const removed = change(() => {
    const current = floor();
    room(roomId).polygon = polygon;
    if (offset) {
      for (const item of current.cameras) {
        if (item.room_id === roomId) item.position = { x: item.position.x + offset.x, y: item.position.y + offset.y };
      }
      for (const item of current.doors) {
        if (item.rooms.includes(roomId) && item.rooms.includes("exterior")) {
          item.segment = translate(item.segment, offset.x, offset.y);
        }
      }
    }
    return reconcileFloor(current);
  });
  removedDoorsNotice(removed);
}

function deleteSelected() {
  const selected = state.selected;
  if (!selected) return;
  if (selected.kind === "room") deleteRoom(selected.id);
  if (selected.kind === "camera") removeCamera(selected.id);
  if (selected.kind === "door") deleteDoor(selected.id);
  if (selected.kind === "link") deleteLink(selected.id);
}

function deleteRoom(id) {
  const target = room(id);
  if (!target) return;
  change(plan => {
    const current = floor();
    current.rooms = current.rooms.filter(item => item.id !== id);
    current.doors = current.doors.filter(item => !item.rooms.includes(id));
    for (const item of current.cameras) if (item.room_id === id) item.room_id = null;
    plan.floor_links = plan.floor_links.filter(item => !item.rooms.includes(id));
    reconcileFloor(current);
  });
  state.selected = null;
  render();
  say(`Sala «${target.name}» borrada. Ctrl+Z la recupera.`, "done");
}

function placeCamera(cameraId, position) {
  const rooms = floor().rooms;
  const inside = cameraRoom(position, 180, rooms);
  const target = inside ? room(inside) : null;
  // A new camera looks towards the middle of its room.
  const heading = target && !same(position, roomCentre(target)) ? headingTowards(position, roomCentre(target)) : 180;
  change(() => {
    floor().cameras.push({
      camera_id: cameraId,
      room_id: cameraRoom(position, heading, rooms),
      position,
      heading_deg: heading,
    });
  });
  select("camera", cameraId);
}

function updateCamera(cameraId, position, heading) {
  change(() => {
    const target = camera(cameraId);
    target.position = position;
    target.heading_deg = heading;
    target.room_id = cameraRoom(position, heading, floor().rooms);
  });
}

function removeCamera(cameraId) {
  change(() => {
    floor().cameras = floor().cameras.filter(item => item.camera_id !== cameraId);
  });
  state.selected = null;
  render();
  say(`${cameraId} vuelve a «sin colocar». Ctrl+Z la recupera.`, "done");
}

function createDoor(start, end, rooms) {
  const id = newId("door", allIds());
  change(() => { floor().doors.push({ id, rooms, segment: [start, end] }); });
  select("door", id);
}

function deleteDoor(id) {
  change(() => { floor().doors = floor().doors.filter(item => item.id !== id); });
  state.selected = null;
  render();
}

function createLink(fromId, toId, kind) {
  const exists = state.plan.floor_links.some(item => item.kind === kind
    && item.rooms.includes(fromId) && item.rooms.includes(toId));
  if (exists) {
    say(`Esas dos salas ya están unidas con ${LINK_KINDS[kind].toLowerCase()}.`);
    return;
  }
  const id = newId("link", allIds());
  change(plan => { plan.floor_links.push({ id, kind, rooms: [fromId, toId] }); });
  state.linking = null;
  select("link", id);
}

function deleteLink(id) {
  change(plan => { plan.floor_links = plan.floor_links.filter(item => item.id !== id); });
  state.selected = null;
  render();
}

function addFloor() {
  if (state.plan.floors.length >= MAX_FLOORS) return;
  const id = newId("floor", allIds());
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
    plan.floor_links = plan.floor_links.filter(item => !item.rooms.some(roomId => gone.has(roomId)));
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

function roomDragPreview(pointer, point, raw) {
  const { original, index } = pointer;
  let polygon;
  let offset = null;
  if (pointer.kind === "corner") {
    polygon = moveCorner(original, index, point);
  } else if (pointer.kind === "edge") {
    const next = original[(index + 1) % original.length];
    const step = original[index].y === next.y
      ? Math.round(raw.y - pointer.raw.y) : Math.round(raw.x - pointer.raw.x);
    polygon = moveEdge(original, index, step);
  } else {
    offset = { x: Math.round(raw.x - pointer.raw.x), y: Math.round(raw.y - pointer.raw.y) };
    polygon = translate(original, offset.x, offset.y);
  }
  const clean = simplify(polygon);
  return {
    kind: "room", roomId: pointer.roomId, polygon, clean, offset,
    problem: roomProblem(clean, others(pointer.roomId), state.plan.grid),
    changed: JSON.stringify(clean) !== JSON.stringify(original),
  };
}

// ----------------------------------------------------------------- doors

function doorUnits() {
  const taken = new Map();
  for (const item of floor().doors) {
    for (const key of segmentUnits(...item.segment)) taken.set(key, item.id);
  }
  return taken;
}

// The wall step nearest to the pointer, if the pointer is close to a wall.
function wallUnderPointer(raw, walls) {
  const across = { x: Math.floor(raw.x), y: Math.round(raw.y), horizontal: true, distance: Math.abs(raw.y - Math.round(raw.y)) };
  const along = { x: Math.round(raw.x), y: Math.floor(raw.y), horizontal: false, distance: Math.abs(raw.x - Math.round(raw.x)) };
  return [across, along]
    .map(option => ({ ...option, key: unitKey(option.x, option.y, option.horizontal) }))
    .filter(option => walls.has(option.key) && option.distance <= 0.45)
    .sort((a, b) => a.distance - b.distance)[0] ?? null;
}

// Grows a door from its first wall step towards the pointer, along the same
// wall, stopping before another kind of wall or another door.
function doorFrom(start, raw, walls, taken) {
  const target = start.horizontal ? Math.floor(raw.x) : Math.floor(raw.y);
  const origin = start.horizontal ? start.x : start.y;
  const direction = Math.sign(target - origin);
  const kind = doorRooms([start.key], walls);
  let end = origin;
  for (let index = origin + direction; direction && index !== target + direction; index += direction) {
    const key = start.horizontal ? unitKey(index, start.y, true) : unitKey(start.x, index, false);
    if (taken.has(key) || !doorRooms([key], walls) || doorRooms([key], walls).join() !== kind.join()) break;
    end = index;
  }
  const [low, high] = [Math.min(origin, end), Math.max(origin, end) + 1];
  const segment = start.horizontal
    ? [{ x: low, y: start.y }, { x: high, y: start.y }]
    : [{ x: start.x, y: low }, { x: start.x, y: high }];
  return { segment, rooms: kind };
}

// ------------------------------------------------------------- pointer

svg.addEventListener("pointerdown", event => {
  if (event.button !== 0 || !state.plan) return;
  const point = gridPoint(event);
  const raw = gridPoint(event, false);
  const hit = event.target.closest("[data-hit]");
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
  if (state.tool === "door") {
    const walls = wallMap(floor().rooms);
    const taken = doorUnits();
    const unit = wallUnderPointer(raw, walls);
    if (unit && taken.has(unit.key)) {
      select("door", taken.get(unit.key));
    } else if (unit) {
      state.pointer = { kind: "door", unit, walls, taken };
      state.preview = { kind: "door", ...doorFrom(unit, raw, walls, taken) };
      svg.setPointerCapture(event.pointerId);
      renderCanvas();
    } else {
      say("Pulsa sobre una pared para poner una puerta.");
    }
    return;
  }
  if (state.tool === "link") {
    const target = hit?.dataset.room ? room(hit.dataset.room) : null;
    if (!target) {
      state.linking = null;
      select(null);
      return;
    }
    state.selected = { kind: "room", id: target.id };
    state.linking = { roomId: target.id, kind: state.linking?.kind ?? "stairs" };
    render();
    return;
  }
  // Select tool.
  if (!hit) {
    select(null);
    return;
  }
  const kind = hit.dataset.hit;
  if (kind === "link") {
    select("link", hit.dataset.link);
    return;
  }
  if (kind === "door") {
    select("door", hit.dataset.door);
    return;
  }
  if (kind === "camera" || kind === "heading") {
    const target = camera(hit.dataset.camera);
    if (!isSelected("camera", target.camera_id)) select("camera", target.camera_id);
    state.pointer = { kind, cameraId: target.camera_id, original: { ...target.position }, heading: target.heading_deg, raw };
    svg.setPointerCapture(event.pointerId);
    return;
  }
  const target = room(hit.dataset.room);
  if (!target) return;
  if (!isSelected("room", target.id)) select("room", target.id);
  state.pointer = {
    kind: kind === "room" ? "body" : kind, roomId: target.id, index: Number(hit.dataset.index),
    original: target.polygon, raw,
  };
  svg.setPointerCapture(event.pointerId);
});

svg.addEventListener("pointermove", event => {
  if (!state.plan) return;
  const point = gridPoint(event);
  const raw = gridPoint(event, false);
  const moved = !state.hover || !same(point, state.hover);
  state.hover = point;
  state.hoverRaw = raw;
  const pointer = state.pointer;
  if (!pointer) {
    if (moved || state.tool === "door") renderCanvas();
    return;
  }
  if (pointer.kind === "door") {
    state.preview = { kind: "door", ...doorFrom(pointer.unit, raw, pointer.walls, pointer.taken) };
  } else if (pointer.kind === "camera") {
    const position = halfStep({ x: pointer.original.x + raw.x - pointer.raw.x, y: pointer.original.y + raw.y - pointer.raw.y });
    state.preview = { kind: "camera", cameraId: pointer.cameraId, position, heading: pointer.heading };
  } else if (pointer.kind === "heading") {
    state.preview = { kind: "camera", cameraId: pointer.cameraId, position: pointer.original,
      heading: headingTowards(pointer.original, raw) };
  } else if (pointer.kind !== "new") {
    state.preview = roomDragPreview(pointer, point, raw);
  }
  renderCanvas();
});

svg.addEventListener("pointerup", event => {
  const pointer = state.pointer;
  if (!pointer) return;
  state.pointer = null;
  const preview = state.preview;
  state.preview = null;
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
  if (pointer.kind === "door" && preview) {
    createDoor(...preview.segment, preview.rooms);
    return;
  }
  if (preview?.kind === "camera") {
    const target = camera(preview.cameraId);
    if (!same(preview.position, target.position) || preview.heading !== target.heading_deg) {
      updateCamera(preview.cameraId, preview.position, preview.heading);
    }
  } else if (preview?.changed) {
    if (preview.problem) say(`No se puede: ${preview.problem}.`);
    else applyRoomShape(pointer.roomId, preview.clean, preview.offset);
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
  state.hoverRaw = null;
  renderCanvas();
});

svg.addEventListener("dblclick", event => {
  if (state.tool === "select" && event.target.closest("[data-hit]")) focusName();
});

// A camera from the tray follows the pointer and lands where it is released.
function trayDrag(chip, cameraId) {
  chip.addEventListener("pointerdown", event => {
    if (event.button !== 0) return;
    chip.setPointerCapture(event.pointerId);
    state.pointer = null;
    state.preview = null;
    state.drawing = null;
    state.tool = "select";
    state.pointer = { kind: "tray", cameraId };
    // Not the inspector: rebuilding it would drop the chip being dragged.
    renderModes();
    renderCanvas();
  });
  const over = event => {
    const box = svg.getBoundingClientRect();
    return event.clientX >= box.left && event.clientX <= box.right && event.clientY >= box.top && event.clientY <= box.bottom;
  };
  chip.addEventListener("pointermove", event => {
    if (state.pointer?.kind !== "tray") return;
    state.preview = over(event) ? { kind: "tray", cameraId, position: halfStep(gridPoint(event, false)) } : null;
    renderCanvas();
  });
  chip.addEventListener("pointerup", () => {
    if (state.pointer?.kind !== "tray") return;
    const preview = state.preview;
    state.pointer = null;
    state.preview = null;
    if (preview) placeCamera(cameraId, preview.position);
    else render();
  });
}

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
    if (state.drawing || state.pointer || state.linking) cancelInteraction();
    else state.selected = null;
    render();
  } else if (key === "enter" && state.drawing) {
    tryClose();
  } else if (key === "backspace" && state.drawing) {
    event.preventDefault();
    removeLastCorner();
  } else if ((key === "delete" || key === "backspace") && state.selected) {
    event.preventDefault();
    deleteSelected();
  } else if (key === "v") {
    setTool("select");
  } else if (key === "s") {
    setTool("room");
  } else if (key === "p") {
    setTool("door");
  } else if (key === "e") {
    setTool("link");
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
  renderModes();
  renderCanvas();
  renderFloors();
  renderInspector();
  renderHeader();
}

function renderModes() {
  for (const tool of ["select", "room", "door", "link"]) app.classList.toggle(`mode-${tool}`, state.tool === tool);
  for (const button of document.querySelectorAll("[data-tool]")) {
    button.classList.toggle("on", button.dataset.tool === state.tool);
  }
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
  const step = 1 / pixelsPerStep();
  layers.ghost.innerHTML = below
    ? below.rooms.map(item => `<polygon class="ghost" points="${pointsAttr(item.polygon)}"/>`).join("")
    : "";
  const previewed = state.preview?.kind === "room" && state.preview.changed ? state.preview.roomId : null;
  layers.rooms.innerHTML = current.rooms
    .filter(item => item.id !== previewed)
    .map(item => `<g class="room${isSelected("room", item.id) ? " selected" : ""}" data-hit="room" data-room="${item.id}">
      <polygon points="${pointsAttr(item.polygon)}"/>${labelMarkup(item.polygon, item.name, item.id)}</g>`)
    .join("");
  layers.doors.innerHTML = current.doors.map(item => doorMarkup(item, step)).join("");
  layers.cameras.innerHTML = current.cameras.map(item => {
    const moving = state.preview?.kind === "camera" && state.preview.cameraId === item.camera_id;
    return cameraMarkup(item.camera_id, moving ? state.preview.position : item.position,
      moving ? state.preview.heading : item.heading_deg, item.room_id, step);
  }).join("");
  layers.handles.innerHTML = handlesMarkup(step);
  layers.preview.innerHTML = previewMarkup(step);
  layers.cursor.innerHTML = cursorMarkup(step);
  const empty = !current.rooms.length && !state.drawing && !state.pointer;
  $("empty-hint").hidden = !empty;
  $("empty-hint").textContent = state.tool === "room"
    ? "Arrastra sobre la rejilla para dibujar la primera sala"
    : "Planta vacía · elige ▭ Sala (S) y arrastra sobre la rejilla";
  $("floor-title").textContent = below ? `${current.name} · en gris: ${below.name}` : current.name;
  $("hint").textContent = hintText();
}

function linksOf(roomId) {
  return state.plan.floor_links.filter(item => item.rooms.includes(roomId)).map(item => {
    const other = findRoom(item.rooms.find(id => id !== roomId));
    return { link: item, other };
  }).filter(entry => entry.other);
}

function labelMarkup(polygon, name, roomId = null) {
  const space = largestRectangle(polygon);
  if (!space) return "";
  const { x0, x1, y0, y1 } = space;
  const fontSize = clamp(Math.min(1.1, (x1 - x0 - 0.4) / (name.length * 0.62 + 0.3), (y1 - y0) * 0.5), 0.3, 1.1);
  const x = (x0 + x1) / 2;
  const marks = roomId ? linksOf(roomId) : [];
  const y = (y0 + y1) / 2 - (marks.length ? 0.35 * marks.length : 0);
  const markSize = clamp(Math.min(0.48, (x1 - x0 - 0.4) / 18), 0.28, 0.48);
  const markup = marks.map(({ link: item, other }, index) => `<text class="link-mark${isSelected("link", item.id) ? " selected" : ""}"
      data-hit="link" data-link="${item.id}" x="${x}" y="${y + fontSize * 0.9 + markSize * 1.5 * (index + 0.5)}"
      font-size="${markSize}" text-anchor="middle" dominant-baseline="central">⇅ ${escapeText(
        `${LINK_KINDS[item.kind]} · ${other.floor.name} · ${other.room.name}`)}</text>`).join("");
  return `<text x="${x}" y="${y}" font-size="${fontSize}" text-anchor="middle" dominant-baseline="central">${escapeText(name)}</text>${markup}`;
}

function doorMarkup(item, step) {
  const [a, b] = item.segment;
  const horizontal = a.y === b.y;
  const tick = 0.35;
  const ticks = [a, b].map(end => horizontal
    ? `<line class="door-tick" x1="${end.x}" y1="${end.y - tick}" x2="${end.x}" y2="${end.y + tick}"/>`
    : `<line class="door-tick" x1="${end.x - tick}" y1="${end.y}" x2="${end.x + tick}" y2="${end.y}"/>`).join("");
  let outside = "";
  if (item.rooms.includes("exterior")) {
    // Arrow and word on the side of the wall away from the room.
    const inside = room(item.rooms.find(id => id !== "exterior"));
    const own = inside ? cells(inside.polygon) : new Set();
    const mid = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
    const probe = horizontal ? { x: Math.floor(mid.x - 0.01), y: a.y } : { x: a.x, y: Math.floor(mid.y - 0.01) };
    const roomBelowOrRight = horizontal ? own.has(probe.y * 1000 + probe.x) : own.has(probe.y * 1000 + probe.x);
    const out = horizontal ? { x: 0, y: roomBelowOrRight ? -1 : 1 } : { x: roomBelowOrRight ? -1 : 1, y: 0 };
    const tip = { x: mid.x + out.x * 0.6, y: mid.y + out.y * 0.6 };
    const side = { x: out.y * 0.3, y: out.x * 0.3 };
    const base = { x: mid.x + out.x * 0.2, y: mid.y + out.y * 0.2 };
    const wordSize = 12 * step;
    const word = { x: mid.x + out.x * (0.6 + wordSize * 1.4), y: mid.y + out.y * (0.6 + wordSize * 1.4) };
    outside = `<polygon class="door-arrow" points="${tip.x},${tip.y} ${base.x + side.x},${base.y + side.y} ${base.x - side.x},${base.y - side.y}"/>
      <text class="door-word" x="${word.x}" y="${word.y}" font-size="${wordSize}" text-anchor="middle" dominant-baseline="central">EXTERIOR</text>`;
  }
  const coords = `x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}"`;
  return `<g class="door${item.rooms.includes("exterior") ? " entrance" : ""}${isSelected("door", item.id) ? " selected" : ""}"
      data-hit="door" data-door="${item.id}">
    <line class="door-hit" ${coords}/><line class="door-gap" ${coords}/><line class="door-line" ${coords}/>${ticks}${outside}</g>`;
}

function cameraMarkup(cameraId, position, heading, roomId, step, extra = "") {
  const info = cameraInfo(cameraId);
  const left = headingVector(heading - 45);
  const right = headingVector(heading + 45);
  const ahead = headingVector(heading);
  const { x, y } = position;
  const radius = 0.42;
  const classes = ["camera", info.counts ? "" : "nocount", roomId ? "" : "outside",
    isSelected("camera", cameraId) ? "selected" : "", extra].filter(Boolean).join(" ");
  return `<g class="${classes}" data-hit="camera" data-camera="${escapeText(cameraId)}">
    <path class="camera-view" d="M ${x} ${y} L ${x + left.x * VIEW_RADIUS} ${y + left.y * VIEW_RADIUS}
      A ${VIEW_RADIUS} ${VIEW_RADIUS} 0 0 1 ${x + right.x * VIEW_RADIUS} ${y + right.y * VIEW_RADIUS} Z"/>
    <circle class="camera-body" cx="${x}" cy="${y}" r="${radius}"/>
    <line class="camera-notch" x1="${x}" y1="${y}" x2="${x + ahead.x * radius}" y2="${y + ahead.y * radius}"/>
    <text class="camera-label" x="${x}" y="${y + radius + 14 * step}" font-size="${11 * step}" text-anchor="middle">${escapeText(cameraId)}</text>
  </g>`;
}

function handlesMarkup(step) {
  if (state.tool !== "select" || !state.selected) return "";
  const half = 6 * step;
  if (state.selected.kind === "camera") {
    const target = camera(state.selected.id);
    if (!target) return "";
    const moving = state.preview?.kind === "camera" ? state.preview : null;
    const position = moving ? moving.position : target.position;
    const ahead = headingVector(moving ? moving.heading : target.heading_deg);
    const handle = { x: position.x + ahead.x * HANDLE_DISTANCE, y: position.y + ahead.y * HANDLE_DISTANCE };
    return `<line class="heading-line" x1="${position.x}" y1="${position.y}" x2="${handle.x}" y2="${handle.y}"/>
      <circle class="heading-handle" data-hit="heading" data-camera="${escapeText(target.camera_id)}"
        cx="${handle.x}" cy="${handle.y}" r="${7 * step}"/>`;
  }
  if (state.selected.kind !== "room" || !room(state.selected.id)) return "";
  const polygon = state.preview?.kind === "room" && state.preview.changed ? state.preview.polygon : room(state.selected.id).polygon;
  const id = state.selected.id;
  const walls = edges(polygon).map(([a, b], index) => `<line class="edge-hit ${a.y === b.y ? "across" : "along"}"
    data-hit="edge" data-room="${id}" data-index="${index}" x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}"/>`);
  const corners = polygon.map((point, index) => `<rect class="corner" data-hit="corner" data-room="${id}"
    data-index="${index}" x="${point.x - half}" y="${point.y - half}" width="${2 * half}" height="${2 * half}"/>`);
  return walls.join("") + corners.join("");
}

function previewMarkup(step) {
  const preview = state.preview;
  if (preview?.kind === "room" && preview.changed) {
    const { polygon, problem } = preview;
    const target = room(preview.roomId);
    return `<polygon class="preview${problem ? " invalid" : ""}" points="${pointsAttr(polygon)}"/>`
      + (target ? labelMarkup(polygon, target.name).replace("<text", '<text class="preview-label"') : "")
      + tagMarkup(polygon, problem ? `✕ ${problem}` : size(polygon), problem, step);
  }
  if (preview?.kind === "door") {
    const [a, b] = preview.segment;
    const label = preview.rooms.includes("exterior") ? "entrada desde el Exterior" : "puerta entre salas";
    return `<line class="door-preview" x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}"/>`
      + tagMarkup(preview.segment, `${label} · ${Math.abs(b.x - a.x) + Math.abs(b.y - a.y)}`, null, step);
  }
  if (preview?.kind === "tray") {
    const roomId = cameraRoom(preview.position, 180, floor().rooms);
    const where = roomId ? `en «${room(roomId).name}»` : "fuera de las salas";
    return cameraMarkup(preview.cameraId, preview.position, 180, roomId, step, "ghost-camera")
      + tagMarkup([preview.position], where, !roomId, step);
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
function tagMarkup(points, text, problem, step) {
  const box = bounds(points);
  const fontSize = 14 * step;
  const y = box.y0 > 1 ? box.y0 - 6 * step : box.y1 + 18 * step;
  const width = (text.length * 0.6 + 1) * fontSize;
  return `<rect class="label-plate" x="${box.x0}" y="${y - fontSize}" width="${width}" height="${fontSize * 1.4}"/>
    <text class="preview-label${problem ? " invalid" : ""}" x="${box.x0 + fontSize * 0.5}" y="${y}"
      font-size="${fontSize}">${escapeText(text)}</text>`;
}

function cursorMarkup(step) {
  const hover = state.hover;
  if (state.tool === "door" && state.hoverRaw && !state.pointer) {
    const unit = wallUnderPointer(state.hoverRaw, wallMap(floor().rooms));
    if (!unit) return "";
    const taken = doorUnits().has(unit.key);
    const end = unit.horizontal ? { x: unit.x + 1, y: unit.y } : { x: unit.x, y: unit.y + 1 };
    return `<line class="wall-hover${taken ? " taken" : ""}" x1="${unit.x}" y1="${unit.y}" x2="${end.x}" y2="${end.y}"/>`;
  }
  if (!hover || (state.tool !== "room" && !["new", "corner", "edge", "body"].includes(state.pointer?.kind))) return "";
  const { columns, rows } = state.plan.grid;
  return `<line class="cursor-line" x1="0" y1="${hover.y}" x2="${columns}" y2="${hover.y}"/>
    <line class="cursor-line" x1="${hover.x}" y1="0" x2="${hover.x}" y2="${rows}"/>
    <circle class="cursor-point" cx="${hover.x}" cy="${hover.y}" r="${5 * step}"/>`;
}

function hintText() {
  if (state.drawing) {
    return "Clic: siguiente esquina · clic en la primera esquina o Intro: cerrar · Retroceso: borrar la última · Esc: cancelar";
  }
  if (state.tool === "room") {
    return "Arrastra para dibujar un rectángulo · o haz clic en cada esquina para una forma en L · V: seleccionar";
  }
  if (state.tool === "door") {
    return "Pulsa sobre una pared y arrastra a lo largo de ella · en una pared que da afuera, es una entrada desde el Exterior · clic en una puerta: elegirla";
  }
  if (state.tool === "link") {
    return state.linking ? "Elige en el panel la sala de la otra planta · Esc: cancelar"
      : "Haz clic en la sala donde está la escalera o el ascensor";
  }
  if (state.selected?.kind === "camera") {
    return "Arrastra la cámara para moverla · el círculo de la punta la gira · Supr: devolverla a «sin colocar»";
  }
  if (state.selected?.kind === "room") {
    return "Arrastra una esquina o una pared para cambiar la forma · arrastra el interior para mover la sala · Supr: borrar · Esc: soltar";
  }
  return "Haz clic en una sala, cámara o puerta para elegirla · arrastra las cámaras desde «sin colocar» · Ctrl+Z: deshacer";
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
    count.textContent = plural(item.rooms.length, "sala", "salas");
    button.append(name, count);
    button.addEventListener("click", () => setFloor(item.id));
    entry.append(button);
    return entry;
  }));
  const full = state.plan.floors.length >= MAX_FLOORS;
  $("add-floor").disabled = full;
  $("add-floor").textContent = full ? `Máximo ${MAX_FLOORS} plantas` : "+ Nueva planta";
}

// ------------------------------------------------------------- inspector

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function nameField(labelText, value, maxLength, apply) {
  const label = element("label");
  const input = element("input");
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
  label.append(element("span", "bc-label", labelText), input);
  return label;
}

const note = text => element("div", "note", text);

function section(...children) {
  const node = element("div", "section");
  node.append(...children.filter(Boolean));
  return node;
}

function actionButton(text, onClick, className = "") {
  const button = element("button", className, text);
  button.addEventListener("click", onClick);
  return button;
}

function item(glyph, text, action = null) {
  const row = element("div", "item");
  row.append(element("span", "glyph", glyph), element("span", "text", text));
  if (action) row.append(actionButton(action.text, action.run));
  return row;
}

function list(...rows) {
  const node = element("div", "items");
  node.append(...rows);
  return node;
}

function doorText(target) {
  const [first, second] = target.rooms.map(id => (id === "exterior" ? null : room(id)?.name));
  if (first && second) return `Puerta entre «${first}» y «${second}»`;
  return `Entrada desde el Exterior a «${first || second}»`;
}

function kindToggle(current, onPick) {
  const toggle = element("div", "toggle");
  for (const [kind, label] of Object.entries(LINK_KINDS)) {
    toggle.append(actionButton(label, () => onPick(kind), kind === current ? "on" : ""));
  }
  return toggle;
}

function roomSection(target) {
  const doors = floor().doors.filter(entry => entry.rooms.includes(target.id));
  const links = linksOf(target.id);
  const cameras = floor().cameras.filter(entry => entry.room_id === target.id);
  const connections = [
    ...doors.map(entry => item("⇥", entry.rooms.includes("exterior") ? "Entrada desde el Exterior"
      : `Puerta con «${room(entry.rooms.find(id => id !== target.id))?.name}»`,
    { text: "Ver", run: () => select("door", entry.id) })),
    ...links.map(({ link: entry, other }) => item("⇅", `${LINK_KINDS[entry.kind]} a «${other.room.name}» · ${other.floor.name}`,
      { text: "Ver", run: () => select("link", entry.id) })),
  ];
  return section(
    nameField("Sala", target.name, 60, name => { room(target.id).name = name; }),
    note(`Tamaño: ${size(target.polygon)} casillas · ${target.polygon.length} esquinas`),
    element("div", "bc-label", "Cámaras"),
    cameras.length ? list(...cameras.map(entry => item("◉", entry.camera_id,
      { text: "Ver", run: () => select("camera", entry.camera_id) }))) : note("Ninguna. Arrastra una desde «sin colocar»."),
    element("div", "bc-label", "Conexiones"),
    connections.length ? list(...connections) : note("Ninguna. Pon una puerta (P) o únela con otra planta."),
    state.linking?.roomId === target.id ? linkPicker(target)
      : actionButton("⇅ Unir con otra planta", () => { state.linking = { roomId: target.id, kind: "stairs" }; render(); }),
    actionButton("Borrar sala · Supr", () => deleteRoom(target.id), "danger"),
  );
}

function linkPicker(target) {
  const kind = state.linking.kind;
  const targets = state.plan.floors.filter(entry => entry.id !== state.floorId && entry.rooms.length);
  const box = section(
    element("div", "bc-label", `Unir «${target.name}» con`),
    kindToggle(kind, picked => { state.linking.kind = picked; render(); }),
  );
  if (!targets.length) {
    box.append(note("Primero dibuja una sala en otra planta (+ Nueva planta)."));
  }
  for (const other of targets) {
    box.append(element("div", "note", other.name));
    box.append(list(...other.rooms.map(entry => actionButton(entry.name,
      () => createLink(target.id, entry.id, kind), "item"))));
  }
  box.append(actionButton("Cancelar · Esc", () => { state.linking = null; render(); }));
  return box;
}

function cameraSection(target) {
  const info = cameraInfo(target.camera_id);
  const where = target.room_id ? `Cuenta en «${room(target.room_id)?.name}»` : "Fuera de las salas: no cuenta en ninguna";
  return section(
    element("div", "bc-label", "Cámara"),
    element("div", "", target.camera_id),
    note([info.source ? info.source.toUpperCase() : "", info.note].filter(Boolean).join(" · ") || " "),
    item(target.room_id ? "▣" : "▲", where),
    item("➜", `Mira hacia ${target.heading_deg}°`),
    info.counts ? null : note("Esta cámara no cuenta personas: solo muestra imagen."),
    note("Arrástrala para moverla. El círculo de la punta la gira. En una pared entre dos salas, cuenta en la sala hacia la que mira."),
    actionButton("Devolver a «sin colocar» · Supr", () => removeCamera(target.camera_id), "danger"),
  );
}

function doorSection(target) {
  const [a, b] = target.segment;
  return section(
    element("div", "bc-label", target.rooms.includes("exterior") ? "Entrada del edificio" : "Puerta"),
    element("div", "", doorText(target)),
    note(`Ancho: ${plural(Math.abs(b.x - a.x) + Math.abs(b.y - a.y), "casilla", "casillas")}. Para cambiarlo, bórrala y dibújala de nuevo.`),
    note("Los tiempos de paso no se escriben: se medirán cuando las cámaras estén en su sitio."),
    actionButton("Borrar puerta · Supr", () => deleteDoor(target.id), "danger"),
  );
}

function linkSection(target) {
  const [first, second] = target.rooms.map(findRoom);
  return section(
    element("div", "bc-label", "Conexión entre plantas"),
    kindToggle(target.kind, picked => change(() => { link(target.id).kind = picked; })),
    element("div", "", `Une «${first?.room.name}» (${first?.floor.name}) con «${second?.room.name}» (${second?.floor.name})`),
    actionButton("Borrar conexión · Supr", () => deleteLink(target.id), "danger"),
  );
}

function floorSections() {
  const current = floor();
  const count = current.rooms.length;
  const remove = actionButton("Borrar planta", () => {
    if (count && !remove.classList.contains("armed")) {
      remove.classList.add("armed");
      remove.textContent = `¿Seguro? Borra ${plural(count, "sala", "salas")}`;
      setTimeout(() => { remove.classList.remove("armed"); remove.textContent = "Borrar planta"; }, 4000);
      return;
    }
    deleteFloor(current.id);
  }, "danger");
  remove.disabled = state.plan.floors.length === 1;
  return [
    section(
      nameField("Planta", current.name, 40, name => { floor().name = name; }),
      note(`${plural(count, "sala", "salas")}. Las plantas se muestran en el mapa una al lado de otra, en este orden.`),
      remove,
    ),
    section(
      nameField("Espacio de trabajo", state.plan.workspace.name, 120, name => { state.plan.workspace.name = name; }),
      note("El edificio y sus salas son datos del sitio: el plano se guarda solo en este PC."),
    ),
  ];
}

function traySection() {
  const placed = new Set(state.plan.floors.flatMap(entry => entry.cameras.map(item => item.camera_id)));
  const waiting = state.cameras.filter(entry => !placed.has(entry.camera_id));
  const tray = element("div", "tray");
  for (const entry of waiting) {
    const chip = element("span", `chip${entry.counts ? "" : " nocount"}`);
    chip.title = "Arrástrala a su sala";
    chip.append(element("span", "", entry.camera_id));
    const tag = [entry.source.toUpperCase(), entry.note.toUpperCase()].filter(Boolean).join(" · ");
    if (tag) chip.append(element("span", "tag", tag));
    trayDrag(chip, entry.camera_id);
    tray.append(chip);
  }
  return section(
    element("div", "bc-label", "Cámaras sin colocar"),
    waiting.length ? tray : note("Todas las cámaras conocidas están en el plano."),
    waiting.length ? note("Arrastra cada cámara a su sala.") : null,
  );
}

// Short warnings about the whole workspace; a click shows the room or camera.
function warnings() {
  const result = [];
  const many = state.plan.floors.length > 1;
  for (const entry of state.plan.floors) {
    const prefix = many ? `${entry.name} · ` : "";
    for (const target of entry.rooms) {
      const cameras = entry.cameras.filter(item => item.room_id === target.id);
      const counting = cameras.filter(item => cameraInfo(item.camera_id).counts);
      const connected = entry.doors.some(item => item.rooms.includes(target.id))
        || state.plan.floor_links.some(item => item.rooms.includes(target.id));
      const show = { floorId: entry.id, kind: "room", id: target.id };
      if (!cameras.length) result.push({ level: "warning", text: `${prefix}«${target.name}» sin cámara`, show });
      else if (!counting.length) result.push({ level: "warning", text: `${prefix}«${target.name}» solo tiene cámaras que no cuentan personas`, show });
      else if (counting.length > 1) result.push({ level: "info", text: `${prefix}«${target.name}» la ven ${counting.length} cámaras: se cuenta una sola vez`, show });
      if (!connected) result.push({ level: "warning", text: `${prefix}«${target.name}» aislada: sin puertas ni escaleras`, show });
    }
    for (const item of entry.cameras) {
      if (!item.room_id) {
        result.push({ level: "warning", text: `${prefix}${item.camera_id} fuera de las salas`,
          show: { floorId: entry.id, kind: "camera", id: item.camera_id } });
      }
    }
  }
  const rooms = state.plan.floors.some(entry => entry.rooms.length);
  const entrances = state.plan.floors.some(entry => entry.doors.some(item => item.rooms.includes("exterior")));
  if (rooms && !entrances) result.push({ level: "warning", text: "Ninguna entrada desde el Exterior", show: null });
  return result;
}

function warningSection() {
  const found = warnings();
  const rows = found.map(entry => {
    const button = actionButton(`${entry.level === "info" ? "●" : "▲"} ${entry.text}`, () => {
      if (!entry.show) return;
      if (entry.show.floorId !== state.floorId) setFloor(entry.show.floorId);
      state.tool = "select";
      select(entry.show.kind, entry.show.id);
    }, `item ${entry.level}`);
    return button;
  });
  return section(
    element("div", "bc-label", `Avisos · ${found.filter(entry => entry.level === "warning").length}`),
    rows.length ? list(...rows) : note("✓ Sin avisos."),
  );
}

function renderInspector() {
  const selected = state.selected;
  let first;
  if (selected?.kind === "room" && room(selected.id)) first = [roomSection(room(selected.id))];
  else if (selected?.kind === "camera" && camera(selected.id)) first = [cameraSection(camera(selected.id))];
  else if (selected?.kind === "door" && door(selected.id)) first = [doorSection(door(selected.id))];
  else if (selected?.kind === "link" && link(selected.id)) first = [linkSection(link(selected.id))];
  else first = floorSections();
  $("inspector").replaceChildren(...first, traySection(), warningSection());
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
  message.textContent = kind === "error" || kind === "warn" ? `▲ ${text}` : text;
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

// The cameras the system knows, for the tray; refreshed now and then.
async function loadCameras() {
  try {
    const response = await fetch("/api/space-map/cameras", { cache: "no-store" });
    if (!response.ok) return;
    const next = (await response.json()).cameras;
    if (JSON.stringify(next) !== JSON.stringify(state.cameras)) {
      state.cameras = next;
      if (state.plan && !state.pointer) render();
    }
  } catch {
    // Keep the last list: the tray only helps placing cameras.
  }
}

async function load() {
  try {
    const response = await fetch("/api/space-map", { cache: "no-store" });
    const answer = await response.json();
    if (!response.ok) throw new Error(answer.error || `error ${response.status}`);
    await loadCameras();
    state.plan = answer.plan;
    state.revision = answer.revision;
    state.savedJson = JSON.stringify(state.plan);
    state.floorId = state.plan.floors[0].id;
    renderGrid();
    render();
    setInterval(loadCameras, 15000);
  } catch (error) {
    const fatal = element("div", "fatal");
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
