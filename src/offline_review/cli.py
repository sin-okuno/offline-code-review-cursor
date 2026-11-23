"""Command-line entry point for the offline review context extractor."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from offline_review import __version__
from offline_review.context_builder import build_context
from offline_review.context_selector import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_DEPTH,
    DEFAULT_MAX_FILES,
    DEFAULT_MAX_LINES,
    DEFAULT_MAX_TOTAL_LINES,
    ContextBudget,
)
from offline_review.dependency_index import normalize_python_roots
from offline_review.git_adapter import GitAdapter, is_within
from offline_review.parse_cache import ParseCache
from offline_review.models import DiffParseError, GitError, RevisionError, UsageError
from offline_review.context_validator import load_json_object, validate_context, write_validation
from offline_review.evaluation.commands import (
    add_evaluation_parsers,
    run_evaluate_context,
    run_evaluate_findings,
)
from offline_review.reporters import write_reports


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="offline_review",
        description=(
            "Extract a local read-only code review context from Git objects. "
            "The report includes changed paths, old and new line ranges, affected Python "
            "definitions, and file-level import candidates. "
            "It does not run Angular semantic analysis, network Git operations, "
            "or code from the reviewed repository."
        ),
    )
    parser.add_argument("--version", action="version", version=f"offline_review {__version__}")
    subcommands = parser.add_subparsers(dest="command", required=True)
    analyze = subcommands.add_parser(
        "analyze",
        help="Write review-context.json and review-context.md for a commit range",
    )
    analyze.add_argument("--repo", required=True, help="Path to a local Git work tree")
    analyze.add_argument(
        "--base",
        required=True,
        help="Base revision. It is resolved to a commit object id before diffing",
    )
    analyze.add_argument(
        "--head",
        required=True,
        help="Head revision. It is resolved to a commit object id before diffing",
    )
    analyze.add_argument(
        "--output",
        required=True,
        help="Directory outside the repository where the reports are written",
    )
    analyze.add_argument(
        "--comparison",
        required=True,
        choices=("merge-base", "direct"),
        help=(
            "merge-base compares merge-base(base, head) to head (three-dot). "
            "direct compares base to head (two-dot)"
        ),
    )
    analyze.add_argument(
        "--python-root",
        action="append",
        dest="python_roots",
        default=None,
        help=(
            "Repository-relative Python import root. Repeat for each root. "
            "Use . when the repository root is on the import path. "
            "Without this option, import targets are not matched to files."
        ),
    )
    analyze.add_argument(
        "--cache",
        default=None,
        help=(
            "Optional directory outside the repository for parsed Python trees. "
            "The key is the commit id, parser version, and --python-root values. "
            "Omit it to parse on every run."
        ),
    )
    analyze.add_argument(
        "--max-depth",
        type=_non_negative_int,
        default=DEFAULT_MAX_DEPTH,
        help=(
            "How many confirmed dependency steps to follow from a changed file. "
            f"Default: {DEFAULT_MAX_DEPTH}. Zero keeps changed files only."
        ),
    )
    analyze.add_argument(
        "--max-files",
        type=_non_negative_int,
        default=DEFAULT_MAX_FILES,
        help=(
            "Maximum number of base or head paths included as excerpts. "
            f"Default: {DEFAULT_MAX_FILES}. Zero selects none."
        ),
    )
    analyze.add_argument(
        "--max-bytes",
        type=_non_negative_int,
        default=DEFAULT_MAX_BYTES,
        help=(
            "Maximum UTF-8 bytes of source excerpts in the report. "
            f"Default: {DEFAULT_MAX_BYTES}. Zero selects no excerpt text."
        ),
    )
    analyze.add_argument(
        "--max-lines-per-excerpt",
        type=_non_negative_int,
        default=DEFAULT_MAX_LINES,
        help=(
            "Maximum lines kept from each selected range. "
            f"Default: {DEFAULT_MAX_LINES}. Zero keeps no lines."
        ),
    )
    analyze.add_argument(
        "--max-total-lines",
        type=_non_negative_int,
        default=DEFAULT_MAX_TOTAL_LINES,
        help=(
            "Maximum excerpt lines across the Angular review context. "
            f"Default: {DEFAULT_MAX_TOTAL_LINES}. Zero keeps no excerpt lines."
        ),
    )
    validate = subcommands.add_parser(
        "validate-context",
        help="Compare a review-context.json file with a golden expectation file",
    )
    validate.add_argument("--context", required=True, help="Existing review-context.json. It is not modified")
    validate.add_argument("--golden", required=True, help="Golden expectation JSON")
    validate.add_argument(
        "--output",
        required=True,
        help="JSON report path. A Markdown report is written beside it",
    )
    validate.add_argument(
        "--require-changed-lines",
        action="store_true",
        help="Fail when a Git changed line is absent from the selected excerpts",
    )
    add_evaluation_parsers(subcommands)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return _exit_code(exc.code)
    try:
        if args.command == "analyze":
            return run_analyze(args)
        if args.command == "validate-context":
            return run_validate(args)
        if args.command == "evaluate-context":
            return run_evaluate_context(args)
        if args.command == "evaluate-findings":
            return run_evaluate_findings(args)
        parser.print_help(sys.stderr)
        return 2
    except UsageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except (RevisionError, GitError, DiffParseError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def run_validate(args: argparse.Namespace) -> int:
    context_path = Path(args.context)
    output = Path(args.output)
    if output.resolve() == context_path.resolve():
        raise UsageError("validation output must not replace the context file")
    report = validate_context(
        load_json_object(context_path),
        load_json_object(Path(args.golden)),
        require_changed_lines=args.require_changed_lines,
    )
    write_validation(report, output)
    return 0 if report["passed"] else 1


def run_analyze(args: argparse.Namespace) -> int:
    repo = Path(args.repo)
    if not repo.exists():
        raise GitError("repository path does not exist")
    if not repo.is_dir():
        raise UsageError("repository path must be a directory")
    with GitAdapter(repo) as adapter:
        adapter.ensure_work_tree()
        output = _directory_outside_repository(adapter, Path(args.output), "output directory")
        cache = None
        if args.cache is not None:
            cache_dir = _directory_outside_repository(adapter, Path(args.cache), "parse cache directory")
            cache = ParseCache(cache_dir)
        context = build_context(
            adapter,
            repository=str(adapter.repo),
            base=args.base,
            head=args.head,
            comparison=args.comparison,
            python_roots=normalize_python_roots(list(args.python_roots or [])),
            cache=cache,
            budget=ContextBudget(
                max_depth=args.max_depth,
                max_files=args.max_files,
                max_bytes=args.max_bytes,
                max_lines=args.max_lines_per_excerpt,
                max_total_lines=args.max_total_lines,
            ),
        )
        write_reports(context, output)
    return 0


def _directory_outside_repository(adapter: GitAdapter, directory: Path, label: str) -> Path:
    resolved = directory.expanduser().resolve()
    git_dir = adapter.absolute_git_dir().resolve()
    if is_within(resolved, adapter.repo) or is_within(resolved, git_dir):
        raise UsageError(
            f"{label} must be outside the repository so the tool does not modify the work tree"
        )
    return resolved


def _non_negative_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected an integer") from exc
    if number < 0:
        raise argparse.ArgumentTypeError("expected a non-negative integer")
    return number


def _exit_code(code: object) -> int:
    if code is None:
        return 0
    if isinstance(code, int):
        return code
    print(str(code), file=sys.stderr)
    return 1
