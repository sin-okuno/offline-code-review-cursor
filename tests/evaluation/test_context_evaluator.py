"""Context coverage, including a file split across review units."""

from __future__ import annotations

import unittest

from offline_review.evaluation.context_evaluator import evaluate_context
from tests.evaluation.test_schemas import truth


EFFECT = """\
export const loadBeaconsEffect = createEffect(
  () => actions$.pipe(
    switchMap(() => api.loadBeacons()),
  ),
);
"""

SERVICE = """\
export class BeaconService {
  loadBeacons(): Observable<Beacon[]> {
    return this.http.get('/api/beacons');
  }
}
"""

LINKED_FORM = """\
export class LedgerComponent {
  constructor(private formBuilder: FormBuilder) {
    this.form = this.formBuilder.group({
      name: ['', Validators.required],
    });
  }

  save(): void {
    if (this.form.invalid) {
      return;
    }
  }
}
"""

UNLINKED_FORM = LINKED_FORM.replace("this.form =", "this.other =")

LINKED_HTML = '<form>\n  <input formControlName="name" required />\n</form>\n'

UNLINKED_HTML = '<form>\n  <input formControlName="title" required />\n</form>\n'


def context_for(
    units: list[dict[str, object]],
    *,
    included: int = 4,
    total: int = 4,
    omitted: int = 9,
    symbols: list[dict[str, object]] | None = None,
    warnings: list[dict[str, str]] | None = None,
) -> dict[str, object]:
    return {
        "angular_context": {
            "changed_line_files": [
                {
                    "changed_lines_included": included,
                    "changed_lines_omitted": total - included,
                    "changed_lines_total": total,
                    "path": "src/app/beacon/beacon.effects.ts",
                    "revision": "head",
                }
            ],
            "changed_symbols": [] if symbols is None else symbols,
            "coverage": {
                "line_budget": 2000,
                "lines_omitted": omitted,
                "lines_selected": 20,
            },
            "review_units": units,
            "warnings": [{"code": "ANGULAR_SEMANTIC_OUT_OF_SCOPE", "message": "partial"}, *(warnings or [])],
        },
        "comparison": {"base_commit": "a" * 40, "head_commit": "b" * 40},
        "warnings": [],
    }


def unit(unit_id: str, excerpts: list[tuple[object, ...]], *, line_count: int = 10) -> dict[str, object]:
    parsed = []
    for item in excerpts:
        path, text = str(item[0]), str(item[1])
        revision = str(item[2]) if len(item) > 2 else ""
        start = int(item[3]) if len(item) > 3 else 0
        end = int(item[4]) if len(item) > 4 else 0
        parsed.append(
            {"content": text, "line_end": end, "line_start": start, "path": path, "revision": revision}
        )
    return {"excerpts": parsed, "line_count": line_count, "unit_id": unit_id}


def effect_symbols() -> list[dict[str, object]]:
    return [
        {
            "line_end": 5,
            "line_start": 1,
            "path": "src/app/beacon/beacon.effects.ts",
            "qualname": "loadBeaconsEffect",
            "revision": revision,
        }
        for revision in ("base", "head")
    ]


