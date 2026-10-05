// Exact grid geometry of the building plan, for the space editor.
//
// Mirrors the room rules of batcomputer_ui/spaces.py: corners on grid
// points, walls horizontal or vertical, corners alternating between the two,
// no crossing walls, and rooms never sharing a cell. The server stays the
// authority on save; these functions give the owner the same answer while
// drawing. Points are {x, y} in grid steps, y growing downwards.

export function edges(points) {
  return points.map((point, index) => [point, points[(index + 1) % points.length]]);
}

const horizontal = ([a, b]) => a.y === b.y;
const same = (a, b) => a.x === b.x && a.y === b.y;

function touch([a, b], [c, d]) {
  return Math.min(a.x, b.x) <= Math.max(c.x, d.x) && Math.min(c.x, d.x) <= Math.max(a.x, b.x)
    && Math.min(a.y, b.y) <= Math.max(c.y, d.y) && Math.min(c.y, d.y) <= Math.max(a.y, b.y);
}

// The reason an outline is not a valid room, or null.
export function outlineProblem(points) {
  if (points.length < 4) return "una sala necesita al menos 4 esquinas";
  const sides = edges(points);
  for (const [a, b] of sides) {
    if (same(a, b)) return "hay una esquina repetida";
    if (a.x !== b.x && a.y !== b.y) return "hay una pared en diagonal";
  }
  const count = sides.length;
  for (let index = 0; index < count; index += 1) {
    if (horizontal(sides[index]) === horizontal(sides[(index + 1) % count])) {
      return "hay una esquina en mitad de una pared";
    }
  }
  for (let first = 0; first < count; first += 1) {
    for (let second = first + 2; second < count; second += 1) {
      if (first === 0 && second === count - 1) continue;
      if (touch(sides[first], sides[second])) return "las paredes se cruzan";
    }
  }
  return null;
}

// Removes repeated corners and corners in the middle of a straight wall, so
// clicking along a wall or dragging a corner onto its neighbour still ends
// in a clean outline.
export function simplify(points) {
  let result = points.map(({ x, y }) => ({ x, y }));
  let changed = true;
  while (changed && result.length > 2) {
    changed = false;
    for (let index = 0; index < result.length; index += 1) {
      const previous = result[(index - 1 + result.length) % result.length];
      const point = result[index];
      const next = result[(index + 1) % result.length];
      const repeated = same(previous, point);
      const straight = (previous.x === point.x && point.x === next.x)
        || (previous.y === point.y && point.y === next.y);
      if (repeated || straight) {
        result.splice(index, 1);
        changed = true;
        break;
      }
    }
  }
  return result;
}

// Grid cells whose centre lies inside the outline, as numeric keys.
export function cells(points) {
  const verticals = edges(points)
    .filter(([a, b]) => a.x === b.x)
    .map(([a, b]) => [a.x, Math.min(a.y, b.y), Math.max(a.y, b.y)]);
  const ys = points.map(point => point.y);
  const result = new Set();
  for (let y = Math.min(...ys); y < Math.max(...ys); y += 1) {
    const crossings = verticals.filter(([, top, bottom]) => top <= y && y < bottom)
      .map(([x]) => x).sort((a, b) => a - b);
    for (let index = 0; index + 1 < crossings.length; index += 2) {
      for (let x = crossings[index]; x < crossings[index + 1]; x += 1) result.add(cellKey(x, y));
    }
  }
  return result;
}

export const cellKey = (x, y) => y * 1000 + x;

export function insideGrid(points, grid) {
  return points.every(({ x, y }) => x >= 0 && y >= 0 && x <= grid.columns && y <= grid.rows);
}

// The reason a room outline cannot be placed among `others`, or null.
export function roomProblem(points, others, grid) {
  if (!insideGrid(points, grid)) return "se sale de la rejilla";
  const outline = outlineProblem(points);
  if (outline) return outline;
  const mine = cells(points);
  for (const other of others) {
    const theirs = cells(other.polygon);
    for (const key of mine) {
      if (theirs.has(key)) return `se solapa con «${other.name}»`;
    }
  }
  return null;
}

