"""Bounded Angular excerpts in the existing review report."""

from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

from offline_review.cli import main
from offline_review.context_selector import ContextBudget, select_angular_context
from tests.support import commit_all, head_sha, init_repo, write

COMPONENT = """\
import { Component } from '@angular/core';
import { TreeService } from './tree.service';

@Component({
  selector: 'app-tree',
  templateUrl: './tree.component.html',
  standalone: true,
})
export class TreeComponent {}
"""

COMPONENT_HEAD = """\
import { Component } from '@angular/core';
import { TreeService } from './tree.service';

@Component({
  selector: 'app-tree',
  templateUrl: './tree.component.html',
  standalone: true,
})
export class TreeComponent {
  title = "kept";
}
"""

COMPONENT_DIRTY = COMPONENT_HEAD.replace("kept", "WORKTREE_ONLY")

MODULE = """\
import { NgModule } from '@angular/core';

@NgModule({
  declarations: [LoneComponent],
})
export class TreeModule {}
"""

MODULE_HEAD = MODULE.replace("export class TreeModule {}", "export class TreeModule {\n  ready = true;\n}")

GONE = """\
export function gone() {
  return "```";
}
"""

PANEL = """\
import { TreeComponent } from './tree.component';

export class PanelComponent {}
"""


class AngularContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.repo = root / "repo"
        self.output = root / "out"
        init_repo(self.repo)
        app = self.repo / "src" / "app"
        write(app / "tree.component.ts", COMPONENT.encode())
        write(app / "tree.component.html", b"<p>tree</p>\n")
        write(app / "tree.service.ts", b"export class TreeService {}\n")
        write(app / "tree.module.ts", MODULE.encode())
        write(app / "panel.component.ts", PANEL.encode())
        write(app / "lone.component.ts", b"export class LoneComponent {}\n")
        write(app / "gone.ts", GONE.encode())
        self.base = commit_all(self.repo, "base")
        write(app / "tree.component.ts", COMPONENT_HEAD.encode())
        write(app / "tree.module.ts", MODULE_HEAD.encode())
        (app / "gone.ts").unlink()
        self.head = commit_all(self.repo, "head")
        write(app / "tree.component.ts", COMPONENT_DIRTY.encode())
        write(app / "tree.service.ts", b"export class TreeService {}\n// WORKTREE_ONLY\n")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_budget_rejects_negative_values(self) -> None:
        with self.assertRaises(ValueError):
            select_angular_context(
                object(),
                (),
                diff_base="a" * 40,
                head="b" * 40,
                budget=ContextBudget(max_bytes=-1),
            )

    def test_priority_excerpts_come_from_blobs(self) -> None:
        report, markdown = self._analyze()
        self.assertEqual(report["schema_version"], "1.1.0")
        for key in (
            "comparison",
            "coverage",
            "errors",
            "files",
            "import_relationships",
            "python_roots",
            "repository",
            "schema_version",
            "tool",
            "warnings",
        ):
            self.assertIn(key, report)
        self.assertEqual(report["coverage"]["angular_semantic"], "out_of_scope")
        self.assertIn("angular_semantic_out_of_scope", report["coverage"]["partial_reasons"])
        self.assertFalse(report["coverage"]["analysis_complete"])
        angular = report["angular_context"]
        self.assertEqual(angular["schema"], "1")
        self.assertEqual(angular["status"], "partial")
        self.assertEqual(angular["analysis_method"], "conservative_lexical")
        self.assertEqual(angular["coverage"]["typescript_semantic"], "partial")
        self.assertEqual(angular["coverage"]["template_bindings"], "partial")
        edges = {
            (edge["kind"], edge["confidence"], edge["to_path"]) for edge in angular["edges"]
        }
        self.assertIn(("service_reference", "confirmed", "src/app/tree.service.ts"), edges)
        self.assertIn(("component_template", "confirmed", "src/app/tree.component.html"), edges)
        self.assertTrue(any(node["path"] == "src/app/gone.ts" for node in angular["nodes"]))
        reasons = [item["reason"] for item in angular["selected"]]
        self.assertLess(reasons.index("changed file"), reasons.index("confirmed direct dependency"))
        self.assertLess(
            reasons.index("confirmed direct dependency"),
            reasons.index("confirmed reverse dependency"),
        )
        self.assertLess(
            reasons.index("confirmed reverse dependency"),
            reasons.index("candidate dependency"),
        )
        gone = self._one(angular["selected"], "src/app/gone.ts", "base")
        self.assertEqual(gone["commit"], self.base)
        self.assertEqual(gone["content"], 'export function gone() {\n  return "```";\n}')
        self.assertEqual(gone["line_start"], 1)
        self.assertEqual(gone["line_end"], 3)
        head_component = self._one(angular["selected"], "src/app/tree.component.ts", "head")
        self.assertEqual(head_component["commit"], self.head)
        self.assertIn('title = "kept"', head_component["content"])
        self.assertNotIn("WORKTREE_ONLY", head_component["content"])
        service = self._one(angular["selected"], "src/app/tree.service.ts", "head")
        self.assertEqual(service["reason"], "confirmed direct dependency")
        self.assertNotIn("WORKTREE_ONLY", service["content"])
        self.assertNotIn("WORKTREE_ONLY", markdown)
        self.assertIn(self.base, markdown)
        self.assertIn("src/app/gone.ts", markdown)
        self.assertIn("changed file", markdown)
        self.assertIn('````\nexport function gone() {\n  return "```";\n}\n````', markdown)
        again, _again_markdown = self._analyze()
        self.assertEqual(again, report)
        self.assertEqual(_again_markdown, markdown)

    def test_depth_zero_keeps_changed_files_only(self) -> None:
        report, _markdown = self._analyze("--max-depth", "0")
        reasons = {item["reason"] for item in report["angular_context"]["selected"]}
        self.assertEqual(reasons, {"changed file"})
        self.assertFalse(report["angular_context"]["coverage"]["truncated"])

    def test_limits_are_recorded(self) -> None:
        limited, _markdown = self._analyze("--max-files", "1")
        angular = limited["angular_context"]
        self.assertEqual([item["path"] for item in angular["selected"]], ["src/app/gone.ts"])
        self.assertIn("max_files", {item["reason"] for item in angular["omitted"]})
        self.assertTrue(angular["coverage"]["truncated"])
        self.assertEqual(angular["status"], "partial")

        lines, _markdown = self._analyze("--max-lines-per-excerpt", "1")
        gone = self._one(lines["angular_context"]["selected"], "src/app/gone.ts", "base")
        self.assertEqual(gone["content"], "export function gone() {")
        self.assertEqual(gone["line_end"], 1)
        self.assertIn("max_lines", {item["reason"] for item in lines["angular_context"]["omitted"]})

        small, _markdown = self._analyze("--max-bytes", "4")
        excerpt = small["angular_context"]["selected"][0]
        self.assertLessEqual(len(excerpt["content"].encode("utf-8")), 4)
        self.assertIn("max_bytes", {item["reason"] for item in small["angular_context"]["omitted"]})

    def test_negative_arguments_exit_with_usage(self) -> None:
        self.assertEqual(self._code("--max-depth", "-1"), 2)
        self.assertEqual(self._code("--max-files", "-1"), 2)
        self.assertEqual(self._code("--max-bytes", "nope"), 2)
        self.assertEqual(self._code("--max-lines-per-excerpt", "-5"), 2)
        self.assertEqual(self._code("--max-total-lines", "-1"), 2)

    def _analyze(self, *extra: str) -> tuple[dict[str, object], str]:
        code, stderr = self._invoke(extra)
        if code != 0:
            raise AssertionError(stderr)
        report = json.loads((self.output / "review-context.json").read_text(encoding="utf-8"))
        markdown = (self.output / "review-context.md").read_text(encoding="utf-8")
        return report, markdown

    def _code(self, *extra: str) -> int:
        code, _stderr = self._invoke(extra)
        return code

    def _invoke(self, extra: tuple[str, ...]) -> tuple[int, str]:
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()) as stderr:
            code = main(
                [
                    "analyze",
                    "--repo",
                    str(self.repo),
                    "--base",
                    self.base,
                    "--head",
                    self.head,
                    "--output",
                    str(self.output),
                    "--comparison",
                    "direct",
                    *extra,
                ]
            )
        return code, stderr.getvalue()

    def _one(self, selected: list[dict[str, object]], path: str, revision: str) -> dict[str, object]:
        matches = [item for item in selected if item["path"] == path and item["revision"] == revision]
        self.assertEqual(len(matches), 1, matches)
        return matches[0]


