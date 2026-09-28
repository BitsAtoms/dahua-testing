"""Tracking-oriented view of the source-neutral ``space_map.v1`` contract."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any


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
        if not isinstance(document, dict) or document.get("schema_version") != "space_map.v1":
            raise TopologyError("space map must use schema_version space_map.v1")
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
