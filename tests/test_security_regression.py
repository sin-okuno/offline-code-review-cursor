"""Adversarial TypeScript fixtures for the review report.

Comments, strings, and malformed source must not become confirmed edges.
The repository work tree is not modified, and excerpt text is redacted.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

from offline_review.cli import main
from tests.support import commit_all, index_digest, init_repo, write

TOKEN = "abc.def-ghi"
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
COMPONENT_HEAD = f"""\
import {{ Component }} from '@angular/core';
import {{ TreeService }} from './tree.service';

/* import {{ SecretService }} from './secret.service'; */
// @Component({{ templateUrl: './fake.html' }})
// Authorization: Bearer {TOKEN}
const text = "import {{ Evil }} from './evil'";

@Component({{
  selector: 'app-tree',
  templateUrl: './tree.component.html',
  standalone: true,
}})
export class TreeComponent {{}}
"""
BROKEN = """\
export class Broken {
  name = 'import { Evil } from "./evil";
}
"""
ESCAPE = """\
import { Outside } from '../../../../outside';

export class Escape {}
"""
UNICODE_PATH = "src/app/木.ts"


class AdversarialReportTests(unittest.TestCase):
    def test_fake_imports_and_malformed_source_are_not_confirmed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "repo"
            output = root / "out"
            init_repo(repo)
            app = repo / "src" / "app"
            write(app / "tree.component.ts", COMPONENT.encode())
            write(app / "tree.component.html", b"<p>tree</p>\n")
            write(app / "tree.service.ts", b"export class TreeService {}\n")
            write(app / "secret.service.ts", b"export class SecretService {}\n")
            write(app / "evil.ts", b"export class Evil {}\n")
            write(app / "fake.html", b"<p>fake</p>\n")
            write(app / "broken.ts", b"export class Broken {}\n")
            write(app / "escape.ts", b"export class Escape {}\n")
            write(repo / UNICODE_PATH, "export const label = 'before';\n".encode())
            base = commit_all(repo, "base")
            write(app / "tree.component.ts", COMPONENT_HEAD.encode())
            write(app / "broken.ts", BROKEN.encode())
            write(app / "escape.ts", ESCAPE.encode())
            write(repo / UNICODE_PATH, "export const label = 'after';\n".encode())
            head = commit_all(repo, "head")
            write(app / "tree.component.ts", COMPONENT_HEAD.encode() + b"\n// WORKTREE_ONLY\n")
            before = index_digest(repo)

            first = self._run(repo, output, base, head)
            second = self._run(repo, output, base, head)
            self.assertEqual(second, first)
            self.assertEqual(index_digest(repo), before)
            self.assertFalse((repo / "outside.ts").exists())

            report = json.loads(first)
            self.assertEqual(report["schema_version"], "1.1.0")
            self.assertEqual(report["coverage"]["angular_semantic"], "out_of_scope")
            self.assertFalse(report["coverage"]["analysis_complete"])
            angular = report["angular_context"]
            self.assertEqual(angular["status"], "partial")
            self.assertEqual(angular["analysis_method"], "conservative_lexical")
            targets = {edge["to_path"] for edge in angular["edges"]}
            confirmed = {
                (edge["kind"], edge["confidence"], edge["to_path"])
                for edge in angular["edges"]
                if edge["confidence"] == "confirmed"
            }
            self.assertIn(("service_reference", "confirmed", "src/app/tree.service.ts"), confirmed)
            self.assertIn(("component_template", "confirmed", "src/app/tree.component.html"), confirmed)
            self.assertNotIn("src/app/secret.service.ts", targets)
            self.assertNotIn("src/app/evil.ts", targets)
            self.assertNotIn("src/app/fake.html", targets)
            self.assertFalse(any(path is not None and (path.startswith(("/", "\\")) or ".." in path) for path in targets))
            codes = {(item["code"], item["path"]) for item in angular["warnings"]}
            self.assertIn(("TS_UNCLOSED", "src/app/broken.ts"), codes)
            self.assertIn(("TS_IMPORT_UNRESOLVED", "src/app/escape.ts"), codes)
            selected = {(item["revision"], item["path"]) for item in angular["selected"]}
            self.assertIn(("head", "src/app/broken.ts"), selected)
            self.assertIn(("head", UNICODE_PATH), selected)
            self.assertNotIn(TOKEN, first.decode("utf-8"))
            self.assertNotIn("WORKTREE_ONLY", first.decode("utf-8"))
            markdown = (output / "review-context.md").read_text(encoding="utf-8")
            self.assertIn(head, markdown)
            self.assertIn(UNICODE_PATH, markdown)
            self.assertNotIn(TOKEN, markdown)
            self.assertNotIn("WORKTREE_ONLY", markdown)

    def _run(self, repo: Path, output: Path, base: str, head: str) -> bytes:
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
                ]
            )
        if code != 0:
            raise AssertionError(stderr.getvalue())
        return (output / "review-context.json").read_bytes()


if __name__ == "__main__":
    unittest.main()
