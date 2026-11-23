"""Aggregate case results. A zero denominator is N/A, not zero."""

from __future__ import annotations

from offline_review.models import UsageError

DEFAULT_GOALS = {
    "changed_line_coverage": 1.0,
    "context_sufficiency": 0.9,
    "precision": 0.8,
    "recall": 0.8,
}


def summarize(
    context_rows: list[dict[str, object]],
    finding_rows: list[dict[str, object]] | None = None,
    goals: dict[str, float] | None = None,
) -> dict[str, object]:
    recorded = dict(DEFAULT_GOALS if goals is None else {**DEFAULT_GOALS, **goals})
    for name, value in recorded.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= float(value) <= 1:
            raise UsageError(f"goal {name} must be between 0 and 1")
        recorded[name] = float(value)
    changed_total = sum(int(row["changed_lines_total"]) for row in context_rows)
    changed_included = sum(int(row["changed_lines_included"]) for row in context_rows)
    defect_context = [row for row in context_rows if row.get("case_kind") == "defect" and row.get("revision_match") is True]
    sufficient = sum(1 for row in defect_context if row.get("case_context_sufficient") is True)
    findings = finding_rows or []
    decided = [row for row in findings if row.get("status") not in {"not_evaluated", "needs_manual_review"}]
    true_positive = sum(1 for row in decided if row.get("status") == "true_positive")
    false_negative = sum(int(row.get("false_negative", 0)) for row in decided if row.get("case_kind") == "defect")
    false_positive_cases = [row for row in decided if row.get("false_positive_finding_ids")]
    false_positive = sum(len(row["false_positive_finding_ids"]) for row in false_positive_cases)
    clean_evaluated = [
        row
        for row in findings
        if row.get("case_kind") == "clean" and row.get("status") not in {"not_evaluated", "needs_manual_review"}
    ]
    clean_false_positive = sum(len(row.get("false_positive_finding_ids", ())) for row in clean_evaluated)
    recall = _ratio(true_positive, true_positive + false_negative)
    precision = _ratio(true_positive, true_positive + false_positive)
    return {
        "changed_line_coverage": _ratio(changed_included, changed_total),
        "context_lines_omitted": sum(int(row.get("context_lines_omitted", 0)) for row in context_rows),
        "context_sufficiency_rate": _ratio(sufficient, len(defect_context)),
        "cross_unit_cases": sum(1 for row in context_rows if row.get("cross_unit_dependency") is True),
        "f1": _f1(precision, recall),
        "false_negatives": false_negative,
        "false_positives": false_positive,
        "false_positives_per_clean_case": _ratio(clean_false_positive, len(clean_evaluated)),
        "goals": recorded,
        "operational_proof": False,
        "precision": precision,
        "recall": recall,
        "sample_size": len(context_rows),
        "schema_version": "1.0",
        "true_positives": true_positive,
    }


def _f1(precision: float | str, recall: float | str) -> float | str:
    if not isinstance(precision, float) or not isinstance(recall, float):
        return "N/A"
    total = precision + recall
    if total == 0:
        return "N/A"
    return round(2 * precision * recall / total, 4)


def _ratio(numerator: int, denominator: int) -> float | str:
    if denominator <= 0:
        return "N/A"
    return round(numerator / denominator, 4)
