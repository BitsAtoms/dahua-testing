"""Strict ``space_map.v2`` contract: the building plan drawn in the editor.

One workspace (a building or office) has up to three floors drawn on a shared
grid. Room corners sit on grid points and walls run horizontally or
vertically, so every room is an exact union of grid cells: overlaps, shared
walls and doors are checked with integers, never with tolerances.

* A **door** joins two rooms of one floor through their shared wall, or a room
  and the exterior through an outer wall (a building entrance).
* A **floor link** (stairs or lift) joins rooms of two floors and has no
  geometry.
* A **camera** belongs to one room of its floor, or to none yet. Positions use
  half grid steps so that "inside or on the wall" stays exact. Its optional
  ``also_sees`` lists other rooms of the floor that appear in part of its
  image ("vista adicional"). That only declares overlapping views: until the
  image zones of each room are drawn (phase 7), everyone the camera sees
  counts in its own room.

Travel times are not part of the plan: the tracking engine uses a general
window until the times of each door are measured (phase 7).

The plan names real rooms, so the working file is site data and lives under
``runtime/``. Messages are in Spanish because the editor shows them to the
owner.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import threading
from typing import Any


SCHEMA_VERSION = "space_map.v2"
EXTERIOR = "exterior"
MAX_FLOORS = 3
GRID_MIN, GRID_MAX = 4, 200
LINK_KINDS = {"stairs", "elevator"}
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

Point = tuple[int, int]
# One grid step of wall, endpoints in ascending order.
Unit = tuple[Point, Point]


class SpaceMapError(ValueError):
    """The plan is inconsistent; the message is meant for the owner."""


class SpaceMapConflict(RuntimeError):
    """The saved plan changed after the editor loaded it."""


def default_space_map() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "workspace": {"id": "main", "name": "Espacio de trabajo"},
        "grid": {"columns": 48, "rows": 30},
        "floors": [
            {"id": "floor_0", "name": "Planta 0", "rooms": [], "cameras": [], "doors": []}
        ],
        "floor_links": [],
    }


def validate_space_map(document: Any) -> None:
    root = _exact(document, "el plano",
                  {"schema_version", "workspace", "grid", "floors", "floor_links"})
    if root["schema_version"] != SCHEMA_VERSION:
        raise SpaceMapError(f"el plano debe tener la versión {SCHEMA_VERSION}")
    workspace = _exact(root["workspace"], "el espacio de trabajo", {"id", "name"})
    _identifier(workspace["id"], "el identificador del espacio de trabajo")
    _text(workspace["name"], "el nombre del espacio de trabajo", 120)
    grid = _exact(root["grid"], "la rejilla", {"columns", "rows"})
    columns = _integer(grid["columns"], "las columnas de la rejilla", GRID_MIN, GRID_MAX)
    rows = _integer(grid["rows"], "las filas de la rejilla", GRID_MIN, GRID_MAX)

    floors = _list(root["floors"], "las plantas")
    if not 1 <= len(floors) <= MAX_FLOORS:
        raise SpaceMapError(f"un espacio de trabajo tiene entre 1 y {MAX_FLOORS} plantas")
    ids = _Ids()
    room_floor: dict[str, str] = {}
    room_names: dict[str, str] = {}
    for value in floors:
        floor = _exact(value, "una planta", {"id", "name", "rooms", "cameras", "doors"})
        floor_id = ids.add("planta", floor["id"])
        floor_name = _text(floor["name"], "el nombre de una planta", 40)
        rooms = _check_rooms(floor, floor_name, columns, rows, ids)
        for room_id, room in rooms.items():
            room_floor[room_id] = floor_id
            room_names[room_id] = room.name
        _check_doors(floor, floor_name, rooms, columns, rows, ids)
        _check_cameras(floor, floor_name, rooms, columns, rows, ids)

    for value in _list(root["floor_links"], "las conexiones entre plantas"):
        link = _exact(value, "una conexión entre plantas", {"id", "kind", "rooms"})
        ids.add("conexión entre plantas", link["id"])
        if link["kind"] not in LINK_KINDS:
            raise SpaceMapError("una conexión entre plantas es una escalera o un ascensor")
        pair = _list(link["rooms"], "las salas de una conexión entre plantas")
        if len(pair) != 2 or any(not isinstance(room, str) or room not in room_floor
                                 for room in pair):
            raise SpaceMapError("una conexión entre plantas une dos salas que existen")
        if room_floor[pair[0]] == room_floor[pair[1]]:
            raise SpaceMapError(
                f"«{room_names[pair[0]]}» y «{room_names[pair[1]]}» están en la misma "
                "planta: se unen con una puerta"
            )


class _Room:
    def __init__(self, name: str, corners: list[Point]) -> None:
        self.name = name
        self.cells = _cells(corners)
        self.boundary = {unit for edge in _edges(corners) for unit in _units(*edge)}


class _Ids:
    """Identifiers are unique across the whole workspace, per kind."""

    def __init__(self) -> None:
        self.seen: dict[str, set[str]] = {}

    def add(self, kind: str, value: Any) -> str:
        identifier = _identifier(value, f"el identificador de una {kind}")
        seen = self.seen.setdefault(kind, set())
        if identifier in seen:
            raise SpaceMapError(f"hay dos con el identificador {identifier} ({kind})")
        seen.add(identifier)
        return identifier


def _check_rooms(floor: dict[str, Any], floor_name: str, columns: int, rows: int,
                 ids: _Ids) -> dict[str, _Room]:
    rooms: dict[str, _Room] = {}
    for value in _list(floor["rooms"], f"las salas de {floor_name}"):
        room = _exact(value, "una sala", {"id", "name", "polygon"})
        room_id = ids.add("sala", room["id"])
        if room_id == EXTERIOR:
            raise SpaceMapError(f"«{EXTERIOR}» está reservado para el Exterior")
        name = _text(room["name"], "el nombre de una sala", 60)
        polygon = _list(room["polygon"], f"las esquinas de «{name}»")
        corners = [_grid_point(point, f"una esquina de «{name}»", columns, rows)
                   for point in polygon]
        _check_outline(corners, name)
        rooms[room_id] = _Room(name, corners)
    placed = list(rooms.values())
    for index, room in enumerate(placed):
        for other in placed[index + 1:]:
            if room.cells & other.cells:
                raise SpaceMapError(f"las salas «{room.name}» y «{other.name}» se solapan")
    return rooms


def _check_outline(corners: list[Point], name: str) -> None:
    if len(corners) < 4:
        raise SpaceMapError(f"la sala «{name}» necesita al menos 4 esquinas")
    edges = _edges(corners)
    for start, end in edges:
        if start == end:
            raise SpaceMapError(f"la sala «{name}» tiene una esquina repetida")
        if start[0] != end[0] and start[1] != end[1]:
            raise SpaceMapError(f"la sala «{name}» tiene una pared en diagonal")
    count = len(edges)
    for index in range(count):
        if _horizontal(edges[index]) == _horizontal(edges[(index + 1) % count]):
            raise SpaceMapError(f"la sala «{name}» tiene una esquina en mitad de una pared")
    for first in range(count):
        for second in range(first + 2, count):
            if first == 0 and second == count - 1:
                continue
            if _touch(edges[first], edges[second]):
                raise SpaceMapError(f"las paredes de la sala «{name}» se cruzan")


def _check_doors(floor: dict[str, Any], floor_name: str, rooms: dict[str, _Room],
                 columns: int, rows: int, ids: _Ids) -> None:
    used: set[Unit] = set()
    for value in _list(floor["doors"], f"las puertas de {floor_name}"):
        door = _exact(value, "una puerta", {"id", "rooms", "segment"})
        ids.add("puerta", door["id"])
        pair = _list(door["rooms"], "las salas de una puerta")
        if (len(pair) != 2 or pair[0] == pair[1]
                or any(not isinstance(room, str) or (room != EXTERIOR and room not in rooms)
                       for room in pair)):
            raise SpaceMapError(
                f"una puerta de {floor_name} une dos salas de esa planta, o una sala y el Exterior"
            )
        segment = _list(door["segment"], "los extremos de una puerta")
        if len(segment) != 2:
            raise SpaceMapError("una puerta va de un punto de la rejilla a otro")
        start, end = (_grid_point(point, "un extremo de una puerta", columns, rows)
                      for point in segment)
        if start == end or (start[0] != end[0] and start[1] != end[1]):
            raise SpaceMapError("una puerta ocupa un tramo recto de pared")
        units = set(_units(start, end))
        if units & used:
            raise SpaceMapError(f"dos puertas de {floor_name} se solapan")
        used |= units
        inside = [rooms[room] for room in pair if room != EXTERIOR]
        if len(inside) == 2:
            first, second = inside
            if not units <= first.boundary & second.boundary:
                raise SpaceMapError(
                    f"la puerta entre «{first.name}» y «{second.name}» tiene que estar en "
                    "la pared que comparten"
                )
        else:
            (room,) = inside
            others = set().union(*(other.boundary for other in rooms.values() if other is not room))
            if not units <= room.boundary or units & others:
                raise SpaceMapError(
                    f"la entrada de «{room.name}» tiene que estar en una pared que da al Exterior"
                )


def _check_cameras(floor: dict[str, Any], floor_name: str, rooms: dict[str, _Room],
                   columns: int, rows: int, ids: _Ids) -> None:
    for value in _list(floor["cameras"], f"las cámaras de {floor_name}"):
        camera = _fields(value, "una cámara", {"camera_id", "room_id", "position", "heading_deg"},
                         {"also_sees"})
        camera_id = ids.add("cámara", camera["camera_id"])
        position = _exact(camera["position"], f"la posición de {camera_id}", {"x", "y"})
        x = _half_step(position["x"], f"la posición de {camera_id}", columns)
        y = _half_step(position["y"], f"la posición de {camera_id}", rows)
        heading = _number(camera["heading_deg"], f"la dirección de {camera_id}")
        if not 0 <= heading < 360:
            raise SpaceMapError(f"la dirección de {camera_id} va de 0 a 359 grados")
        room_id = camera["room_id"]
        if room_id is not None:
            if not isinstance(room_id, str) or room_id not in rooms:
                raise SpaceMapError(f"{camera_id} está en una sala que no existe en {floor_name}")
            if not _touching_cells(x, y) & rooms[room_id].cells:
                raise SpaceMapError(f"{camera_id} está fuera de «{rooms[room_id].name}»")
        extra = _list(camera.get("also_sees", []), f"la vista adicional de {camera_id}")
        if (not extra and "also_sees" in camera) or len(set(map(str, extra))) != len(extra):
            raise SpaceMapError(f"la vista adicional de {camera_id} repite salas o está vacía")
        for seen in extra:
            if not isinstance(seen, str) or seen not in rooms or seen == room_id:
                raise SpaceMapError(
                    f"la vista adicional de {camera_id} solo admite otras salas de {floor_name}"
                )


def _edges(corners: list[Point]) -> list[tuple[Point, Point]]:
    return [(corners[index], corners[(index + 1) % len(corners)])
            for index in range(len(corners))]


def _horizontal(edge: tuple[Point, Point]) -> bool:
    return edge[0][1] == edge[1][1]


def _touch(first: tuple[Point, Point], second: tuple[Point, Point]) -> bool:
    """Whether two axis-aligned segments share at least one point."""
    (ax0, ax1), (ay0, ay1) = _span(first)
    (bx0, bx1), (by0, by1) = _span(second)
    return ax0 <= bx1 and bx0 <= ax1 and ay0 <= by1 and by0 <= ay1


def _span(edge: tuple[Point, Point]) -> tuple[tuple[int, int], tuple[int, int]]:
    (x0, y0), (x1, y1) = edge
    return (min(x0, x1), max(x0, x1)), (min(y0, y1), max(y0, y1))


def _units(start: Point, end: Point) -> list[Unit]:
    (x0, x1), (y0, y1) = _span((start, end))
    if x0 == x1:
        return [((x0, y), (x0, y + 1)) for y in range(y0, y1)]
    return [((x, y0), (x + 1, y0)) for x in range(x0, x1)]


def _cells(corners: list[Point]) -> set[Point]:
    """Grid cells whose centre lies inside the outline (scanline, exact)."""
    verticals = [(start[0], *sorted((start[1], end[1])))
                 for start, end in _edges(corners) if start[0] == end[0]]
    cells: set[Point] = set()
    for y in range(min(c[1] for c in corners), max(c[1] for c in corners)):
        crossings = sorted(x for x, top, bottom in verticals if top <= y < bottom)
        for left, right in zip(crossings[0::2], crossings[1::2]):
            cells.update((x, y) for x in range(left, right))
    return cells


def _touching_cells(x: float, y: float) -> set[Point]:
    """Cells whose closed square contains the point: inside or on a wall."""
    def around(value: float) -> list[int]:
        return [int(value) - 1, int(value)] if value.is_integer() else [math.floor(value)]
    return {(cx, cy) for cx in around(x) for cy in around(y)}


def _exact(value: Any, name: str, fields: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SpaceMapError(f"{name} no tiene el formato esperado")
    if set(value) != fields:
        raise SpaceMapError(f"{name} debe tener exactamente: {', '.join(sorted(fields))}")
    return value


def _fields(value: Any, name: str, required: set[str], optional: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SpaceMapError(f"{name} no tiene el formato esperado")
    if not required <= set(value) <= required | optional:
        raise SpaceMapError(f"{name} debe tener: {', '.join(sorted(required))}"
                            f" (y si acaso: {', '.join(sorted(optional))})")
    return value


def _list(value: Any, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise SpaceMapError(f"{name}: tienen que ser una lista")
    return value


def _identifier(value: Any, name: str) -> str:
    if not isinstance(value, str) or not ID_RE.fullmatch(value):
        raise SpaceMapError(f"{name} solo admite letras, números, guion y guion bajo")
    return value


def _text(value: Any, name: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise SpaceMapError(f"{name} no puede estar vacío ni pasar de {maximum} caracteres")
    return value.strip()


def _number(value: Any, name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        raise SpaceMapError(f"{name}: tiene que ser un número")
    return float(value)


def _integer(value: Any, name: str, minimum: int, maximum: int) -> int:
    number = _number(value, name)
    if not number.is_integer() or not minimum <= number <= maximum:
        raise SpaceMapError(f"{name}: tiene que ser un entero entre {minimum} y {maximum}")
    return int(number)


def _grid_point(value: Any, name: str, columns: int, rows: int) -> Point:
    point = _exact(value, name, {"x", "y"})
    x, y = _number(point["x"], name), _number(point["y"], name)
    if not (x.is_integer() and y.is_integer()):
        raise SpaceMapError(f"{name} tiene que caer en un punto de la rejilla")
    if not (0 <= x <= columns and 0 <= y <= rows):
        raise SpaceMapError(f"{name} queda fuera de la rejilla")
    return int(x), int(y)


def _half_step(value: Any, name: str, limit: int) -> float:
    number = _number(value, name)
    if not (number * 2).is_integer() or not 0 <= number <= limit:
        raise SpaceMapError(f"{name} va en pasos de media casilla dentro de la rejilla")
    return number


class SpaceMapStore:
    """Atomic save with a copy of each replaced version and stale-save checks.

    The revision is the SHA-256 of the saved bytes. A save must name the
    revision it was edited from, so two editor windows cannot silently
    overwrite each other.
    """

    def __init__(self, path: Path, backups: Path | None = None, keep: int = 50) -> None:
        self.path = path
        self.backups = backups if backups is not None else path.parent / "backups"
        self.keep = keep
        self._lock = threading.Lock()

    def load(self) -> tuple[dict[str, Any], str | None]:
        with self._lock:
            if not self.path.exists():
                return default_space_map(), None
            wire = self.path.read_bytes()
        document = json.loads(wire.decode("utf-8-sig"))
        validate_space_map(document)
        return document, _revision(wire)

    def save(self, document: Any, revision: str | None) -> str:
        validate_space_map(document)
        wire = (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        with self._lock:
            current = self.path.read_bytes() if self.path.exists() else None
            if (None if current is None else _revision(current)) != revision:
                raise SpaceMapConflict(
                    "el plano cambió desde que lo abriste: recarga la página antes de guardar"
                )
            if current == wire:
                return revision
            if current is not None:
                self._backup(current)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(self.path.suffix + ".tmp")
            temporary.write_bytes(wire)
            temporary.replace(self.path)
        return _revision(wire)

    def _backup(self, wire: bytes) -> None:
        self.backups.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        target = self.backups / f"{self.path.stem}.{stamp}.json"
        suffix = 0
        while target.exists():
            suffix += 1
            target = self.backups / f"{self.path.stem}.{stamp}-{suffix}.json"
        target.write_bytes(wire)
        copies = sorted(self.backups.glob(f"{self.path.stem}.*.json"))
        for old in copies[:-self.keep]:
            old.unlink()


def _revision(wire: bytes) -> str:
    return hashlib.sha256(wire).hexdigest()
