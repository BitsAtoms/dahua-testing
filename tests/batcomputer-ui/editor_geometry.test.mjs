// Run with: node --test tests/batcomputer-ui/editor_geometry.test.mjs
import assert from "node:assert/strict";
import test from "node:test";

import {
  cameraRoom, cells, doorRooms, largestRectangle, moveCorner, moveEdge, neighbours, orthogonalStep, outlineProblem,
  reconcileFloor, rectangle, roomProblem, roomsAt, segmentUnits, simplify, translate, wallMap,
} from "../../services/batcomputer-ui/web/editor/geometry.js";

const P = (...pairs) => pairs.map(([x, y]) => ({ x, y }));
const GRID = { columns: 48, rows: 30 };
const L_SHAPE = P([0, 0], [6, 0], [6, 3], [3, 3], [3, 6], [0, 6]);

test("rectangles and L shapes are valid outlines", () => {
  assert.equal(outlineProblem(rectangle({ x: 4, y: 1 }, { x: 0, y: 3 })), null);
  assert.equal(outlineProblem(L_SHAPE), null);
});

test("invalid outlines say why", () => {
  assert.match(outlineProblem(P([0, 0], [4, 0], [2, 3])), /4 esquinas/);
  assert.match(outlineProblem(P([0, 0], [4, 0], [4, 4], [1, 3])), /diagonal/);
  assert.match(outlineProblem(P([0, 0], [2, 0], [4, 0], [4, 4], [0, 4])), /mitad/);
  assert.match(outlineProblem(P([0, 0], [2, 0], [2, 4], [4, 4], [4, 2], [0, 2])), /cruzan/);
});

test("cells of an L shape", () => {
  assert.equal(cells(rectangle({ x: 0, y: 0 }, { x: 3, y: 2 })).size, 6);
  assert.equal(cells(L_SHAPE).size, 6 * 3 + 3 * 3);
});

test("rooms may share a wall but not a cell", () => {
  const left = { name: "Izquierda", polygon: rectangle({ x: 0, y: 0 }, { x: 4, y: 4 }) };
  assert.equal(roomProblem(rectangle({ x: 4, y: 0 }, { x: 8, y: 4 }), [left], GRID), null);
  assert.match(roomProblem(rectangle({ x: 3, y: 3 }, { x: 8, y: 8 }), [left], GRID), /«Izquierda»/);
  assert.match(roomProblem(rectangle({ x: 40, y: 0 }, { x: 49, y: 4 }), [], GRID), /rejilla/);
});

test("simplify drops repeated and straight-through corners", () => {
  assert.deepEqual(simplify(P([0, 0], [2, 0], [4, 0], [4, 4], [4, 4], [0, 4])),
    P([0, 0], [4, 0], [4, 4], [0, 4]));
});

test("moving a corner keeps the walls straight", () => {
  const square = rectangle({ x: 0, y: 0 }, { x: 4, y: 4 });
  assert.deepEqual(moveCorner(square, 2, { x: 6, y: 5 }), P([0, 0], [6, 0], [6, 5], [0, 5]));
  const moved = moveCorner(L_SHAPE, 3, { x: 4, y: 2 });
  assert.equal(outlineProblem(moved), null);
  assert.deepEqual(moved, P([0, 0], [6, 0], [6, 2], [4, 2], [4, 6], [0, 6]));
});

test("moving a wall moves both of its corners", () => {
  const square = rectangle({ x: 0, y: 0 }, { x: 4, y: 4 });
  assert.deepEqual(moveEdge(square, 1, 2), P([0, 0], [6, 0], [6, 4], [0, 4]));
  assert.deepEqual(moveEdge(square, 2, -1), P([0, 0], [4, 0], [4, 3], [0, 3]));
  assert.deepEqual(translate(square, 1, 2)[0], { x: 1, y: 2 });
});

test("drawing steps go straight across or down", () => {
  assert.deepEqual(orthogonalStep({ x: 2, y: 2 }, { x: 9, y: 4 }), { x: 9, y: 2 });
  assert.deepEqual(orthogonalStep({ x: 2, y: 2 }, { x: 3, y: 8 }), { x: 2, y: 8 });
});

