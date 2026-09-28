"""Local, source-neutral visual re-identification experiments."""

from .adaptive_capture import (
    AdaptiveCaptureCoordinator,
    AdaptiveCapturePolicy,
    CaptureDecision,
    EvidenceObservation,
    EvidenceReservoir,
    EvidenceState,
)
from .media_catalog import MediaAsset, iter_media_assets, summarize_assets
from .local_tracking import (
    DetectionFrame,
    FrameDetection,
    LocalTrackerProvider,
    LocalTrackUpdate,
    LocalTrackUpdateKind,
    PixelBox,
)
from .deep_sort_realtime_provider import (
    DeepSortRealtimeConfig,
    DeepSortRealtimeProvider,
)
from .boxmot_provider import BoxMotConfig, BoxMotProvider
from .adaptive_media import AdaptiveMediaStore, adaptive_media_revisions
from .quality import QualityAssessment, assess_image_quality
from .rtsp_buffer import BufferedFrame, RollingJpegBuffer, RtspBufferWorker
from .sequence_assignment import HandoffOption, SequenceResolution, resolve_sequences
from .templates import (
    TemplateComparison,
    TemplateObservation,
    TrackTemplate,
    build_template,
    compare_templates,
)
from .track_visuals import TrackVisual, load_track_visual_sets, load_track_visuals

__all__ = [
    "AdaptiveCaptureCoordinator",
    "AdaptiveCapturePolicy",
    "AdaptiveMediaStore",
    "BufferedFrame",
    "CaptureDecision",
    "EvidenceObservation",
    "EvidenceReservoir",
    "EvidenceState",
    "HandoffOption",
    "DetectionFrame",
    "FrameDetection",
    "LocalTrackerProvider",
    "LocalTrackUpdate",
    "LocalTrackUpdateKind",
    "MediaAsset",
    "PixelBox",
    "DeepSortRealtimeConfig",
    "DeepSortRealtimeProvider",
    "BoxMotConfig",
    "BoxMotProvider",
    "QualityAssessment",
    "RollingJpegBuffer",
    "SequenceResolution",
    "TemplateComparison",
    "TemplateObservation",
    "TrackTemplate",
    "RtspBufferWorker",
    "TrackVisual",
    "assess_image_quality",
    "adaptive_media_revisions",
    "build_template",
    "compare_templates",
    "iter_media_assets",
    "load_track_visuals",
    "load_track_visual_sets",
    "resolve_sequences",
    "summarize_assets",
]
