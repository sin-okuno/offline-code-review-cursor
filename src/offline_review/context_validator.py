"""Compare a review context with a golden set of required line ranges.

The validator reads JSON only. It does not open the reviewed repository, and it
does not treat a selected file as success unless every required line is present.
"""

from __future__ import annotations

import json
from pathlib import Path

from offline_review.models import UsageError
from offline_review.reporters import _write_atomic

VALIDATION_SCHEMA = "1"
_CRITICALITY = frozenset({"critical", "high", "normal"})
_BUDGET_REASONS = frozenset({"max_bytes", "max_files", "max_lines", "max_total_lines"})
_DEFAULT_WEIGHTS = {"critical": 3, "high": 2, "normal": 1}


def load_json_object(path: Path) -> dict[str, object]:
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
        raise UsageError(f"JSON file must contain an object: {path}")
    return value


def validate_context(
    context: dict[str, object],
    golden: dict[str, object],
    *,
    require_changed_lines: bool = False,
) -> dict[str, object]:
    """Return a validation report. ``passed`` is true only when the gate holds."""

    weights = _weights(golden.get("weights", _DEFAULT_WEIGHTS))
    items = _golden_items(golden.get("items", []))
    candidates = _candidates(golden.get("unresolved_candidates", []))
    identity = _identity(context, golden)
    selected = _selected(context)
    omitted = _omitted(context)
    unresolved = _unresolved(context)
    edges = _edges(context)
    evaluated = [_evaluate(item, selected, omitted, unresolved) for item in items]
    candidate_rows = [_candidate_row(item, unresolved, edges) for item in candidates]
    metrics = _metrics(evaluated, selected, _changed_lines(context), weights)
    changed_gap = _changed_gap(metrics)
    confirmed_candidate = any(row["status"] == "incorrectly_confirmed" for row in candidate_rows)
    critical = metrics["critical_recall"]
    passed = bool(
        identity["matched"]
        and isinstance(critical, dict)
        and critical["hits"] == critical["total"]
        and metrics["unresolved_critical_count"] == 0
        and not confirmed_candidate
        and (not require_changed_lines or not changed_gap)
    )
    return {
        "candidates": candidate_rows,
        "changed_line_gate": "fail" if require_changed_lines and changed_gap else ("warn" if changed_gap else "pass"),
        "excluded": golden.get("excluded", []),
        "identity": identity,
        "items": evaluated,
        "metrics": metrics,
        "passed": passed,
        "require_changed_lines": require_changed_lines,
        "schema": VALIDATION_SCHEMA,
        "weights": weights,
    }


