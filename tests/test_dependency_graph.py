"""Angular dependency edges from indexed blobs."""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from offline_review.dependency_graph import (
    build_angular_graph,
    build_revision_graphs,
    edges_to,
    path_aliases,
    removed_edges,
    walk_forward,
)
from offline_review.git_adapter import GitAdapter
from offline_review.typescript_index import (
    HtmlFileIndex,
    TsCommitIndex,
    TsFileIndex,
    scan_typescript,
)
from tests.support import commit_all, git, head_sha, init_repo, write

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

EFFECT = """\
import { ofType } from '@ngrx/effects';
import { loadTree } from './tree.actions';

export const loadTreeEffect = createEffect(() => ofType(loadTree));
"""

MODULE = """\
import { NgModule } from '@angular/core';
import { TreeComponent } from './tree.component';

@NgModule({
  declarations: [TreeComponent],
})
export class TreeModule {}
"""


def _ts(path: str, source: str) -> TsFileIndex:
    indexed = scan_typescript(source.encode())
    return replace(indexed, path=path, warnings=tuple(replace(item, path=path) for item in indexed.warnings))


def _commit(*files: TsFileIndex, html: tuple[str, ...] = (), commit: str = "a" * 40) -> TsCommitIndex:
    pages = tuple(HtmlFileIndex(path, "parsed", 1, ()) for path in html)
    return TsCommitIndex(commit, tuple(files), pages, ())


class GraphTests(unittest.TestCase):
    def test_component_links_html_and_service_and_reverse_import(self) -> None:
        component = _ts("src/app/tree.component.ts", COMPONENT)
        service = _ts("src/app/tree.service.ts", "export class TreeService {}\n")
        graph = build_angular_graph(
            _commit(component, service, html=("src/app/tree.component.html",))
        )
        self.assertEqual(graph.status, "partial")
        edges = {(edge.kind, edge.confidence, edge.to_path) for edge in graph.edges}
        self.assertIn(("component_template", "confirmed", "src/app/tree.component.html"), edges)
        self.assertIn(("service_reference", "confirmed", "src/app/tree.service.ts"), edges)
        self.assertIn(("ts_import", "confirmed", "src/app/tree.service.ts"), edges)
        self.assertFalse(any(edge.to_path == "@angular/core" for edge in graph.edges))
        reverse = edges_to(graph, "src/app/tree.service.ts")
        self.assertTrue(any(edge.from_path == "src/app/tree.component.ts" for edge in reverse))
        self.assertTrue(all(edge.source_revision == "a" * 40 for edge in graph.edges))

    def test_effect_of_type_links_the_imported_action(self) -> None:
        effect = _ts("src/app/tree.effects.ts", EFFECT)
        action = _ts(
            "src/app/tree.actions.ts",
            "import { createAction } from '@ngrx/store';\nexport const loadTree = createAction('[Tree] Load');\n",
        )
        graph = build_angular_graph(_commit(effect, action))
        matched = [
            edge
            for edge in graph.edges
            if edge.kind == "ngrx_action_reference" and edge.confidence == "confirmed"
        ]
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0].from_path, "src/app/tree.effects.ts")
        self.assertEqual(matched[0].to_path, "src/app/tree.actions.ts")
        self.assertEqual(matched[0].to_symbol, "loadTree")

    def test_ngmodule_declaration_uses_the_import(self) -> None:
        module = _ts("src/app/tree.module.ts", MODULE)
        component = _ts("src/app/tree.component.ts", "export class TreeComponent {}\n")
        graph = build_angular_graph(_commit(module, component))
        declared = [
            edge
            for edge in graph.edges
            if edge.kind == "ngmodule_declaration" and edge.confidence == "confirmed"
        ]
        self.assertEqual(declared[0].to_path, "src/app/tree.component.ts")
        self.assertEqual(declared[0].to_symbol, "TreeComponent")

    def test_unimported_declaration_is_only_a_candidate(self) -> None:
        module = _ts(
            "src/app/tree.module.ts",
            "@NgModule({ declarations: [OnlyOne] })\nexport class TreeModule {}\n",
        )
        owner = _ts("src/app/only.ts", "export class OnlyOne {}\n")
        other = _ts("src/app/other.ts", "export class Other {}\n")
        graph = build_angular_graph(_commit(module, owner, other))
        declared = [edge for edge in graph.edges if edge.kind == "ngmodule_declaration"]
        self.assertEqual([(edge.confidence, edge.to_path) for edge in declared], [("candidate", "src/app/only.ts")])

    def test_relative_index_and_ambiguous_extensions(self) -> None:
        importer = _ts("src/app/main.ts", "import { Item } from './models';\nimport { A } from './dup';\n")
        models = _ts("src/app/models/index.ts", "export class Item {}\n")
        dup_ts = _ts("src/app/dup.ts", "export class A {}\n")
        dup_tsx = _ts("src/app/dup.tsx", "export class A {}\n")
        graph = build_angular_graph(_commit(importer, models, dup_ts, dup_tsx))
        confirmed = [edge for edge in graph.edges if edge.kind == "ts_import" and edge.confidence == "confirmed"]
        self.assertEqual([edge.to_path for edge in confirmed], ["src/app/models/index.ts"])
        ambiguous = [edge for edge in graph.edges if edge.confidence == "ambiguous"]
        self.assertEqual(ambiguous[0].to_path, None)
        self.assertTrue(any(item.code == "TS_IMPORT_AMBIGUOUS" for item in graph.warnings))

    def test_path_alias_confirms_one_target_and_rejects_many(self) -> None:
        safe, warnings = path_aliases(
            b'{"compilerOptions":{"baseUrl":".","paths":{"@app/*":["src/app/*"]}},"extends":"./base.json"}'
        )
        self.assertTrue(any(item.code == "TSCONFIG_EXTENDS_UNSUPPORTED" for item in warnings))
        importer = _ts("src/main.ts", "import { TreeService } from '@app/tree.service';\n")
        service = _ts("src/app/tree.service.ts", "export class TreeService {}\n")
        graph = build_angular_graph(_commit(importer, service), safe)
        self.assertTrue(
            any(
                edge.kind == "ts_import" and edge.confidence == "confirmed" and edge.to_path == "src/app/tree.service.ts"
                for edge in graph.edges
            )
        )
        ambiguous, _extra = path_aliases(
            b'{"compilerOptions":{"paths":{"@app/*":["src/app/*","lib/app/*"]}}}'
        )
        again = build_angular_graph(_commit(importer, service), ambiguous)
        self.assertFalse(any(edge.confidence == "confirmed" and edge.to_path == "src/app/tree.service.ts" for edge in again.edges))
        self.assertTrue(any(edge.confidence == "ambiguous" for edge in again.edges))

    def test_cycle_stops(self) -> None:
        left = _ts("src/a.ts", "import { B } from './b';\nexport class A {}\n")
        right = _ts("src/b.ts", "import { A } from './a';\nexport class B {}\n")
        graph = build_angular_graph(_commit(left, right))
        walked = walk_forward(graph, "src/a.ts")
        self.assertIn(("src/a.ts", "src/b.ts", "src/a.ts"), walked.cycles)
        self.assertLessEqual(walked.paths.count("src/a.ts"), 1)

    def test_deleted_component_edge_stays_on_the_base_graph(self) -> None:
        component = _ts("src/app/tree.component.ts", COMPONENT)
        service = _ts("src/app/tree.service.ts", "export class TreeService {}\n")
        base = build_angular_graph(_commit(component, service, html=("src/app/tree.component.html",), commit="b" * 40))
        head = build_angular_graph(_commit(service, commit="c" * 40))
        removed = removed_edges(base, head)
        kinds = {edge.kind for edge in removed}
        self.assertIn("component_template", kinds)
        self.assertIn("service_reference", kinds)
        self.assertTrue(all(edge.source_revision == "b" * 40 for edge in removed))
        self.assertFalse(edges_to(head, "src/app/tree.component.html"))


