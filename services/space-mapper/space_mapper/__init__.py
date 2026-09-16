"""Local space-map configuration and validation."""

from .model import MapError, default_map, validate_map
from .store import SpaceMapStore, discover_camera_ids

__all__ = [
    "MapError",
    "SpaceMapStore",
    "default_map",
    "discover_camera_ids",
    "validate_map",
]
