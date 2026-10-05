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
