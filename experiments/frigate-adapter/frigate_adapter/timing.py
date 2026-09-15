"""Build diagnostic timing records without changing the tracking contract."""

from __future__ import annotations

from datetime import datetime
from typing import Any


def elapsed_ms(start_ns: int, end_ns: int) -> float:
    """Return a rounded monotonic duration in milliseconds."""
    return round((end_ns - start_ns) / 1_000_000, 3)


def wall_clock_ms(start: str, end: str) -> float:
    """Return a rounded ISO-8601 wall-clock difference in milliseconds."""
    return round(
        (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds()
        * 1000,
        3,
    )


def build_pipeline_timing(
    update: dict[str, Any],
    *,
    mqtt_received_at: str,
    output_persisted_at: str,
    received_ns: int,
    dequeued_ns: int,
    decoded_ns: int,
    raw_persisted_ns: int,
    normalize_started_ns: int,
    normalized_ns: int,
    output_persisted_ns: int,
) -> dict[str, Any]:
    """Create one diagnostic record for a persisted tracking update.

    ``event_age_at_mqtt_ms`` includes Frigate processing and broker delivery;
    it is deliberately not labelled as MQTT transport latency. Durations after
    the local callback use the monotonic clock and therefore are not affected
    by wall-clock adjustments.
    """
    return {
        "schema_version": "pipeline_timing.v1",
        "message_id": update["message_id"],
        "camera_id": update["camera_id"],
        "local_track_id": update["subject"]["local_track_id"],
        "phase": update["phase"],
        "timestamps": {
            "observed_at": update["observed_at"],
            "mqtt_received_at": mqtt_received_at,
            "normalized_at": update["published_at"],
            "output_persisted_at": output_persisted_at,
        },
        "duration_ms": {
            "event_age_at_mqtt": wall_clock_ms(
                update["observed_at"], mqtt_received_at
            ),
            "queue_wait": elapsed_ms(received_ns, dequeued_ns),
            "json_decode": elapsed_ms(dequeued_ns, decoded_ns),
            "raw_persist": elapsed_ms(decoded_ns, raw_persisted_ns),
            "normalize": elapsed_ms(normalize_started_ns, normalized_ns),
            "update_persist": elapsed_ms(normalized_ns, output_persisted_ns),
            "mqtt_callback_to_output": elapsed_ms(
                received_ns, output_persisted_ns
            ),
        },
    }
