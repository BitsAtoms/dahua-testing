from __future__ import annotations

from pathlib import Path
import sys
import unittest

import numpy as np


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT_ROOT = REPOSITORY_ROOT / "experiments" / "visual-reid"
sys.path.insert(0, str(EXPERIMENT_ROOT))

from visual_reid.templates import (  # noqa: E402
    TemplateObservation,
    build_template,
    compare_templates,
)


class TemplateTests(unittest.TestCase):
    def test_rejects_within_track_outlier_and_builds_stable_template(self) -> None:
        observations = [
            TemplateObservation("a", 1, np.array([1.0, 0.0]), 0.9),
            TemplateObservation("b", 2, np.array([0.98, 0.02]), 0.8),
            TemplateObservation("outlier", 3, np.array([0.0, 1.0]), 0.7),
        ]

        template = build_template(observations)

        self.assertEqual(template.observation_ids, ("a", "b"))
        self.assertEqual(template.rejected_ids, ("outlier",))
        self.assertLess(template.dispersion, 0.01)

    def test_comparison_reports_sample_and_consistency_reliability(self) -> None:
        left = build_template(
            [
                TemplateObservation("a", 1, np.array([1.0, 0.0]), 1.0),
                TemplateObservation("b", 2, np.array([0.99, 0.01]), 1.0),
            ]
        )
        right = build_template(
            [
                TemplateObservation("c", 3, np.array([0.98, 0.02]), 1.0),
                TemplateObservation("d", 4, np.array([1.0, 0.0]), 1.0),
            ]
        )

        result = compare_templates(left, right)

        self.assertGreater(result.similarity, 0.99)
        self.assertGreater(result.reliability, 0.65)


if __name__ == "__main__":
    unittest.main()
