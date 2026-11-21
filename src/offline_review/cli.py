"""Command-line entry point for the offline review context extractor."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from offline_review import __version__
from offline_review.context_builder import build_context
from offline_review.dependency_index import normalize_python_roots
from offline_review.git_adapter import GitAdapter, is_within
from offline_review.parse_cache import ParseCache
from offline_review.models import DiffParseError, GitError, RevisionError, UsageError
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
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return _exit_code(exc.code)
    if args.command != "analyze":
        parser.print_help(sys.stderr)
        return 2
    try:
        return run_analyze(args)
    except UsageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except (RevisionError, GitError, DiffParseError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


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


def _exit_code(code: object) -> int:
    if code is None:
        return 0
    if isinstance(code, int):
        return code
    print(str(code), file=sys.stderr)
    return 1
