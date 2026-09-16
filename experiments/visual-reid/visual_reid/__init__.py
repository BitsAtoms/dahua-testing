"""Local, source-neutral visual re-identification experiments."""

from .media_catalog import MediaAsset, iter_media_assets, summarize_assets
from .track_visuals import TrackVisual, load_track_visuals

__all__ = [
    "MediaAsset",
    "TrackVisual",
    "iter_media_assets",
    "load_track_visuals",
    "summarize_assets",
]
