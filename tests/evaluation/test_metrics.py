"""Aggregate scores use N/A when a denominator is zero."""

from __future__ import annotations

import unittest

from offline_review.evaluation.metrics import summarize


class MetricsTests(unittest.TestCase):
    def test_empty_inputs_are_not_zero(self) -> None:
        report = summarize([])
        self.assertEqual(report["recall"], "N/A")
        self.assertEqual(report["precision"], "N/A")
        self.assertEqual(report["f1"], "N/A")
        self.assertEqual(report["changed_line_coverage"], "N/A")
        self.assertEqual(report["context_sufficiency_rate"], "N/A")
        self.assertEqual(report["false_positives_per_clean_case"], "N/A")
        self.assertFalse(report["operational_proof"])

    def test_clean_false_positives_are_averaged_per_case(self) -> None:
        report = summarize(
            [
                {
                    "case_kind": "clean",
                    "changed_lines_included": 1,
                    "changed_lines_total": 1,
                    "context_lines_omitted": 0,
                    "revision_match": True,
                }
            ],
            [
                {
                    "case_kind": "clean",
                    "false_negative": 0,
                    "false_positive_finding_ids": ["F-1", "F-2"],
                    "status": "false_positive",
                }
            ],
        )
        self.assertEqual(report["false_positives_per_clean_case"], 2.0)
        self.assertEqual(report["precision"], 0.0)


if __name__ == "__main__":
    unittest.main()
