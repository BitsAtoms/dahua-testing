from __future__ import annotations

from pathlib import Path
import sys
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "services" / "tracking-engine"))

from tracking_engine import (  # noqa: E402
    Link,
    PresenceParams,
    TrackSpan,
    build_presences,
    occupancy,
    span_from_track,
)


T = 1_790_685_000.0
SEAT = (0.12, 0.60)
SPACES = {"dahua_212": "space_2", "dahua_213": "space_2", "frigate_hall": "space_1"}


def span(track_id: str, start: float, end: float, *, active: bool = False,
         camera: str = "dahua_212", first=SEAT, last=SEAT) -> TrackSpan:
    return TrackSpan(track_id, camera, T + start, T + end, active, first, last)


class PresenceTest(unittest.TestCase):
    def test_camera_re_detections_of_a_static_person_are_one_presence(self) -> None:
        # Pattern seen on dahua_212: closed and re-opened tracks at one seat.
        spans = [span("560", 0, 150), span("562", 169, 245), span("566", 250, 274),
                 span("576", 280, 500, active=True)]
        (presence,) = build_presences(spans)
        self.assertEqual(presence.track_ids, ["560", "562", "566", "576"])
        for now in (10, 160, 247, 400):
            self.assertEqual(occupancy(spans, SPACES, T + now)["spaces"]["space_2"]["count"], 1)

    def test_overlapping_duplicates_at_one_position_count_once(self) -> None:
        spans = [span("554", 0, 6), span("555", 0.1, 0.2), span("556", 3, 12, active=True)]
        self.assertEqual(len(build_presences(spans)), 1)
        self.assertEqual(occupancy(spans, SPACES, T + 5)["total"], 1)

    def test_separate_positions_are_separate_presences(self) -> None:
        spans = [span("a", 0, 60, active=True), span("b", 5, 60, active=True, first=(0.7, 0.6), last=(0.7, 0.6))]
        self.assertEqual(occupancy(spans, SPACES, T + 30)["spaces"]["space_2"]["count"], 2)

    def test_short_one_off_detection_never_counts(self) -> None:
        spans = [span("ghost", 0, 0.6, first=(0.9, 0.7), last=(0.9, 0.7))]
        for now in (0.5, 1, 10):
            self.assertEqual(occupancy(spans, SPACES, T + now)["total"], 0)

    def test_presence_counts_only_after_confirmation(self) -> None:
        spans = [span("new", 0, 0, active=True)]
        self.assertEqual(occupancy(spans, SPACES, T + 2.9)["total"], 0)
        self.assertEqual(occupancy(spans, SPACES, T + 3.0)["total"], 1)

    def test_presence_is_held_then_released_after_departure(self) -> None:
        spans = [span("walker", 0, 30)]
        self.assertEqual(occupancy(spans, SPACES, T + 45)["total"], 1)
        self.assertEqual(occupancy(spans, SPACES, T + 50)["total"], 1)
        self.assertEqual(occupancy(spans, SPACES, T + 50.1)["total"], 0)

    def test_a_late_track_after_the_hold_is_a_new_presence(self) -> None:
        spans = [span("before", 0, 10), span("after", 40, 60)]
        self.assertEqual(len(build_presences(spans)), 2)

    def test_tracks_of_different_cameras_are_never_joined(self) -> None:
        spans = [span("a", 0, 20), span("b", 5, 20, camera="dahua_213")]
        self.assertEqual(len(build_presences(spans)), 2)

    def test_space_with_two_cameras_counts_the_maximum(self) -> None:
        spans = [span("a", 0, 60, active=True), span("b", 0, 60, active=True, camera="dahua_213"),
                 span("c", 0, 60, active=True, camera="dahua_213", first=(0.7, 0.6), last=(0.7, 0.6))]
        result = occupancy(spans, SPACES, T + 30)
        self.assertEqual(result["spaces"]["space_2"]["by_camera"], {"dahua_212": 1, "dahua_213": 2})
        self.assertEqual(result["spaces"]["space_2"]["count"], 2)
        self.assertEqual(result["total"], 2)

    def test_unmapped_camera_is_reported_but_not_counted(self) -> None:
        spans = [span("x", 0, 60, active=True, camera="unknown_cam")]
        result = occupancy(spans, SPACES, T + 30)
        self.assertEqual(result["total"], 0)
        self.assertEqual(result["unmapped_presences"], 1)

    def test_span_from_track_row(self) -> None:
        row = {
            "track_id": "dahua:dahua_212:live:S:560", "camera_id": "dahua_212", "status": "active",
            "first_observed_at": "2026-09-29T13:32:33+00:00", "last_observed_at": "2026-09-29T13:35:15+00:00",
            "geometry": {"center": {"x": 0.1, "y": 0.6}}, "first_geometry": None, "attributes": {},
        }
        result = span_from_track(row)
        self.assertTrue(result.active)
        self.assertEqual(result.first_center, (0.1, 0.6))  # falls back to latest geometry
        self.assertEqual(result.end - result.start, 162)

    def test_ineligible_tracks_are_ignored(self) -> None:
        row = {"track_id": "t", "camera_id": "c", "status": "active",
               "first_observed_at": "2026-09-29T13:32:33+00:00", "last_observed_at": "2026-09-29T13:32:40+00:00",
               "geometry": None, "attributes": {"track_eligibility": {"state": "excluded"}}}
        self.assertIsNone(span_from_track(row))

    # Transfer: a person last seen in the hall (space_1) appears in space_2.
    def walk(self, *, hall_active: bool = False) -> list[TrackSpan]:
        return [span("h1", 0, 20, active=hall_active, camera="frigate_hall"),
                span("d1", 25, 40, active=True)]

    def test_a_linked_new_presence_takes_over_the_one_no_longer_seen(self) -> None:
        link = [Link("h1", "d1", 0.8)]
        result = occupancy(self.walk(), SPACES, T + 30, links=link)
        self.assertEqual((result["spaces"].get("space_1", {}).get("count", 0),
                          result["spaces"]["space_2"]["count"], result["total"]), (0, 1, 1))
        self.assertEqual(result["transfers"], [{
            "origin_presence_id": "h1", "destination_presence_id": "d1",
            "from_space": "space_1", "to_space": "space_2", "at": T + 25,
        }])
        # Without the link the hall keeps holding the person: two places at once.
        self.assertEqual(occupancy(self.walk(), SPACES, T + 30)["total"], 2)

    def test_the_origin_is_held_until_the_new_presence_counts(self) -> None:
        result = occupancy(self.walk(), SPACES, T + 26, links=[Link("h1", "d1", 0.8)])
        self.assertEqual((result["total"], result["transfers"]), (1, []))
        self.assertEqual(result["spaces"]["space_1"]["count"], 1)

    def test_no_transfer_while_the_origin_is_still_seen(self) -> None:
        result = occupancy(self.walk(hall_active=True), SPACES, T + 30, links=[Link("h1", "d1", 0.8)])
        self.assertEqual((result["total"], result["transfers"]), (2, []))

    def test_no_transfer_from_an_earlier_fragment_of_a_presence(self) -> None:
        # The hall presence went on with h2: h1 ending did not mean leaving.
        spans = self.walk() + [span("h2", 21, 24, camera="frigate_hall")]
        result = occupancy(spans, SPACES, T + 30, links=[Link("h1", "d1", 0.8)])
        self.assertEqual((result["total"], result["transfers"]), (2, []))

    def test_only_a_new_presence_takes_over(self) -> None:
        # d2 continues the presence d1 started, so it is not an arrival.
        spans = self.walk() + [span("d2", 26, 40, active=True)]
        result = occupancy(spans, SPACES, T + 30, links=[Link("h1", "d2", 0.9)])
        self.assertEqual(result["transfers"], [])

    def test_each_origin_moves_once_to_the_first_arrival(self) -> None:
        spans = [span("h1", 0, 20, camera="frigate_hall"),
                 span("h9", 0, 21, camera="frigate_hall", first=(0.9, 0.9), last=(0.9, 0.9)),
                 span("d1", 25, 40, active=True),
                 span("d2", 27, 40, active=True, first=(0.8, 0.2), last=(0.8, 0.2))]
        links = [Link("h1", "d1", 0.8), Link("h1", "d2", 0.9), Link("h9", "d2", 0.5)]
        moved = [(item["origin_presence_id"], item["destination_presence_id"])
                 for item in occupancy(spans, SPACES, T + 32, links=links)["transfers"]]
        self.assertEqual(moved, [("h1", "d1"), ("h9", "d2")])

    def test_links_within_one_space_are_ignored(self) -> None:
        spans = [span("a", 0, 20), span("b", 25, 40, active=True, first=(0.9, 0.9), last=(0.9, 0.9))]
        self.assertEqual(occupancy(spans, SPACES, T + 30, links=[Link("a", "b", 1)])["transfers"], [])

    def test_rule_is_reported_with_the_result(self) -> None:
        params = PresenceParams(join_distance=0.2, confirm_seconds=5, hold_seconds=30)
        self.assertEqual(occupancy([], SPACES, T, params)["rule"], params.as_dict())


if __name__ == "__main__":
    unittest.main()
