"""Unit and integration tests for the read-only Git adapter."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from offline_review.git_adapter import GitAdapter, is_lfs_pointer, validate_revision
from offline_review.models import GitError, RevisionError
from tests.support import commit_all, git, head_sha, init_repo, write


class RevisionValidationTests(unittest.TestCase):
    def test_rejects_option_injection_and_control_characters(self) -> None:
        for revision in (
            "",
            "  HEAD",
            "HEAD  ",
            "--upload-pack=evil",
            "-h",
            "a\nb",
            "a\x00b",
            "https://user:s3cret-token@example.invalid/a",
            "ext::python evil.py",
        ):
            with self.assertRaises(RevisionError):
                validate_revision(revision)
        self.assertEqual(validate_revision("HEAD"), "HEAD")
        self.assertEqual(validate_revision("HEAD@{1}"), "HEAD@{1}")
        self.assertEqual(validate_revision("feature/ü"), "feature/ü")
        with self.assertRaises(RevisionError) as caught:
            validate_revision("https://user:s3cret-token@example.invalid/a")
        self.assertNotIn("s3cret-token", str(caught.exception))


class GitAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name) / "repo"
        init_repo(self.repo, branch="develop")
        write(self.repo / "pkg" / "app.py", b"alpha\nbravo\ncharlie\n")
        commit_all(self.repo, "base")
        self.base = head_sha(self.repo)
        write(self.repo / "pkg" / "app.py", b"alpha\nbravo-changed\ncharlie\n")
        commit_all(self.repo, "head")
        self.head = head_sha(self.repo)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_resolves_immutable_commit_and_reads_object_not_worktree(self) -> None:
        with GitAdapter(self.repo) as adapter:
            resolved = adapter.resolve_commit("develop")
            self.assertEqual(resolved, self.head)
            self.assertEqual(len(resolved), 40)
            self.assertEqual(adapter.merge_base(self.base, self.head), self.base)
            content = adapter.read_bytes(self.head, "pkg/app.py")
            write(self.repo / "pkg" / "app.py", b"DIRTY-WORKTREE-LINE\n")
            self.assertEqual(adapter.read_bytes(self.head, "pkg/app.py"), content)
            self.assertNotEqual(content, b"DIRTY-WORKTREE-LINE\n")
            self.assertIsNone(adapter.read_bytes(self.head, "missing.py"))
            self.assertTrue(adapter.worktree_differs_from_head())

    def test_option_like_revision_is_not_executed(self) -> None:
        with GitAdapter(self.repo) as adapter:
            with self.assertRaises(RevisionError):
                adapter.resolve_commit("--upload-pack=evil")
            self.assertEqual(adapter.invocations, [])
            with self.assertRaises(RevisionError):
                adapter.resolve_commit("does-not-exist")
            self.assertTrue(all("fetch" not in command for command in adapter.invocations))
            joined = [" ".join(command) for command in adapter.invocations]
            self.assertTrue(all("ls-remote" not in command for command in joined))
            self.assertTrue(any("protocol.allow=never" in command for command in joined))

    def test_diff_commands_disable_external_diff_and_textconv(self) -> None:
        with GitAdapter(self.repo) as adapter:
            raw, _notice = adapter.diff_raw(self.base, self.head)
            patch, _notice = adapter.diff_patch(self.base, self.head)
            self.assertIn(b"pkg/app.py", raw)
            self.assertIn("bravo-changed", patch)
            diff_commands = [
                command
                for command in adapter.invocations
                if "diff" in command and ("--raw" in command or "--patch" in command)
            ]
            self.assertGreaterEqual(len(diff_commands), 2)
            for command in diff_commands:
                self.assertIn("--no-ext-diff", command)
                self.assertIn("--no-textconv", command)
                self.assertIn("--end-of-options", command)

    def test_blocks_network_subcommands(self) -> None:
        with GitAdapter(self.repo) as adapter:
            with self.assertRaises(GitError):
                adapter._run(["fetch", "origin"])
            with self.assertRaises(GitError):
                adapter._run(["clone", "https://example.invalid/repo.git"])

    def test_lfs_pointer_recognizes_pointer_text_only(self) -> None:
        pointer = (
            b"version https://git-lfs.github.com/spec/v1\n"
            b"oid sha256:" + (b"a" * 64) + b"\n"
            b"size 4\n"
        )
        self.assertTrue(is_lfs_pointer(pointer))
        self.assertFalse(is_lfs_pointer(b"version https://git-lfs.github.com/spec/v1\n"))
        self.assertFalse(is_lfs_pointer(pointer + b"\0"))


class ExternalDiffTests(unittest.TestCase):
    def test_repo_external_diff_is_not_executed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "repo"
            marker = root / "external-diff-marker"
            init_repo(repo)
            write(repo / "f.txt", b"a\n")
            commit_all(repo, "base")
            base = head_sha(repo)
            write(repo / "f.txt", b"b\n")
            commit_all(repo, "head")
            head = head_sha(repo)
            script = root / "evil.py"
            script.write_text(
                "from pathlib import Path\n"
                f"Path({str(marker)!r}).write_text('pwned', encoding='utf-8')\n",
                encoding="utf-8",
            )
            import sys

            executable = Path(sys.executable).as_posix()
            script_path = script.as_posix()
            git(repo, "config", "diff.external", f'"{executable}" "{script_path}"')
            unprotected = git(repo, "diff", "--no-color", base, head, check=False)
            self.assertTrue(
                marker.exists(),
                "the fixture could not prove that diff.external runs: "
                + unprotected.stderr.decode("utf-8", "replace"),
            )
            marker.unlink()
            with GitAdapter(repo) as adapter:
                _raw, _notice = adapter.diff_raw(base, head)
                patch, _notice = adapter.diff_patch(base, head)
            self.assertFalse(marker.exists())
            self.assertIn("-a", patch)
            self.assertIn("+b", patch)


if __name__ == "__main__":
    unittest.main()
