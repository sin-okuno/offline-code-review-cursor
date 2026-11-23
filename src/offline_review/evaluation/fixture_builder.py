"""Build the deterministic beacon-ledger Git fixture."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from offline_review.evaluation.harbor_fixture import BASE_FILES, CASES, CasePatch
from offline_review.evaluation.schemas import validate_ground_truth
from offline_review.models import UsageError
from offline_review.typescript_index import scan_typescript

_BASELINE_WHEN = "2026-01-15T00:00:00+00:00"
_FORBIDDEN = ("ANG-", "CLEAN-", "buggy", "ground truth", "match_terms", "expected_finding")


def build_fixture(repo: Path, cases_dir: Path) -> tuple[Path, tuple[dict[str, object], ...]]:
    """Create a new repository and write one ground-truth file per case."""

    if repo.exists():
        raise UsageError("fixture repository already exists")
    if cases_dir.exists() and any(cases_dir.iterdir()):
        raise UsageError("ground-truth directory must be empty")
    repo.mkdir(parents=True)
    cases_dir.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "review-fixture@example.com")
    _git(repo, "config", "user.name", "Review Fixture")
    _git(repo, "config", "commit.gpgsign", "false")
    _git(repo, "config", "core.autocrlf", "false")
    _write_files(repo, BASE_FILES)
    _assert_sources(BASE_FILES)
    baseline = _commit(repo, "create beacon ledger", _BASELINE_WHEN)
    _git(repo, "tag", "baseline")
    written: list[dict[str, object]] = []
    for case in CASES:
        _git(repo, "checkout", "-B", _branch(case.case_id), baseline)
        files = dict(BASE_FILES)
        files[case.path] = _apply(case)
        _write_files(repo, {case.path: files[case.path]})
        _assert_sources(files)
        head = _commit(repo, case.message, case.when)
        document = _ground_truth(case, files[case.path], baseline, head)
        validate_ground_truth(document)
        target = cases_dir / f"{case.case_id}.json"
        target.write_text(json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        written.append(document)
    _git(repo, "checkout", "baseline")
    manifest = {
        "base_revision": baseline,
        "cases": [item["case_id"] for item in written],
        "schema_version": "1.0",
    }
    (cases_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return repo, tuple(written)


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Rebuild the Phase 16 synthetic Git fixture")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--cases", required=True)
    args = parser.parse_args()
    build_fixture(Path(args.repo), Path(args.cases))
    return 0


def _apply(case: CasePatch) -> str:
    text = BASE_FILES[case.path]
    for old, new in ((case.old, case.new), *case.also):
        if text.count(old) != 1:
            raise UsageError(f"{case.case_id} edit is not unique")
        text = text.replace(old, new, 1)
    if text == BASE_FILES[case.path]:
        raise UsageError(f"{case.case_id} did not change the baseline")
    return text


def _ground_truth(case: CasePatch, text: str, base: str, head: str) -> dict[str, object]:
    locations: list[dict[str, object]] = []
    required = [
        {"path": path, "reason": reason, "symbol": symbol}
        for path, symbol, reason in case.required
    ]
    if case.case_kind == "defect":
        start = _line(text, case.anchor)
        end = start + max(0, len(case.new.strip().splitlines()) - 1)
        locations.append(
            {"end_line": end, "path": case.path, "start_line": start, "symbol": case.symbol}
        )
    return {
        "ambiguous": False,
        "base_revision": base,
        "buggy_revision": head,
        "case_id": case.case_id,
        "case_kind": case.case_kind,
        "expected_behavior": case.expected_behavior,
        "expected_finding": case.expected_finding,
        "match_terms": list(case.match_terms),
        "primary_locations": locations,
        "reproduction": case.reproduction,
        "required_context": required,
        "schema_version": "1.0",
        "severity": case.severity,
        "tags": list(case.tags),
        "title": case.title,
    }


def _line(text: str, anchor: str) -> int:
    needle = anchor.strip()
    index = text.find(needle)
    if index < 0:
        raise UsageError("the defect anchor is not in the changed file")
    return text.count("\n", 0, index) + 1


def _assert_sources(files: dict[str, str]) -> None:
    for path, text in files.items():
        lowered = text.casefold()
        if any(token.casefold() in lowered for token in _FORBIDDEN):
            raise UsageError(f"{path} contains an evaluation label")
        if path.endswith(".ts"):
            indexed = scan_typescript(text.encode())
            if indexed.status != "parsed":
                raise UsageError(f"{path} was not parsed: {indexed.status}")


def _write_files(repo: Path, files: dict[str, str]) -> None:
    for relative, text in files.items():
        path = repo.joinpath(*relative.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")


def _commit(repo: Path, message: str, when: str) -> str:
    env = os.environ.copy()
    env["GIT_AUTHOR_DATE"] = when
    env["GIT_COMMITTER_DATE"] = when
    _git(repo, "add", "-A", "--", ".", env=env)
    _git(repo, "commit", "-m", message, env=env)
    return _git(repo, "rev-parse", "HEAD", env=env).stdout.decode().strip()


def _git(repo: Path, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[bytes]:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        check=False,
        env=env,
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace")[:400]
        raise UsageError(f"git {' '.join(args)} failed: {detail}")
    return result


def _branch(case_id: str) -> str:
    return "case-" + case_id.lower().replace("-", "")


if __name__ == "__main__":
    raise SystemExit(main())
