"""Strict ``space_map.v1`` contract used by the editor and tracking engine."""

from __future__ import annotations

import re
from typing import Any


ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class MapError(ValueError):
    """Raised when a space-map document is unsafe or inconsistent."""


def default_map() -> dict[str, Any]:
    return {
        "schema_version": "space_map.v1",
        "site": {"id": "main", "name": "Nuevo espacio"},
        "spaces": [],
        "cameras": [],
        "transitions": [],
    }


def validate_map(document: Any) -> None:
    root = _exact_object(
        document,
        "map",
        {"schema_version", "site", "spaces", "cameras", "transitions"},
    )
    if root["schema_version"] != "space_map.v1":
        raise MapError("schema_version must be space_map.v1")

    site = _exact_object(root["site"], "site", {"id", "name"})
    _identifier(site["id"], "site.id")
    _text(site["name"], "site.name", 120)

    spaces = _list(root["spaces"], "spaces")
    space_ids: set[str] = set()
    for index, value in enumerate(spaces):
        space = _exact_object(value, f"spaces[{index}]", {"id", "name", "polygon"})
        space_id = _identifier(space["id"], f"spaces[{index}].id")
        if space_id in space_ids:
            raise MapError(f"duplicate space id: {space_id}")
        space_ids.add(space_id)
        _text(space["name"], f"spaces[{index}].name", 120)
        polygon = _list(space["polygon"], f"spaces[{index}].polygon")
        if len(polygon) < 3:
            raise MapError(f"spaces[{index}].polygon needs at least 3 points")
        for point_index, point in enumerate(polygon):
            _point(point, f"spaces[{index}].polygon[{point_index}]")

    cameras = _list(root["cameras"], "cameras")
    camera_ids: set[str] = set()
    for index, value in enumerate(cameras):
        name = f"cameras[{index}]"
        camera = _exact_object(
            value,
            name,
            {
                "camera_id",
                "label",
                "position",
                "space_id",
                "heading_deg",
                "fov_deg",
                "range",
                "fixed_pose",
            },
        )
        camera_id = _identifier(camera["camera_id"], f"{name}.camera_id")
        if camera_id in camera_ids:
            raise MapError(f"duplicate camera id: {camera_id}")
        camera_ids.add(camera_id)
        _text(camera["label"], f"{name}.label", 120)
        _point(camera["position"], f"{name}.position")
        if camera["space_id"] is not None and camera["space_id"] not in space_ids:
            raise MapError(f"{name}.space_id references an unknown space")
        _number(camera["heading_deg"], f"{name}.heading_deg", 0, 359.999)
        _number(camera["fov_deg"], f"{name}.fov_deg", 1, 180)
        _number(camera["range"], f"{name}.range", 0.01, 1)
        if not isinstance(camera["fixed_pose"], bool):
            raise MapError(f"{name}.fixed_pose must be boolean")

    transitions = _list(root["transitions"], "transitions")
    transition_ids: set[str] = set()
    for index, value in enumerate(transitions):
        name = f"transitions[{index}]"
        transition = _exact_object(
            value,
            name,
            {
                "id",
                "from_space_id",
                "to_space_id",
                "bidirectional",
                "min_seconds",
                "max_seconds",
            },
        )
        transition_id = _identifier(transition["id"], f"{name}.id")
        if transition_id in transition_ids:
            raise MapError(f"duplicate transition id: {transition_id}")
        transition_ids.add(transition_id)
        origin = transition["from_space_id"]
        destination = transition["to_space_id"]
        if origin not in space_ids or destination not in space_ids:
            raise MapError(f"{name} references an unknown space")
        if origin == destination:
            raise MapError(f"{name} must connect different spaces")
        if not isinstance(transition["bidirectional"], bool):
            raise MapError(f"{name}.bidirectional must be boolean")
        minimum = _number(transition["min_seconds"], f"{name}.min_seconds", 0, 3600)
        maximum = _number(transition["max_seconds"], f"{name}.max_seconds", 0, 3600)
        if minimum > maximum:
            raise MapError(f"{name}.min_seconds must not exceed max_seconds")


def _exact_object(value: Any, name: str, fields: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise MapError(f"{name} must be an object")
    if set(value) != fields:
        raise MapError(f"{name} must contain exactly: {', '.join(sorted(fields))}")
    return value


def _list(value: Any, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise MapError(f"{name} must be a list")
    return value


def _identifier(value: Any, name: str) -> str:
    if not isinstance(value, str) or not ID_RE.fullmatch(value):
        raise MapError(f"{name} must use letters, numbers, underscore or hyphen")
    return value


def _text(value: Any, name: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise MapError(f"{name} must be non-empty and at most {maximum} characters")
    return value


def _number(value: Any, name: str, minimum: float, maximum: float) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise MapError(f"{name} must be numeric")
    number = float(value)
    if not minimum <= number <= maximum:
        raise MapError(f"{name} must be between {minimum} and {maximum}")
    return number


def _point(value: Any, name: str) -> None:
    point = _exact_object(value, name, {"x", "y"})
    _number(point["x"], f"{name}.x", 0, 1)
    _number(point["y"], f"{name}.y", 0, 1)
