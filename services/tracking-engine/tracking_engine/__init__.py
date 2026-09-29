"""Provider-neutral local track state projection."""

from .handoffs import HandoffEngine, TopologySyncResult
from .presence import PresenceParams, TrackSpan, build_presences, occupancy, span_from_track
from .runner import BatchResult, TrackingRunner
from .store import ProjectionResult, TrackingStore
from .topology import SpaceTopology, TopologyError

__all__ = [
    "BatchResult",
    "HandoffEngine",
    "PresenceParams",
    "ProjectionResult",
    "SpaceTopology",
    "TopologyError",
    "TopologySyncResult",
    "TrackSpan",
    "TrackingRunner",
    "TrackingStore",
    "build_presences",
    "occupancy",
    "span_from_track",
]
