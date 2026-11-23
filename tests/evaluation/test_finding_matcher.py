"""True positives, duplicates, manual review, and missing AI results."""

from __future__ import annotations

import unittest

from offline_review.evaluation.finding_matcher import match_findings
from tests.evaluation.test_schemas import truth


def finding(finding_id: str, **overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "description": "The API failure does not emit a failure action.",
        "evidence": "catchError is gone",
        "finding_id": finding_id,
        "line": 3,
        "path": "src/app/beacon/beacon.effects.ts",
        "severity": "high",
        "suggestion": "emit the failure action",
        "title": "API failure is dropped",
    }
    value.update(overrides)
    return value


def findings(*rows: dict[str, object]) -> dict[str, object]:
    return {
        "case_id": "ANG-001",
        "findings": list(rows),
        "review_unit_id": "unit-1",
        "schema_version": "1.0",
    }


class FindingMatcherTests(unittest.TestCase):
    def test_known_finding_is_one_true_positive(self) -> None:
        report = match_findings(findings(finding("F-001"), finding("F-002")), truth())
        self.assertEqual(report["status"], "true_positive")
        self.assertEqual(report["true_positive_finding_ids"], ["F-001"])
        self.assertEqual(report["duplicate_finding_ids"], ["F-002"])
        self.assertEqual(report["false_negative"], 0)

    def test_unrelated_finding_is_a_false_positive_and_a_miss(self) -> None:
        report = match_findings(
            findings(
                finding(
                    "F-009",
                    path="src/app/beacon/beacon.models.ts",
                    line=1,
                    title="Rename a heading",
                    description="The title changed.",
                    evidence="text",
                )
            ),
            truth(),
        )
        self.assertEqual(report["status"], "false_negative")
        self.assertEqual(report["false_positive_finding_ids"], ["F-009"])

    def test_one_phrase_is_not_an_automatic_true_positive(self) -> None:
        report = match_findings(
            findings(finding("F-003", title="failure action", description="Something changed.", evidence="diff")),
            truth(),
        )
        self.assertEqual(report["status"], "needs_manual_review")
        self.assertEqual(report["false_negative"], 0)
        self.assertEqual(report["true_positive_finding_ids"], [])

    def test_missing_findings_are_not_a_false_negative(self) -> None:
        report = match_findings(None, truth())
        self.assertEqual(report["status"], "not_evaluated")
        self.assertEqual(report["miss_cause"], "not_evaluated")
        self.assertEqual(report["false_negative"], 0)

    def test_sufficient_context_without_a_hit_is_an_ai_miss(self) -> None:
        report = match_findings(
            findings(),
            truth(),
            {"case_id": "ANG-001", "case_context_sufficient": True, "revision_match": True},
        )
        self.assertEqual(report["status"], "false_negative")
        self.assertEqual(report["miss_cause"], "ai_missed_with_sufficient_context")

    def test_split_context_is_not_blamed_on_the_model(self) -> None:
        report = match_findings(
            findings(),
            truth(),
            {
                "case_id": "ANG-001",
                "case_context_sufficient": False,
                "combined_units_sufficient": True,
                "cross_unit_dependency": True,
                "revision_match": True,
            },
        )
        self.assertEqual(report["miss_cause"], "cross_unit_context_gap")

    def test_missing_context_is_an_extractor_miss(self) -> None:
        report = match_findings(
            findings(),
            truth(),
            {
                "case_id": "ANG-001",
                "case_context_sufficient": False,
                "combined_units_sufficient": False,
                "cross_unit_dependency": False,
                "revision_match": True,
            },
        )
        self.assertEqual(report["miss_cause"], "extractor_missing_context")

    def test_clean_case_findings_are_false_positives(self) -> None:
        clean = truth(
            case_id="CLEAN-001",
            case_kind="clean",
            match_terms=[],
            primary_locations=[],
            required_context=[],
        )
        report = match_findings(
            {
                "case_id": "CLEAN-001",
                "findings": [finding("F-010")],
                "review_unit_id": "unit-1",
                "schema_version": "1.0",
            },
            clean,
        )
        self.assertEqual(report["status"], "false_positive")
        self.assertEqual(report["false_positive_finding_ids"], ["F-010"])


if __name__ == "__main__":
    unittest.main()
