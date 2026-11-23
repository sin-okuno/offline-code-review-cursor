"""Review-context quality: JSONC, changed-line priority, and review units."""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from offline_review.context_builder import build_context
from offline_review.context_selector import ContextBudget
from offline_review.dependency_graph import build_angular_graph, path_aliases
from offline_review.git_adapter import GitAdapter
from offline_review.typescript_index import TsCommitIndex, scan_typescript
from tests.support import commit_all, head_sha, init_repo, write

JSONC = b"""
{
  // comment
  "compilerOptions": {
    "baseUrl": ".",
    "paths": {
      "@app/*": ["src/app/*"],
    },
  },
}
"""

QUOTED_SLASHES = b"""
{
  "compilerOptions": {
    "paths": {
      "@keep/*": ["src/app/* // keep"]
    }
  }
}
"""

CHILD = b"""
{
  "extends": "./base.json",
  "compilerOptions": {"baseUrl": "."}
}
"""

PARENT = b"""
{
  "compilerOptions": {
    "paths": {"@app/*": ["src/app/*"]}
  }
}
"""

MODULE = b"""
import { AppService } from './app.service';

@NgModule({
  declarations: [],
  imports: [],
  providers: [AppService],
})
export class AppModule {}
"""

SERVICE = b"export class AppService {}\n"

FUNCTION_BASE = """
export function load(): number {
  const value = 1;
  return 0;
}
"""

FUNCTION_HEAD = """
export function load(): number {
  const value = 1;
  return value;
}
"""


class JsoncTests(unittest.TestCase):
    def test_comments_and_trailing_commas_are_read(self) -> None:
        aliases, warnings = path_aliases(JSONC)
        self.assertFalse(any(item.code == "TSCONFIG_UNREADABLE" for item in warnings))
        self.assertIn(("@app/*", "src/app/*"), {(item.key, item.target) for item in aliases})

    def test_slashes_inside_strings_stay_in_the_target(self) -> None:
        aliases, warnings = path_aliases(QUOTED_SLASHES)
        self.assertFalse(any(item.code == "TSCONFIG_UNREADABLE" for item in warnings))
        self.assertTrue(any("keep" in item.target for item in aliases))

    def test_relative_extends_is_followed_from_the_child_directory(self) -> None:
        seen: list[str] = []

        def follow(path: str) -> bytes:
            seen.append(path)
            return PARENT

        aliases, warnings = path_aliases(CHILD, follow=follow, origin="src/tsconfig.app.json")
        self.assertEqual(seen, ["src/base.json"])
        self.assertFalse(any(item.code == "TSCONFIG_EXTENDS_UNSUPPORTED" for item in warnings))
        self.assertIn(("@app/*", "src/app/*"), {(item.key, item.target) for item in aliases})

    def test_parent_directory_and_package_extends_are_not_followed(self) -> None:
        def follow(path: str) -> bytes:
            raise AssertionError(path)

        for spec in ("../outside.json", "@scope/tsconfig.json", "https://example.com/tsconfig.json"):
            _, warnings = path_aliases(
                f'{{"extends": "{spec}", "compilerOptions": {{}}}}'.encode(),
                follow=follow,
            )
            self.assertTrue(any(item.code == "TSCONFIG_EXTENDS_UNSUPPORTED" for item in warnings))

    def test_extends_without_a_reader_stays_unsupported(self) -> None:
        _, warnings = path_aliases(CHILD)
        self.assertTrue(any(item.code == "TSCONFIG_EXTENDS_UNSUPPORTED" for item in warnings))


class ProviderTests(unittest.TestCase):
    def test_imported_provider_is_a_confirmed_service_edge(self) -> None:
        module = replace(scan_typescript(MODULE), path="src/app.module.ts")
        service = replace(scan_typescript(SERVICE), path="src/app.service.ts")
        graph = build_angular_graph(TsCommitIndex("a" * 40, (module, service), (), ()))
        edges = {(item.kind, item.to_symbol, item.confidence) for item in graph.edges}
        self.assertIn(("service_reference", "AppService", "confirmed"), edges)


