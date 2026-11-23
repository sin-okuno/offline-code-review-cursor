"""Write evaluation JSON and Markdown without copying source excerpts."""

from __future__ import annotations

import json
from pathlib import Path

from offline_review.models import UsageError
from offline_review.reporters import _write_atomic, is_reparse_point


def write_evaluation(report: dict[str, object], output: Path, *, inputs: tuple[Path, ...]) -> tuple[Path, Path]:
    if output.suffix.lower() != ".json":
        raise UsageError("evaluation output must be a .json file")
    parent = output.parent
    if not parent.exists() or not parent.is_dir() or is_reparse_point(parent) or is_reparse_point(output):
        raise UsageError("evaluation output directory must exist and must not be a symlink")
    resolved = output.resolve()
    for source in inputs:
        if source.resolve() == resolved:
            raise UsageError("evaluation output must not replace an input file")
    markdown = output.with_suffix(".md")
    _write_atomic(output, json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    _write_atomic(markdown, render_markdown(report))
    return output, markdown


def render_markdown(report: dict[str, object]) -> str:
    lines = [
        "# Evaluation report",
        "",
        f"- Schema: {report.get('schema_version')}",
        f"- Case: {report.get('case_id', '(summary)')}",
        "",
        "## Metrics",
        "",
    ]
    for key in (
        "changed_line_coverage",
        "changed_lines_included",
        "changed_lines_omitted",
        "changed_lines_total",
        "context_lines_omitted",
        "required_context_coverage",
        "case_context_sufficient",
        "combined_units_sufficient",
        "cross_unit_dependency",
        "status",
        "miss_cause",
        "recall",
        "precision",
        "f1",
        "context_sufficiency_rate",
        "false_positives_per_clean_case",
        "true_positives",
        "false_positives",
        "false_negatives",
        "operational_proof",
    ):
        if key in report:
            lines.append(f"- {key}: {report[key]}")
    summary = report.get("summary")
    if isinstance(summary, dict):
        lines.extend(["", "## Summary", ""])
        for key in (
            "changed_line_coverage",
            "context_sufficiency_rate",
            "recall",
            "precision",
            "f1",
            "false_positives_per_clean_case",
            "true_positives",
            "false_positives",
            "false_negatives",
            "cross_unit_cases",
            "operational_proof",
        ):
            if key in summary:
                lines.append(f"- {key}: {summary[key]}")
    lines.extend(
        [
            "",
            "This sample does not prove operational review quality.",
            "",
        ]
    )
    return "\n".join(lines)