class ContextEvaluatorTests(unittest.TestCase):
    def test_changed_lines_and_required_context_are_separate(self) -> None:
        report = evaluate_context(
            context_for(
                [
                    unit(
                        "unit-1",
                        [
                            ("src/app/beacon/beacon.effects.ts", EFFECT, "base", 1, 5),
                            ("src/app/beacon/beacon.effects.ts", EFFECT, "head", 1, 5),
                        ],
                    )
                ],
                symbols=effect_symbols(),
            ),
            truth(),
        )
        self.assertEqual(report["changed_line_coverage"], 1.0)
        self.assertEqual(report["changed_lines_omitted"], 0)
        self.assertEqual(report["context_lines_omitted"], 9)
        self.assertEqual(report["required_context_coverage"], 1.0)
        self.assertTrue(report["case_context_sufficient"])

    def test_heading_identifier_is_not_sufficient(self) -> None:
        report = evaluate_context(
            context_for([unit("unit-1", [("src/app/beacon/beacon.effects.ts", "# loadBeaconsEffect\n")])]),
            truth(),
        )
        self.assertFalse(report["case_context_sufficient"])
        self.assertEqual(report["required_items"][0]["gap"], "heading_only")

    def test_declaration_without_body_is_not_sufficient(self) -> None:
        report = evaluate_context(
            context_for(
                [
                    unit(
                        "unit-1",
                        [("src/app/beacon/beacon.effects.ts", "export const loadBeaconsEffect = createEffect(\n")],
                    )
                ]
            ),
            truth(),
        )
        self.assertFalse(report["case_context_sufficient"])
        self.assertEqual(report["required_items"][0]["gap"], "declaration_without_body")

    def test_same_name_in_another_file_is_not_sufficient(self) -> None:
        report = evaluate_context(
            context_for([unit("unit-1", [("src/app/beacon/beacon.service.ts", EFFECT)])]),
            truth(),
        )
        self.assertIn("loadBeaconsEffect", report["missing_required"])
        self.assertFalse(report["case_context_sufficient"])

    def test_base_excerpt_does_not_cover_a_surviving_head_symbol(self) -> None:
        report = evaluate_context(
            context_for(
                [unit("unit-1", [("src/app/beacon/beacon.effects.ts", EFFECT, "base", 1, 5)])],
                symbols=effect_symbols(),
            ),
            truth(),
        )
        self.assertFalse(report["case_context_sufficient"])
        self.assertEqual(report["required_items"][0]["gap"], "head_missing")

    def test_revision_mismatch_cannot_pass(self) -> None:
        context = context_for(
            [
                unit(
                    "unit-1",
                    [
                        ("src/app/beacon/beacon.effects.ts", EFFECT, "base", 1, 5),
                        ("src/app/beacon/beacon.effects.ts", EFFECT, "head", 1, 5),
                    ],
                )
            ],
            symbols=effect_symbols(),
        )
        context["comparison"] = {"base_commit": "c" * 40, "head_commit": "d" * 40}
        report = evaluate_context(context, truth())
        self.assertEqual(report["required_context_coverage"], 1.0)
        self.assertFalse(report["revision_match"])
        self.assertFalse(report["case_context_sufficient"])

    def test_removed_symbol_uses_the_base_body(self) -> None:
        gone = "export function gone(): number {\n  return 1;\n}\n"
        report = evaluate_context(
            context_for(
                [unit("unit-1", [("src/gone.ts", gone, "base", 1, 3)])],
                symbols=[
                    {
                        "line_end": 3,
                        "line_start": 1,
                        "path": "src/gone.ts",
                        "qualname": "gone",
                        "revision": "base",
                    }
                ],
                warnings=[
                    {
                        "code": "TS_SYMBOL_ABSENT",
                        "message": "gone (function) is not on the head revision. No head excerpt was created for it.",
                        "path": "src/gone.ts",
                    }
                ],
            ),
            truth(
                primary_locations=[
                    {"end_line": 3, "path": "src/gone.ts", "start_line": 1, "symbol": "gone"}
                ],
                required_context=[{"path": "src/gone.ts", "reason": "removed", "symbol": "gone"}],
            ),
        )
        self.assertTrue(report["case_context_sufficient"])
        self.assertEqual(report["required_items"][0]["head_status"], "absent")

    def test_unrelated_validator_is_not_the_saved_form(self) -> None:
        report = evaluate_context(
            context_for(
                [
                    unit(
                        "unit-1",
                        [
                            ("src/app/beacon/beacon.component.ts", UNLINKED_FORM),
                            ("src/app/beacon/beacon.component.html", LINKED_HTML),
                        ],
                    )
                ]
            ),
            truth(
                case_id="ANG-006",
                primary_locations=[
                    {
                        "end_line": 12,
                        "path": "src/app/beacon/beacon.component.ts",
                        "start_line": 10,
                        "symbol": "save",
                    }
                ],
                required_context=[
                    {
                        "path": "src/app/beacon/beacon.component.ts",
                        "reason": "validator",
                        "symbol": "Validators",
                    },
                    {
                        "path": "src/app/beacon/beacon.component.html",
                        "reason": "template",
                        "symbol": "required",
                    },
                ],
            ),
        )
        self.assertFalse(report["case_context_sufficient"])
        self.assertEqual(report["required_items"][0]["gap"], "form_not_linked")

    def test_linked_form_and_template_control_pass(self) -> None:
        report = evaluate_context(
            context_for(
                [
                    unit(
                        "unit-1",
                        [
                            ("src/app/beacon/beacon.component.ts", LINKED_FORM),
                            ("src/app/beacon/beacon.component.html", LINKED_HTML),
                        ],
                    )
                ]
            ),
            truth(
                case_id="ANG-006",
                primary_locations=[
                    {
                        "end_line": 12,
                        "path": "src/app/beacon/beacon.component.ts",
                        "start_line": 10,
                        "symbol": "save",
                    }
                ],
                required_context=[
                    {
                        "path": "src/app/beacon/beacon.component.ts",
                        "reason": "validator",
                        "symbol": "Validators",
                    },
                    {
                        "path": "src/app/beacon/beacon.component.html",
                        "reason": "template",
                        "symbol": "required",
                    },
                ],
            ),
        )
        self.assertTrue(report["case_context_sufficient"])
        self.assertEqual(report["missing_required"], [])

    def test_unrelated_template_control_is_not_sufficient(self) -> None:
        report = evaluate_context(
            context_for(
                [
                    unit(
                        "unit-1",
                        [
                            ("src/app/beacon/beacon.component.ts", LINKED_FORM),
                            ("src/app/beacon/beacon.component.html", UNLINKED_HTML),
                        ],
                    )
                ]
            ),
            truth(
                case_id="ANG-006",
                primary_locations=[
                    {
                        "end_line": 12,
                        "path": "src/app/beacon/beacon.component.ts",
                        "start_line": 10,
                        "symbol": "save",
                    }
                ],
                required_context=[
                    {
                        "path": "src/app/beacon/beacon.component.ts",
                        "reason": "validator",
                        "symbol": "Validators",
                    },
                    {
                        "path": "src/app/beacon/beacon.component.html",
                        "reason": "template",
                        "symbol": "required",
                    },
                ],
            ),
        )
        self.assertFalse(report["case_context_sufficient"])
        self.assertEqual(report["required_items"][1]["gap"], "form_not_linked")

    def test_a_missing_related_file_is_extractor_gap(self) -> None:
        report = evaluate_context(
            context_for(
                [
                    unit(
                        "unit-1",
                        [("src/app/beacon/beacon.service.ts", "loadBeacons() {")],
                    )
                ]
            ),
            truth(),
        )
        self.assertFalse(report["case_context_sufficient"])
        self.assertFalse(report["combined_units_sufficient"])
        self.assertIn("loadBeaconsEffect", report["missing_required"])
        self.assertFalse(report["cross_unit_dependency"])

    def test_required_symbols_split_across_units_are_cross_unit(self) -> None:
        report = evaluate_context(
            context_for(
                [
                    unit("unit-1", [("src/app/beacon/beacon.effects.ts", EFFECT)]),
                    unit("unit-2", [("src/app/beacon/beacon.service.ts", SERVICE)]),
                ]
            ),
            truth(
                required_context=[
                    {
                        "path": "src/app/beacon/beacon.effects.ts",
                        "reason": "effect",
                        "symbol": "loadBeaconsEffect",
                    },
                    {
                        "path": "src/app/beacon/beacon.service.ts",
                        "reason": "service",
                        "symbol": "loadBeacons",
                    },
                ]
            ),
        )
        self.assertTrue(report["combined_units_sufficient"])
        self.assertTrue(report["cross_unit_dependency"])
        self.assertFalse(report["case_context_sufficient"])
        self.assertEqual(report["split_reason"], "across_units")

    def test_more_than_three_required_files_cannot_be_one_input(self) -> None:
        required = [
            {"path": f"src/app/part-{index}.ts", "reason": "part", "symbol": f"part{index}"}
            for index in range(4)
        ]
        report = evaluate_context(
            context_for(
                [
                    unit(
                        "unit-1",
                        [(item["path"], f"export const {item['symbol']} = (state) => state.{item['symbol']};") for item in required],
                    )
                ]
            ),
            truth(required_context=required),
        )
        self.assertEqual(report["split_reason"], "file_limit")
        self.assertFalse(report["case_context_sufficient"])


if __name__ == "__main__":
    unittest.main()
