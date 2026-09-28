from __future__ import annotations

from pathlib import Path
import sys
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT_ROOT = REPOSITORY_ROOT / "experiments" / "visual-reid"
sys.path.insert(0, str(EXPERIMENT_ROOT))

from visual_reid.sequence_assignment import (  # noqa: E402
    AssignmentState,
    HandoffOption,
    resolve_sequences,
)


def option(
    identifier,
    origin,
    destination,
    score,
    coverage,
    observed,
    origin_first=None,
    destination_first=None,
):
    return HandoffOption(
        identifier,
        origin,
        destination,
        score,
        coverage,
        observed,
        origin_first,
        destination_first,
    )


class SequenceAssignmentTests(unittest.TestCase):
    def test_selects_full_path_and_rejects_direct_shortcut(self) -> None:
        resolution = resolve_sequences(
            [
                option("ab", "a", "b", 0.75, 1.0, 1),
                option("bc", "b", "c", 0.72, 1.0, 2),
                option("ac", "a", "c", 0.60, 1.0, 2),
                option("stale", "stale", "c", 0.30, 0.0, 2),
            ]
        )

        self.assertEqual(resolution.sequences, (("a", "b", "c"),))
        self.assertEqual(resolution.confirmed_sequences, (("a", "b", "c"),))
        states = {item.option.candidate_id: item.state for item in resolution.handoffs}
        self.assertEqual(states["ab"], AssignmentState.CONFIRMED)
        self.assertEqual(states["bc"], AssignmentState.CONFIRMED)
        self.assertEqual(states["ac"], AssignmentState.REJECTED)
        self.assertEqual(states["stale"], AssignmentState.REJECTED)

    def test_keeps_close_competitor_pending(self) -> None:
        resolution = resolve_sequences(
            [
                option("ab", "a", "b", 0.62, 0.8, 1),
                option("ac", "a", "c", 0.60, 0.8, 1),
            ]
        )

        selected = next(
            item for item in resolution.handoffs if item.state != AssignmentState.REJECTED
        )
        self.assertEqual(selected.option.candidate_id, "ab")
        self.assertEqual(selected.state, AssignmentState.PENDING)
        self.assertEqual(selected.reason, "needs_confirmation")
        self.assertEqual(resolution.confirmed_sequences, ())

    def test_rejects_cycle(self) -> None:
        resolution = resolve_sequences(
            [
                option("ab", "a", "b", 0.8, 1.0, 1),
                option("ba", "b", "a", 0.7, 1.0, 2),
            ]
        )

        selected = [
            item for item in resolution.handoffs if item.state != AssignmentState.REJECTED
        ]
        self.assertEqual(len(selected), 1)

    def test_reciprocal_overlap_follows_first_observation_chronology(self) -> None:
        resolution = resolve_sequences(
            [
                option("later-to-earlier", "b", "a", 0.70, 1.0, 3, 20, 10),
                option("earlier-to-later", "a", "b", 0.70, 1.0, 3, 10, 20),
            ]
        )

        assignments = {
            item.option.candidate_id: item for item in resolution.handoffs
        }
        self.assertEqual(resolution.sequences, (("a", "b"),))
        self.assertEqual(
            assignments["later-to-earlier"].state,
            AssignmentState.REJECTED,
        )
        self.assertEqual(
            assignments["later-to-earlier"].reason,
            "reverse_chronology",
        )

    def test_unidirectional_overlap_is_not_rejected_by_chronology(self) -> None:
        resolution = resolve_sequences(
            [option("only", "b", "a", 0.70, 1.0, 3, 20, 10)]
        )

        selected = next(
            item for item in resolution.handoffs if item.state != AssignmentState.REJECTED
        )
        self.assertEqual(selected.option.candidate_id, "only")

    def test_marginal_two_hop_chain_does_not_displace_strong_direct_edge(self) -> None:
        resolution = resolve_sequences(
            [
                option("direct", "a", "c", 0.765, 1.0, 3),
                option("weak-ab", "a", "b", 0.543, 0.615, 1),
                option("weak-bc", "b", "c", 0.581, 0.615, 2),
            ]
        )

        selected = {
            item.option.candidate_id
            for item in resolution.handoffs
            if item.state != AssignmentState.REJECTED
        }
        self.assertEqual(selected, {"direct"})


if __name__ == "__main__":
    unittest.main()
