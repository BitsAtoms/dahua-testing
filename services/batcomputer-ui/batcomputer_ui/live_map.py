"""Live map for top_right: people per room of the building plan, and their
moves between rooms.

Recent local tracks of the tracking database (read only) become presences,
one dot each, counted in the room of their camera in the space_map.v2 plan.
Handoff candidates become transfers: a dot crossing the door between two
rooms (see tracking_engine.presence: presences and transfers). A room seen
by several cameras counts the maximum over them, so its dots come from the
camera that sees most people.

No track number or score is shown: the map shows people, not internal data.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys
import threading
import time
from typing import Any, Callable

from .spaces import EXTERIOR, SpaceMapStore

TRACKING_ENGINE_ROOT = Path(__file__).resolve().parents[2] / "tracking-engine"
if str(TRACKING_ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(TRACKING_ENGINE_ROOT))

from tracking_engine.presence import Link, occupancy, span_from_track  # noqa: E402

# Tracks older than this cannot affect the current count: visits stay below
# ten minutes per room and a presence is released 20 s after its last track.
LOOKBACK_SECONDS = 900
# Moves older than this are not sent: the screen only animates fresh ones.
MOVE_SECONDS = 60


def read_tracking(database: Path, now: float) -> tuple[list, list[Link]] | None:
    """Recent track spans and handoff links, or None when there is no database."""
    if not database.is_file():
        return None
    cutoff_us = round((now - LOOKBACK_SECONDS) * 1_000_000)
    try:
        connection = sqlite3.connect(f"file:{database.resolve().as_posix()}?mode=ro", uri=True, timeout=1)
        try:
            columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(local_tracks)")}

            def optional(column: str, fallback: str = "NULL") -> str:
                return column if column in columns else f"{fallback} AS {column}"

            spans = []
            for row in connection.execute(
                f"""
                SELECT track_id, camera_id, status, first_observed_at, last_observed_at,
                       {optional("geometry_json")}, {optional("first_geometry_json")},
                       {optional("attributes_json", "'{}'")}
                FROM local_tracks
                WHERE status = 'active' OR last_received_us >= ?
                """,
                (cutoff_us,),
            ):
                span = span_from_track({
                    "track_id": row[0], "camera_id": row[1], "status": row[2],
                    "first_observed_at": row[3], "last_observed_at": row[4],
                    "geometry": json.loads(row[5]) if row[5] else None,
                    "first_geometry": json.loads(row[6]) if row[6] else None,
                    "attributes": json.loads(row[7]) if row[7] else {},
                })
                if span is not None:
                    spans.append(span)
            tables = {str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            links = []
            if "handoff_candidates" in tables:
                links = [
                    Link(str(origin), str(destination), float(score))
                    for origin, destination, score in connection.execute(
                        "SELECT origin_track_id, destination_track_id, score FROM handoff_candidates "
                        "WHERE observed_us >= ?",
                        (cutoff_us,),
                    )
                ]
        finally:
            connection.close()
    except (sqlite3.Error, ValueError):
        return None
    return spans, links


class LiveMap:
    def __init__(
        self,
        spaces: SpaceMapStore,
        tracking: Callable[[float], tuple[list, list[Link]] | None],
        clock: Callable[[], float] = time.time,
        cache_seconds: float = 1.0,
    ) -> None:
        self.spaces = spaces
        self.tracking = tracking
        self.clock = clock
        self.cache_seconds = cache_seconds
        self.cached: dict | None = None
        self.cached_at = 0.0
        self.lock = threading.Lock()

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            now = self.clock()
            if self.cached is None or now - self.cached_at >= self.cache_seconds:
                self.cached = self._evaluate(now)
                self.cached_at = now
            return self.cached

    def _evaluate(self, now: float) -> dict[str, Any]:
        generated = datetime.fromtimestamp(now, timezone.utc).isoformat()
        try:
            plan, revision = self.spaces.load()
        except ValueError:
            plan, revision = None, None
        rooms = [room for floor in plan["floors"] for room in floor["rooms"]] if plan else []
        if not rooms:
            return {"generated_at": generated, "state": "no_plan", "revision": revision,
                    "plan": None, "total": 0, "rooms": {}, "moves": [], "unmapped": 0}
        camera_rooms = {camera["camera_id"]: camera["room_id"]
                        for floor in plan["floors"] for camera in floor["cameras"] if camera["room_id"]}
        data = self.tracking(now)
        result = {
            "generated_at": generated,
            "state": "ok" if data is not None else "no_data",
            "revision": revision,
            "plan": _drawing(plan),
            "total": 0,
            "rooms": {room["id"]: {"count": 0, "people": []} for room in rooms},
            "moves": [],
            "unmapped": 0,
        }
        if data is None:
            return result
        spans, links = data
        counted = occupancy(spans, camera_rooms, now, links=links)
        for room_id, entry in counted["spaces"].items():
            if room_id not in result["rooms"]:
                continue
            # The dots of the camera that sees most people, as the count.
            busiest = min(entry["by_camera"], key=lambda camera: (-entry["by_camera"][camera], camera))
            result["rooms"][room_id] = {
                "count": entry["count"],
                "people": [
                    {"id": person["presence_id"], "seen": person["active"]}
                    for person in sorted(entry["presences"], key=lambda item: item["first_seen"])
                    if person["camera_id"] == busiest
                ],
            }
        result["total"] = counted["total"]
        result["unmapped"] = counted["unmapped_presences"]
        result["moves"] = [
            {
                "id": move["destination_presence_id"],
                "from_id": move["origin_presence_id"],
                "from_room": move["from_space"],
                "to_room": move["to_space"],
                "via": _passage(plan, move["from_space"], move["to_space"]),
                "age": round(now - move["at"], 1),
            }
            for move in counted["transfers"]
            if now - move["at"] <= MOVE_SECONDS
        ]
        return result


def _drawing(plan: dict[str, Any]) -> dict[str, Any]:
    """What the screen draws: floors, rooms, doors and links, and where each
    camera stands in its room (the origin of its sonar pulses), without names."""
    return {
        "workspace": plan["workspace"]["name"],
        "grid": plan["grid"],
        "floors": [
            {"id": floor["id"], "name": floor["name"], "rooms": floor["rooms"], "doors": floor["doors"],
             "sensors": [{"room_id": camera["room_id"], "position": camera["position"]}
                         for camera in floor["cameras"] if camera["room_id"]]}
            for floor in plan["floors"]
        ],
        "floor_links": plan["floor_links"],
    }


def _passage(plan: dict[str, Any], origin: str, destination: str) -> dict[str, str] | None:
    """The door, or else the stairs or lift, a move between two rooms goes through."""
    pair = {origin, destination}
    for floor in plan["floors"]:
        for door in floor["doors"]:
            if set(door["rooms"]) == pair and EXTERIOR not in pair:
                return {"kind": "door", "id": door["id"]}
    for link in plan["floor_links"]:
        if set(link["rooms"]) == pair:
            return {"kind": "floor", "id": link["id"]}
    return None
