"""Match saved findings to one ground-truth case without calling a model."""

from __future__ import annotations

import re

from offline_review.evaluation.schemas import validate_findings, validate_ground_truth

_CAUSES = {
    "ai_missed_with_sufficient_context",
    "ambiguous_ground_truth",
    "cross_unit_context_gap",
    "extractor_missing_context",
    "not_evaluated",
}


def match_findings(
    findings: dict[str, object] | None,
    ground_truth: dict[str, object],
    context_evaluation: dict[str, object] | None = None,
) -> dict[str, object]:
    """Classify one case. A missing findings document is not a false negative."""

    truth = validate_ground_truth(ground_truth)
    if findings is None:
        return _result(truth, "not_evaluated", "not_evaluated", (), (), (), ())
    parsed = validate_findings(findings)
    if parsed["case_id"] != truth["case_id"]:
        return _result(truth, "not_evaluated", "ambiguous_ground_truth", (), (), (), ())
    rows = parsed["findings"]
    assert isinstance(rows, list)
    if truth["case_kind"] == "clean":
        return _result(
            truth,
            "false_positive" if rows else "true_negative",
            "",
            (),
            tuple(str(item["finding_id"]) for item in rows),
            (),
            (),
        )
    true_positive: str | None = None
    duplicates: list[str] = []
    manual: list[dict[str, str]] = []
    false_positive: list[str] = []
    for item in rows:
        assert isinstance(item, dict)
        decision = _decision(item, truth)
        finding_id = str(item["finding_id"])
        if decision == "match":
            if true_positive is None:
                true_positive = finding_id
            else:
                duplicates.append(finding_id)
        elif decision == "manual":
            manual.append({"finding_id": finding_id, "reason": "path and phrases did not both meet the automatic rule"})
        else:
            false_positive.append(finding_id)
    if true_positive is not None:
        status = "true_positive"
        cause = ""
    elif manual:
        status = "needs_manual_review"
        cause = "ambiguous_ground_truth"
    else:
        status = "false_negative"
        cause = _cause(truth, context_evaluation)
    return _result(
        truth,
        status,
        cause,
        (true_positive,) if true_positive else (),
        tuple(false_positive),
        tuple(duplicates),
        tuple(manual),
    )


def _decision(finding: dict[str, object], truth: dict[str, object]) -> str:
    locations = truth["primary_locations"]
    assert isinstance(locations, tuple)
    path_hit = any(finding["path"] == item["path"] for item in locations)
    line = finding["line"]
    line_hit = any(
        isinstance(line, int) and int(item["start_line"]) <= line <= int(item["end_line"]) for item in locations
    )
    terms = truth["match_terms"]
    assert isinstance(terms, tuple)
    text = " ".join(str(finding[key]) for key in ("title", "description", "evidence", "suggestion"))
    hits = sum(1 for term in terms if _term(text, str(term)))
    if path_hit and line_hit and hits >= 2:
        return "match"
    if (path_hit and hits >= 1) or hits >= 2 or (path_hit and line_hit):
        return "manual"
    return "unrelated"


def _cause(truth: dict[str, object], context_evaluation: dict[str, object] | None) -> str:
    if truth["ambiguous"] is True:
        return "ambiguous_ground_truth"
    if context_evaluation is None:
        return "not_evaluated"
    if context_evaluation.get("case_id") != truth["case_id"]:
        return "ambiguous_ground_truth"
    if context_evaluation.get("revision_match") is not True:
        return "ambiguous_ground_truth"
    if context_evaluation.get("case_context_sufficient") is True:
        return "ai_missed_with_sufficient_context"
    if context_evaluation.get("cross_unit_dependency") is True:
        return "cross_unit_context_gap"
    if context_evaluation.get("combined_units_sufficient") is not True:
        return "extractor_missing_context"
    return "extractor_missing_context"


def _term(text: str, term: str) -> bool:
    haystack = text.casefold()
    needle = term.casefold()
    if any(character.isspace() for character in needle) or not needle.isalnum():
        return needle in haystack
    return re.search(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])", haystack) is not None


def _result(
    truth: dict[str, object],
    status: str,
    cause: str,
    true_positive: tuple[str, ...],
    false_positive: tuple[str, ...],
    duplicates: tuple[str, ...],
    manual: tuple[dict[str, str], ...],
) -> dict[str, object]:
    if cause and cause not in _CAUSES:
        cause = "ambiguous_ground_truth"
    return {
        "case_id": truth["case_id"],
        "case_kind": truth["case_kind"],
        "duplicate_finding_ids": list(duplicates),
        "false_negative": 1 if status == "false_negative" else 0,
        "false_positive_finding_ids": list(false_positive),
        "manual_review": [dict(item) for item in manual],
        "miss_cause": cause,
        "schema_version": "1.0",
        "status": status,
        "true_positive_finding_ids": list(true_positive),
    }