export function rectangle(a, b) {
  const [x0, x1] = [Math.min(a.x, b.x), Math.max(a.x, b.x)];
  const [y0, y1] = [Math.min(a.y, b.y), Math.max(a.y, b.y)];
  return [{ x: x0, y: y0 }, { x: x1, y: y0 }, { x: x1, y: y1 }, { x: x0, y: y1 }];
}

// Moves one corner; its two neighbours follow along their walls, so every
// wall stays horizontal or vertical (a rectangle resizes from that corner).
export function moveCorner(points, index, target) {
  const count = points.length;
  const result = points.map(({ x, y }) => ({ x, y }));
  result[index] = { x: target.x, y: target.y };
  for (const neighbour of [(index - 1 + count) % count, (index + 1) % count]) {
    if (points[neighbour].y === points[index].y) result[neighbour].y = target.y;
    else result[neighbour].x = target.x;
  }
  return result;
}

// Moves the wall from corner `index` to the next one across itself.
export function moveEdge(points, index, offset) {
  const next = (index + 1) % points.length;
  const result = points.map(({ x, y }) => ({ x, y }));
  const axis = points[index].y === points[next].y ? "y" : "x";
  result[index][axis] += offset;
  result[next][axis] += offset;
  return result;
}

export function translate(points, dx, dy) {
  return points.map(({ x, y }) => ({ x: x + dx, y: y + dy }));
}

// The next corner while drawing: straight across or straight down from the
// last one, whichever is closer to the pointer.
export function orthogonalStep(last, pointer) {
  return Math.abs(pointer.x - last.x) >= Math.abs(pointer.y - last.y)
    ? { x: pointer.x, y: last.y }
    : { x: last.x, y: pointer.y };
}

export function bounds(points) {
  const xs = points.map(point => point.x);
  const ys = points.map(point => point.y);
  return { x0: Math.min(...xs), y0: Math.min(...ys), x1: Math.max(...xs), y1: Math.max(...ys) };
}

// The largest rectangle of whole cells inside the room, where its name is
// written: an L-shaped room gets its name centred in its wider part.
// {x0, y0, x1, y1} in grid steps, or null for an empty outline.
export function largestRectangle(points) {
  const room = cells(points);
  if (!room.size) return null;
  const box = bounds(points);
  const width = box.x1 - box.x0;
  const heights = new Array(width).fill(0);
  let best = null;
  for (let y = box.y0; y < box.y1; y += 1) {
    for (let column = 0; column < width; column += 1) {
      heights[column] = room.has(cellKey(box.x0 + column, y)) ? heights[column] + 1 : 0;
    }
    const stack = [];
    for (let column = 0; column <= width; column += 1) {
      const height = column < width ? heights[column] : 0;
      while (stack.length && heights[stack[stack.length - 1]] >= height) {
        const top = heights[stack.pop()];
        const left = stack.length ? stack[stack.length - 1] + 1 : 0;
        const area = top * (column - left);
        if (top && (!best || area > best.area)) {
          best = { area, x0: box.x0 + left, x1: box.x0 + column, y0: y - top + 1, y1: y + 1 };
        }
      }
      stack.push(column);
    }
  }
  return { x0: best.x0, y0: best.y0, x1: best.x1, y1: best.y1 };
}

// ------------------------------------------------------------ walls, doors
//
// A wall unit is one grid step of wall: "x,y,h" runs from (x,y) to (x+1,y),
// "x,y,v" from (x,y) to (x,y+1). Doors are made of whole units, like the
// server checks them.

export const unitKey = (x, y, horizontal) => `${x},${y},${horizontal ? "h" : "v"}`;

export function segmentUnits(a, b) {
  const keys = [];
  if (a.y === b.y) {
    for (let x = Math.min(a.x, b.x); x < Math.max(a.x, b.x); x += 1) keys.push(unitKey(x, a.y, true));
  } else {
    for (let y = Math.min(a.y, b.y); y < Math.max(a.y, b.y); y += 1) keys.push(unitKey(a.x, y, false));
  }
  return keys;
}