test("the name goes in the largest rectangle inside the room", () => {
  assert.deepEqual(largestRectangle(L_SHAPE), { x0: 0, y0: 0, x1: 6, y1: 3 });
  const wide = P([14, 2], [30, 2], [30, 8], [22, 8], [22, 12], [14, 12]);
  assert.deepEqual(largestRectangle(wide), { x0: 14, y0: 2, x1: 30, y1: 8 });
  assert.deepEqual(largestRectangle(rectangle({ x: 1, y: 1 }, { x: 3, y: 5 })), { x0: 1, y0: 1, x1: 3, y1: 5 });
});

const LEFT = { id: "left", name: "Izquierda", polygon: rectangle({ x: 0, y: 0 }, { x: 4, y: 4 }) };
const RIGHT = { id: "right", name: "Derecha", polygon: rectangle({ x: 4, y: 0 }, { x: 8, y: 4 }) };

test("doors join the rooms of a shared wall, or a room and the exterior", () => {
  const walls = wallMap([LEFT, RIGHT]);
  assert.deepEqual(doorRooms(segmentUnits({ x: 4, y: 1 }, { x: 4, y: 3 }), walls), ["left", "right"]);
  assert.deepEqual(doorRooms(segmentUnits({ x: 1, y: 4 }, { x: 3, y: 4 }), walls), ["left", "exterior"]);
  assert.equal(doorRooms(segmentUnits({ x: 3, y: 4 }, { x: 5, y: 4 }), walls), null);
  assert.equal(doorRooms(segmentUnits({ x: 2, y: 2 }, { x: 3, y: 2 }), walls), null);
});

test("a camera on a shared wall counts in the room it looks into", () => {
  assert.deepEqual(roomsAt({ x: 4, y: 2 }, [LEFT, RIGHT]).map(room => room.id), ["left", "right"]);
  assert.equal(cameraRoom({ x: 4, y: 2 }, 90, [LEFT, RIGHT]), "right");
  assert.equal(cameraRoom({ x: 4, y: 2 }, 270, [LEFT, RIGHT]), "left");
  assert.equal(cameraRoom({ x: 1.5, y: 1.5 }, 0, [LEFT, RIGHT]), "left");
  assert.equal(cameraRoom({ x: 20, y: 20 }, 0, [LEFT, RIGHT]), null);
});

test("reconcile keeps valid doors, turns a covered entrance into a door and drops the rest", () => {
  const floor = {
    rooms: [structuredClone(LEFT)],
    cameras: [{ camera_id: "c", room_id: "left", position: { x: 6, y: 2 }, heading_deg: 0 }],
    doors: [{ id: "out", rooms: ["left", "exterior"], segment: [{ x: 4, y: 1 }, { x: 4, y: 2 }] },
      { id: "south", rooms: ["left", "exterior"], segment: [{ x: 1, y: 4 }, { x: 2, y: 4 }] }],
  };
  floor.rooms.push(structuredClone(RIGHT));
  assert.equal(reconcileFloor(floor), 0);
  assert.deepEqual(floor.doors.map(door => door.rooms), [["left", "right"], ["left", "exterior"]]);
  assert.equal(floor.cameras[0].room_id, "right");
  floor.rooms[1].polygon = rectangle({ x: 4, y: 2 }, { x: 8, y: 6 });
  assert.equal(reconcileFloor(floor), 1);
  assert.deepEqual(floor.doors.map(door => door.id), ["south"]);
});

test("the additional view keeps only other rooms that still exist", () => {
  const floor = {
    rooms: [structuredClone(LEFT), structuredClone(RIGHT)],
    cameras: [{ camera_id: "c", room_id: "left", position: { x: 4, y: 2 }, heading_deg: 270, also_sees: ["right"] }],
    doors: [],
  };
  assert.deepEqual([...neighbours("left", floor.rooms)], ["right"]);
  reconcileFloor(floor);
  assert.deepEqual(floor.cameras[0].also_sees, ["right"]);
  floor.cameras[0].heading_deg = 90;
  floor.cameras[0].room_id = cameraRoom(floor.cameras[0].position, 90, floor.rooms);
  reconcileFloor(floor);
  assert.equal(floor.cameras[0].room_id, "right");
  assert.equal("also_sees" in floor.cameras[0], false);
});
