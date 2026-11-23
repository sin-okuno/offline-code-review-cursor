"""Conservative TypeScript indexing from bytes and Git blobs."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from offline_review.git_adapter import GitAdapter
from offline_review.typescript_index import (
    index_typescript_commit,
    removed_indexed_paths,
    removed_typescript_symbols,
    scan_html,
    scan_typescript,
)
from tests.support import commit_all, head_sha, init_repo, write

COMPONENT = """\
import { Component } from '@angular/core';
import type {
  Product,
  TreeService as Trees,
} from '../tree.service';

@Component({
  selector: 'app-tree',
  templateUrl: './tree.component.html',
  styleUrls: ['./tree.component.scss'],
  standalone: false,
})
export class TreeComponent {
  constructor() {}

  load(): void {
    return;
  }
}

export function helper(): number {
  return 1;
}
"""

FAKE = """\
/* @Component({ selector: 'fake', templateUrl: './fake.html' }) */
// import { Fake } from './fake';
const label = "import { Nope } from './nope'";
const pattern = /import x from 'mod'/;
export class Real {
  stay(): void {}
}
"""

NGMODULE = """\
import { NgModule } from '@angular/core';
import { TreeComponent } from './tree.component';

@NgModule({
  declarations: [TreeComponent],
  imports: [CommonModule],
})
export class TreeModule {}
"""


class ScanTests(unittest.TestCase):
    def test_component_imports_and_methods(self) -> None:
        indexed = scan_typescript(COMPONENT.encode())
        self.assertEqual(indexed.status, "parsed")
        self.assertEqual(indexed.component.status, "present")
        self.assertEqual(indexed.component.selector, "app-tree")
        self.assertEqual(indexed.component.template_url, "./tree.component.html")
        self.assertEqual(indexed.component.style_urls, ("./tree.component.scss",))
        self.assertIs(indexed.component.standalone, False)
        modules = {item.module: item for item in indexed.imports}
        self.assertEqual(modules["@angular/core"].specifiers, ("Component",))
        self.assertEqual(modules["../tree.service"].form, "type")
        self.assertEqual(modules["../tree.service"].specifiers, ("Product", "Trees"))
        self.assertGreater(modules["../tree.service"].line, modules["@angular/core"].line)
        names = {(item.qualname, item.kind): item for item in indexed.symbols}
        self.assertIn(("TreeComponent", "class"), names)
        self.assertIn(("TreeComponent.constructor", "method"), names)
        self.assertIn(("TreeComponent.load", "method"), names)
        self.assertIn(("helper", "function"), names)
        self.assertLess(names[("TreeComponent", "class")].start_line, names[("TreeComponent.load", "method")].start_line)
        self.assertIn("TreeComponent", indexed.exports)
        self.assertIn("helper", indexed.exports)

    def test_comments_strings_and_regex_are_not_imports(self) -> None:
        indexed = scan_typescript(FAKE.encode())
        self.assertEqual(indexed.status, "parsed")
        self.assertEqual(indexed.imports, ())
        self.assertEqual(indexed.component.status, "absent")
        self.assertEqual([item.qualname for item in indexed.symbols], ["Real", "Real.stay"])

    def test_backslash_specifier_is_preserved(self) -> None:
        source = "import panel from '..\\\\panel';\n"
        indexed = scan_typescript(source.encode())
        self.assertEqual(indexed.status, "parsed")
        self.assertEqual(indexed.imports[0].module, "..\\panel")
        self.assertEqual(indexed.imports[0].specifiers, ("panel",))

    def test_reexport_and_relative_specifier_are_recorded(self) -> None:
        source = "export { Widget as Button } from './widget';\nexport * from './more';\n"
        indexed = scan_typescript(source.encode())
        self.assertEqual(indexed.status, "parsed")
        forms = {(item.module, item.specifiers) for item in indexed.imports}
        self.assertIn(("./widget", ("Button",)), forms)
        self.assertIn(("./more", ()), forms)

    def test_computed_template_url_is_unknown(self) -> None:
        source = (
            "@Component({ selector: 'app-a', templateUrl: './a.html' + extra })\n"
            "export class A {}\n"
        )
        indexed = scan_typescript(source.encode())
        self.assertEqual(indexed.status, "parsed")
        self.assertEqual(indexed.component.status, "unknown")
        self.assertIsNone(indexed.component.template_url)
        self.assertIsNone(indexed.component.selector)
        self.assertTrue(any(item.code == "TS_COMPONENT_UNKNOWN" for item in indexed.warnings))
        self.assertIn("A", [item.qualname for item in indexed.symbols])

    def test_ngmodule_identifiers(self) -> None:
        indexed = scan_typescript(NGMODULE.encode())
        self.assertEqual(indexed.ng_module.status, "present")
        self.assertEqual(indexed.ng_module.declarations, ("TreeComponent",))
        self.assertEqual(indexed.ng_module.imports, ("CommonModule",))

    def test_spread_ngmodule_is_unknown(self) -> None:
        source = "@NgModule({ declarations: [One, ...rest] })\nexport class Mod {}\n"
        indexed = scan_typescript(source.encode())
        self.assertEqual(indexed.ng_module.status, "unknown")
        self.assertEqual(indexed.ng_module.declarations, ())
        self.assertTrue(any(item.code == "TS_NGMODULE_UNKNOWN" for item in indexed.warnings))

    def test_unclosed_string_has_no_symbols(self) -> None:
        indexed = scan_typescript(b"export class Broken {\n  name = 'unterminated;\n}\n")
        self.assertEqual(indexed.status, "unknown")
        self.assertEqual(indexed.symbols, ())
        self.assertEqual(indexed.warnings[0].code, "TS_UNCLOSED")

    def test_unbalanced_braces_have_no_symbols(self) -> None:
        indexed = scan_typescript(b"export class Broken {\n  go() {\n    return 1;\n}\n")
        self.assertEqual(indexed.status, "unknown")
        self.assertEqual(indexed.symbols, ())
        self.assertEqual(indexed.warnings[0].code, "TS_UNCLOSED")

    def test_multiline_call_binding_keeps_its_name(self) -> None:
        source = (
            "export const loadBeaconsEffect = createEffect(\n"
            "  () => actions$.pipe(ofType(loadBeacons)),\n"
            ");\n"
            "export const READY_LABEL = 'Ready';\n"
        )
        indexed = scan_typescript(source.encode())
        bindings = [item for item in indexed.symbols if item.qualname == "loadBeaconsEffect"]
        self.assertEqual(len(bindings), 1)
        self.assertEqual(bindings[0].kind, "binding")
        self.assertEqual(bindings[0].start_line, 1)
        self.assertGreaterEqual(bindings[0].end_line, 3)
        self.assertNotIn("READY_LABEL", [item.qualname for item in indexed.symbols])

    def test_nested_function_is_not_a_symbol(self) -> None:
        source = "export function outer(): void {\n  function inner(): void {}\n}\n"
        indexed = scan_typescript(source.encode())
        self.assertEqual([item.qualname for item in indexed.symbols], ["outer"])
        self.assertTrue(any(item.code == "TS_NESTED_FUNCTION" for item in indexed.warnings))

    def test_nul_and_size_and_generated_text(self) -> None:
        self.assertEqual(scan_typescript(b"export class A {}\0\n").status, "binary")
        self.assertEqual(scan_typescript(b"x" * (1_048_576 + 1)).status, "too_large")
        generated = scan_typescript(b"// @generated\nexport class A {}\n")
        self.assertEqual(generated.status, "skipped")
        self.assertEqual(generated.symbols, ())
        self.assertEqual(scan_html(b"<p>Hello</p>\n").status, "parsed")
        self.assertEqual(scan_html(b"<p>Hello</p>\n").line_count, 1)
        self.assertEqual(scan_html(b"\0").status, "binary")


class GitRevisionTests(unittest.TestCase):
    def test_both_revisions_come_from_blobs_and_removals_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "repo"
            init_repo(repo)
            write(repo / "src" / "app" / "tree.component.ts", COMPONENT.encode())
            write(repo / "src" / "app" / "tree.component.html", b"<p>base</p>\n")
            write(repo / "src" / "app" / "gone.ts", b"export function gone(): void {}\n")
            commit_all(repo, "base")
            base = head_sha(repo)
            write(
                repo / "src" / "app" / "tree.component.ts",
                COMPONENT.replace("  load(): void {\n    return;\n  }\n", "").encode(),
            )
            write(repo / "src" / "app" / "tree.component.html", b"<p>head</p>\n")
            from tests.support import git

            git(repo, "rm", "--", "src/app/gone.ts")
            commit_all(repo, "head")
            head = head_sha(repo)
            write(repo / "src" / "app" / "tree.component.ts", b"export function dirtyOnly(): void {}\n")
            with GitAdapter(repo) as adapter:
                base_index = index_typescript_commit(adapter, base)
                head_index = index_typescript_commit(adapter, head)
            self.assertEqual(base_index.commit, base)
            self.assertEqual(head_index.commit, head)
            base_tree = {item.path: item for item in base_index.files}
            head_tree = {item.path: item for item in head_index.files}
            self.assertIn("TreeComponent.load", [item.qualname for item in base_tree["src/app/tree.component.ts"].symbols])
            self.assertNotIn(
                "TreeComponent.load",
                [item.qualname for item in head_tree["src/app/tree.component.ts"].symbols],
            )
            self.assertNotIn("dirtyOnly", [item.qualname for item in head_tree["src/app/tree.component.ts"].symbols])
            self.assertEqual(removed_indexed_paths(base_index, head_index), ("src/app/gone.ts",))
            removed = removed_typescript_symbols(base_index, head_index)
            removed_names = {(item.path, item.qualname) for item in removed}
            self.assertIn(("src/app/gone.ts", "gone"), removed_names)
            self.assertIn(("src/app/tree.component.ts", "TreeComponent.load"), removed_names)
            self.assertTrue(all(item.base_commit == base for item in removed))
            self.assertEqual(head_index.html_files[0].path, "src/app/tree.component.html")
            self.assertEqual(head_index.html_files[0].status, "parsed")
            self.assertEqual((repo / "src" / "app" / "tree.component.ts").read_bytes(), b"export function dirtyOnly(): void {}\n")


if __name__ == "__main__":
    unittest.main()
