"""Measure whether a review context contains the ground-truth evidence."""

from __future__ import annotations

import re

from offline_review.evaluation.schemas import validate_ground_truth
from offline_review.models import UsageError

_BLOCKING = {"TS_IMPORT_UNRESOLVED", "TSCONFIG_UNREADABLE"}
_NOTED = _BLOCKING | {
    "ANGULAR_SEMANTIC_OUT_OF_SCOPE",
    "CHANGED_CONTEXT_OMITTED",
    "TS_NGMODULE_UNRESOLVED",
    "TSCONFIG_EXTENDS_UNSUPPORTED",
    "WORKTREE_DIRTY",
}


def evaluate_context(
    context: dict[str, object],
    ground_truth: dict[str, object],
    *,
    max_input_files: int = 3,
) -> dict[str, object]:
    """Return coverage and sufficiency. Combined units are not an AI input."""

    if max_input_files < 1:
        raise UsageError("max input files must be positive")
    truth = validate_ground_truth(ground_truth)
    angular = _object(context.get("angular_context"), "angular_context")
    comparison = _object(context.get("comparison"), "comparison")
    coverage = _object(angular.get("coverage"), "coverage")
    units = _units(angular.get("review_units"))
    changed = _changed(angular.get("changed_line_files"))
    total = sum(item["changed_lines_total"] for item in changed)
    included = sum(item["changed_lines_included"] for item in changed)
    omitted = sum(item["changed_lines_omitted"] for item in changed)
    budget = _count(coverage.get("line_budget"))
    context_omitted = _count(coverage.get("lines_omitted"))
    base = comparison.get("base_commit")
    head = comparison.get("head_commit")
    revision_match = base == truth["base_revision"] and head == truth["buggy_revision"]
    required = truth["required_context"]
    changed_symbols = _symbols(angular.get("changed_symbols"))
    locations = truth["primary_locations"]
    symbol_warnings = _symbol_warnings(angular)
    per_item = tuple(
        _item_coverage(item, units, changed_symbols, symbol_warnings, locations, required)
        for item in required
    )
    present = [item for item in per_item if item["covered"]]
    single_units = _single_units(required, units, per_item, max_input_files, budget)
    sufficient = bool(single_units) and revision_match and truth["case_kind"] == "defect"
    if truth["case_kind"] == "clean":
        sufficient = revision_match
    combined = (not required or len(present) == len(per_item)) and revision_match
    cross = bool(required) and combined and not single_units and revision_match
    missing = [item for item in per_item if not item["covered"]]
    split_reason = ""
    if cross:
        split_reason = _split_reason(required, per_item, max_input_files)
    warnings = _warnings(context, angular)
    return {
        "case_id": truth["case_id"],
        "case_kind": truth["case_kind"],
        "case_context_sufficient": sufficient,
        "changed_line_coverage": _ratio(included, total),
        "changed_lines_included": included,
        "changed_lines_omitted": omitted,
        "changed_lines_total": total,
        "combined_units_sufficient": combined and truth["case_kind"] == "defect",
        "context_lines_omitted": context_omitted,
        "context_lines_selected": _count(coverage.get("lines_selected")),
        "cross_unit_dependency": cross,
        "files_for_ai_input": sorted({str(item["path"]) for item in required}),
        "line_budget": budget,
        "max_input_files": max_input_files,
        "missing_details": [
            {"gap": item["gap"], "path": item["path"], "symbol": item["symbol"]} for item in missing
        ],
        "missing_required": [item["symbol"] for item in missing],
        "required_context_coverage": _ratio(len(present), len(per_item)),
        "required_items": per_item,
        "resolution_warnings": warnings,
        "blocking_warnings": [item for item in warnings if item in _BLOCKING],
        "revision_match": revision_match,
        "review_units": [
            {"line_count": unit["line_count"], "over_budget": unit["line_count"] > budget, "unit_id": unit["unit_id"]}
            for unit in units
        ],
        "schema_version": "1.0",
        "split_reason": split_reason,
        "sufficient_units": single_units,
    }


