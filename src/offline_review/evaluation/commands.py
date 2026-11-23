"""CLI handlers for context and finding evaluation."""

from __future__ import annotations

import argparse
from pathlib import Path

from offline_review.evaluation.context_evaluator import evaluate_context
from offline_review.evaluation.finding_matcher import match_findings
from offline_review.evaluation.metrics import summarize
from offline_review.evaluation.report import write_evaluation
from offline_review.evaluation.schemas import load_object


def add_evaluation_parsers(subcommands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    context = subcommands.add_parser(
        "evaluate-context",
        help="Measure changed-line and required-context coverage for one ground-truth case",
    )
    context.add_argument("--context", required=True, help="review-context.json produced by analyze")
    context.add_argument("--ground-truth", required=True, help="Phase 16 ground-truth JSON")
    context.add_argument("--output", required=True, help="JSON report path. Markdown is written beside it")
    context.add_argument(
        "--max-input-files",
        type=_positive_int,
        default=3,
        help="Maximum files treated as one local AI input. Default: 3",
    )
    findings = subcommands.add_parser(
        "evaluate-findings",
        help="Compare a saved AI findings file with one ground-truth case",
    )
    findings.add_argument("--findings", required=True, help="Saved findings JSON. This command does not call a model")
    findings.add_argument("--ground-truth", required=True, help="Phase 16 ground-truth JSON")
    findings.add_argument("--output", required=True, help="JSON report path. Markdown is written beside it")
    findings.add_argument(
        "--context-evaluation",
        default=None,
        help="Optional evaluate-context JSON used to separate extractor misses from AI misses",
    )


def run_evaluate_context(args: argparse.Namespace) -> int:
    context_path = Path(args.context)
    truth_path = Path(args.ground_truth)
    output = Path(args.output)
    report = evaluate_context(
        load_object(context_path),
        load_object(truth_path),
        max_input_files=args.max_input_files,
    )
    write_evaluation(report, output, inputs=(context_path, truth_path))
    return 0


def run_evaluate_findings(args: argparse.Namespace) -> int:
    findings_path = Path(args.findings)
    truth_path = Path(args.ground_truth)
    output = Path(args.output)
    context_path = Path(args.context_evaluation) if args.context_evaluation else None
    truth = load_object(truth_path)
    context_evaluation = load_object(context_path) if context_path is not None else None
    matched = match_findings(load_object(findings_path), truth, context_evaluation)
    summary = summarize(
        [context_evaluation] if isinstance(context_evaluation, dict) and "changed_lines_total" in context_evaluation else [],
        [matched],
    )
    report = {**matched, "summary": summary}
    inputs = (findings_path, truth_path) if context_path is None else (findings_path, truth_path, context_path)
    write_evaluation(report, output, inputs=inputs)
    return 0


def _positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected an integer") from exc
    if number < 1:
        raise argparse.ArgumentTypeError("expected a positive integer")
    return number
