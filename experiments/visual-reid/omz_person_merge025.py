"""Open Model Zoo person tracker config used by the controlled benchmark.

This is the upstream ``configs/person.py`` configuration with only
``sct_config.merge_thresh`` changed from 0.15 to 0.25.  Keeping the complete
configuration here makes the experiment reproducible without modifying the
ignored Open Model Zoo checkout.
"""

from types import SimpleNamespace as namespace


random_seed = 100

obj_det = namespace(trg_classes=(1,))
obj_segm = namespace(trg_classes=(1,))

mct_config = namespace(
    time_window=20,
    global_match_thresh=0.2,
    bbox_min_aspect_ratio=1.2,
)

sct_config = namespace(
    time_window=10,
    continue_time_thresh=2,
    track_clear_thresh=3000,
    match_threshold=0.25,
    merge_thresh=0.25,
    n_clusters=4,
    max_bbox_velocity=0.2,
    detection_occlusion_thresh=0.7,
    track_detection_iou_thresh=0.5,
    process_curr_features_number=0,
    interpolate_time_thresh=10,
    detection_filter_speed=0.6,
    rectify_thresh=0.1,
)

normalizer_config = namespace(
    enabled=False,
    clip_limit=0.5,
    tile_size=8,
)

visualization_config = namespace(
    show_all_detections=True,
    max_window_size=(1920, 1080),
    stack_frames="vertical",
)

analyzer = namespace(
    enable=False,
    show_distances=True,
    save_distances="",
    concatenate_imgs_with_distances=True,
    plot_timeline_freq=0,
    save_timeline="",
    crop_size=(32, 64),
)

embeddings = namespace(
    save_path="",
    use_images=True,
    step=0,
)
