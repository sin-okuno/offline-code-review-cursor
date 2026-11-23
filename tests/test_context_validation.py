"""Golden-set checks for Angular review context omissions."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from offline_review.cli import main
from offline_review.context_validator import load_json_object, validate_context
from offline_review.models import UsageError

BASE = "b" * 40
HEAD = "c" * 40
SERVICE = "src/app/product-tree/api/product-api.service.ts"
EFFECT = "src/app/product-tree/state/product-tree.effects.ts"
FIXTURE = Path(__file__).resolve().parents[0] / "fixtures" / "angular-context-golden.json"


def _excerpt(revision: str, path: str, start: int, end: int) -> dict[str, object]:
    return {
        "revision": revision,
        "path": path,
        "line_start": start,
        "line_end": end,
        "content": "x",
    }


def _context(
    selected: list[dict[str, object]],
    *,
    omitted: list[dict[str, object]] | None = None,
    unresolved: list[dict[str, object]] | None = None,
    edges: list[dict[str, object]] | None = None,
    files: list[dict[str, object]] | None = None,
    base: str = BASE,
    head: str = HEAD,
) -> dict[str, object]:
    return {
        "repository": r"C:\work\00_src\ai-driven\tree-sample",
        "comparison": {"base_commit": base, "head_commit": head, "mode": "direct"},
        "files": files or [],
        "angular_context": {
            "selected": selected,
            "omitted": omitted or [],
            "unresolved": unresolved or [],
            "edges": edges or [],
        },
    }


def _golden() -> dict[str, object]:
    return {
        "repository": "tree-sample",
        "base_commit": BASE,
        "head_commit": HEAD,
        "comparison": "direct",
        "items": [
            {
                "id": "api-signature",
                "path": SERVICE,
                "symbol": "ProductApiService.getProductTree",
                "criticality": "critical",
                "why_required": "Both return types are required.",
                "evidence": [{"revision": "base", "line": 15}, {"revision": "head", "line": 15}],
                "ranges": [
                    {"revision": "base", "line_start": 15, "line_end": 16},
                    {"revision": "head", "line_start": 15, "line_end": 16},
                ],
            },
            {
                "id": "effect-call",
                "path": EFFECT,
                "symbol": "ProductTreeEffects.loadTree$",
                "criticality": "critical",
                "why_required": "The call and its map must both be present.",
                "evidence": [{"revision": "head", "line": 35}],
                "ranges": [
                    {"revision": "base", "line_start": 20, "line_end": 21},
                    {"revision": "head", "line_start": 35, "line_end": 36},
                ],
            },
        ],
        "unresolved_candidates": [
            {
                "id": "risk-detail-module",
                "path": "src/app/product-tree/product-tree.module.ts",
                "symbol": "RiskDetailComponent",
            }
        ],
    }


class ValidatorTests(unittest.TestCase):
    def test_contract_ranges_on_both_revisions_pass(self) -> None:
        report = validate_context(
            _context(
                [
                    _excerpt("base", SERVICE, 15, 16),
                    _excerpt("head", SERVICE, 15, 16),
                    _excerpt("base", EFFECT, 20, 21),
                    _excerpt("head", EFFECT, 35, 36),
                ]
            ),
            _golden(),
        )
        self.assertTrue(report["passed"])
        self.assertEqual(report["metrics"]["critical_recall"]["hits"], 2)
        self.assertEqual(report["metrics"]["high_recall"], "not_applicable")

    def test_selected_file_without_the_required_line_is_partial(self) -> None:
        report = validate_context(
            _context(
                [
                    _excerpt("base", SERVICE, 15, 16),
                    _excerpt("head", SERVICE, 15, 15),
                    _excerpt("base", EFFECT, 20, 21),
                    _excerpt("head", EFFECT, 36, 36),
                ]
            ),
            _golden(),
        )
        self.assertFalse(report["passed"])
        by_id = {item["id"]: item for item in report["items"]}
        self.assertEqual(by_id["api-signature"]["status"], "partial")
        self.assertEqual(by_id["effect-call"]["status"], "partial")
        self.assertEqual(by_id["effect-call"]["cause"], "range_too_narrow")
        self.assertIn(35, by_id["effect-call"]["missing_lines"][0]["lines"])

    def test_one_revision_does_not_satisfy_a_contract(self) -> None:
        report = validate_context(
            _context(
                [
                    _excerpt("base", SERVICE, 15, 16),
                    _excerpt("base", EFFECT, 20, 21),
                ]
            ),
            _golden(),
        )
        self.assertFalse(report["passed"])
        self.assertTrue(all(item["status"] == "revision_mismatch" for item in report["items"]))

    def test_budget_omission_is_named(self) -> None:
        report = validate_context(
            _context(
                [_excerpt("base", SERVICE, 15, 16), _excerpt("base", EFFECT, 20, 21)],
                omitted=[
                    {"revision": "head", "path": SERVICE, "reason": "max_total_lines"},
                    {"revision": "head", "path": EFFECT, "reason": "max_total_lines"},
                ],
            ),
            _golden(),
        )
        self.assertEqual(report["metrics"]["budget_omission_count"], 2)
        self.assertTrue(all(item["status"] == "omitted_by_budget" for item in report["items"]))

    def test_missing_component_import_is_not_treated_as_resolved(self) -> None:
        unresolved = validate_context(
            _context(
                [
                    _excerpt("base", SERVICE, 15, 16),
                    _excerpt("head", SERVICE, 15, 16),
                    _excerpt("base", EFFECT, 20, 21),
                    _excerpt("head", EFFECT, 35, 36),
                ],
                unresolved=[
                    {
                        "from_path": "src/app/product-tree/product-tree.module.ts",
                        "to_symbol": "RiskDetailComponent",
                        "evidence": "import { RiskDetailComponent } from './risk-detail/risk-detail.component';",
                        "confidence": "unresolved",
                        "line": 11,
                    }
                ],
            ),
            _golden(),
        )
        self.assertEqual(unresolved["candidates"][0]["status"], "still_unresolved")
        self.assertTrue(unresolved["passed"])
        confirmed = validate_context(
            _context(
                [
                    _excerpt("base", SERVICE, 15, 16),
                    _excerpt("head", SERVICE, 15, 16),
                    _excerpt("base", EFFECT, 20, 21),
                    _excerpt("head", EFFECT, 35, 36),
                ],
                edges=[
                    {
                        "from_path": "src/app/product-tree/product-tree.module.ts",
                        "to_symbol": "RiskDetailComponent",
                        "confidence": "confirmed",
                        "evidence": "RiskDetailComponent",
                    }
                ],
            ),
            _golden(),
        )
        self.assertEqual(confirmed["candidates"][0]["status"], "incorrectly_confirmed")
        self.assertFalse(confirmed["passed"])

    def test_commit_mismatch_fails(self) -> None:
        report = validate_context(_context([_excerpt("base", SERVICE, 15, 16)], head="d" * 40), _golden())
        self.assertFalse(report["identity"]["matched"])
        self.assertFalse(report["passed"])

    def test_duplicate_lines_are_counted_once(self) -> None:
        report = validate_context(
            _context(
                [
                    _excerpt("base", SERVICE, 15, 16),
                    _excerpt("base", SERVICE, 15, 16),
                    _excerpt("head", SERVICE, 15, 16),
                    _excerpt("base", EFFECT, 20, 21),
                    _excerpt("head", EFFECT, 35, 36),
                ],
                files=[
                    {"old_path": SERVICE, "new_path": SERVICE, "old_changed_lines": [15, 15], "new_changed_lines": [16]}
                ],
            ),
            _golden(),
        )
        self.assertEqual(report["metrics"]["changed_line_coverage"]["base"]["total"], 1)
        self.assertEqual(report["metrics"]["changed_line_coverage"]["base"]["covered"], 1)
        self.assertEqual(report["metrics"]["context_efficiency"]["selected"], 8)

    def test_empty_golden_is_not_success(self) -> None:
        golden = _golden()
        golden["items"] = []
        golden["unresolved_candidates"] = []
        report = validate_context(_context([]), golden)
        self.assertEqual(report["metrics"]["critical_recall"], "not_applicable")
        self.assertEqual(report["metrics"]["context_efficiency"], "not_applicable")
        self.assertFalse(report["passed"])

    def test_unknown_criticality_and_broken_excerpt_are_usage_errors(self) -> None:
        golden = _golden()
        golden["items"][0]["criticality"] = "urgent"
        with self.assertRaises(UsageError):
            validate_context(_context([]), golden)
        broken = _context([{"revision": "base", "path": SERVICE}])
        with self.assertRaises(UsageError):
            validate_context(broken, _golden())

    def test_fixture_lists_the_contract_and_the_missing_component(self) -> None:
        golden = load_json_object(FIXTURE)
        critical = [item["id"] for item in golden["items"] if item["criticality"] == "critical"]
        self.assertEqual(
            critical,
            [
                "api-signature",
                "effect-call",
                "success-action",
                "reducer-state",
                "reducer-success",
                "selector",
                "view-read",
                "mock-tree",
            ],
        )
        self.assertIn("RiskDetailComponent", {item["symbol"] for item in golden["unresolved_candidates"]})

    def test_cli_writes_a_report_without_replacing_the_context(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            context = root / "review-context.json"
            original = _context(
                [
                    _excerpt("base", SERVICE, 15, 16),
                    _excerpt("head", SERVICE, 1, 10),
                    _excerpt("base", EFFECT, 20, 21),
                    _excerpt("head", EFFECT, 35, 36),
                ]
            )
            context.write_text(json.dumps(original), encoding="utf-8")
            golden = root / "golden.json"
            golden.write_text(json.dumps(_golden()), encoding="utf-8")
            output = root / "context-validation.json"
            code = main(
                [
                    "validate-context",
                    "--context",
                    str(context),
                    "--golden",
                    str(golden),
                    "--output",
                    str(output),
                ]
            )
            self.assertEqual(code, 1)
            self.assertEqual(json.loads(context.read_text(encoding="utf-8")), original)
            written = json.loads(output.read_text(encoding="utf-8"))
            self.assertFalse(written["passed"])
            self.assertTrue((root / "context-validation.md").exists())
            self.assertEqual(main(["validate-context", "--context", str(root / "missing.json"), "--golden", str(golden), "--output", str(output)]), 2)
            broken = root / "broken.json"
            broken.write_text("{", encoding="utf-8")
            self.assertEqual(
                main(["validate-context", "--context", str(broken), "--golden", str(golden), "--output", str(output)]),
                2,
            )
            self.assertEqual(
                main(["validate-context", "--context", str(context), "--golden", str(golden), "--output", str(context)]),
                2,
            )
