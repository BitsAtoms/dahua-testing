"""Strict zero-dependency validation for the track_update.v1 contract."""

from __future__ import annotations

from datetime import datetime
from typing import Any


TOP_LEVEL_FIELDS = frozenset(
    {
        "schema_version",
        "message_id",
        "track_id",
        "source",
        "camera_id",
        "phase",
        "sequence",
        "observed_at",
        "published_at",
        "subject",
        "geometry",
        "zones",
        "attributes",
        "media",
        "quality",
        "source_ref",
    }
)
PHASES = frozenset({"new", "update", "end", "snapshot"})
SOURCE_TYPES = frozenset({"dahua", "frigate"})


class ContractError(ValueError):
    """Raised when a message does not satisfy track_update.v1."""


def validate_track_update(update: Any) -> None:
    """Raise ``ContractError`` unless the complete v1 message is valid."""
    root = _mapping(update, "message")
    missing = TOP_LEVEL_FIELDS - root.keys()
    unknown = root.keys() - TOP_LEVEL_FIELDS
    if missing:
        raise ContractError(f"missing fields: {', '.join(sorted(missing))}")
    if unknown:
        raise ContractError(f"unknown fields: {', '.join(sorted(unknown))}")
    if root["schema_version"] != "track_update.v1":
        raise ContractError("schema_version must be track_update.v1")

    _text(root["message_id"], "message_id")
    _text(root["track_id"], "track_id")
    _text(root["camera_id"], "camera_id")
    if root["phase"] not in PHASES:
        raise ContractError("phase is invalid")
    if (
        not isinstance(root["sequence"], int)
        or isinstance(root["sequence"], bool)
        or root["sequence"] < 1
    ):
        raise ContractError("sequence must be a positive integer")
    if root["observed_at"] is not None:
        _timestamp(root["observed_at"], "observed_at")
    _timestamp(root["published_at"], "published_at")

    source = _exact_mapping(root["source"], "source", {"type", "instance_id"})
    if source["type"] not in SOURCE_TYPES:
        raise ContractError("source.type is invalid")
    if source["instance_id"] is not None:
        _text(source["instance_id"], "source.instance_id")

    subject = _mapping(root["subject"], "subject")
    for field in ("type", "local_track_id", "confidence"):
        if field not in subject:
            raise ContractError(f"subject.{field} is required")
    _text(subject["type"], "subject.type")
    if subject["local_track_id"] is not None:
        _text(subject["local_track_id"], "subject.local_track_id")
    confidence = subject["confidence"]
    if confidence is not None:
        _unit_number(confidence, "subject.confidence")

    _validate_geometry(root["geometry"])
    zones = _exact_mapping(root["zones"], "zones", {"current", "entered"})
    _text_list(zones["current"], "zones.current")
    _text_list(zones["entered"], "zones.entered")
    _mapping(root["attributes"], "attributes")
    _validate_media(root["media"])

    quality = _mapping(root["quality"], "quality")
    if quality.get("status") not in {"partial", "complete"}:
        raise ContractError("quality.status is invalid")
    if quality.get("source_lifecycle") not in {"live", "finalized_only"}:
        raise ContractError("quality.source_lifecycle is invalid")

    source_ref = _exact_mapping(
        root["source_ref"], "source_ref", {"event_id", "message_id"}
    )
    _text(source_ref["event_id"], "source_ref.event_id")
    _text(source_ref["message_id"], "source_ref.message_id")


def _validate_geometry(value: Any) -> None:
    if value is None:
        return
    geometry = _exact_mapping(
        value, "geometry", {"coordinate_space", "box", "center"}
    )
    if geometry["coordinate_space"] != "normalized_0_1":
        raise ContractError("geometry.coordinate_space is invalid")
    box = _exact_mapping(
        geometry["box"], "geometry.box", {"x_min", "y_min", "x_max", "y_max"}
    )
    for field in ("x_min", "y_min", "x_max", "y_max"):
        _unit_number(box[field], f"geometry.box.{field}")
    if box["x_min"] > box["x_max"] or box["y_min"] > box["y_max"]:
        raise ContractError("geometry.box bounds are reversed")
    center = _exact_mapping(geometry["center"], "geometry.center", {"x", "y"})
    _unit_number(center["x"], "geometry.center.x")
    _unit_number(center["y"], "geometry.center.y")


def _validate_media(value: Any) -> None:
    if not isinstance(value, list):
        raise ContractError("media must be a list")
    for index, item in enumerate(value):
        media = _mapping(item, f"media[{index}]")
        for field in ("role", "content_type", "path"):
            if field not in media:
                raise ContractError(f"media[{index}].{field} is required")
            _text(media[field], f"media[{index}].{field}")


def _mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError(f"{name} must be an object")
    return value


def _exact_mapping(
    value: Any, name: str, fields: set[str]
) -> dict[str, Any]:
    result = _mapping(value, name)
    if set(result) != fields:
        raise ContractError(f"{name} must contain exactly: {', '.join(sorted(fields))}")
    return result


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ContractError(f"{name} must be a non-empty string")
    return value


def _timestamp(value: Any, name: str) -> datetime:
    _text(value, name)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ContractError(f"{name} must be an ISO-8601 timestamp") from error
    if parsed.tzinfo is None:
        raise ContractError(f"{name} must include a timezone")
    return parsed


def _unit_number(value: Any, name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ContractError(f"{name} must be numeric")
    result = float(value)
    if not 0 <= result <= 1:
        raise ContractError(f"{name} must be between 0 and 1")
    return result


def _text_list(value: Any, name: str) -> None:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ContractError(f"{name} must be a string list")
