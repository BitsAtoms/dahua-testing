"""Project enriched source observations onto the lean tracking contract."""

from __future__ import annotations

from copy import deepcopy
import re
from typing import Any


_REVISION_RE = re.compile(r":r(?P<revision>[1-9]\d*)$")


def observation_to_track_update(observation: dict[str, Any]) -> dict[str, Any]:
    """Convert one Dahua ``observation.v1`` revision to ``track_update.v1``.

    HumanTrait is a finalized capture on the tested camera, so it is exposed as
    a ``snapshot`` rather than pretending to be a live lifecycle update. Raw
    provider structures deliberately remain in the observation stream.
    """
    observation_id = _required_string(observation, "observation_id")
    observation_message_id = _required_string(observation, "message_id")
    camera_id = _required_string(observation, "camera_id")
    source = deepcopy(observation.get("source"))
    if not isinstance(source, dict) or source.get("type") != "dahua":
        raise ValueError("a Dahua observation source is required")

    revision_match = _REVISION_RE.search(observation_message_id)
    sequence = int(revision_match.group("revision")) if revision_match else 1
    timing = observation.get("timing") or {}
    published_at = timing.get("collector_published_at") or observation.get(
        "ingested_at"
    )
    if not isinstance(published_at, str) or not published_at:
        raise ValueError("collector publication or ingestion time is required")

    quality = deepcopy(observation.get("quality") or {})
    quality["source_lifecycle"] = "finalized_only"

    return {
        "schema_version": "track_update.v1",
        "message_id": f"track-update:{observation_message_id}",
        "track_id": observation_id,
        "source": source,
        "camera_id": camera_id,
        "phase": "snapshot",
        "sequence": sequence,
        "observed_at": observation.get("observed_at"),
        "published_at": published_at,
        "subject": deepcopy(observation.get("subject") or {}),
        "geometry": deepcopy(observation.get("geometry")),
        "zones": deepcopy(
            observation.get("zones") or {"current": [], "entered": []}
        ),
        # Dahua's numeric attribute enums are provider-specific. Keep them in
        # observation.v1 until a shared semantic mapping is defined.
        "attributes": {},
        "media": deepcopy(observation.get("media") or []),
        "quality": quality,
        "source_ref": {
            "event_id": observation_id,
            "message_id": observation_message_id,
        },
    }


def _required_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} is required")
    return value