export function boundaryUnits(points) {
  return new Set(edges(points).flatMap(([a, b]) => segmentUnits(a, b)));
}

// Every wall unit of a floor with the rooms it borders (one for an outer
// wall, two for a shared one).
export function wallMap(rooms) {
  const walls = new Map();
  for (const room of rooms) {
    for (const key of boundaryUnits(room.polygon)) {
      if (!walls.has(key)) walls.set(key, []);
      walls.get(key).push(room.id);
    }
  }
  return walls;
}

// Which rooms a door on these wall units joins, by the server's rules: the
// wall two rooms share, or an outer wall of one room towards the exterior.
// Null when the units are not one kind of wall.
export function doorRooms(keys, walls) {
  if (!keys.length) return null;
  const first = walls.get(keys[0]);
  if (!first) return null;
  const pair = [...first].sort();
  for (const key of keys) {
    const rooms = walls.get(key);
    if (!rooms || rooms.length !== pair.length || [...rooms].sort().some((id, i) => id !== pair[i])) return null;
  }
  return pair.length === 2 ? pair : [pair[0], "exterior"];
}

// Cells whose closed square holds the point: inside a room or on its wall.
export function touchingCells(point) {
  const around = value => (Number.isInteger(value) ? [value - 1, value] : [Math.floor(value)]);
  return around(point.x).flatMap(x => around(point.y).map(y => cellKey(x, y)));
}

export function roomsAt(point, rooms) {
  const keys = touchingCells(point);
  return rooms.filter(room => {
    const inside = cells(room.polygon);
    return keys.some(key => inside.has(key));
  });
}

export function headingVector(degrees) {
  const radians = (degrees * Math.PI) / 180;
  return { x: Math.sin(radians), y: -Math.cos(radians) };
}

// The room a camera counts for: the one it stands in, or, on a wall two
// rooms share, the one it looks into. Null outside every room.
export function cameraRoom(position, heading, rooms) {
  const candidates = roomsAt(position, rooms);
  if (candidates.length < 2) return candidates[0]?.id ?? null;
  const ahead = headingVector(heading);
  const probe = { x: position.x + ahead.x * 0.3, y: position.y + ahead.y * 0.3 };
  const key = cellKey(Math.floor(probe.x), Math.floor(probe.y));
  return (candidates.find(room => cells(room.polygon).has(key)) ?? candidates[0]).id;
}

// Keeps the doors and cameras of a floor consistent with its rooms after a
// change: a door stays while it still lies on its wall; an entrance becomes a
// door between rooms when a room is drawn against it; other doors are
// removed. A camera that left its room moves to the room under it, or to
// none. Returns how many doors were removed.
export function reconcileFloor(floor) {
  const walls = wallMap(floor.rooms);
  const ids = new Set(floor.rooms.map(room => room.id));
  let removed = 0;
  floor.doors = floor.doors.filter(door => {
    const keys = segmentUnits(...door.segment);
    const now = doorRooms(keys, walls);
    const before = [...door.rooms].sort();
    const same = now && now.length === 2 && [...now].sort().every((id, i) => id === before[i]);
    if (same) return true;
    const entrance = door.rooms.includes("exterior") ? door.rooms.find(id => id !== "exterior") : null;
    if (entrance && now && !now.includes("exterior") && now.includes(entrance)) {
      door.rooms = [entrance, now.find(id => id !== entrance)];
      return true;
    }
    removed += 1;
    return false;
  });
  for (const camera of floor.cameras) {
    const inRoom = camera.room_id && ids.has(camera.room_id)
      && roomsAt(camera.position, floor.rooms).some(room => room.id === camera.room_id);
    if (!inRoom) camera.room_id = cameraRoom(camera.position, camera.heading_deg, floor.rooms);
  }
  return removed;
}
