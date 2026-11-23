"""Template bindings, NgRx paths, and the review line budget."""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from offline_review.context_builder import build_context
from offline_review.context_selector import ContextBudget
from offline_review.dependency_graph import build_angular_graph, walk_forward
from offline_review.git_adapter import GitAdapter
from offline_review.html_bindings import scan_bindings
from offline_review.typescript_index import TsCommitIndex, scan_html, scan_typescript
from tests.support import commit_all, head_sha, init_repo, write

FILTER_TS = """\
import { Component } from '@angular/core';
import { Store } from '@ngrx/store';
import { SetFilterAction } from './product-tree.actions';

@Component({
  selector: 'app-product-tree-filter',
  templateUrl: './product-tree-filter.component.html',
})
export class ProductTreeFilterComponent {
  constructor(private store: Store) {}

  toggleRisk(level: string): void {
    this.store.dispatch(SetFilterAction({ riskLevels: [level] }));
  }
}
"""

FILTER_HTML = """\
<!-- (click)="toggleRisk(level)" -->
<button (click)="toggleRisk(level)">{{ level }}</button>
@if (visible) {
  <app-risk-detail></app-risk-detail>
}
@for (item of levels; track item) {
  <span [value]="item">{{ item | async }}</span>
}
<button (click)="missingMethod()">nope</button>
<button (click)="one() + two()">complex</button>
"""

INLINE = """\
import { Component } from '@angular/core';

@Component({
  selector: 'app-inline',
  template: '<button (click)="save()">save</button>',
})
export class InlineComponent {
  save(): void {}
}
"""

ACTIONS = """\
import { createAction, props } from '@ngrx/store';

export const SetFilterAction = createAction(
  '[Product Tree] Set Filter',
  props<{ riskLevels: string[] }>(),
);
"""

REDUCER = """\
import { createReducer, createSelector, on } from '@ngrx/store';
import { SetFilterAction } from './product-tree.actions';

export const productTreeReducer = createReducer(
  {},
  on(SetFilterAction, (state) => state),
);

export const selectRiskFilter = createSelector(
  (state: object) => state,
  (state) => state,
);
"""

EFFECT = """\
import { inject } from '@angular/core';
import { Actions, createEffect, ofType } from '@ngrx/effects';
import { SetFilterAction } from './product-tree.actions';
import { ProductTreeService } from './product-tree.service';

export const saveFilter = createEffect(
  (actions$ = inject(Actions), api = inject(ProductTreeService)) =>
    actions$.pipe(ofType(SetFilterAction)),
  { functional: true },
);
"""

API = """\
export class ProductTreeService {
  load(): void {}
}
"""

VIEW = """\
import { Component } from '@angular/core';
import { Store } from '@ngrx/store';
import { selectRiskFilter } from './product-tree.reducer';

@Component({ selector: 'app-product-tree-view', template: '' })
export class ProductTreeViewComponent {
  constructor(private store: Store) {
    this.store.select(selectRiskFilter);
  }
}
"""

DETAIL = """\
import { Component } from '@angular/core';

@Component({ selector: 'app-risk-detail', template: '' })
export class RiskDetailComponent {}
"""

CYCLE_A = """\
import { B } from './b';
export class A {
  other: B | null = null;
}
"""

CYCLE_B = """\
import { A } from './a';
export class B {
  other: A | null = null;
}
"""


def _ts(path: str, source: str):
    indexed = scan_typescript(source.encode())
    return replace(indexed, path=path, warnings=tuple(replace(item, path=path) for item in indexed.warnings))


def _html(path: str, source: str):
    indexed = scan_html(source.encode())
    return replace(indexed, path=path)


def _graph(*files, html=()):
    index = TsCommitIndex("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", files, html, ())
    return build_angular_graph(index, ())


class BindingTests(unittest.TestCase):
    def test_event_if_for_and_comments(self) -> None:
        bindings = scan_bindings(FILTER_HTML)
        events = [item for item in bindings if item.kind == "event"]
        self.assertEqual(events[0].name, "toggleRisk")
        self.assertTrue(events[0].simple)
        self.assertEqual(next(item.name for item in bindings if item.kind == "if_block"), "visible")
        self.assertEqual(next(item.name for item in bindings if item.kind == "for_block"), "levels")
        self.assertEqual(next(item.name for item in bindings if item.kind == "selector"), "app-risk-detail")
        self.assertIn("pipe", {item.kind for item in bindings})
        self.assertFalse(any(item.name == "toggleRisk" and item.line == 1 for item in bindings))

    def test_comment_does_not_confirm_a_call(self) -> None:
        bindings = scan_bindings("<!-- (click)=\"hidden()\" -->\n")
        self.assertEqual(bindings, ())


