# Additive-only execution contract (mandatory)
- Preserve every existing file under `.cursor/rules/`, `docs/`, and `prompts/`. Do not delete, rename, overwrite, or replace any existing instructions or documents.
- This prompt is an extension to original prompts `01-scaffold.md`, `02-python-analysis.md`, `03-hardening.md`, `04-performance.md`; do not rerun or replace them.
- Inspect real source and tests first; preserve working Git diff, Python AST, CLI, and output schema. Add features and tests only, with minimal necessary edits to implementation files.
- Before changes, show `git status --short` and identify affected files. Do not discard user modifications. If conflict arises with existing instructions, stop and ask for approval.
- Cursor is for development with synthetic fixtures only; offline review execution never uses Cursor, remote AI, or external network.

# Prompt 03 — Angular/NgRx dependency graph

Implement graph and resolution per `docs/angular-extension/02-requirements.md` and `docs/angular-extension/03-architecture.md`.

- Resolve direct relative imports, extension/index candidates and safely supported tsconfig paths. Distinguish confirmed/ambiguous/unresolved.
- Link explicit `@Component.templateUrl` to HTML, NgModule declarations and directly evidenced NgRx Action/Effect/Reducer/Selector/Service references.
- Implement reverse dependency lookup and revision-aware graph for base/head. No guess-based confirmed edges.
- Add tests for Component -> HTML, Component -> Service, Effect -> Action, reverse import, deletion-only and cycle.
- Report status and limitations accurately.

Run all tests; document evidence rules and coverage gaps.