def write_validation(report: dict[str, object], output: Path) -> tuple[Path, Path]:
    if output.suffix.lower() != ".json":
        raise UsageError("validation output must be a .json file")
    parent = output.parent
    if not parent.exists() or not parent.is_dir():
        raise UsageError("validation output directory does not exist")
    markdown = output.with_suffix(".md")
    _write_atomic(output, json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    _write_atomic(markdown, _markdown(report))
    return output, markdown


def _weights(value: object) -> dict[str, int]:
    if not isinstance(value, dict):
        raise UsageError("golden weights must be an object")
    weights: dict[str, int] = {}
    for name in ("critical", "high", "normal"):
        raw = value.get(name, _DEFAULT_WEIGHTS[name])
        if isinstance(raw, bool) or not isinstance(raw, int) or raw <= 0:
            raise UsageError(f"golden weight {name} must be a positive integer")
        weights[name] = raw
    return weights


def _golden_items(value: object) -> tuple[dict[str, object], ...]:
    if not isinstance(value, list):
        raise UsageError("golden items must be a list")
    items: list[dict[str, object]] = []
    seen: set[str] = set()
    for raw in value:
        if not isinstance(raw, dict):
            raise UsageError("golden item must be an object")
        item_id = _text(raw.get("id"), "golden item id")
        if item_id in seen:
            raise UsageError(f"duplicate golden item id: {item_id}")
        seen.add(item_id)
        criticality = _text(raw.get("criticality"), f"{item_id} criticality")
        if criticality not in _CRITICALITY:
            raise UsageError(f"unknown criticality for {item_id}: {criticality}")
        ranges = _ranges(raw.get("ranges"), item_id)
        why = raw.get("why_required")
        evidence = raw.get("evidence")
        items.append(
            {
                "criticality": criticality,
                "evidence": evidence if isinstance(evidence, list) and evidence else [],
                "id": item_id,
                "path": _repo_path(raw.get("path"), item_id),
                "ranges": ranges,
                "symbol": raw.get("symbol") if isinstance(raw.get("symbol"), str) else "",
                "why_required": why if isinstance(why, str) and why.strip() else "",
            }
        )
    return tuple(items)


def _ranges(value: object, item_id: str) -> tuple[dict[str, object], ...]:
    if not isinstance(value, list) or not value:
        raise UsageError(f"{item_id} needs at least one range")
    ranges: list[dict[str, object]] = []
    for raw in value:
        if not isinstance(raw, dict):
            raise UsageError(f"{item_id} range must be an object")
        revision = _text(raw.get("revision"), f"{item_id} revision")
        if revision not in {"base", "head"}:
            raise UsageError(f"{item_id} revision must be base or head")
        start = _line(raw.get("line_start"), item_id)
        end = _line(raw.get("line_end"), item_id)
        if end < start:
            raise UsageError(f"{item_id} line range is reversed")
        ranges.append({"line_end": end, "line_start": start, "revision": revision})
    return tuple(ranges)


def _candidates(value: object) -> tuple[dict[str, str], ...]:
    if not isinstance(value, list):
        raise UsageError("golden unresolved_candidates must be a list")
    rows: list[dict[str, str]] = []
    for raw in value:
        if not isinstance(raw, dict):
            raise UsageError("unresolved candidate must be an object")
        rows.append(
            {
                "id": _text(raw.get("id"), "candidate id"),
                "path": _repo_path(raw.get("path"), "candidate"),
                "symbol": _text(raw.get("symbol"), "candidate symbol"),
            }
        )
    return tuple(rows)


def _identity(context: dict[str, object], golden: dict[str, object]) -> dict[str, object]:
    comparison = context.get("comparison")
    reasons: list[str] = []
    if not isinstance(comparison, dict):
        return {"matched": False, "reasons": ["context comparison is missing"]}
    pairs = (
        ("base_commit", "base_commit"),
        ("head_commit", "head_commit"),
        ("comparison", "mode"),
    )
    for golden_key, context_key in pairs:
        expected = golden.get(golden_key)
        actual = comparison.get(context_key)
        if not isinstance(expected, str) or expected != actual:
            reasons.append(f"{golden_key} does not match the context")
    expected_repo = golden.get("repository")
    actual_repo = context.get("repository")
    if not isinstance(expected_repo, str) or not isinstance(actual_repo, str) or not _same_repo(expected_repo, actual_repo):
        reasons.append("repository does not match the context")
    return {"matched": not reasons, "reasons": reasons}


def _same_repo(expected: str, actual: str) -> bool:
    exp = expected.replace("\\", "/").rstrip("/")
    act = actual.replace("\\", "/").rstrip("/")
    return act == exp or act.endswith("/" + exp)


def _selected(context: dict[str, object]) -> dict[tuple[str, str], set[int]]:
    angular = _angular(context)
    rows = angular.get("selected", [])
    if not isinstance(rows, list):
        raise UsageError("angular_context.selected must be a list")
    covered: dict[tuple[str, str], set[int]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise UsageError("selected excerpt must be an object")
        revision = row.get("revision")
        path = row.get("path")
        if revision not in {"base", "head"} or not isinstance(path, str):
            raise UsageError("selected excerpt needs a base or head revision and a path")
        start = row.get("line_start")
        end = row.get("line_end")
        if isinstance(start, bool) or isinstance(end, bool) or not isinstance(start, int) or not isinstance(end, int):
            raise UsageError("selected excerpt line range must be integers")
        if end < start or start <= 0:
            continue
        covered.setdefault((revision, path.replace("\\", "/")), set()).update(range(start, end + 1))
    return covered


def _omitted(context: dict[str, object]) -> list[dict[str, object]]:
    rows = _angular(context).get("omitted", [])
    if not isinstance(rows, list):
        raise UsageError("angular_context.omitted must be a list")
    return [row for row in rows if isinstance(row, dict)]


def _unresolved(context: dict[str, object]) -> list[dict[str, object]]:
    rows = _angular(context).get("unresolved", [])
    if not isinstance(rows, list):
        raise UsageError("angular_context.unresolved must be a list")
    return [row for row in rows if isinstance(row, dict)]


def _angular(context: dict[str, object]) -> dict[str, object]:
    angular = context.get("angular_context")
    if not isinstance(angular, dict):
        raise UsageError("context angular_context must be an object")
    return angular


def _evaluate(
    item: dict[str, object],
    selected: dict[tuple[str, str], set[int]],
    omitted: list[dict[str, object]],
    unresolved: list[dict[str, object]],
) -> dict[str, object]:
    if not item["why_required"] or not item["evidence"]:
        return _row(item, "unknown", "golden_data_issue", [], "The golden item has no requirement text or evidence.", item["ranges"])
    missing: list[dict[str, object]] = []
    range_states: list[str] = []
    causes: list[str] = []
    path = str(item["path"])
    for spec in item["ranges"]:
        assert isinstance(spec, dict)
        revision = str(spec["revision"])
        start = int(spec["line_start"])
        end = int(spec["line_end"])
        needed = set(range(start, end + 1))
        found = selected.get((revision, path), set())
        absent = sorted(needed - found)
        if not absent:
            range_states.append("complete")
            continue
        if found & needed:
            state, cause = "partial", "range_too_narrow"
        elif _budget_omission(omitted, revision, path):
            state, cause = "omitted_by_budget", "budget_limit"
        elif _unresolved_hit(unresolved, path, needed):
            state, cause = "unresolved", "resolution_failure"
        else:
            state, cause = "missing", ("ranking_issue" if _mentioned(path, omitted, unresolved) else "graph_missing_edge")
        range_states.append(state)
        causes.append(cause)
        missing.append({"cause": cause, "lines": absent, "revision": revision})
    status, cause = _status(range_states, causes)
    if status == "revision_mismatch":
        cause = "revision_mismatch"
    return _row(item, status, cause, missing, item["why_required"], item["ranges"])


def _status(states: list[str], causes: list[str]) -> tuple[str, str]:
    if all(state == "complete" for state in states):
        return "complete", ""
    if "partial" in states:
        return "partial", "range_too_narrow"
    if "omitted_by_budget" in states:
        return "omitted_by_budget", "budget_limit"
    if "unresolved" in states:
        return "unresolved", "resolution_failure"
    if "complete" in states and "missing" in states:
        return "revision_mismatch", "revision_mismatch"
    return "missing", causes[0] if causes else "graph_missing_edge"


def _budget_omission(omitted: list[dict[str, object]], revision: str, path: str) -> bool:
    return any(
        row.get("revision") == revision
        and str(row.get("path", "")).replace("\\", "/") == path
        and row.get("reason") in _BUDGET_REASONS
        for row in omitted
    )


def _unresolved_hit(unresolved: list[dict[str, object]], path: str, lines: set[int]) -> bool:
    for row in unresolved:
        if str(row.get("from_path", "")).replace("\\", "/") != path:
            continue
        line = row.get("line")
        if isinstance(line, int) and not isinstance(line, bool) and line in lines:
            return True
    return False


def _mentioned(path: str, omitted: list[dict[str, object]], unresolved: list[dict[str, object]]) -> bool:
    for row in (*omitted, *unresolved):
        for key in ("path", "from_path", "to_path"):
            if str(row.get(key, "")).replace("\\", "/") == path:
                return True
    return False


def _row(
    item: dict[str, object],
    status: str,
    cause: str,
    missing: list[dict[str, object]],
    detail: object,
    ranges: object,
) -> dict[str, object]:
    return {
        "cause": cause,
        "criticality": item["criticality"],
        "detail": detail,
        "id": item["id"],
        "missing_lines": missing,
        "path": item["path"],
        "ranges": ranges,
        "status": status,
        "symbol": item["symbol"],
    }


def _edges(context: dict[str, object]) -> list[dict[str, object]]:
    rows = _angular(context).get("edges", [])
    if not isinstance(rows, list):
        raise UsageError("angular_context.edges must be a list")
    return [row for row in rows if isinstance(row, dict)]


def _candidate_row(
    candidate: dict[str, str],
    unresolved: list[dict[str, object]],
    edges: list[dict[str, object]],
) -> dict[str, str]:
    symbol = candidate["symbol"]
    path = candidate["path"]
    if any(_mentions(row, symbol, path) and row.get("confidence") == "confirmed" for row in edges):
        return {**candidate, "status": "incorrectly_confirmed"}
    if any(_mentions(row, symbol, path) for row in unresolved):
        return {**candidate, "status": "still_unresolved"}
    return {**candidate, "status": "not_seen"}


def _mentions(row: dict[str, object], symbol: str, path: str) -> bool:
    if str(row.get("from_path", "")).replace("\\", "/") != path:
        return False
    text = " ".join(str(row.get(key, "")) for key in ("evidence", "to_symbol"))
    return symbol in text


def _metrics(
    items: list[dict[str, object]],
    selected: dict[tuple[str, str], set[int]],
    changed: dict[str, set[tuple[str, int]]],
    weights: dict[str, int],
) -> dict[str, object]:
    return {
        "budget_omission_count": sum(1 for item in items if item["status"] == "omitted_by_budget"),
        "changed_line_coverage": {
            revision: _ratio(changed[revision], _covered_changed(revision, changed[revision], selected))
            for revision in ("base", "head")
        },
        "context_efficiency": _efficiency(items, selected),
        "critical_recall": _recall(items, "critical"),
        "high_recall": _recall(items, "high"),
        "unresolved_critical_count": _unresolved_critical(items),
        "weighted_recall": _weighted(items, weights),
    }


def _recall(items: list[dict[str, object]], criticality: str) -> object:
    group = [item for item in items if item["criticality"] == criticality]
    if not group:
        return "not_applicable"
    hits = sum(1 for item in group if item["status"] == "complete")
    return {"hits": hits, "ratio": hits / len(group), "total": len(group)}


def _weighted(items: list[dict[str, object]], weights: dict[str, int]) -> object:
    if not items:
        return "not_applicable"
    total = sum(weights[str(item["criticality"])] for item in items)
    hits = sum(weights[str(item["criticality"])] for item in items if item["status"] == "complete")
    if total == 0:
        return "not_applicable"
    return {"hits": hits, "ratio": hits / total, "total": total}


def _unresolved_critical(items: list[dict[str, object]]) -> int:
    return sum(1 for item in items if item["criticality"] == "critical" and item["status"] == "unresolved")


def _changed_lines(context: dict[str, object]) -> dict[str, set[tuple[str, int]]]:
    files = context.get("files", [])
    found = {"base": set(), "head": set()}
    if not isinstance(files, list):
        raise UsageError("context files must be a list")
    for row in files:
        if not isinstance(row, dict):
            continue
        for revision, path_key, line_key in (
            ("base", "old_path", "old_changed_lines"),
            ("head", "new_path", "new_changed_lines"),
        ):
            path = row.get(path_key)
            lines = row.get(line_key, [])
            if not isinstance(path, str) or not isinstance(lines, list):
                continue
            normalized = path.replace("\\", "/")
            for line in lines:
                if isinstance(line, int) and not isinstance(line, bool) and line > 0:
                    found[revision].add((normalized, line))
    return found


def _covered_changed(
    revision: str, changed: set[tuple[str, int]], selected: dict[tuple[str, str], set[int]]
) -> set[tuple[str, int]]:
    return {(path, line) for path, line in changed if line in selected.get((revision, path), set())}


def _ratio(universe: set[tuple[str, int]], covered: set[tuple[str, int]]) -> object:
    if not universe:
        return "not_applicable"
    return {"covered": len(covered), "ratio": len(covered) / len(universe), "total": len(universe)}


def _efficiency(items: list[dict[str, object]], selected: dict[tuple[str, str], set[int]]) -> object:
    selected_lines = {(revision, path, line) for (revision, path), lines in selected.items() for line in lines}
    if not selected_lines:
        return "not_applicable"
    required: set[tuple[str, str, int]] = set()
    for item in items:
        if item["status"] == "unknown":
            continue
        ranges = item.get("ranges")
        if not isinstance(ranges, tuple) and not isinstance(ranges, list):
            continue
        for spec in ranges:
            if not isinstance(spec, dict):
                continue
            start = int(spec["line_start"])
            end = int(spec["line_end"])
            for line in range(start, end + 1):
                required.add((str(spec["revision"]), str(item["path"]), line))
    matching = selected_lines & required
    return {"matching": len(matching), "ratio": len(matching) / len(selected_lines), "selected": len(selected_lines)}


def _changed_gap(metrics: dict[str, object]) -> bool:
    coverage = metrics["changed_line_coverage"]
    assert isinstance(coverage, dict)
    for value in coverage.values():
        if isinstance(value, dict) and value["covered"] != value["total"]:
            return True
    return False


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise UsageError(f"{label} must be a non-empty string")
    return value


def _repo_path(value: object, label: str) -> str:
    path = _text(value, label).replace("\\", "/")
    if path.startswith("/") or path.startswith("../") or "/../" in path:
        raise UsageError(f"{label} path must stay inside the repository")
    return path


def _line(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise UsageError(f"{label} line must be a positive integer")
    return value


def _markdown(report: dict[str, object]) -> str:
    identity = report["identity"]
    assert isinstance(identity, dict)
    metrics = report["metrics"]
    assert isinstance(metrics, dict)
    lines = [
        "# Context validation",
        "",
        f"- Passed: {_yes(report['passed'])}",
        f"- Identity matched: {_yes(identity['matched'])}",
        f"- Changed-line gate: {report['changed_line_gate']}",
        "",
        "## Metrics",
        "",
        f"- critical_recall: {_metric(metrics['critical_recall'])}",
        f"- high_recall: {_metric(metrics['high_recall'])}",
        f"- weighted_recall: {_metric(metrics['weighted_recall'])} (critical {report['weights']['critical']}, high {report['weights']['high']}, normal {report['weights']['normal']})",
        f"- changed_line_coverage base: {_metric(metrics['changed_line_coverage']['base'])}",
        f"- changed_line_coverage head: {_metric(metrics['changed_line_coverage']['head'])}",
        f"- budget_omission_count: {metrics['budget_omission_count']}",
        f"- unresolved_critical_count: {metrics['unresolved_critical_count']}",
        f"- context_efficiency: {_metric(metrics['context_efficiency'])}",
        "",
        "## Missing expectations",
        "",
    ]
    items = report["items"]
    assert isinstance(items, list)
    gaps = [item for item in items if isinstance(item, dict) and item.get("status") != "complete"]
    if not gaps:
        lines.append("(none)")
    for item in gaps:
        assert isinstance(item, dict)
        lines.append(
            f"- {item['id']} | {item['criticality']} | {item['status']} | {item['cause']} | {item['path']}"
        )
        for hole in item["missing_lines"]:
            assert isinstance(hole, dict)
            listed = ", ".join(str(line) for line in hole["lines"])
            lines.append(f"  - {hole['revision']} | {hole['cause']} | lines {listed}")
    lines.extend(["", "## Unresolved candidates", ""])
    candidates = report["candidates"]
    assert isinstance(candidates, list)
    if not candidates:
        lines.append("(none)")
    for row in candidates:
        assert isinstance(row, dict)
        lines.append(f"- {row['id']} | {row['symbol']} | {row['status']} | {row['path']}")
    if identity["reasons"]:
        lines.extend(["", "## Identity mismatches", ""])
        for reason in identity["reasons"]:
            lines.append(f"- {reason}")
    lines.append("")
    return "\n".join(lines)


def _metric(value: object) -> str:
    if not isinstance(value, dict):
        return str(value)
    if "hits" in value:
        return f"{value['hits']}/{value['total']}"
    if "covered" in value:
        return f"{value['covered']}/{value['total']}"
    return f"{value['matching']}/{value['selected']}"


def _yes(value: object) -> str:
    return "yes" if value else "no"
