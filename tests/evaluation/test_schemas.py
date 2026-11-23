"""Ground-truth and findings schema checks."""

from __future__ import annotations

import unittest

from offline_review.evaluation.schemas import validate_findings, validate_ground_truth
from offline_review.models import UsageError


def truth(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "ambiguous": False,
        "base_revision": "a" * 40,
        "buggy_revision": "b" * 40,
        "case_id": "ANG-001",
        "case_kind": "defect",
        "expected_behavior": "Failure action is emitted",
        "expected_finding": "API failure path does not emit a failure action",
        "match_terms": ["failure action", "api failure"],
        "primary_locations": [
            {
                "end_line": 4,
                "path": "src/app/beacon/beacon.effects.ts",
                "start_line": 2,
                "symbol": "loadBeaconsEffect",
            }
        ],
        "reproduction": "Compare the load effect with the baseline.",
        "required_context": [
            {
                "path": "src/app/beacon/beacon.effects.ts",
                "reason": "failure path",
                "symbol": "loadBeaconsEffect",
            }
        ],
        "schema_version": "1.0",
        "severity": "high",
        "tags": ["angular"],
        "title": "Load effect drops API failures",
    }
    value.update(overrides)
    return value


class SchemaTests(unittest.TestCase):
    def test_missing_required_fields_are_rejected(self) -> None:
        broken = truth()
        del broken["expected_finding"]
        with self.assertRaises(UsageError):
            validate_ground_truth(broken)

    def test_parent_path_and_bad_line_numbers_are_rejected(self) -> None:
        with self.assertRaises(UsageError):
            validate_ground_truth(
                truth(
                    primary_locations=[
                        {
                            "end_line": 2,
                            "path": "../secret.ts",
                            "start_line": 1,
                            "symbol": "load",
                        }
                    ]
                )
            )
        with self.assertRaises(UsageError):
            validate_ground_truth(
                truth(
                    primary_locations=[
                        {
                            "end_line": 1,
                            "path": "src/app/beacon/beacon.effects.ts",
                            "start_line": 3,
                            "symbol": "load",
                        }
                    ]
                )
            )

    def test_clean_case_cannot_carry_match_phrases(self) -> None:
        with self.assertRaises(UsageError):
            validate_ground_truth(
                truth(
                    case_id="CLEAN-001",
                    case_kind="clean",
                    primary_locations=[],
                    required_context=[],
                )
            )

    def test_finding_line_must_be_positive(self) -> None:
        with self.assertRaises(UsageError):
            validate_findings(
                {
                    "case_id": "ANG-001",
                    "findings": [
                        {
                            "description": "detail",
                            "evidence": "line",
                            "finding_id": "F-001",
                            "line": 0,
                            "path": "src/app/beacon/beacon.effects.ts",
                            "severity": "high",
                            "suggestion": "emit the failure",
                            "title": "missing failure",
                        }
                    ],
                    "review_unit_id": "unit-1",
                    "schema_version": "1.0",
                }
            )


if __name__ == "__main__":
    unittest.main()
