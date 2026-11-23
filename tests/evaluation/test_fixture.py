"""The synthetic beacon ledger has ten defects, two clean edits, and valid commits."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from offline_review.context_builder import build_context
from offline_review.evaluation.context_evaluator import evaluate_context
from offline_review.evaluation.fixture_builder import build_fixture
from offline_review.evaluation.harbor_fixture import BASE_FILES, CASES
from offline_review.git_adapter import GitAdapter
from offline_review.typescript_index import scan_typescript

ROOT = Path(__file__).resolve().parents[2]
COMMITTED_CASES = ROOT / "fixtures" / "phase16" / "cases"


def _evaluation_payload(context: object) -> dict[str, object]:
    angular = context.angular_context
    return {
        "angular_context": {
            "changed_line_files": [
                {
                    "changed_lines_included": item.changed_lines_included,
                    "changed_lines_omitted": item.changed_lines_omitted,
                    "changed_lines_total": item.changed_lines_total,
                }
                for item in angular.changed_line_files
            ],
            "coverage": {
                "line_budget": angular.coverage.line_budget,
                "lines_omitted": angular.coverage.lines_omitted,
                "lines_selected": angular.coverage.lines_selected,
            },
            "changed_symbols": [
                {
                    "kind": item.kind,
                    "line_end": item.line_end,
                    "line_start": item.line_start,
                    "path": item.path,
                    "qualname": item.qualname,
                    "revision": item.revision,
                }
                for item in angular.changed_symbols
            ],
            "review_units": [
                {
                    "excerpts": [
                        {
                            "content": item.content,
                            "line_end": item.line_end,
                            "line_start": item.line_start,
                            "path": item.path,
                            "revision": item.revision,
                        }
                        for item in unit.excerpts
                    ],
                    "line_count": unit.line_count,
                    "unit_id": unit.unit_id,
                }
                for unit in angular.review_units
            ],
            "warnings": [
                {"code": item.code, "message": item.message, "path": item.path} for item in angular.warnings
            ],
        },
        "comparison": {
            "base_commit": context.comparison.base_commit,
            "head_commit": context.comparison.head_commit,
        },
        "warnings": [{"code": item.code} for item in context.warnings],
    }


class FixtureTests(unittest.TestCase):
    def test_rebuild_matches_committed_ground_truth(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / "repo"
            cases = Path(temp) / "cases"
            build_fixture(repo, cases)
            ids = [item.case_id for item in CASES]
            self.assertEqual(sum(item.case_kind == "defect" for item in CASES), 10)
            self.assertGreaterEqual(sum(item.case_kind == "clean" for item in CASES), 2)
            with GitAdapter(repo) as adapter:
                for case_id in ids:
                    fresh = json.loads((cases / f"{case_id}.json").read_text(encoding="utf-8"))
                    committed = json.loads((COMMITTED_CASES / f"{case_id}.json").read_text(encoding="utf-8"))
                    self.assertEqual(fresh, committed)
                    self.assertEqual(adapter.resolve_commit(fresh["base_revision"]), fresh["base_revision"])
                    self.assertEqual(adapter.resolve_commit(fresh["buggy_revision"]), fresh["buggy_revision"])
                    self._assert_only_the_intended_edit(adapter, fresh, case_id)

    def test_generated_context_has_no_answer_labels(self) -> None:
        case = json.loads((COMMITTED_CASES / "ANG-001.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / "repo"
            build_fixture(repo, Path(temp) / "cases")
            with GitAdapter(repo) as adapter:
                context = build_context(
                    adapter,
                    repository="beacon-ledger",
                    base=case["base_revision"],
                    head=case["buggy_revision"],
                    comparison="direct",
                )
            rendered = "\n".join(
                excerpt.content
                for unit in context.angular_context.review_units
                for excerpt in unit.excerpts
            )
            for label in ("ANG-001", "buggy", "match_terms", "expected_finding"):
                self.assertNotIn(label, rendered)
            effects = [
                item
                for unit in context.angular_context.review_units
                for item in unit.excerpts
                if item.path == "src/app/beacon/beacon.effects.ts" and item.reason == "changed file"
            ]
            by_revision = {item.revision: item for item in effects}
            self.assertEqual(set(by_revision), {"base", "head"})
            self.assertGreater(by_revision["head"].line_start, 0)
            self.assertIn("export const loadBeaconsEffect", by_revision["head"].content)
            self.assertIn("loadBeaconsSuccess", by_revision["head"].content)
            self.assertNotIn("loadBeaconsFailure", by_revision["head"].content)
            self.assertIn("loadBeaconsFailure", by_revision["base"].content)
            head_symbols = [
                item
                for item in context.angular_context.changed_symbols
                if item.revision == "head" and item.qualname == "loadBeaconsEffect"
            ]
            self.assertEqual(len(head_symbols), 1)
            self.assertGreater(head_symbols[0].line_start, 0)
            self.assertGreaterEqual(head_symbols[0].line_end, head_symbols[0].line_start)
            report = evaluate_context(_evaluation_payload(context), case)
        self.assertEqual(report["changed_lines_omitted"], 0)
        self.assertEqual(report["changed_line_coverage"], 1.0)
        self.assertEqual(report["required_context_coverage"], 1.0)
        self.assertTrue(report["case_context_sufficient"])
        self.assertIn("context_lines_omitted", report)

    def test_every_defect_keeps_its_required_symbols(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / "repo"
            cases = Path(temp) / "cases"
            build_fixture(repo, cases)
            with GitAdapter(repo) as adapter:
                for path in sorted(cases.glob("ANG-*.json")):
                    truth = json.loads(path.read_text(encoding="utf-8"))
                    context = build_context(
                        adapter,
                        repository="beacon-ledger",
                        base=truth["base_revision"],
                        head=truth["buggy_revision"],
                        comparison="direct",
                    )
                    report = evaluate_context(_evaluation_payload(context), truth)
                    self.assertEqual(report["changed_lines_omitted"], 0, truth["case_id"])
                    self.assertEqual(report["missing_required"], [], truth["case_id"])
                    self.assertTrue(report["case_context_sufficient"], truth["case_id"])

    def _assert_only_the_intended_edit(self, adapter: GitAdapter, document: dict[str, object], case_id: str) -> None:
        spec = next(item for item in CASES if item.case_id == case_id)
        base_blob = adapter.read_bytes(str(document["base_revision"]), spec.path)
        head_blob = adapter.read_bytes(str(document["buggy_revision"]), spec.path)
        self.assertIsNotNone(base_blob)
        self.assertIsNotNone(head_blob)
        assert base_blob is not None and head_blob is not None
        base_text = base_blob.decode()
        head_text = head_blob.decode()
        self.assertIn(spec.old, base_text)
        self.assertNotIn(spec.old, head_text)
        if spec.new:
            self.assertIn(spec.new, head_text)
        indexed = scan_typescript(head_text.encode())
        self.assertEqual(indexed.status, "parsed")
        for path, text in BASE_FILES.items():
            if not path.endswith(".ts"):
                continue
            blob = adapter.read_bytes(str(document["buggy_revision"]), path)
            self.assertIsNotNone(blob)
            assert blob is not None
            self.assertEqual(scan_typescript(blob).status, "parsed")
            self.assertNotIn("ANG-", blob.decode())
            if path != spec.path:
                self.assertEqual(blob.decode(), text)
