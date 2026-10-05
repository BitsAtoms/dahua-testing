from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "services" / "batcomputer-ui"))

from batcomputer_ui.spaces import (  # noqa: E402
    SpaceMapConflict,
    SpaceMapError,
    SpaceMapStore,
    default_space_map,
    validate_space_map,
)


EXAMPLE = REPOSITORY_ROOT / "services" / "batcomputer-ui" / "space-map.example.json"


def corners(*pairs: tuple[int, int]) -> list[dict[str, int]]:
    return [{"x": x, "y": y} for x, y in pairs]


def rectangle(x0: int, y0: int, x1: int, y1: int) -> list[dict[str, int]]:
    return corners((x0, y0), (x1, y0), (x1, y1), (x0, y1))


def plan_with(rooms: list[dict], doors: list[dict] | None = None,
              cameras: list[dict] | None = None) -> dict:
    plan = default_space_map()
    floor = plan["floors"][0]
    floor["rooms"] = rooms
    floor["doors"] = doors or []
    floor["cameras"] = cameras or []
    return plan


def room(room_id: str, polygon: list[dict[str, int]]) -> dict:
    return {"id": room_id, "name": room_id.title(), "polygon": polygon}


def door(door_id: str, rooms: list[str], start: tuple[int, int], end: tuple[int, int]) -> dict:
    return {"id": door_id, "rooms": rooms, "segment": corners(start, end)}


def camera(camera_id: str, room_id: str | None, x: float, y: float) -> dict:
    return {"camera_id": camera_id, "room_id": room_id,
            "position": {"x": x, "y": y}, "heading_deg": 90}


TWO_ROOMS = [room("a", rectangle(0, 0, 4, 4)), room("b", rectangle(4, 0, 8, 4))]