def _item_coverage(
    item: dict[str, str],
    units: list[dict[str, object]],
    changed_symbols: list[dict[str, object]],
    symbol_warnings: list[dict[str, str]],
    locations: tuple[dict[str, object], ...],
    required: tuple[dict[str, str], ...],
) -> dict[str, object]:
    revisions, head_status = _needed_revisions(item, changed_symbols, symbol_warnings)
    hits: list[str] = []
    gaps: list[str] = []
    for unit in units:
        gap = _unit_gap(item, unit, revisions, changed_symbols, locations, required)
        if gap is None:
            hits.append(str(unit["unit_id"]))
        elif gap:
            gaps.append(gap)
    return {
        "covered": bool(hits),
        "gap": "" if hits else (gaps[0] if gaps else "missing"),
        "head_status": head_status,
        "path": item["path"],
        "reason": item["reason"],
        "symbol": item["symbol"],
        "units": hits,
    }


def _needed_revisions(
    item: dict[str, str],
    changed_symbols: list[dict[str, object]],
    symbol_warnings: list[dict[str, str]],
) -> tuple[set[str], str]:
    matches = [
        symbol
        for symbol in changed_symbols
        if symbol["path"] == item["path"] and _same_symbol(str(symbol["qualname"]), item["symbol"])
    ]
    revisions = {str(symbol["revision"]) for symbol in matches}
    absent = any(
        warning["code"] == "TS_SYMBOL_ABSENT"
        and warning["path"] == item["path"]
        and _identifier(warning["message"], item["symbol"])
        for warning in symbol_warnings
    )
    if absent and "head" not in revisions:
        return revisions or {"base"}, "absent"
    if not revisions:
        return set(), "not_required"
    return revisions, "present"


def _unit_gap(
    item: dict[str, str],
    unit: dict[str, object],
    revisions: set[str],
    changed_symbols: list[dict[str, object]],
    locations: tuple[dict[str, object], ...],
    required: tuple[dict[str, str], ...],
) -> str | None:
    excerpts = unit["excerpts"]
    assert isinstance(excerpts, list)
    same = [excerpt for excerpt in excerpts if excerpt["path"] == item["path"]]
    if revisions:
        for revision in revisions:
            span = _symbol_span(changed_symbols, item, revision)
            candidates = [
                excerpt
                for excerpt in same
                if excerpt["revision"] == revision and _covers_span(excerpt, span) and _body_gap(str(excerpt["content"]), item["symbol"]) is None
            ]
            if candidates:
                continue
            return _revision_gap(same, item["symbol"], revision)
    else:
        contents = [str(excerpt["content"]) for excerpt in same]
        gaps = [_body_gap(content, item["symbol"]) for content in contents]
        if not contents or all(gap is not None for gap in gaps):
            return gaps[0] if gaps else "missing"
    return _relationship_gap(item, excerpts, locations, required)


def _revision_gap(excerpts: list[dict[str, object]], symbol: str, revision: str) -> str:
    rows = [excerpt for excerpt in excerpts if excerpt["revision"] in {revision, ""}]
    if not rows:
        return "head_missing" if revision == "head" else "missing"
    if any(int(excerpt["line_start"]) <= 0 for excerpt in rows):
        return "head_missing" if revision == "head" else "missing"
    gaps = [_body_gap(str(excerpt["content"]), symbol) for excerpt in rows]
    for gap in gaps:
        if gap is not None:
            return gap
    return "declaration_without_body"


def _relationship_gap(
    item: dict[str, str],
    excerpts: list[dict[str, object]],
    locations: tuple[dict[str, object], ...],
    required: tuple[dict[str, str], ...],
) -> str | None:
    if item["symbol"] == "Validators":
        method = _primary_method(locations, item["path"])
        text = "\n".join(str(excerpt["content"]) for excerpt in excerpts)
        if _validators_linked(text, method):
            return None
        return "form_not_linked"
    if item["symbol"] == "required" and item["path"].endswith(".html") and any(row["symbol"] == "Validators" for row in required):
        html = "\n".join(str(excerpt["content"]) for excerpt in excerpts)
        return None if _required_control_linked(html, excerpts) else "form_not_linked"
    return None


def _single_units(
    required: tuple[dict[str, str], ...],
    units: list[dict[str, object]],
    per_item: tuple[dict[str, object], ...],
    max_files: int,
    budget: int,
) -> list[str]:
    if not required:
        return [str(unit["unit_id"]) for unit in units if int(unit["line_count"]) <= budget][:1] or (
            [str(units[0]["unit_id"])] if units and int(units[0]["line_count"]) <= budget else []
        )
    if len({item["path"] for item in required}) > max_files:
        return []
    common = set(per_item[0]["units"]) if per_item else set()
    for item in per_item[1:]:
        units_for_item = item["units"]
        assert isinstance(units_for_item, list)
        common &= set(units_for_item)
    return [
        str(unit["unit_id"])
        for unit in units
        if unit["unit_id"] in common and int(unit["line_count"]) <= budget
    ]


