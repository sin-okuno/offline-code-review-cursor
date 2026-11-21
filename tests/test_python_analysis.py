"""Python symbol mapping and import-index tests."""

from __future__ import annotations

import json
import tempfile
import unittest
from io import StringIO
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from offline_review.cli import main
from offline_review.dependency_index import (
    IndexedModule,
    analyze_python,
    module_identity,
    normalize_python_roots,
)
from offline_review.git_adapter import parse_ls_tree_z
from offline_review.models import FileChange, UsageError
from offline_review.python_indexer import map_affected_symbols, parse_python_source
from tests.support import commit_all, git, head_sha, init_repo, write

SAMPLE_BASE = """\
VALUE = 1

class Outer:
    def method(self):
        async def inner():
            marker = 1
            return marker
        return inner

def keep():
    first = 1
    remove_me = 2
    third = 3

def untouched():
    return 0
"""

SAMPLE_HEAD = """\
VALUE = 2

class Outer:
    def method(self):
        async def inner():
            marker = 3
            return marker
        return inner

def keep():
    first = 1
    third = 3

def untouched():
    return 0
"""

USE_BASE = """\
from .helper import helper
import os

def run():
    return helper()
"""

USE_HEAD = """\
from .helper import helper
import os

def run():
    return helper() + 1
"""

BROKEN = "def broken(\n"
GONE = "def removed():\n    value = 1\n"
HELPER = "def helper():\n    return 1\n"
CALLER = "import pkg.sample\n"


class SymbolTests(unittest.TestCase):
    def test_nested_async_class_and_top_level_lines(self) -> None:
        parsed = parse_python_source(SAMPLE_HEAD.encode())
        self.assertEqual(parsed.status, "parsed")
        affected = map_affected_symbols(parsed.symbols, (1, 6), line_count=parsed.line_count)
        kinds = {(symbol.qualname, symbol.kind): symbol.changed_lines for symbol in affected}
        self.assertEqual(kinds[("assign:VALUE", "statement")], (1,))
        self.assertEqual(kinds[("Outer", "class")], (6,))
        self.assertEqual(kinds[("Outer.method", "function")], (6,))
        self.assertEqual(kinds[("Outer.method.inner", "async_function")], (6,))
        self.assertNotIn(("untouched", "function"), kinds)
        self.assertNotIn(("keep", "function"), kinds)

    def test_deletion_only_lines_stay_on_the_old_side(self) -> None:
        parsed = parse_python_source(SAMPLE_BASE.encode())
        old = map_affected_symbols(parsed.symbols, (12,), line_count=parsed.line_count)
        new = map_affected_symbols(parsed.symbols, (), line_count=parsed.line_count)
        self.assertEqual(old[0].qualname, "keep")
        self.assertEqual(old[0].changed_lines, (12,))
        self.assertEqual(new, ())

    def test_syntax_error_decode_error_and_duplicate_qualnames(self) -> None:
        broken = parse_python_source(BROKEN.encode())
        self.assertEqual(broken.status, "syntax_error")
        self.assertIn("line 1", broken.error or "")
        decoded = parse_python_source(b"def bad():\n    x = '\xff'\n")
        self.assertEqual(decoded.status, "decode_error")
        latin = parse_python_source(b"# coding: latin-1\ndef ok():\n    x = '\xff'\n")
        self.assertEqual(latin.status, "parsed")
        duplicated = parse_python_source(b"def f():\n    return 1\ndef f():\n    return 2\n")
        names = [symbol.qualname for symbol in duplicated.symbols if symbol.kind == "function"]
        self.assertEqual(names, ["f@1", "f@3"])

    def test_decorator_line_belongs_to_the_function(self) -> None:
        source = b"@decorator\ndef target():\n    return 1\n"
        parsed = parse_python_source(source)
        affected = map_affected_symbols(parsed.symbols, (1,), line_count=parsed.line_count)
        self.assertEqual(affected[0].qualname, "target")
        self.assertEqual(affected[0].start_line, 1)