class GitGraphTests(unittest.TestCase):
    def test_revisions_come_from_blobs(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "repo"
            init_repo(repo)
            write(repo / "tsconfig.json", b'{"compilerOptions":{"baseUrl":".","paths":{"@app/*":["src/app/*"]}}}\n')
            write(repo / "src" / "app" / "tree.component.ts", COMPONENT.encode())
            write(repo / "src" / "app" / "tree.component.html", b"<p>tree</p>\n")
            write(repo / "src" / "app" / "tree.service.ts", b"export class TreeService {}\n")
            write(repo / "src" / "app" / "tree.effects.ts", EFFECT.encode())
            write(
                repo / "src" / "app" / "tree.actions.ts",
                b"export const loadTree = createAction('[Tree] Load');\n",
            )
            commit_all(repo, "base")
            base = head_sha(repo)
            git(repo, "rm", "--", "src/app/tree.component.ts")
            commit_all(repo, "head")
            head = head_sha(repo)
            write(repo / "src" / "app" / "tree.service.ts", b"export class DirtyService {}\n")
            with GitAdapter(repo) as adapter:
                graphs = build_revision_graphs(adapter, base, head)
            self.assertEqual(graphs.base.status, "partial")
            self.assertEqual(graphs.head.status, "partial")
            self.assertTrue(
                any(
                    edge.kind == "component_template" and edge.confidence == "confirmed"
                    for edge in graphs.base.edges
                )
            )
            self.assertTrue(
                any(
                    edge.kind == "ngrx_action_reference"
                    and edge.confidence == "confirmed"
                    and edge.to_path == "src/app/tree.actions.ts"
                    for edge in graphs.base.edges
                )
            )
            self.assertTrue(any(edge.kind == "component_template" for edge in removed_edges(graphs.base, graphs.head)))
            self.assertFalse(any(edge.to_symbol == "DirtyService" for edge in graphs.head.edges))
            self.assertEqual(
                (repo / "src" / "app" / "tree.service.ts").read_bytes(),
                b"export class DirtyService {}\n",
            )


if __name__ == "__main__":
    unittest.main()