def _split_reason(
    required: tuple[dict[str, str], ...],
    per_item: tuple[dict[str, object], ...],
    max_files: int,
) -> str:
    if len({item["path"] for item in required}) > max_files:
        return "file_limit"
    common = set(per_item[0]["units"]) if per_item else set()
    for item in per_item[1:]:
        units_for_item = item["units"]
        assert isinstance(units_for_item, list)
        common &= set(units_for_item)
    if common:
        return "line_budget"
    return "across_units"


def _identifier(text: str, symbol: str) -> bool:
    return re.search(rf"(?<![A-Za-z0-9_$]){re.escape(symbol)}(?![A-Za-z0-9_$])", text) is not None


def _same_symbol(qualname: str, symbol: str) -> bool:
    return qualname == symbol or qualname.endswith("." + symbol)


def _symbols(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    rows: list[dict[str, object]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        start = item.get("line_start")
        end = item.get("line_end")
        if isinstance(start, bool) or isinstance(end, bool) or not isinstance(start, int) or not isinstance(end, int):
            continue
        path = item.get("path")
        qualname = item.get("qualname")
        revision = item.get("revision")
        if not isinstance(path, str) or not isinstance(qualname, str) or not isinstance(revision, str):
            continue
        rows.append(
            {"line_end": end, "line_start": start, "path": path, "qualname": qualname, "revision": revision}
        )
    return rows


def _symbol_warnings(angular: dict[str, object]) -> list[dict[str, str]]:
    source = angular.get("warnings")
    if not isinstance(source, list):
        return []
    rows: list[dict[str, str]] = []
    for item in source:
        if not isinstance(item, dict) or not isinstance(item.get("code"), str):
            continue
        rows.append(
            {
                "code": item["code"],
                "message": item.get("message") if isinstance(item.get("message"), str) else "",
                "path": item.get("path") if isinstance(item.get("path"), str) else "",
            }
        )
    return rows


def _symbol_span(changed_symbols: list[dict[str, object]], item: dict[str, str], revision: str) -> tuple[int, int] | None:
    spans = [
        (int(symbol["line_start"]), int(symbol["line_end"]))
        for symbol in changed_symbols
        if symbol["path"] == item["path"]
        and symbol["revision"] == revision
        and _same_symbol(str(symbol["qualname"]), item["symbol"])
        and int(symbol["line_start"]) > 0
    ]
    if not spans:
        return None
    return min(spans, key=lambda span: (span[1] - span[0], span[0]))


def _covers_span(excerpt: dict[str, object], span: tuple[int, int] | None) -> bool:
    if span is None:
        return int(excerpt["line_start"]) > 0
    start = int(excerpt["line_start"])
    end = int(excerpt["line_end"])
    return start > 0 and start <= span[0] and end >= span[1]


def _body_gap(content: str, symbol: str) -> str | None:
    masked = _mask_literals(content)
    if _html_attribute(content, symbol):
        return None
    if not _identifier(masked, symbol):
        return "missing"
    lines = masked.splitlines() or [masked]
    declared = _declaration_span(masked, symbol)
    if declared is not None:
        return None if _span_has_body(masked, declared) else "declaration_without_body"
    if _inside_other_body(masked, symbol):
        return None
    occurrences = [line for line in lines if _identifier(line, symbol)]
    if occurrences and all(line.lstrip().startswith("#") for line in occurrences):
        return "heading_only"
    return "identifier_only"


def _html_attribute(content: str, symbol: str) -> bool:
    name = re.escape(symbol)
    return re.search(rf"<[^>]*?(?:\[{name}\]|\b{name}\b)[^>]*>", content, re.S) is not None


def _declaration_span(text: str, symbol: str) -> tuple[int, int] | None:
    name = re.escape(symbol)
    patterns = (
        rf"(?m)^[ \t]*(?:export[ \t]+)?(?:declare[ \t]+)?(?:const|let|var|function|class)[ \t]+{name}\b",
        rf"(?m)^[ \t]*(?:public[ \t]+|private[ \t]+|protected[ \t]+|readonly[ \t]+|async[ \t]+|get[ \t]+|set[ \t]+)*{name}[ \t]*(?:<[^;{{\n]*>)?[ \t]*\(",
        rf"(?m)^[ \t]*constructor[ \t]*\(" if symbol == "constructor" else r"(?!)",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match is None:
            continue
        end = _statement_end(text, match.start())
        if end is None or end <= match.start():
            return match.start(), match.start()
        return match.start(), end
    return None


def _span_has_body(text: str, span: tuple[int, int]) -> bool:
    if span[1] <= span[0]:
        return False
    body = text[span[0] : span[1]]
    if "{" in body:
        return _substantive(body.split("{", 1)[1])
    if "=>" in body:
        return _substantive(body.split("=>", 1)[1])
    if "=" in body:
        return _substantive(body.split("=", 1)[1])
    return False


def _substantive(text: str) -> bool:
    for line in text.splitlines() or [text]:
        stripped = line.strip()
        if stripped and stripped not in {");", "}", "};", "),", "},"} and re.fullmatch(r"[)\]},;]+", stripped) is None:
            return True
    return False


def _inside_other_body(text: str, symbol: str) -> bool:
    for match in re.finditer(
        r"(?m)^[ \t]*(?:export[ \t]+)?(?:declare[ \t]+)?(?:const|let|var|function|class)[ \t]+[A-Za-z_$][\w$]*|^[ \t]*(?:public[ \t]+|private[ \t]+|protected[ \t]+|readonly[ \t]+|async[ \t]+)*[A-Za-z_$][\w$]*[ \t]*\([^;{\n]*\)[ \t]*(?::[ \t]*[^;{\n]+)?[ \t]*\{",
        text,
    ):
        end = _statement_end(text, match.start())
        if end is None:
            continue
        chunk = text[match.start() : end]
        if _span_has_body(text, (match.start(), end)) and _identifier(chunk, symbol):
            return True
    return False


def _statement_end(text: str, start: int) -> int | None:
    depth = 0
    seen_brace = False
    seen_equals = False
    index = start
    length = len(text)
    while index < length:
        char = text[index]
        nxt = text[index + 1] if index + 1 < length else ""
        if char == "/" and nxt == "/":
            newline = text.find("\n", index)
            index = length if newline < 0 else newline
            continue
        if char == "/" and nxt == "*":
            end = text.find("*/", index + 2)
            if end < 0:
                return None
            index = end + 2
            continue
        if char in "\"'`":
            index = _skip_quote(text, index)
            if index is None:
                return None
            continue
        if char in "([{":
            depth += 1
            seen_brace = seen_brace or char == "{"
        elif char in ")]}":
            depth -= 1
            if depth < 0:
                return None
            if depth == 0 and seen_brace and char == "}":
                return index + 1
        elif char == "=" and depth == 0:
            seen_equals = True
        elif char == ";" and depth == 0 and (seen_equals or seen_brace):
            return index + 1
        index += 1
    if depth == 0 and seen_equals:
        return length
    return None


def _skip_quote(text: str, start: int) -> int | None:
    quote = text[start]
    index = start + 1
    while index < len(text):
        char = text[index]
        if char == "\\":
            index += 2
            continue
        if quote == "`" and char == "$" and index + 1 < len(text) and text[index + 1] == "{":
            return None
        if char == quote:
            return index + 1
        if char == "\n" and quote != "`":
            return None
        index += 1
    return None


def _mask_literals(text: str) -> str:
    chars = list(text)
    index = 0
    while index < len(chars):
        char = chars[index]
        nxt = chars[index + 1] if index + 1 < len(chars) else ""
        if char == "/" and nxt == "/":
            while index < len(chars) and chars[index] != "\n":
                chars[index] = " "
                index += 1
            continue
        if char == "/" and nxt == "*":
            chars[index] = " "
            chars[index + 1] = " "
            index += 2
            while index < len(chars) and not (chars[index] == "*" and index + 1 < len(chars) and chars[index + 1] == "/"):
                if chars[index] != "\n":
                    chars[index] = " "
                index += 1
            continue
        if char in "\"'`":
            end = _skip_quote("".join(chars), index)
            if end is None:
                break
            for cursor in range(index + 1, end - 1):
                if chars[cursor] != "\n":
                    chars[cursor] = " "
            index = end
            continue
        index += 1
    return "".join(chars)


def _primary_method(locations: tuple[dict[str, object], ...], path: str) -> str | None:
    for location in locations:
        if location["path"] == path and isinstance(location["symbol"], str):
            return location["symbol"]
    return None


def _validators_linked(content: str, method: str | None) -> bool:
    members = _validator_members(content)
    if not members:
        return False
    if method is None:
        return True
    return bool(members & _method_members(content, method))


def _validator_members(content: str) -> set[str]:
    members: set[str] = set()
    for match in re.finditer(r"\.group\s*\(", content):
        end = _statement_end(content, match.start())
        if end is None:
            continue
        body = content[match.end() : end]
        if not _identifier(body, "Validators"):
            continue
        window = content[max(0, match.start() - 160) : match.start()]
        assigned = re.search(r"(?:this\.)?([A-Za-z_$][\w$]*)\s*=\s*[^;=]*$", window)
        if assigned:
            members.add(assigned.group(1))
    return members


def _method_members(content: str, method: str) -> set[str]:
    match = re.search(rf"\b{re.escape(method)}\s*\([^;{{\n]*\)\s*(?::\s*[^;{{\n]+)?\s*\{{", content)
    if match is None:
        return set()
    end = _statement_end(content, match.start())
    if end is None:
        return set()
    return set(re.findall(r"this\.([A-Za-z_$][\w$]*)", content[match.end() : end]))


def _required_control_linked(html: str, excerpts: list[dict[str, object]]) -> bool:
    names = set(re.findall(r"""formControlName\s*=\s*["']([^"']+)["']""", html))
    keys: set[str] = set()
    for excerpt in excerpts:
        if str(excerpt["path"]).endswith(".ts"):
            keys.update(_validator_keys(str(excerpt["content"])))
    return bool(names & keys)


def _validator_keys(content: str) -> set[str]:
    keys: set[str] = set()
    for match in re.finditer(r"\.group\s*\(", content):
        end = _statement_end(content, match.start())
        if end is None:
            continue
        body = content[match.end() : end]
        if not _identifier(body, "Validators"):
            continue
        keys.update(re.findall(r"(?m)^\s*([A-Za-z_$][\w$]*)\s*:", body))
    return keys


def _units(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise UsageError("review_units must be a list")
    units: list[dict[str, object]] = []
    for item in value:
        if not isinstance(item, dict):
            raise UsageError("each review unit must be an object")
        excerpts = item.get("excerpts")
        if not isinstance(excerpts, list):
            raise UsageError("review unit excerpts must be a list")
        parsed: list[dict[str, object]] = []
        for excerpt in excerpts:
            if not isinstance(excerpt, dict):
                raise UsageError("each excerpt must be an object")
            parsed.append(
                {
                    "content": excerpt.get("content") if isinstance(excerpt.get("content"), str) else "",
                    "line_end": excerpt.get("line_end") if isinstance(excerpt.get("line_end"), int) and not isinstance(excerpt.get("line_end"), bool) else 0,
                    "line_start": excerpt.get("line_start") if isinstance(excerpt.get("line_start"), int) and not isinstance(excerpt.get("line_start"), bool) else 0,
                    "path": excerpt.get("path") if isinstance(excerpt.get("path"), str) else "",
                    "revision": excerpt.get("revision") if isinstance(excerpt.get("revision"), str) else "",
                }
            )
        units.append(
            {
                "excerpts": parsed,
                "line_count": _count(item.get("line_count")),
                "unit_id": item.get("unit_id") if isinstance(item.get("unit_id"), str) else "",
            }
        )
    return units


def _changed(value: object) -> list[dict[str, int]]:
    if not isinstance(value, list):
        raise UsageError("changed_line_files must be a list")
    rows: list[dict[str, int]] = []
    for item in value:
        if not isinstance(item, dict):
            raise UsageError("each changed-line row must be an object")
        rows.append(
            {
                "changed_lines_included": _count(item.get("changed_lines_included")),
                "changed_lines_omitted": _count(item.get("changed_lines_omitted")),
                "changed_lines_total": _count(item.get("changed_lines_total")),
            }
        )
    return rows


def _warnings(context: dict[str, object], angular: dict[str, object]) -> list[str]:
    codes: set[str] = set()
    for source in (context.get("warnings"), angular.get("warnings")):
        if not isinstance(source, list):
            continue
        for item in source:
            if isinstance(item, dict) and item.get("code") in _NOTED:
                codes.add(str(item["code"]))
    return sorted(codes)


def _ratio(numerator: int, denominator: int) -> float | str:
    if denominator <= 0:
        return "N/A"
    return round(numerator / denominator, 4)


def _object(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise UsageError(f"{label} must be an object")
    return value


def _count(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise UsageError("a coverage count must be a non-negative integer")
    return value
