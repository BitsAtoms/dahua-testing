"""Source-neutral contracts for replaceable frame-level local trackers."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import math
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class PixelBox:
    x1: float
    y1: float
    x2: float
    y2: float

    def __post_init__(self) -> None:
        values = (self.x1, self.y1, self.x2, self.y2)
        if not all(math.isfinite(value) for value in values):
            raise ValueError("box coordinates must be finite")
        if self.x2 <= self.x1 or self.y2 <= self.y1:
            raise ValueError("box must have positive width and height")


@dataclass(frozen=True)
class FrameDetection:
    box: PixelBox
    confidence: float
    label: str = "person"
    source_detection_id: str | None = None

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("detection confidence must be between zero and one")
        if not self.label.strip():
            raise ValueError("detection label cannot be empty")


@dataclass(frozen=True)
class DetectionFrame:
    camera_id: str
    frame_index: int
    observed_us: int
    width: int
    height: int
    detections: tuple[FrameDetection, ...]
    image: object | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not self.camera_id.strip():
            raise ValueError("camera_id cannot be empty")
        if self.frame_index < 0 or self.observed_us <= 0:
            raise ValueError("frame index and timestamp are invalid")
        if self.width <= 0 or self.height <= 0:
            raise ValueError("frame dimensions must be positive")
        for detection in self.detections:
            box = detection.box
            if box.x1 < 0 or box.y1 < 0 or box.x2 > self.width or box.y2 > self.height:
                raise ValueError("detection box is outside the frame")


class LocalTrackUpdateKind(str, Enum):
    OBSERVED = "observed"
    ENDED = "ended"


@dataclass(frozen=True)
class LocalTrackUpdate:
    camera_id: str
    local_track_id: str
    frame_index: int
    observed_us: int
    kind: LocalTrackUpdateKind
    box: PixelBox | None = None
    confidence: float | None = None

    def __post_init__(self) -> None:
        if not self.camera_id.strip() or not self.local_track_id.strip():
            raise ValueError("camera and local track IDs cannot be empty")
        if self.frame_index < 0 or self.observed_us <= 0:
            raise ValueError("track update frame index and timestamp are invalid")
        if self.kind is LocalTrackUpdateKind.OBSERVED:
            if self.box is None or self.confidence is None:
                raise ValueError("observed tracks require a box and confidence")
            if not 0.0 <= self.confidence <= 1.0:
                raise ValueError("track confidence must be between zero and one")
        elif self.box is not None or self.confidence is not None:
            raise ValueError("ended tracks cannot carry a current observation")


@runtime_checkable
class LocalTrackerProvider(Protocol):
    """Minimal boundary implemented by any external local tracker adapter."""

    @property
    def provider_id(self) -> str: ...

    def process(self, frame: DetectionFrame) -> tuple[LocalTrackUpdate, ...]: ...

    def reset(
        self, camera_id: str, *, frame_index: int, observed_us: int
    ) -> tuple[LocalTrackUpdate, ...]: ...
