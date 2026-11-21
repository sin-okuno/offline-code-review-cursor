"""End-to-end tests against synthetic temporary Git repositories."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

from offline_review.cli import main
from offline_review.diff_parser import parse_raw_diff_z
from offline_review.git_adapter import GitAdapter
from tests.support import blob_sha, commit_all, git, head_sha, index_digest, init_repo, write

APP_BASE = b"alpha\nbravo\ncharlie\ndelta\necho\n"
APP_HEAD = b"alpha\nbravo-changed\ncharlie\necho\n"
GONE = b"one\ntwo\nthree\n"
NOTES = "dir with space/notes ü.txt"
NOTES_RENAMED = "dir with space/notes ü renamed.txt"


def _lfs(size: int) -> bytes:
    return (
        b"version https://git-lfs.github.com/spec/v1\n"
        b"oid sha256:" + (b"ab" * 32) + b"\n"
        + f"size {size}\n".encode("ascii")
    )


class ScaffoldIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.output = self.root / "output"
        init_repo(self.repo, branch="develop")
        write(self.repo / "pkg" / "app.py", APP_BASE)
        write(self.repo / "pkg" / "gone.py", GONE)
        write(self.repo / NOTES, b"keep\n")
        write(self.repo / "assets" / "logo.bin", b"\x00\x01\xff")
        write(self.repo / "media" / "blob.lfs", _lfs(4))
        write(self.repo / "web" / "app.component.ts", b"export class App {}\n")
        commit_all(self.repo, "base")
        submodule = self.repo / "vendor" / "lib"
        init_repo(submodule)
        write(submodule / "readme.txt", b"sub\n")
        commit_all(submodule, "submodule base")
        self.gitlink_old = head_sha(submodule)
        git(
            self.repo,
            "update-index",
            "--add",
            "--cacheinfo",
            f"160000,{self.gitlink_old},vendor/lib",
        )
        git(self.repo, "commit", "-m", "gitlink")
        self.base = head_sha(self.repo)
        write(self.repo / "pkg" / "app.py", APP_HEAD)
        write(self.repo / "pkg" / "app_copy.py", APP_HEAD)
        write(self.repo / "pkg" / "new.py", b"added\n")
        write(self.repo / "assets" / "logo.bin", b"\x00\x02\xff")
        write(self.repo / "media" / "blob.lfs", _lfs(8))
        write(self.repo / "web" / "app.component.ts", b"export class App { changed = true; }\n")
        git(self.repo, "add", "--", "pkg/app.py", "pkg/app_copy.py", "pkg/new.py", "assets/logo.bin", "media/blob.lfs", "web/app.component.ts")
        git(self.repo, "rm", "--", "pkg/gone.py")
        git(self.repo, "mv", "--", NOTES, NOTES_RENAMED)
        write(submodule / "readme.txt", b"sub-head\n")
        commit_all(submodule, "submodule head")
        self.gitlink_new = head_sha(submodule)
        git(
            self.repo,
            "update-index",
            "--add",
            "--cacheinfo",
            f"160000,{self.gitlink_new},vendor/lib",
        )
        git(self.repo, "commit", "-m", "head")
        self.head = head_sha(self.repo)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_modified_and_deleted_lines_are_traced_to_commits(self) -> None:
        before_index = index_digest(self.repo)
        before_submodule = index_digest(self.repo / "vendor" / "lib")
        before_app = (self.repo / "pkg" / "app.py").read_bytes()
        code = self._analyze("merge-base")
        self.assertEqual(code, 0, self._stderr)
        self.assertEqual(index_digest(self.repo), before_index)
        self.assertEqual(index_digest(self.repo / "vendor" / "lib"), before_submodule)
        self.assertEqual((self.repo / "pkg" / "app.py").read_bytes(), before_app)
        first = (self.output / "review-context.json").read_bytes()
        markdown = (self.output / "review-context.md").read_text(encoding="utf-8")
        self.assertEqual(self._analyze("merge-base"), 0)
        self.assertEqual((self.output / "review-context.json").read_bytes(), first)

        report = json.loads(first.decode("utf-8"))
        self.assertTrue(first.decode("utf-8").startswith('{\n  "comparison"'))
        self.assertEqual(report["schema_version"], "1.1.0")
        self.assertEqual(report["tool"]["phase"], "2-python")
        comparison = report["comparison"]
        self.assertEqual(comparison["mode"], "merge-base")
        self.assertEqual(comparison["semantics"], "three-dot")
        self.assertEqual(comparison["requested_head"], "develop")
        self.assertEqual(comparison["base_commit"], self.base)
        self.assertEqual(comparison["head_commit"], self.head)
        self.assertEqual(comparison["diff_base_commit"], self.base)
        self.assertEqual(comparison["merge_base_commit"], self.base)
        self.assertEqual(len(self.base), 40)
        self.assertFalse(report["coverage"]["analysis_complete"])
        self.assertTrue(report["coverage"]["partial"])
        self.assertIn("python_roots_not_configured", report["coverage"]["partial_reasons"])
        self.assertNotIn("python_ast_not_implemented", report["coverage"]["partial_reasons"])
        self.assertEqual(report["coverage"]["python_ast"], "parsed")
        self.assertEqual(report["coverage"]["dependency_index"], "not_configured")
        self.assertEqual(report["coverage"]["angular_semantic"], "out_of_scope")
        self.assertGreaterEqual(report["coverage"]["deletion_hunks"], 1)
        self.assertEqual(report["errors"], [])

        by_key = {(item["old_path"], item["new_path"]): item for item in report["files"]}
        app = by_key[("pkg/app.py", "pkg/app.py")]
        self.assertEqual(app["status_code"], "M")
        self.assertEqual(app["old_changed_lines"], [2, 4])
        self.assertEqual(app["new_changed_lines"], [2])
        self.assertEqual([hunk["kind"] for hunk in app["hunks"]], ["edit", "deletion"])
        self.assertEqual(app["hunks"][1]["old_count"], 1)
        self.assertEqual(app["hunks"][1]["new_count"], 0)
        self.assertEqual(app["old_changed_line_ranges"], [{"start": 2, "end": 2}, {"start": 4, "end": 4}])
        self.assertEqual(app["diff_base_commit"], self.base)
        self.assertEqual(app["head_commit"], self.head)
        self.assertEqual(app["old_blob"], blob_sha(self.repo, self.base, "pkg/app.py"))
        self.assertEqual(app["new_blob"], blob_sha(self.repo, self.head, "pkg/app.py"))

        gone = by_key[("pkg/gone.py", None)]
        self.assertEqual(gone["status"], "deleted")
        self.assertEqual(gone["old_changed_lines"], [1, 2, 3])
        self.assertEqual(gone["new_changed_lines"], [])
        self.assertEqual(gone["hunks"][0]["kind"], "deletion")
        self.assertEqual(gone["hunks"][0]["new_count"], 0)
        self.assertIsNone(gone["new_path"])
        self.assertIsNone(gone["new_blob"])

        added = by_key[(None, "pkg/new.py")]
        self.assertEqual(added["status_code"], "A")
        self.assertEqual(added["new_changed_lines"], [1])
        self.assertEqual(added["hunks"][0]["kind"], "addition")

        renamed = by_key[(NOTES, NOTES_RENAMED)]
        self.assertEqual(renamed["status_code"], "R")
        self.assertEqual(renamed["similarity"], 100)
        self.assertEqual(renamed["hunks"], [])

        logo = by_key[("assets/logo.bin", "assets/logo.bin")]
        self.assertTrue(logo["binary"])
        self.assertEqual(logo["hunks"], [])

        pointer = by_key[("media/blob.lfs", "media/blob.lfs")]
        self.assertTrue(pointer["lfs_pointer_old"])
        self.assertTrue(pointer["lfs_pointer_new"])

        submodule = by_key[("vendor/lib", "vendor/lib")]
        self.assertTrue(submodule["submodule"])
        self.assertEqual(submodule["old_mode"], "160000")
        self.assertEqual(submodule["new_mode"], "160000")
        self.assertEqual(submodule["hunks"], [])
        self.assertEqual(submodule["old_blob"], self.gitlink_old)
        self.assertEqual(submodule["new_blob"], self.gitlink_new)

        self._assert_status_matches_git(report["files"])
        codes = {item["code"] for item in report["warnings"]}
        self.assertIn("PYTHON_ROOTS_NOT_CONFIGURED", codes)
        self.assertNotIn("PYTHON_AST_NOT_IMPLEMENTED", codes)
        self.assertNotIn("DEPENDENCY_INDEX_NOT_IMPLEMENTED", codes)
        self.assertIn("BINARY_FILE", codes)
        self.assertIn("LFS_POINTER", codes)
        self.assertIn("SUBMODULE", codes)
        self.assertIn("ANGULAR_SEMANTIC_OUT_OF_SCOPE", codes)
        self.assertNotIn("WORKTREE_DIRTY", codes)
        self.assertIn("Old changed lines: 2-2, 4-4", markdown)
        self.assertIn("Old changed lines: 1-3", markdown)
        self.assertIn("Three-dot comparison", markdown)
        self.assertIn(NOTES, markdown)
        self.assertNotIn("DIRTY-WORKTREE", markdown)

        with GitAdapter(self.repo) as adapter:
            self.assertEqual(adapter.read_bytes(self.head, "pkg/app.py"), APP_HEAD)
            self.assertEqual(adapter.read_bytes(self.head, NOTES_RENAMED), b"keep\n")

    def test_dirty_worktree_does_not_change_committed_context(self) -> None:
        self.assertEqual(self._analyze("direct"), 0)
        clean = json.loads((self.output / "review-context.json").read_text(encoding="utf-8"))
        write(self.repo / "pkg" / "app.py", b"DIRTY-WORKTREE-LINE\n")
        write(self.repo / "secret-untracked.txt", b"SECRET-UNTRACKED\n")
        dirty_output = self.root / "dirty-output"
        self.assertEqual(self._analyze("direct", output=dirty_output), 0)
        dirty = json.loads((dirty_output / "review-context.json").read_text(encoding="utf-8"))
        self.assertEqual(clean["files"], dirty["files"])
        self.assertEqual(clean["comparison"]["diff_base_commit"], dirty["comparison"]["diff_base_commit"])
        rendered = (dirty_output / "review-context.json").read_text(encoding="utf-8")
        self.assertNotIn("DIRTY-WORKTREE-LINE", rendered)
        self.assertNotIn("SECRET-UNTRACKED", rendered)
        self.assertIn("WORKTREE_DIRTY", {item["code"] for item in dirty["warnings"]})
        with GitAdapter(self.repo) as adapter:
            self.assertEqual(adapter.read_bytes(self.head, "pkg/app.py"), APP_HEAD)

    def test_missing_ref_and_output_inside_repo_write_nothing(self) -> None:
        missing = self.root / "missing-output"
        code = self._analyze("merge-base", output=missing, base="no-such-ref")
        self.assertEqual(code, 1)
        self.assertFalse(missing.exists())
        self.assertIn("cannot resolve", self._stderr)
        inside = self.repo / "inside-output"
        code = self._analyze("direct", output=inside)
        self.assertEqual(code, 2)
        self.assertFalse(inside.exists())

    def test_module_entrypoint_writes_reports(self) -> None:
        env = os.environ.copy()
        src = Path(__file__).resolve().parents[1] / "src"
        env["PYTHONPATH"] = str(src)
        output = self.root / "module-output"
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "offline_review",
                "analyze",
                "--repo",
                str(self.repo),
                "--base",
                self.base,
                "--head",
                "develop",
                "--output",
                str(output),
                "--comparison",
                "direct",
            ],
            cwd=self.root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads((output / "review-context.json").read_text(encoding="utf-8"))
        gone = next(item for item in report["files"] if item["old_path"] == "pkg/gone.py")
        self.assertEqual(gone["old_changed_lines"], [1, 2, 3])
        self.assertEqual(gone["new_changed_lines"], [])

    def _analyze(
        self,
        comparison: str,
        *,
        output: Path | None = None,
        base: str | None = None,
        head: str | None = None,
    ) -> int:
        stdout = StringIO()
        stderr = StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(
                [
                    "analyze",
                    "--repo",
                    str(self.repo),
                    "--base",
                    self.base if base is None else base,
                    "--head",
                    "develop" if head is None else head,
                    "--output",
                    str(self.output if output is None else output),
                    "--comparison",
                    comparison,
                ]
            )
        self._stderr = stderr.getvalue()
        return code

    def _assert_status_matches_git(self, files: list[dict[str, object]]) -> None:
        raw = git(
            self.repo,
            "diff",
            "--raw",
            "-z",
            "--abbrev=64",
            "-M",
            "-C",
            "--no-ext-diff",
            "--no-textconv",
            self.base,
            self.head,
        ).stdout
        expected = {
            (item.old_path, item.new_path, item.status_code, item.similarity)
            for item in parse_raw_diff_z(raw)
        }
        actual = {
            (item["old_path"], item["new_path"], item["status_code"], item["similarity"])
            for item in files
        }
        self.assertEqual(actual, expected)


class ComparisonModeTests(unittest.TestCase):
    def test_merge_base_and_direct_use_different_diff_bases(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "repo"
            init_repo(repo)
            write(repo / "story.txt", b"base\n")
            root_commit = commit_all(repo, "root")
            git(repo, "checkout", "-b", "feature")
            write(repo / "story.txt", b"feature\n")
            feature = commit_all(repo, "feature")
            git(repo, "checkout", "main")
            write(repo / "story.txt", b"mainline\n")
            mainline = commit_all(repo, "mainline")
            merge_base_report = _run(repo, root / "merge-base", "main", "feature", "merge-base")
            direct_report = _run(repo, root / "direct", "main", "feature", "direct")
            self.assertEqual(merge_base_report["comparison"]["diff_base_commit"], root_commit)
            self.assertEqual(merge_base_report["comparison"]["merge_base_commit"], root_commit)
            self.assertEqual(merge_base_report["comparison"]["semantics"], "three-dot")
            self.assertEqual(direct_report["comparison"]["diff_base_commit"], mainline)
            self.assertEqual(direct_report["comparison"]["base_commit"], mainline)
            self.assertEqual(direct_report["comparison"]["head_commit"], feature)
            self.assertEqual(direct_report["comparison"]["semantics"], "two-dot")
            merge_file = merge_base_report["files"][0]
            direct_file = direct_report["files"][0]
            self.assertEqual(merge_file["old_blob"], blob_sha(repo, root_commit, "story.txt"))
            self.assertEqual(direct_file["old_blob"], blob_sha(repo, mainline, "story.txt"))
            self.assertEqual(merge_file["new_blob"], blob_sha(repo, feature, "story.txt"))
            self.assertNotEqual(merge_file["old_blob"], direct_file["old_blob"])

    def test_unrelated_histories_require_direct_comparison(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "repo"
            init_repo(repo)
            write(repo / "left.txt", b"left\n")
            commit_all(repo, "left")
            git(repo, "checkout", "--orphan", "other")
            git(repo, "rm", "-rf", "--", ".")
            write(repo / "right.txt", b"right\n")
            git(repo, "add", "--", "right.txt")
            git(repo, "commit", "-m", "right")
            stderr = StringIO()
            with redirect_stderr(stderr):
                code = main(
                    [
                        "analyze",
                        "--repo",
                        str(repo),
                        "--base",
                        "main",
                        "--head",
                        "other",
                        "--output",
                        str(root / "rejected"),
                        "--comparison",
                        "merge-base",
                    ]
                )
            self.assertEqual(code, 1)
            self.assertIn("no merge base", stderr.getvalue())
            self.assertFalse((root / "rejected").exists())
            report = _run(repo, root / "direct", "main", "other", "direct")
            self.assertIsNone(report["comparison"]["merge_base_commit"])
            self.assertIn("NO_MERGE_BASE", {item["code"] for item in report["warnings"]})
            paths = {(item["old_path"], item["new_path"]) for item in report["files"]}
            self.assertIn(("left.txt", None), paths)
            self.assertIn((None, "right.txt"), paths)


class HelpTests(unittest.TestCase):
    def test_help_and_missing_arguments(self) -> None:
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            self.assertEqual(main(["--help"]), 0)
            self.assertEqual(main(["analyze", "--help"]), 0)
            self.assertEqual(main(["analyze"]), 2)


def _run(repo: Path, output: Path, base: str, head: str, comparison: str) -> dict[str, object]:
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
                comparison,
            ]
        )
    if code != 0:
        raise AssertionError(stderr.getvalue())
    return json.loads((output / "review-context.json").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