class PythonOnlyReportTests(unittest.TestCase):
    def test_angular_section_is_not_applicable(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "repo"
            output = root / "out"
            init_repo(repo)
            write(repo / "app.py", b"def value():\n    return 1\n")
            base = commit_all(repo, "base")
            write(repo / "app.py", b"def value():\n    return 2\n")
            head = commit_all(repo, "head")
            with redirect_stdout(StringIO()), redirect_stderr(StringIO()) as stderr:
                code = main(
                    [
                        "analyze",
                        "--repo",
                        str(repo),
                        "--base",
                        base,
                        "--head",
                        head,
                        "--output",
                        str(output),
                        "--comparison",
                        "direct",
                        "--python-root",
                        ".",
                    ]
                )
            self.assertEqual(code, 0, stderr.getvalue())
            report = json.loads((output / "review-context.json").read_text(encoding="utf-8"))
            self.assertEqual(report["schema_version"], "1.1.0")
            self.assertEqual(report["tool"]["phase"], "2-python")
            self.assertEqual(report["coverage"]["angular_semantic"], "out_of_scope")
            self.assertNotIn("angular_semantic_out_of_scope", report["coverage"]["partial_reasons"])
            self.assertTrue(report["coverage"]["analysis_complete"])
            angular = report["angular_context"]
            self.assertEqual(angular["status"], "not_applicable")
            self.assertEqual(angular["schema"], "1")
            self.assertEqual(angular["analysis_method"], "none")
            self.assertEqual(angular["selected"], [])
            self.assertEqual(angular["edges"], [])
            self.assertEqual(angular["coverage"]["typescript_semantic"], "not_applicable")
            self.assertEqual(angular["coverage"]["template_bindings"], "unsupported")
            self.assertFalse(angular["coverage"]["truncated"])
            markdown = (output / "review-context.md").read_text(encoding="utf-8")
            self.assertIn("## Angular context", markdown)
            self.assertIn("Status: not_applicable", markdown)


if __name__ == "__main__":
    unittest.main()
