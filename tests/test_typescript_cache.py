"""TypeScript cache identity: commit, blob id, and extractor version."""

from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

from offline_review.cli import main
from offline_review.git_adapter import GitAdapter
from offline_review.typescript_cache import TypeScriptCache
from offline_review.typescript_index import index_typescript_commit
from tests.support import commit_all, head_sha, init_repo, write


def _blob_reads(adapter: GitAdapter, start: int) -> int:
    count = 0
    for command in adapter.invocations[start:]:
        if "--batch-check" in command:
            continue
        if "--batch" in command:
            count += 1
    return count


class TypeScriptCacheTests(unittest.TestCase):
    def test_blob_and_commit_records_do_not_cross_revisions(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "repo"
            init_repo(repo)
            for index in range(33):
                write(repo / "src" / f"f{index:02d}.ts", f"export class Item{index:02d} {{}}\n".encode())
            write(repo / "src" / "page.html", b"<p>page</p>\n")
            base = commit_all(repo, "base")
            write(repo / "src" / "f00.ts", b"export class Item00 {\n  next(): number { return 1; }\n}\n")
            commit_all(repo, "head")
            head = head_sha(repo)
            cache_dir = root / "typescript"
            with GitAdapter(repo) as adapter:
                fresh = index_typescript_commit(adapter, head)
                filled = TypeScriptCache(cache_dir)
                cached_base = index_typescript_commit(adapter, base, filled)
                self.assertEqual(index_typescript_commit(adapter, base), cached_base)
                start = len(adapter.invocations)
                reused = index_typescript_commit(adapter, head, TypeScriptCache(cache_dir))
                self.assertEqual(_blob_reads(adapter, start), 1)
                self.assertEqual(reused, fresh)
                self.assertTrue(any("next" in item.qualname for item in reused.files[0].symbols))
                start = len(adapter.invocations)
                again = index_typescript_commit(adapter, head, TypeScriptCache(cache_dir))
                self.assertEqual(_blob_reads(adapter, start), 0)
                self.assertEqual(again, fresh)
                for path in cache_dir.glob("*.json"):
                    if path.name != "blob-pack.json":
                        path.unlink()
                pack = json.loads((cache_dir / "blob-pack.json").read_text(encoding="utf-8"))
                pack["extractor_version"] = "stale"
                (cache_dir / "blob-pack.json").write_text(json.dumps(pack), encoding="utf-8")
                start = len(adapter.invocations)
                reread = index_typescript_commit(adapter, head, TypeScriptCache(cache_dir))
                self.assertGreaterEqual(_blob_reads(adapter, start), 2)
                self.assertEqual(reread, fresh)

    def test_cached_report_matches_the_uncached_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "repo"
            init_repo(repo)
            write(
                repo / "src" / "app" / "tree.component.ts",
                b"import { TreeService } from './tree.service';\nexport class TreeComponent {}\n",
            )
            write(repo / "src" / "app" / "tree.service.ts", b"export class TreeService {}\n")
            base = commit_all(repo, "base")
            write(
                repo / "src" / "app" / "tree.component.ts",
                b"import { TreeService } from './tree.service';\nexport class TreeComponent {\n  ready = true;\n}\n",
            )
            head = commit_all(repo, "head")
            plain = self._report(repo, base, head, root / "plain", None)
            cached = self._report(repo, base, head, root / "cached", root / "cache")
            again = self._report(repo, base, head, root / "again", root / "cache")
            self.assertEqual(plain, cached)
            self.assertEqual(cached, again)
            report = json.loads(plain.decode("utf-8"))
            self.assertEqual(report["schema_version"], "1.1.0")
            self.assertEqual(report["angular_context"]["status"], "partial")

    def _report(self, repo: Path, base: str, head: str, output: Path, cache: Path | None) -> bytes:
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
        ]
        if cache is not None:
            argv.extend(["--cache", str(cache)])
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()) as stderr:
            code = main(argv)
        if code != 0:
            raise AssertionError(stderr.getvalue())
        return (output / "review-context.json").read_bytes()


if __name__ == "__main__":
    unittest.main()