class ValidationTests(unittest.TestCase):
    def assertRejected(self, plan: dict, fragment: str) -> None:
        with self.assertRaises(SpaceMapError) as caught:
            validate_space_map(plan)
        self.assertIn(fragment, str(caught.exception))

    def test_example_and_default_are_valid(self) -> None:
        validate_space_map(json.loads(EXAMPLE.read_text(encoding="utf-8")))
        validate_space_map(default_space_map())

    def test_l_shaped_room_and_shared_wall_door(self) -> None:
        l_shape = corners((4, 0), (10, 0), (10, 3), (7, 3), (7, 6), (4, 6))
        validate_space_map(plan_with(
            [room("a", rectangle(0, 0, 4, 4)), room("b", l_shape)],
            [door("d", ["a", "b"], (4, 1), (4, 3))],
        ))

    def test_walls_are_horizontal_or_vertical(self) -> None:
        self.assertRejected(plan_with([room("a", corners((0, 0), (4, 0), (2, 3)))]),
                            "4 esquinas")
        self.assertRejected(plan_with([room("a", corners((0, 0), (4, 0), (4, 4), (1, 3)))]),
                            "diagonal")
        self.assertRejected(
            plan_with([room("a", corners((0, 0), (2, 0), (4, 0), (4, 4), (0, 4)))]),
            "mitad de una pared",
        )

    def test_crossing_walls_are_rejected(self) -> None:
        # Two squares joined at one corner by a pinched outline.
        bow_tie = corners((0, 0), (2, 0), (2, 4), (4, 4), (4, 2), (0, 2))
        self.assertRejected(plan_with([room("a", bow_tie)]), "se cruzan")

    def test_corners_stay_on_the_grid(self) -> None:
        self.assertRejected(plan_with([room("a", rectangle(0, 0, 49, 4))]), "fuera de la rejilla")
        half = [{"x": 0.5, "y": 0}, {"x": 4, "y": 0}, {"x": 4, "y": 4}, {"x": 0.5, "y": 4}]
        self.assertRejected(plan_with([room("a", half)]), "punto de la rejilla")

    def test_rooms_may_touch_but_not_overlap(self) -> None:
        validate_space_map(plan_with(copy.deepcopy(TWO_ROOMS)))
        self.assertRejected(
            plan_with([room("a", rectangle(0, 0, 4, 4)), room("b", rectangle(3, 3, 6, 6))]),
            "se solapan",
        )
        self.assertRejected(
            plan_with([room("a", rectangle(0, 0, 4, 4)), room("b", rectangle(0, 0, 4, 4))]),
            "se solapan",
        )

    def test_reserved_and_duplicate_ids(self) -> None:
        self.assertRejected(plan_with([room("exterior", rectangle(0, 0, 4, 4))]), "reservado")
        self.assertRejected(
            plan_with([room("a", rectangle(0, 0, 4, 4)), room("a", rectangle(5, 0, 8, 4))]),
            "identificador a",
        )

    def test_doors_need_the_shared_wall(self) -> None:
        self.assertRejected(
            plan_with(copy.deepcopy(TWO_ROOMS), [door("d", ["a", "b"], (0, 0), (2, 0))]),
            "pared que comparten",
        )
        self.assertRejected(
            plan_with(copy.deepcopy(TWO_ROOMS), [door("d", ["a", "b"], (4, 1), (5, 2))]),
            "tramo recto",
        )
        self.assertRejected(
            plan_with(copy.deepcopy(TWO_ROOMS), [door("d", ["a", "z"], (4, 1), (4, 2))]),
            "dos salas de esa planta",
        )

    def test_exterior_doors_need_an_outer_wall(self) -> None:
        validate_space_map(plan_with(copy.deepcopy(TWO_ROOMS),
                                     [door("d", ["exterior", "a"], (1, 4), (3, 4))]))
        self.assertRejected(
            plan_with(copy.deepcopy(TWO_ROOMS), [door("d", ["a", "exterior"], (4, 1), (4, 2))]),
            "da al Exterior",
        )

    def test_doors_do_not_overlap(self) -> None:
        self.assertRejected(
            plan_with(copy.deepcopy(TWO_ROOMS), [door("d1", ["a", "b"], (4, 0), (4, 2)),
                                                  door("d2", ["a", "b"], (4, 1), (4, 3))]),
            "se solapan",
        )

    def test_cameras_inside_or_on_the_wall_of_their_room(self) -> None:
        validate_space_map(plan_with(copy.deepcopy(TWO_ROOMS), cameras=[
            camera("inside", "a", 1.5, 2), camera("wall", "b", 8, 2),
            camera("corner", "a", 0, 0), camera("tray", None, 20, 20),
        ]))
        self.assertRejected(
            plan_with(copy.deepcopy(TWO_ROOMS), cameras=[camera("c", "a", 6, 2)]), "fuera de"
        )
        self.assertRejected(
            plan_with(copy.deepcopy(TWO_ROOMS), cameras=[camera("c", "a", 1.3, 2)]),
            "media casilla",
        )

    def test_camera_ids_are_unique_across_floors(self) -> None:
        plan = plan_with(copy.deepcopy(TWO_ROOMS), cameras=[camera("c", "a", 1, 1)])
        plan["floors"].append({"id": "floor_1", "name": "Planta 1", "rooms": [],
                               "cameras": [camera("c", None, 1, 1)], "doors": []})
        self.assertRejected(plan, "identificador c")

    def test_floor_links_join_different_floors(self) -> None:
        plan = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        plan["floor_links"][0]["rooms"] = ["room_entrada", "room_oficina"]
        self.assertRejected(plan, "misma planta")
        plan["floor_links"][0]["rooms"] = ["room_entrada", "room_missing"]
        self.assertRejected(plan, "dos salas que existen")

    def test_at_most_three_floors(self) -> None:
        plan = default_space_map()
        plan["floors"] = [
            {"id": f"floor_{n}", "name": f"Planta {n}", "rooms": [], "cameras": [], "doors": []}
            for n in range(4)
        ]
        self.assertRejected(plan, "entre 1 y 3 plantas")

    def test_unknown_fields_and_wrong_types_are_rejected(self) -> None:
        plan = default_space_map()
        plan["floors"][0]["rooms"] = [dict(room("a", rectangle(0, 0, 4, 4)), fixed_pose=True)]
        self.assertRejected(plan, "exactamente")
        plan = plan_with(copy.deepcopy(TWO_ROOMS), [door("d", [["a"], "b"], (4, 1), (4, 2))])
        self.assertRejected(plan, "dos salas de esa planta")


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "spaces" / "space-map.json"
        self.store = SpaceMapStore(self.path, keep=2)
        self.plan = json.loads(EXAMPLE.read_text(encoding="utf-8"))

    def backups(self) -> list[Path]:
        return sorted((self.path.parent / "backups").glob("*.json"))

    def test_missing_file_loads_the_default_plan(self) -> None:
        document, revision = self.store.load()
        self.assertEqual(document, default_space_map())
        self.assertIsNone(revision)

    def test_save_round_trip_and_copy_of_each_replaced_version(self) -> None:
        first = self.store.save(self.plan, None)
        self.assertEqual(self.store.load(), (self.plan, first))
        self.assertEqual(self.backups(), [])
        renamed = copy.deepcopy(self.plan)
        renamed["workspace"]["name"] = "Otro nombre"
        second = self.store.save(renamed, first)
        self.assertNotEqual(first, second)
        self.assertEqual(len(self.backups()), 1)
        self.assertEqual(json.loads(self.backups()[0].read_text(encoding="utf-8")), self.plan)

    def test_unchanged_save_writes_nothing(self) -> None:
        revision = self.store.save(self.plan, None)
        self.assertEqual(self.store.save(copy.deepcopy(self.plan), revision), revision)
        self.assertEqual(self.backups(), [])

    def test_stale_revision_is_refused(self) -> None:
        revision = self.store.save(self.plan, None)
        with self.assertRaises(SpaceMapConflict):
            self.store.save(self.plan, None)
        with self.assertRaises(SpaceMapConflict):
            self.store.save(self.plan, "0" * 64)
        self.assertEqual(self.store.load()[1], revision)

    def test_invalid_plan_is_not_written(self) -> None:
        broken = copy.deepcopy(self.plan)
        broken["grid"]["columns"] = 2
        with self.assertRaises(SpaceMapError):
            self.store.save(broken, None)
        self.assertFalse(self.path.exists())

    def test_old_copies_are_pruned(self) -> None:
        revision = self.store.save(self.plan, None)
        for index in range(4):
            changed = copy.deepcopy(self.plan)
            changed["workspace"]["name"] = f"Versión {index}"
            revision = self.store.save(changed, revision)
        self.assertEqual(len(self.backups()), 2)


if __name__ == "__main__":
    unittest.main()