class NgRxGraphTests(unittest.TestCase):
    def test_template_method_and_dispatch_are_separate_edges(self) -> None:
        graph = _graph(
            _ts("src/filter.component.ts", FILTER_TS),
            _ts("src/product-tree.actions.ts", ACTIONS),
            _ts("src/risk-detail.component.ts", DETAIL),
            html=(_html("src/product-tree-filter.component.html", FILTER_HTML),),
        )
        edges = {(item.kind, item.confidence, item.from_path, item.to_symbol, item.resolution_reason) for item in graph.edges}
        self.assertIn(("template_binding", "confirmed", "src/product-tree-filter.component.html", "toggleRisk", ""), edges)
        self.assertIn(("template_binding", "unresolved", "src/product-tree-filter.component.html", "missingMethod", "target_not_found"), edges)
        self.assertIn(("template_binding", "candidate", "src/product-tree-filter.component.html", None, "heuristic"), edges)
        self.assertIn(("ngrx_action_reference", "confirmed", "src/filter.component.ts", "SetFilterAction", ""), edges)
        self.assertIn(("component_selector", "candidate", "src/product-tree-filter.component.html", "app-risk-detail", "heuristic"), edges)

    def test_inline_template_links_the_same_file(self) -> None:
        graph = _graph(_ts("src/inline.component.ts", INLINE))
        confirmed = [
            item for item in graph.edges if item.kind == "template_binding" and item.confidence == "confirmed"
        ]
        self.assertEqual(confirmed[0].from_path, "src/inline.component.ts")
        self.assertEqual(confirmed[0].to_symbol, "save")

    def test_action_reaches_reducer_effect_and_selector(self) -> None:
        graph = _graph(
            _ts("src/filter.component.ts", FILTER_TS),
            _ts("src/product-tree.actions.ts", ACTIONS),
            _ts("src/product-tree.reducer.ts", REDUCER),
            _ts("src/product-tree.effects.ts", EFFECT),
            _ts("src/product-tree.service.ts", API),
            _ts("src/view.component.ts", VIEW),
            html=(_html("src/product-tree-filter.component.html", "<p></p>\n"),),
        )
        kinds = {(item.kind, item.confidence, item.from_path, item.to_path) for item in graph.edges}
        self.assertIn(("ngrx_action_reference", "confirmed", "src/product-tree.reducer.ts", "src/product-tree.actions.ts"), kinds)
        self.assertIn(("ngrx_action_reference", "confirmed", "src/product-tree.effects.ts", "src/product-tree.actions.ts"), kinds)
        self.assertIn(("service_reference", "confirmed", "src/product-tree.effects.ts", "src/product-tree.service.ts"), kinds)
        self.assertIn(("ngrx_selector_reference", "confirmed", "src/view.component.ts", "src/product-tree.reducer.ts"), kinds)

    def test_duplicate_action_import_stays_unresolved(self) -> None:
        source = """\
import { SetFilterAction } from './left.actions';
import { SetFilterAction } from './right.actions';
export class User {
  run(): void { this.store.dispatch(SetFilterAction({})); }
}
"""
        graph = _graph(
            _ts("src/user.ts", source),
            _ts("src/left.actions.ts", "export const SetFilterAction = 1;\n"),
            _ts("src/right.actions.ts", "export const SetFilterAction = 2;\n"),
        )
        matched = [item for item in graph.edges if item.to_symbol == "SetFilterAction"]
        self.assertTrue(matched)
        self.assertTrue(all(item.confidence != "confirmed" for item in matched))

    def test_cycle_stops(self) -> None:
        graph = _graph(_ts("src/a.ts", CYCLE_A), _ts("src/b.ts", CYCLE_B))
        result = walk_forward(graph, "src/a.ts", max_depth=8)
        self.assertTrue(result.cycles)
        self.assertLess(len(result.paths), 20)

    def test_missing_import_is_classified_and_does_not_stop(self) -> None:
        source = "import { Missing } from './missing';\nexport class Host {}\n"
        graph = _graph(_ts("src/host.ts", source))
        unresolved = [item for item in graph.edges if item.confidence == "unresolved"]
        self.assertEqual(unresolved[0].resolution_reason, "target_not_found")
        self.assertIsNone(unresolved[0].to_path)