class ImportIndexTests(unittest.TestCase):
    def test_roots_relative_imports_and_reverse_lookup(self) -> None:
        self.assertEqual(normalize_python_roots(["./src", "src", "."]), (".", "src"))
        with self.assertRaises(UsageError):
            normalize_python_roots(["../secret"])
        with self.assertRaises(UsageError):
            normalize_python_roots(["/abs"])
        modules = {
            "src/pkg/sample.py": _module("src/pkg/sample.py", SAMPLE_HEAD.encode(), "head"),
            "src/pkg/helper.py": _module("src/pkg/helper.py", HELPER.encode(), "head"),
            "src/pkg/use.py": _module("src/pkg/use.py", USE_HEAD.encode(), "head"),
            "src/other/caller.py": _module("src/other/caller.py", CALLER.encode(), "head"),
            "vendor/pkg/sample.py": _module("vendor/pkg/sample.py", b"x = 1\n", "head"),
        }
        identity = module_identity("src/pkg/sample.py", (".", "src"))
        self.assertEqual(identity.certainty, "uncertain")
        self.assertIn("pkg.sample", identity.names)
        self.assertIn("src.pkg.sample", identity.names)
        change = _change("src/pkg/use.py", "src/pkg/use.py", old_lines=(), new_lines=(5,))
        sample_change = _change("src/pkg/sample.py", "src/pkg/sample.py", old_lines=(), new_lines=(1,))
        analysis = analyze_python(
            (change, sample_change),
            {"head": modules, "base": modules},
            ("src",),
        )
        outgoing = [
            item
            for item in analysis.relationships
            if item.direction == "outgoing" and item.changed_path == "src/pkg/use.py"
        ]
        helper = [
            item
            for item in outgoing
            if item.other_module == "pkg.helper" and item.kind == "direct"
        ]
        self.assertEqual(len(helper), 2)
        self.assertTrue(all(item.certainty == "confirmed" for item in helper))
        self.assertEqual(helper[0].other_path, "src/pkg/helper.py")
        unresolved = [item for item in outgoing if item.other_module == "os"]
        self.assertTrue(unresolved)
        self.assertTrue(all(item.certainty == "unresolved" for item in unresolved))
        incoming = [
            item
            for item in analysis.relationships
            if item.direction == "incoming" and item.changed_path == "src/pkg/sample.py"
        ]
        self.assertTrue(any(item.other_path == "src/other/caller.py" and item.certainty == "confirmed" for item in incoming))

    def test_duplicate_module_and_heuristic_import_are_uncertain(self) -> None:
        modules = {
            "src/pkg/sample.py": _module("src/pkg/sample.py", b"VALUE = 1\n", "head"),
            "vendor/pkg/sample.py": _module("vendor/pkg/sample.py", b"VALUE = 2\n", "head"),
            "src/app.py": _module(
                "src/app.py",
                b'import pkg.sample\nimport importlib\nimportlib.import_module("pkg.sample")\nimportlib.import_module(name)\n',
                "head",
            ),
        }
        change = _change("src/app.py", "src/app.py", old_lines=(), new_lines=(1, 3))
        analysis = analyze_python((change,), {"base": modules, "head": modules}, ("src", "vendor"))
        direct = [
            item
            for item in analysis.relationships
            if item.direction == "outgoing" and item.kind == "direct" and item.other_module == "pkg.sample"
        ]
        self.assertGreaterEqual(len(direct), 2)
        self.assertTrue(all(item.certainty == "uncertain" for item in direct))
        heuristic = [item for item in analysis.relationships if item.kind == "heuristic"]
        self.assertTrue(heuristic)
        self.assertTrue(all(item.certainty == "uncertain" for item in heuristic))
        self.assertTrue(any(item.code == "PYTHON_DYNAMIC_IMPORT" for item in analysis.warnings))

    def test_tree_record_parser_keeps_spaced_unicode_paths(self) -> None:
        sha = "a" * 40
        data = f"100644 blob {sha}\tdir with space/ü.py\0".encode()
        self.assertEqual(parse_ls_tree_z(data), (("100644", sha, "dir with space/ü.py"),))


