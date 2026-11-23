"""JSON and Markdown expose the same aggregate numbers."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from offline_review.evaluation.metrics import summarize
from offline_review.evaluation.report import render_markdown, write_evaluation
from offline_review.models import UsageError


class ReportTests(unittest.TestCase):
    def test_markdown_repeats_the_json_summary(self) -> None:
        summary = summarize([])
        report = {"case_id": "ANG-001", "schema_version": "1.0", "summary": summary}
        markdown = render_markdown(report)
        self.assertIn(f"- recall: {summary['recall']}", markdown)
        self.assertIn(f"- precision: {summary['precision']}", markdown)
        self.assertIn(f"- changed_line_coverage: {summary['changed_line_coverage']}", markdown)
        self.assertIn("does not prove operational review quality", markdown)

    def test_report_does_not_replace_an_input(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "ground.json"
            source.write_text("{}", encoding="utf-8")
            with self.assertRaises(UsageError):
                write_evaluation({"schema_version": "1.0"}, source, inputs=(source,))

    def test_written_json_matches_the_markdown_tokens(self) -> None:
        summary = summarize([])
        report = {"schema_version": "1.0", "summary": summary}
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "quality-report.json"
            write_evaluation(report, output, inputs=())
            written = json.loads(output.read_text(encoding="utf-8"))
            markdown = output.with_suffix(".md").read_text(encoding="utf-8")
        self.assertEqual(written["summary"]["recall"], "N/A")
        self.assertIn("- recall: N/A", markdown)


if __name__ == "__main__":
    unittest.main()
