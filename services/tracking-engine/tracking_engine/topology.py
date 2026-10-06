"""Tracking-oriented view of the space map: which room each camera counts
in, and which rooms a person can pass between.

``space_map.v2`` is the building plan of the Batcomputer space editor. It has
no typed travel times: each door between two rooms and each floor link
(stairs or lift) becomes one two-way transition with a general window, until
the times of each door are measured (phase 7). Rooms a camera partly sees
("vista adicional") get a wider overlap, because one person can be seen by
both cameras at once. ``space_map.v1``, the old space mapper's format with
typed transitions, still loads (tests and older maps).
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any


EXTERIOR = "exterior"
GENERAL_MIN_SECONDS = 0.0
GENERAL_MAX_SECONDS = 30.0
GENERAL_OVERLAP_SECONDS = 2.0
# As typed by hand in the old map for the pair one camera partly sees.
PARTIAL_VIEW_OVERLAP_SECONDS = 8.0


class TopologyError(ValueError):
    """Raised when a space map cannot safely drive handoff matching."""


@dataclass(frozen=True)
class CameraPlacement:
    camera_id: str
    space_id: str | None
    fixed_pose: bool


@dataclass(frozen=True)
class Transition:
    transition_id: str
    from_space_id: str
    to_space_id: str
    bidirectional: bool
    min_seconds: float
    max_seconds: float
    overlap_tolerance_seconds: float

    def permits(self, origin: str, destination: str) -> bool:
        direct = self.from_space_id == origin and self.to_space_id == destination
        reverse = (
            self.bidirectional
            and self.from_space_id == destination
            and self.to_space_id == origin
        )
        return direct or reverse


@dataclass(frozen=True)
class SpaceTopology:
    fingerprint: str
    cameras: dict[str, CameraPlacement]
    transitions: tuple[Transition, ...]

    @classmethod
    def load(cls, path: Path) -> "SpaceTopology":
        document = json.loads(path.read_text(encoding="utf-8-sig"))
        version = document.get("schema_version") if isinstance(document, dict) else None
        if version == "space_map.v2":
            return cls._from_plan(document)
        if version != "space_map.v1":
            raise TopologyError("space map must use schema_version space_map.v1 or space_map.v2")
        spaces_value = document.get("spaces")
        cameras_value = document.get("cameras")
        transitions_value = document.get("transitions")
        if not all(isinstance(value, list) for value in (spaces_value, cameras_value, transitions_value)):
            raise TopologyError("spaces, cameras and transitions must be lists")

        space_ids = {_required_text(item, "id", "space") for item in spaces_value}
        if len(space_ids) != len(spaces_value):
            raise TopologyError("space ids must be unique")

        cameras: dict[str, CameraPlacement] = {}
        for item in cameras_value:
            camera_id = _required_text(item, "camera_id", "camera")
            if camera_id in cameras:
                raise TopologyError(f"duplicate camera id: {camera_id}")
            space_id = item.get("space_id")
            if space_id is not None and space_id not in space_ids:
                raise TopologyError(f"camera {camera_id} references an unknown space")
            fixed_pose = item.get("fixed_pose")
            if not isinstance(fixed_pose, bool):
                raise TopologyError(f"camera {camera_id} fixed_pose must be boolean")
            cameras[camera_id] = CameraPlacement(camera_id, space_id, fixed_pose)

        transitions: list[Transition] = []
        transition_ids: set[str] = set()
        for item in transitions_value:
            transition_id = _required_text(item, "id", "transition")
            if transition_id in transition_ids:
                raise TopologyError(f"duplicate transition id: {transition_id}")
            transition_ids.add(transition_id)
            origin = _required_text(item, "from_space_id", "transition")
            destination = _required_text(item, "to_space_id", "transition")
            if origin not in space_ids or destination not in space_ids or origin == destination:
                raise TopologyError(f"transition {transition_id} has invalid spaces")
            bidirectional = item.get("bidirectional")
            if not isinstance(bidirectional, bool):
                raise TopologyError(f"transition {transition_id} bidirectional must be boolean")
            minimum = _number(item.get("min_seconds"), f"transition {transition_id} min_seconds")
            maximum = _number(item.get("max_seconds"), f"transition {transition_id} max_seconds")
            overlap_tolerance = _number(
                item.get("overlap_tolerance_seconds", 2),
                f"transition {transition_id} overlap_tolerance_seconds",
            )
            if minimum < 0 or maximum < minimum:
                raise TopologyError(f"transition {transition_id} has invalid time window")
            if not 0 <= overlap_tolerance <= 60:
                raise TopologyError(
                    f"transition {transition_id} has invalid overlap tolerance"
                )
            transitions.append(
                Transition(
                    transition_id,
                    origin,
                    destination,
                    bidirectional,
                    minimum,
                    maximum,
                    overlap_tolerance,
                )
            )

        canonical = json.dumps(document, sort_keys=True, separators=(",", ":"))
        return cls(
            fingerprint=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            cameras=cameras,
            transitions=tuple(transitions),
        )

    @classmethod
    def _from_plan(cls, document: dict[str, Any]) -> "SpaceTopology":
        floors = document.get("floors")
        links = document.get("floor_links", [])
        if not isinstance(floors, list) or not isinstance(links, list):
            raise TopologyError("floors and floor_links must be lists")
        rooms: set[str] = set()
        cameras: dict[str, CameraPlacement] = {}
        partial: set[frozenset[str]] = set()
        passages: set[frozenset[str]] = set()
        for floor in floors:
            if not isinstance(floor, dict):
                raise TopologyError("floor must be an object")
            rooms |= {_required_text(room, "id", "room") for room in floor.get("rooms", [])}
        for floor in floors:
            for camera in floor.get("cameras", []):
                camera_id = _required_text(camera, "camera_id", "camera")
                if camera_id in cameras:
                    raise TopologyError(f"duplicate camera id: {camera_id}")
                room_id = camera.get("room_id")
                if room_id is not None and room_id not in rooms:
                    raise TopologyError(f"camera {camera_id} references an unknown room")
                cameras[camera_id] = CameraPlacement(camera_id, room_id, True)
                for seen in camera.get("also_sees", []) if room_id else []:
                    partial.add(frozenset((room_id, seen)))
            for door in floor.get("doors", []):
                pair = door.get("rooms") if isinstance(door, dict) else None
                if not isinstance(pair, list) or len(pair) != 2:
                    raise TopologyError("door must join two rooms")
                if EXTERIOR not in pair:
                    passages.add(frozenset(pair))
        for link in links:
            pair = link.get("rooms") if isinstance(link, dict) else None
            if not isinstance(pair, list) or len(pair) != 2:
                raise TopologyError("floor link must join two rooms")
            passages.add(frozenset(pair))
        if any(room not in rooms for pair in passages for room in pair):
            raise TopologyError("a door or floor link references an unknown room")
        transitions = []
        for pair in sorted(passages, key=sorted):
            origin, destination = sorted(pair)
            overlap = PARTIAL_VIEW_OVERLAP_SECONDS if pair in partial else GENERAL_OVERLAP_SECONDS
            transitions.append(Transition(
                f"{origin}~{destination}", origin, destination, True,
                GENERAL_MIN_SECONDS, GENERAL_MAX_SECONDS, overlap,
            ))
        # Only what changes candidates: renaming or redrawing a room does not
        # make the engine rebuild them.
        canonical = json.dumps(
            {
                "schema_version": "space_map.v2",
                "cameras": sorted([item.camera_id, item.space_id] for item in cameras.values()
                                  if item.space_id),
                "transitions": [
                    [item.from_space_id, item.to_space_id, item.min_seconds, item.max_seconds,
                     item.overlap_tolerance_seconds]
                    for item in transitions
                ],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return cls(
            fingerprint=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            cameras=cameras,
            transitions=tuple(transitions),
        )

    def space_for_camera(self, camera_id: str) -> str | None:
        placement = self.cameras.get(camera_id)
        return placement.space_id if placement else None

    def transitions_between(self, origin: str, destination: str) -> list[Transition]:
        return [
            transition
            for transition in self.transitions
            if transition.permits(origin, destination)
        ]


def _required_text(value: Any, field: str, name: str) -> str:
    if not isinstance(value, dict):
        raise TopologyError(f"{name} must be an object")
    result = value.get(field)
    if not isinstance(result, str) or not result:
        raise TopologyError(f"{name}.{field} must be non-empty text")
    return result


def _number(value: Any, name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise TopologyError(f"{name} must be numeric")
    return float(value)