class PythonGitIntegrationTests(unittest.TestCase):
    def test_both_revisions_come_from_git_objects(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "repo"
            init_repo(repo, branch="develop")
            write(repo / "src" / "pkg" / "sample.py", SAMPLE_BASE.encode())
            write(repo / "src" / "pkg" / "helper.py", HELPER.encode())
            write(repo / "src" / "pkg" / "use.py", USE_BASE.encode())
            write(repo / "src" / "other" / "caller.py", CALLER.encode())
            write(repo / "src" / "pkg" / "gone_func.py", GONE.encode())
            commit_all(repo, "base")
            base = head_sha(repo)
            write(repo / "src" / "pkg" / "sample.py", SAMPLE_HEAD.encode())
            write(repo / "src" / "pkg" / "use.py", USE_HEAD.encode())
            write(repo / "src" / "pkg" / "broken.py", BROKEN.encode())
            git(repo, "add", "--", "src/pkg/sample.py", "src/pkg/use.py", "src/pkg/broken.py")
            git(repo, "rm", "--", "src/pkg/gone_func.py")
            git(repo, "commit", "-m", "head")
            head = head_sha(repo)
            output = root / "out"
            report = _analyze(repo, output, base, "develop", "direct", "src")
            self.assertEqual(report["schema_version"], "1.1.0")
            self.assertEqual(report["python_roots"], ["src"])
            self.assertEqual(report["coverage"]["python_ast"], "partial")
            self.assertEqual(report["coverage"]["dependency_index"], "partial")
            files = {(item["old_path"], item["new_path"]): item for item in report["files"]}
            sample = files[("src/pkg/sample.py", "src/pkg/sample.py")]
            self.assertEqual(sample["old_changed_lines"], [1, 6, 12])
            self.assertEqual(sample["new_changed_lines"], [1, 6])
            self.assertIn("keep", _names(sample["old_affected_symbols"]))
            self.assertNotIn("keep", _names(sample["new_affected_symbols"]))
            self.assertIn("Outer.method.inner", _names(sample["new_affected_symbols"]))
            self.assertIn("Outer.method", _names(sample["new_affected_symbols"]))
            self.assertIn("Outer", _names(sample["new_affected_symbols"]))
            self.assertIn("assign:VALUE", _names(sample["new_affected_symbols"]))
            self.assertNotIn("untouched", _names(sample["new_affected_symbols"]))
            gone = files[("src/pkg/gone_func.py", None)]
            self.assertEqual(gone["new_changed_lines"], [])
            self.assertIn("removed", _names(gone["old_affected_symbols"]))
            self.assertEqual(gone["new_python_status"], "absent")
            broken = files[(None, "src/pkg/broken.py")]
            self.assertEqual(broken["new_python_status"], "syntax_error")
            self.assertEqual(broken["new_affected_symbols"], [])
            self.assertTrue(broken["new_changed_lines"])
            codes = {item["code"] for item in report["warnings"]}
            self.assertIn("PYTHON_SYNTAX_ERROR", codes)
            helper_edges = [
                item
                for item in report["import_relationships"]
                if item["changed_path"] == "src/pkg/use.py"
                and item["direction"] == "outgoing"
                and item["other_module"] == "pkg.helper"
                and item["kind"] == "direct"
            ]
            self.assertTrue(helper_edges)
            self.assertTrue(all(item["certainty"] == "confirmed" for item in helper_edges))
            self.assertTrue(
                any(item["certainty"] == "unresolved" and item["other_module"] == "os" for item in report["import_relationships"])
            )
            incoming = [
                item
                for item in report["import_relationships"]
                if item["changed_path"] == "src/pkg/sample.py" and item["direction"] == "incoming"
            ]
            self.assertTrue(any(item["other_path"] == "src/other/caller.py" for item in incoming))
            first = json.dumps(report, sort_keys=True)
            second = json.dumps(_analyze(repo, root / "out2", base, "develop", "direct", "src"), sort_keys=True)
            self.assertEqual(first, second)
            write(repo / "src" / "pkg" / "sample.py", b"DIRTY-WORKTREE-LINE = 1\n")
            dirty = _analyze(repo, root / "dirty", base, "develop", "direct", "src")
            dirty_sample = next(item for item in dirty["files"] if item["new_path"] == "src/pkg/sample.py")
            self.assertEqual(dirty_sample["new_affected_symbols"], sample["new_affected_symbols"])
            self.assertNotIn("DIRTY-WORKTREE-LINE", json.dumps(dirty_sample))
            self.assertEqual(head, dirty["comparison"]["head_commit"])


def _module(path: str, source: bytes, commit: str) -> IndexedModule:
    return IndexedModule(path, commit, parse_python_source(source))


def _change(old: str | None, new: str | None, *, old_lines: tuple[int, ...], new_lines: tuple[int, ...]) -> FileChange:
    return FileChange(
        status="modified",
        status_code="M",
        similarity=None,
        old_path=old,
        new_path=new,
        old_mode="100644",
        new_mode="100644",
        old_blob="a" * 40,
        new_blob="b" * 40,
        diff_base_commit="base",
        head_commit="head",
        binary=False,
        submodule=False,
        symlink=False,
        lfs_pointer=False,
        lfs_pointer_old=False,
        lfs_pointer_new=False,
        old_changed_lines=old_lines,
        new_changed_lines=new_lines,
        old_changed_line_ranges=(),
        new_changed_line_ranges=(),
        hunks=(),
    )


def _names(symbols: list[dict[str, object]]) -> set[object]:
    return {symbol["qualname"] for symbol in symbols}


def _analyze(
    repo: Path, output: Path, base: str, head: str, comparison: str, *roots: str
) -> dict[str, object]:
    args = [
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
    for root in roots:
        args.extend(["--python-root", root])
    with redirect_stdout(StringIO()), redirect_stderr(StringIO()) as stderr:
        code = main(args)
    if code != 0:
        raise AssertionError(stderr.getvalue())
    return json.loads((output / "review-context.json").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
