"""Validate ground-truth and saved AI finding documents."""

from __future__ import annotations

import json
import re
from pathlib import Path

from offline_review.models import UsageError

GROUND_TRUTH_SCHEMA = "1.0"
FINDINGS_SCHEMA = "1.0"
MAX_JSON_BYTES = 1_000_000
_SHA = re.compile(r"^[0-9a-f]{40}$")
_CASE_ID = re.compile(r"^(ANG-[0-9]{3}|CLEAN-[0-9]{3})$")
_SEVERITY = {"low", "medium", "high"}
_KINDS = {"defect", "clean"}


def load_object(path: Path) -> dict[str, object]:
    """Read one JSON object without executing it."""

    try:
        size = path.stat().st_size
    except OSError as exc:
        raise UsageError(f"cannot read JSON file: {path}") from exc
    if size > MAX_JSON_BYTES:
        raise UsageError("JSON file exceeds the evaluation size limit")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise UsageError(f"cannot read JSON file: {path}") from exc
    except UnicodeError as exc:
        raise UsageError(f"JSON file is not UTF-8: {path}") from exc
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise UsageError(f"JSON file is not valid: {path}") from exc
    if not isinstance(value, dict):
        raise UsageError("JSON file must contain an object")
    return value


def validate_ground_truth(value: dict[str, object]) -> dict[str, object]:
    if value.get("schema_version") != GROUND_TRUTH_SCHEMA:
        raise UsageError("ground truth schema_version must be 1.0")
    case_id = _text(value.get("case_id"), "case_id")
    if _CASE_ID.fullmatch(case_id) is None:
        raise UsageError("case_id is not a known evaluation id")
    kind = _text(value.get("case_kind"), "case_kind")
    if kind not in _KINDS:
        raise UsageError("case_kind must be defect or clean")
    severity = _text(value.get("severity"), "severity")
    if severity not in _SEVERITY:
        raise UsageError("severity must be low, medium, or high")
    base = _sha(value.get("base_revision"), "base_revision")
    head = _sha(value.get("buggy_revision"), "buggy_revision")
    if base == head:
        raise UsageError("base_revision and buggy_revision must differ")
    locations = _locations(value.get("primary_locations"))
    required = _required(value.get("required_context"))
    terms = _terms(value.get("match_terms"))
    if kind == "defect":
        if not locations or not required or len(terms) < 2:
            raise UsageError("a defect case needs locations, required context, and two match phrases")
    elif locations or required or terms:
        raise UsageError("a clean case cannot carry defect locations or match phrases")
    ambiguous = value.get("ambiguous", False)
    if not isinstance(ambiguous, bool):
        raise UsageError("ambiguous must be a boolean")
    return {
        "ambiguous": ambiguous,
        "base_revision": base,
        "buggy_revision": head,
        "case_id": case_id,
        "case_kind": kind,
        "expected_behavior": _text(value.get("expected_behavior"), "expected_behavior"),
        "expected_finding": _text(value.get("expected_finding"), "expected_finding"),
        "match_terms": terms,
        "primary_locations": locations,
        "reproduction": _text(value.get("reproduction"), "reproduction"),
        "required_context": required,
        "schema_version": GROUND_TRUTH_SCHEMA,
        "severity": severity,
        "tags": _tags(value.get("tags")),
        "title": _text(value.get("title"), "title"),
    }


def validate_findings(value: dict[str, object]) -> dict[str, object]:
    if value.get("schema_version") != FINDINGS_SCHEMA:
        raise UsageError("findings schema_version must be 1.0")
    case_id = _text(value.get("case_id"), "case_id")
    if _CASE_ID.fullmatch(case_id) is None:
        raise UsageError("findings case_id is not a known evaluation id")
    unit = _text(value.get("review_unit_id"), "review_unit_id")
    raw_findings = value.get("findings")
    if not isinstance(raw_findings, list):
        raise UsageError("findings must be a list")
    findings: list[dict[str, object]] = []
    seen: set[str] = set()
    for item in raw_findings:
        if not isinstance(item, dict):
            raise UsageError("each finding must be an object")
        finding_id = _text(item.get("finding_id"), "finding_id")
        if finding_id in seen:
            raise UsageError("finding_id values must be unique")
        seen.add(finding_id)
        severity = _text(item.get("severity"), "severity")
        if severity not in _SEVERITY:
            raise UsageError("finding severity must be low, medium, or high")
        line = item.get("line")
        if isinstance(line, bool) or not isinstance(line, int) or line < 1:
            raise UsageError("finding line must be a positive integer")
        findings.append(
            {
                "description": _text(item.get("description"), "description"),
                "evidence": _text(item.get("evidence"), "evidence"),
                "finding_id": finding_id,
                "line": line,
                "path": _path(item.get("path"), "path"),
                "severity": severity,
                "suggestion": _text(item.get("suggestion"), "suggestion"),
                "title": _text(item.get("title"), "title"),
            }
        )
    findings.sort(key=lambda item: str(item["finding_id"]))
    return {
        "case_id": case_id,
        "findings": findings,
        "review_unit_id": unit,
        "schema_version": FINDINGS_SCHEMA,
    }


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 2000:
        raise UsageError(f"{label} must be a short non-empty string")
    return value.strip()


def _sha(value: object, label: str) -> str:
    if not isinstance(value, str) or _SHA.fullmatch(value) is None:
        raise UsageError(f"{label} must be a 40-character commit id")
    return value


def _path(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise UsageError(f"{label} must be a relative repository path")
    if (
        not value
        or value.startswith(("/", "\\"))
        or "\\" in value
        or ":" in value
        or value.startswith("../")
        or "/../" in value
        or value.endswith("/..")
        or value == ".."
    ):
        raise UsageError(f"{label} must stay inside the repository")
    if not value.endswith((".ts", ".html")):
        raise UsageError(f"{label} must be a TypeScript or HTML file")
    return value


def _locations(value: object) -> tuple[dict[str, object], ...]:
    if not isinstance(value, list):
        raise UsageError("primary_locations must be a list")
    rows: list[dict[str, object]] = []
    for item in value:
        if not isinstance(item, dict):
            raise UsageError("each primary location must be an object")
        start = item.get("start_line")
        end = item.get("end_line")
        if isinstance(start, bool) or isinstance(end, bool) or not isinstance(start, int) or not isinstance(end, int):
            raise UsageError("location line numbers must be integers")
        if start < 1 or end < start:
            raise UsageError("location line numbers are out of order")
        rows.append(
            {
                "end_line": end,
                "path": _path(item.get("path"), "path"),
                "start_line": start,
                "symbol": _text(item.get("symbol"), "symbol"),
            }
        )
    return tuple(rows)


def _required(value: object) -> tuple[dict[str, str], ...]:
    if not isinstance(value, list):
        raise UsageError("required_context must be a list")
    rows: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            raise UsageError("each required context item must be an object")
        rows.append(
            {
                "path": _path(item.get("path"), "path"),
                "reason": _text(item.get("reason"), "reason"),
                "symbol": _text(item.get("symbol"), "symbol"),
            }
        )
    return tuple(rows)


def _terms(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise UsageError("match_terms must be a list of strings")
    return tuple(item.strip() for item in value)


def _tags(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise UsageError("tags must be a list of strings")
    return tuple(item.strip() for item in value)