class ImpactSelectionTests(unittest.TestCase):
    def test_budget_keeps_the_change_and_reports_the_gap(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / "repo"
            init_repo(repo)
            app = repo / "src"
            write(app / "filter.component.ts", FILTER_TS.encode())
            write(app / "product-tree-filter.component.html", FILTER_HTML.encode())
            write(app / "product-tree.actions.ts", ACTIONS.encode())
            write(app / "product-tree.reducer.ts", REDUCER.encode())
            write(app / "product-tree.effects.ts", EFFECT.encode())
            write(app / "product-tree.service.ts", API.encode())
            write(app / "view.component.ts", VIEW.encode())
            write(app / "risk-detail.component.ts", DETAIL.encode())
            base = commit_all(repo, "base")
            updated = FILTER_TS.replace("toggleRisk", "toggleRisk")
            updated = updated.replace(
                "this.store.dispatch(SetFilterAction({ riskLevels: [level] }));",
                "this.store.dispatch(SetFilterAction({ riskLevels: [level, 'x'] }));",
            )
            write(app / "filter.component.ts", updated.encode())
            head = commit_all(repo, "head")
            self.assertNotEqual(base, head)
            with GitAdapter(repo) as adapter:
                context = build_context(
                    adapter,
                    repository=str(repo),
                    base=base,
                    head=head_sha(repo),
                    comparison="direct",
                    budget=ContextBudget(max_depth=4, max_files=40, max_total_lines=2000),
                )
            angular = context.angular_context
            names = {item.qualname for item in angular.changed_symbols}
            self.assertIn("ProductTreeFilterComponent.toggleRisk", names)
            edge_names = {(item.kind, item.to_symbol, item.confidence) for item in angular.edges}
            self.assertIn(("template_binding", "toggleRisk", "confirmed"), edge_names)
            self.assertIn(("ngrx_action_reference", "SetFilterAction", "confirmed"), edge_names)
            selected_paths = {item.path for item in angular.selected}
            self.assertIn("src/product-tree.reducer.ts", selected_paths)
            self.assertIn("src/view.component.ts", selected_paths)
            self.assertLessEqual(angular.coverage.lines_selected, 2000)
            dependency_revisions = {
                item.revision for item in angular.selected if item.path == "src/product-tree.actions.ts"
            }
            self.assertLessEqual(len(dependency_revisions), 1)

            with GitAdapter(repo) as adapter:
                tight = build_context(
                    adapter,
                    repository=str(repo),
                    base=base,
                    head=head,
                    comparison="direct",
                    budget=ContextBudget(max_total_lines=2, max_files=40),
                )
            omitted = tight.angular_context.omitted
            self.assertTrue(any(item.reason == "max_total_lines" and item.missing_lines > 0 for item in omitted))
            self.assertTrue(any(item.code == "CHANGED_CONTEXT_OMITTED" for item in tight.angular_context.warnings))
            changed = [item for item in tight.angular_context.selected if item.reason == "changed file"]
            self.assertTrue(changed)
            self.assertLessEqual(sum(item.content.count("\n") + 1 for item in changed), 2)


CLASS_ACTIONS = """\
export class LoadTreeAction {
  readonly type = '[Product Tree] Load';
}

export class SetFilterAction {
  constructor(public name: string) {}
  readonly type = '[Product Tree] Set Filter';
}

export class OldAction {
  readonly type = '[Product Tree] Old';
}
"""

CLASS_COMPONENT = """\
import { Component } from '@angular/core';
import { Store } from '@ngrx/store';
import { LoadTreeAction, SetFilterAction } from './product-tree.actions';

@Component({ selector: 'app-filter', template: '' })
export class FilterComponent {
  constructor(private store: Store) {}

  load(): void {
    // this.store.dispatch(new FakeAction());
    const sample = "this.store.dispatch(new FakeAction())";
    this.store.dispatch(new LoadTreeAction());
    this.store.dispatch(
      new SetFilterAction({ name: 'x', nested: (1 + 2) })
    );
  }
}
"""

CLASS_REDUCER = """\
import { on } from '@ngrx/store';
import { LoadTreeAction } from './product-tree.actions';

export const reducer = on(LoadTreeAction, (state) => state);
"""

CLASS_EFFECT = """\
import { ofType } from '@ngrx/effects';
import { LoadTreeAction } from './product-tree.actions';

export const effect = ofType(LoadTreeAction);
"""


class ActionClassTests(unittest.TestCase):
    def test_new_expression_names_the_class_and_not_the_keyword(self) -> None:
        graph = _graph(
            _ts("src/filter.component.ts", CLASS_COMPONENT),
            _ts("src/product-tree.actions.ts", CLASS_ACTIONS),
            _ts("src/product-tree.reducer.ts", CLASS_REDUCER),
            _ts("src/product-tree.effects.ts", CLASS_EFFECT),
        )
        self.assertFalse(any(edge.to_symbol == "new" for edge in graph.edges))
        load = [
            edge
            for edge in graph.edges
            if edge.kind == "ngrx_action_reference" and edge.to_symbol == "LoadTreeAction"
        ]
        self.assertTrue(any(edge.confidence == "confirmed" and edge.to_path == "src/product-tree.actions.ts" for edge in load))
        dispatch = next(edge for edge in load if edge.from_path == "src/filter.component.ts")
        self.assertIn("dispatch(new LoadTreeAction", dispatch.evidence)
        filtered = next(
            edge
            for edge in graph.edges
            if edge.to_symbol == "SetFilterAction" and edge.from_path == "src/filter.component.ts"
        )
        self.assertEqual(filtered.confidence, "confirmed")
        self.assertEqual(filtered.to_path, "src/product-tree.actions.ts")
        self.assertNotIn("FakeAction", {edge.to_symbol for edge in graph.edges})
        self.assertTrue(
            any(edge.from_path == "src/product-tree.reducer.ts" and edge.to_symbol == "LoadTreeAction" for edge in load)
        )
        self.assertTrue(
            any(edge.from_path == "src/product-tree.effects.ts" and edge.to_symbol == "LoadTreeAction" for edge in load)
        )

    def test_alias_namespace_call_and_unresolved_variable(self) -> None:
        alias = """\
import { LoadTreeAction as Load } from './product-tree.actions';
export class FilterComponent {
  run(store: { dispatch(action: object): void }, actionVariable: object): void {
    store.dispatch(new Load());
    store.dispatch(loadTree());
    store.dispatch(actionVariable);
  }
}
"""
        namespace = """\
import * as ProductTreeActions from './product-tree.actions';
export class FilterComponent {
  run(store: { dispatch(action: object): void }): void {
    store.dispatch(ProductTreeActions.loadTree());
  }
}
"""
        creator = "export const loadTree = () => ({ type: '[Product Tree] Load' });\n"
        aliased = _graph(
            _ts("src/filter.component.ts", alias),
            _ts("src/product-tree.actions.ts", CLASS_ACTIONS + creator),
        )
        symbols = {(edge.to_symbol, edge.confidence, edge.resolution_reason) for edge in aliased.edges}
        self.assertIn(("Load", "confirmed", ""), symbols)
        self.assertIn(("loadTree", "unresolved", "target_not_found"), symbols)
        self.assertIn(("actionVariable", "unresolved", "target_not_found"), symbols)
        self.assertNotIn("new", {edge.to_symbol for edge in aliased.edges})
        namespaced = _graph(
            _ts("src/filter.component.ts", namespace),
            _ts("src/product-tree.actions.ts", CLASS_ACTIONS),
        )
        kept = [
            edge
            for edge in namespaced.edges
            if edge.kind == "ngrx_action_reference" and edge.confidence == "confirmed"
        ]
        self.assertEqual(kept[0].to_symbol, "ProductTreeActions")
        self.assertEqual(kept[0].to_path, "src/product-tree.actions.ts")

    def test_same_class_name_in_two_modules_is_not_confirmed(self) -> None:
        source = """\
import { LoadTreeAction } from './left.actions';
import { LoadTreeAction } from './right.actions';
export class FilterComponent {
  run(store: { dispatch(action: object): void }): void {
    store.dispatch(new LoadTreeAction());
  }
}
"""
        graph = _graph(
            _ts("src/filter.component.ts", source),
            _ts("src/left.actions.ts", "export class LoadTreeAction {}\n"),
            _ts("src/right.actions.ts", "export class LoadTreeAction {}\n"),
        )
        matched = [edge for edge in graph.edges if edge.to_symbol == "LoadTreeAction"]
        self.assertTrue(matched)
        self.assertTrue(all(edge.confidence != "confirmed" for edge in matched))
        self.assertTrue(all(edge.resolution_reason == "ambiguous_resolution" for edge in matched))

    def test_base_and_head_keep_different_action_classes(self) -> None:
        base_component = CLASS_COMPONENT.replace("LoadTreeAction", "OldAction").replace(
            "import { OldAction, SetFilterAction }",
            "import { OldAction }",
        )
        base = build_angular_graph(
            TsCommitIndex(
                "b" * 40,
                (
                    _ts("src/filter.component.ts", base_component),
                    _ts("src/product-tree.actions.ts", CLASS_ACTIONS),
                ),
                (),
                (),
            ),
            (),
        )
        head = _graph(
            _ts("src/filter.component.ts", CLASS_COMPONENT),
            _ts("src/product-tree.actions.ts", CLASS_ACTIONS),
        )
        base_symbols = {edge.to_symbol for edge in base.edges if edge.kind == "ngrx_action_reference"}
        head_symbols = {edge.to_symbol for edge in head.edges if edge.kind == "ngrx_action_reference"}
        self.assertIn("OldAction", base_symbols)
        self.assertNotIn("LoadTreeAction", base_symbols)
        self.assertIn("LoadTreeAction", head_symbols)
        self.assertTrue(all(edge.source_revision == "b" * 40 for edge in base.edges))
        self.assertTrue(all(edge.source_revision == "a" * 40 for edge in head.edges))

