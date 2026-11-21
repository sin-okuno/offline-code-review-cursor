"""Cache keys, invalidation, and identical reports. Synthetic repositories only."""

from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

from offline_review.cli import main
from offline_review.context_builder import build_context
from offline_review.dependency_index import IndexedModule
from offline_review.git_adapter import GitAdapter
from offline_review.parse_cache import ParseCache, cache_key
from offline_review.python_indexer import PARSER_VERSION, parse_python_source
from offline_review.reporters import render_json
from tests.support import commit_all, head_sha, init_repo, write


class ParseCacheUnitTests(unittest.TestCase):
    def test_key_depends_on_commit_roots_and_parser_version(self) -> None:
        commit = "a" * 40
        self.assertEqual(cache_key(commit, (".",)), cache_key(commit, (".",)))
        self.assertNotEqual(cache_key(commit, (".",)), cache_key(commit, ("src",)))
        self.assertNotEqual(cache_key(commit, (".",)), cache_key("b" * 40, (".",)))
        self.assertIn(PARSER_VERSION, json.dumps({"parser_version": PARSER_VERSION}))

    def test_parser_version_mismatch_and_corrupt_file_miss(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            cache = ParseCache(directory)
            commit = "c" * 40
            roots = (".",)
            modules = {
                "pkg/app.py": IndexedModule(
                    "pkg/app.py", commit, parse_python_source(b"value = 1\n")
                )
            }
            cache.store(commit, roots, modules, {"pkg/app.py": "d" * 40})
            path = directory / f"{cache_key(commit, roots)}.json"
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["parser_version"] = "not-this-parser"
            path.write_text(json.dumps(payload), encoding="utf-8")
            self.assertIsNone(cache.load(commit, roots))
            path.write_text("{", encoding="utf-8")
            self.assertIsNone(cache.load(commit, roots))
            self.assertFalse(path.exists())

    def test_roots_mismatch_misses_and_eviction_drops_the_oldest(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            cache = ParseCache(directory, max_bytes=1024 * 1024)
            first = "a" * 40
            second = "b" * 40
            modules = {
                "pkg/app.py": IndexedModule(
                    "pkg/app.py", first, parse_python_source(b"value = 1\n")
                )
            }
            cache.store(first, (".",), modules, {"pkg/app.py": "e" * 40})
            self.assertIsNone(cache.load(first, ("src",)))
            cache.store(second, (".",), modules, {"pkg/app.py": "f" * 40})
            newest = directory / f"{cache_key(second, ('.',))}.json"
            oldest = directory / f"{cache_key(first, ('.',))}.json"
            cache.max_bytes = newest.stat().st_size
            cache._evict(keep=newest)
            self.assertTrue(newest.exists())
            self.assertFalse(oldest.exists())


class ParseCacheGitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.output = self.root / "out"
        self.cache_dir = self.root / "cache"
        init_repo(self.repo, branch="develop")
        write(self.repo / "pkg" / "leaf.py", b"value = 1\n")
        write(
            self.repo / "pkg" / "app.py",
            b"from pkg.leaf import value\n\ndef changed():\n    return value\n",
        )
        commit_all(self.repo, "base")
        self.base = head_sha(self.repo)
        write(
            self.repo / "pkg" / "app.py",
            b"from pkg.leaf import value\n\ndef changed():\n    return value + 1\n",
        )
        commit_all(self.repo, "head")
        self.head = head_sha(self.repo)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_second_run_skips_tree_reads_and_matches_uncached_report(self) -> None:
        roots = (".",)
        with GitAdapter(self.repo) as adapter:
            cache = ParseCache(self.cache_dir)
            first = build_context(
                adapter,
                repository="synthetic",
                base=self.base,
                head="develop",
                comparison="direct",
                python_roots=roots,
                cache=cache,
            )
            trees_after_first = _ls_tree_count(adapter)
            self.assertGreaterEqual(trees_after_first, 2)
            second = build_context(
                adapter,
                repository="synthetic",
                base=self.base,
                head="develop",
                comparison="direct",
                python_roots=roots,
                cache=ParseCache(self.cache_dir),
            )
            self.assertEqual(_ls_tree_count(adapter), trees_after_first)
            uncached = build_context(
                adapter,
                repository="synthetic",
                base=self.base,
                head="develop",
                comparison="direct",
                python_roots=roots,
            )
        self.assertEqual(render_json(first), render_json(second))
        self.assertEqual(render_json(first), render_json(uncached))
        self.assertTrue(
            any(
                item.certainty == "confirmed" and item.other_path == "pkg/leaf.py"
                for item in first.import_relationships
            )
        )
        write(
            self.repo / "pkg" / "app.py",
            b"from pkg.leaf import value\n\ndef changed():\n    return value + 2\n",
        )
        commit_all(self.repo, "next")
        with GitAdapter(self.repo) as adapter:
            build_context(
                adapter,
                repository="synthetic",
                base=self.base,
                head="develop",
                comparison="direct",
                python_roots=roots,
                cache=ParseCache(self.cache_dir),
            )
            self.assertGreater(_ls_tree_count(adapter), 0)

    def test_cli_cache_matches_and_rejects_a_directory_inside_the_repo(self) -> None:
        cached = self.root / "cached-out"
        code, stderr = _analyze(self.repo, self.base, "develop", cached, self.cache_dir)
        self.assertEqual(code, 0, stderr)
        first = (cached / "review-context.json").read_bytes()
        code, stderr = _analyze(self.repo, self.base, "develop", cached, self.cache_dir)
        self.assertEqual(code, 0, stderr)
        self.assertEqual((cached / "review-context.json").read_bytes(), first)
        plain = self.root / "plain-out"
        code, stderr = _analyze(self.repo, self.base, "develop", plain, None)
        self.assertEqual(code, 0, stderr)
        self.assertEqual((plain / "review-context.json").read_bytes(), first)
        inside = self.repo / "cache"
        code, stderr = _analyze(self.repo, self.base, "develop", self.root / "unused", inside)
        self.assertEqual(code, 2, stderr)
        self.assertIn("outside the repository", stderr)
        self.assertFalse(inside.exists())
        self.assertFalse((self.root / "unused").exists())


def _ls_tree_count(adapter: GitAdapter) -> int:
    return sum(1 for command in adapter.invocations if "ls-tree" in command)


def _analyze(
    repo: Path, base: str, head: str, output: Path, cache: Path | None
) -> tuple[int, str]:
    argv = [
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
    if cache is not None:
        argv.extend(["--cache", str(cache)])
    stderr = StringIO()
    with redirect_stdout(StringIO()), redirect_stderr(stderr):
        code = main(argv)
    return code, stderr.getvalue()


if __name__ == "__main__":
    unittest.main()