class ReviewUnitTests(unittest.TestCase):
    def test_changed_overflow_is_kept_in_a_later_unit(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / "repo"
            init_repo(repo)
            source = repo / "src" / "values.ts"
            write(source, "\n".join(f"export const value{index} = 0;" for index in range(12)).encode() + b"\n")
            base = commit_all(repo, "base")
            write(source, "\n".join(f"export const value{index} = 1;" for index in range(12)).encode() + b"\n")
            head = commit_all(repo, "head")
            with GitAdapter(repo) as adapter:
                context = build_context(
                    adapter,
                    repository=str(repo),
                    base=base,
                    head=head,
                    comparison="direct",
                    budget=ContextBudget(max_total_lines=4),
                )
            angular = context.angular_context
            self.assertLessEqual(angular.coverage.lines_selected, 4)
            self.assertGreaterEqual(len(angular.review_units), 2)
            self.assertEqual(angular.review_units[0].unit_id, "unit-1")
            stats = [item for item in angular.changed_line_files if item.path == "src/values.ts" and item.revision == "head"]
            self.assertEqual(len(stats), 1)
            self.assertEqual(stats[0].changed_lines_omitted, 0)
            self.assertEqual(stats[0].changed_lines_included, stats[0].changed_lines_total)
            self.assertTrue(
                any(item.code == "CHANGED_CONTEXT_OMITTED" for item in angular.warnings)
            )

    def test_changed_function_keeps_unchanged_lines_inside_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / "repo"
            init_repo(repo)
            source = repo / "src" / "load.ts"
            write(source, FUNCTION_BASE.encode())
            base = commit_all(repo, "base")
            write(source, FUNCTION_HEAD.encode())
            commit_all(repo, "head")
            with GitAdapter(repo) as adapter:
                context = build_context(
                    adapter,
                    repository=str(repo),
                    base=base,
                    head=head_sha(repo),
                    comparison="direct",
                    budget=ContextBudget(max_total_lines=2000),
                )
            excerpts = [
                item.content
                for item in context.angular_context.selected
                if item.path == "src/load.ts" and item.revision == "head"
            ]
            self.assertTrue(excerpts)
            self.assertIn("const value = 1;", excerpts[0])
            self.assertIn("return value;", excerpts[0])

    def test_short_gap_between_changed_lines_stays_in_the_excerpt(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / "repo"
            init_repo(repo)
            source = repo / "src" / "gap.ts"
            write(source, b"export const first = 0;\nexport const middle = 1;\nexport const last = 2;\n")
            base = commit_all(repo, "base")
            write(source, b"export const first = 1;\nexport const middle = 1;\nexport const last = 3;\n")
            commit_all(repo, "head")
            with GitAdapter(repo) as adapter:
                context = build_context(
                    adapter,
                    repository=str(repo),
                    base=base,
                    head=head_sha(repo),
                    comparison="direct",
                    budget=ContextBudget(max_total_lines=2000),
                )
            excerpts = [
                item.content
                for item in context.angular_context.selected
                if item.path == "src/gap.ts" and item.revision == "head"
            ]
            self.assertTrue(excerpts)
            self.assertIn("export const middle = 1;", "\n".join(excerpts))

    def test_method_change_keeps_the_constructor_when_the_class_fits(self) -> None:
        source = """\
import { Component } from '@angular/core';
import { FormBuilder, Validators } from '@angular/forms';

@Component({ selector: 'app-ledger', template: '' })
export class LedgerComponent {
  constructor(private formBuilder: FormBuilder) {
    this.formBuilder.group({ name: ['', Validators.required] });
  }

  save(): void {
    return;
  }
}
"""
        updated = source.replace("return;", "return undefined;")
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / "repo"
            init_repo(repo)
            path = repo / "src" / "ledger.component.ts"
            write(path, source.encode())
            base = commit_all(repo, "base")
            write(path, updated.encode())
            commit_all(repo, "head")
            with GitAdapter(repo) as adapter:
                context = build_context(
                    adapter,
                    repository=str(repo),
                    base=base,
                    head=head_sha(repo),
                    comparison="direct",
                    budget=ContextBudget(max_total_lines=2000),
                )
            excerpts = [
                item.content
                for item in context.angular_context.selected
                if item.path == "src/ledger.component.ts" and item.revision == "head"
            ]
            self.assertTrue(excerpts)
            self.assertIn("Validators.required", excerpts[0])
            self.assertIn("return undefined;", excerpts[0])

    def test_deleted_line_keeps_the_surviving_head_declaration(self) -> None:
        base_source = """\
export const loadBeaconsEffect = createEffect(
  () => actions$.pipe(
    switchMap(() => api.load().pipe(
      map(() => ok(")")),
      // )
      catchError((error) => of(fail(error))),
    )),
  ),
);
export const after = 1;
"""
        head_source = base_source.replace("      catchError((error) => of(fail(error))),\n", "")
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / "repo"
            init_repo(repo)
            path = repo / "src" / "beacon.effects.ts"
            write(path, base_source.encode())
            base = commit_all(repo, "base")
            write(path, head_source.encode())
            commit_all(repo, "head")
            angular = _angular(repo, base)
        head = _changed(angular, "src/beacon.effects.ts", "head")
        base_excerpt = _changed(angular, "src/beacon.effects.ts", "base")
        self.assertGreater(head.line_start, 0)
        self.assertGreaterEqual(head.line_end, head.line_start)
        self.assertIn("export const loadBeaconsEffect", head.content)
        self.assertIn('ok(")")', head.content)
        self.assertIn("// )", head.content)
        self.assertNotIn("catchError", head.content)
        self.assertNotIn("export const after", head.content)
        self.assertIn("export const loadBeaconsEffect", base_excerpt.content)
        self.assertIn("catchError", base_excerpt.content)
        symbols = [
            item
            for item in angular.changed_symbols
            if item.path == "src/beacon.effects.ts" and item.qualname == "loadBeaconsEffect"
        ]
        self.assertEqual({item.revision for item in symbols}, {"base", "head"})
        self.assertTrue(all(item.line_start > 0 and item.line_end >= item.line_start for item in symbols))
        self.assertFalse(any(item.code == "TS_SYMBOL_ABSENT" for item in angular.warnings))

    def test_removed_symbol_is_absent_on_head(self) -> None:
        source = "export const kept = 1;\nexport function gone(): number {\n  return 1;\n}\n"
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / "repo"
            init_repo(repo)
            path = repo / "src" / "gone.ts"
            write(path, source.encode())
            base = commit_all(repo, "base")
            write(path, b"export const kept = 1;\n")
            commit_all(repo, "head")
            angular = _angular(repo, base)
        self.assertFalse(_changed_or_none(angular, "src/gone.ts", "head"))
        self.assertIn("function gone", _changed(angular, "src/gone.ts", "base").content)
        self.assertTrue(
            any(item.code == "TS_SYMBOL_ABSENT" and "gone" in item.message for item in angular.warnings)
        )

    def test_deleted_file_names_the_missing_symbol(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / "repo"
            init_repo(repo)
            path = repo / "src" / "gone.ts"
            write(path, b"export function gone(): number {\n  return 1;\n}\n")
            base = commit_all(repo, "base")
            path.unlink()
            commit_all(repo, "head")
            angular = _angular(repo, base)
        self.assertFalse(any(item.revision == "head" and item.path == "src/gone.ts" for item in angular.selected))
        self.assertTrue(
            any(item.code == "TS_SYMBOL_ABSENT" and item.path == "src/gone.ts" for item in angular.warnings)
        )

    def test_duplicate_declaration_is_not_matched(self) -> None:
        source = (
            "export const load = createEffect(\n"
            "  () => actions$.pipe(\n"
            "    map(() => one()),\n"
            "  ),\n"
            ");\n"
            "export const load = createEffect(\n"
            "  () => actions$.pipe(\n"
            "    map(() => two()),\n"
            "  ),\n"
            ");\n"
        )
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / "repo"
            init_repo(repo)
            path = repo / "src" / "dup.ts"
            write(path, source.encode())
            base = commit_all(repo, "base")
            write(path, source.replace("    map(() => one()),\n", "").encode())
            commit_all(repo, "head")
            angular = _angular(repo, base)
        self.assertFalse(_changed_or_none(angular, "src/dup.ts", "head"))
        self.assertTrue(any(item.code == "TS_SYMBOL_MATCH_AMBIGUOUS" for item in angular.warnings))

    def test_unparsed_head_is_not_filled_from_base_lines(self) -> None:
        base_source = (
            "export const load = createEffect(\n"
            "  () => note(`start\n"
            "end`),\n"
            ");\n"
        )
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / "repo"
            init_repo(repo)
            path = repo / "src" / "broken.ts"
            write(path, base_source.encode())
            base = commit_all(repo, "base")
            write(path, base_source.replace("end`),\n", "").encode())
            commit_all(repo, "head")
            angular = _angular(repo, base)
        head = _changed(angular, "src/broken.ts", "head")
        self.assertEqual((head.line_start, head.line_end), (0, 0))
        self.assertEqual(head.content, "")
        self.assertNotIn("export const load", head.content)
        self.assertTrue(any(item.revision == "base" and item.qualname == "load" for item in angular.changed_symbols))
        self.assertTrue(any(item.code == "TS_SYMBOL_MATCH_UNCERTAIN" for item in angular.warnings))

    def test_in_symbol_edit_still_extracts_both_revisions(self) -> None:
        source = (
            "export const loadBeaconsEffect = createEffect(\n"
            "  () => actions$.pipe(map(() => ok())),\n"
            ");\n"
        )
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / "repo"
            init_repo(repo)
            path = repo / "src" / "beacon.effects.ts"
            write(path, source.encode())
            base = commit_all(repo, "base")
            write(path, source.replace("ok()", "ok(1)").encode())
            commit_all(repo, "head")
            angular = _angular(repo, base)
        for revision, needle in (("base", "ok()"), ("head", "ok(1)")):
            excerpt = _changed(angular, "src/beacon.effects.ts", revision)
            self.assertGreater(excerpt.line_start, 0)
            self.assertIn("export const loadBeaconsEffect", excerpt.content)
            self.assertIn(needle, excerpt.content)

    def test_zero_line_budget_keeps_no_excerpt_and_no_later_unit(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / "repo"
            init_repo(repo)
            source = repo / "src" / "load.ts"
            write(source, FUNCTION_BASE.encode())
            base = commit_all(repo, "base")
            write(source, FUNCTION_HEAD.encode())
            commit_all(repo, "head")
            with GitAdapter(repo) as adapter:
                context = build_context(
                    adapter,
                    repository=str(repo),
                    base=base,
                    head=head_sha(repo),
                    comparison="direct",
                    budget=ContextBudget(max_total_lines=0),
                )
            angular = context.angular_context
            self.assertEqual(angular.selected, ())
            self.assertEqual(len(angular.review_units), 1)
            self.assertEqual(angular.review_units[0].excerpts, ())
            stats = [item for item in angular.changed_line_files if item.revision == "head"]
            self.assertTrue(stats)
            self.assertTrue(all(item.changed_lines_included == 0 for item in stats))
            self.assertTrue(all(item.changed_lines_omitted == item.changed_lines_total for item in stats))


def _angular(repo: Path, base: str):
    with GitAdapter(repo) as adapter:
        context = build_context(
            adapter,
            repository=str(repo),
            base=base,
            head=head_sha(repo),
            comparison="direct",
            budget=ContextBudget(max_total_lines=2000),
        )
    return context.angular_context


def _changed(angular, path: str, revision: str):
    found = _changed_or_none(angular, path, revision)
    if found is None:
        raise AssertionError(f"no changed excerpt for {revision} {path}")
    return found


def _changed_or_none(angular, path: str, revision: str):
    matches = [
        item
        for item in angular.selected
        if item.path == path and item.revision == revision and item.reason == "changed file"
    ]
    if not matches:
        return None
    if len(matches) != 1:
        raise AssertionError(matches)
    return matches[0]


if __name__ == "__main__":
    unittest.main()
