# Additive-only execution contract (mandatory)
- Preserve every existing file under `.cursor/rules/`, `docs/`, and `prompts/`. Do not delete, rename, overwrite, or replace any existing instructions or documents.
- This prompt is an extension to original prompts `01-scaffold.md`, `02-python-analysis.md`, `03-hardening.md`, `04-performance.md`; do not rerun or replace them.
- Inspect real source and tests first; preserve working Git diff, Python AST, CLI, and output schema. Add features and tests only, with minimal necessary edits to implementation files.
- Before changes, show `git status --short` and identify affected files. Do not discard user modifications. If conflict arises with existing instructions, stop and ask for approval.
- Cursor is for development with synthetic fixtures only; offline review execution never uses Cursor, remote AI, or external network.

# Prompt 01 — Audit existing implementation first

Read `.cursor/rules/*.mdc` and `docs/*.md`. Work ONLY in this synthetic/nonconfidential development workspace.

1. Inspect actual `offline_review` package, CLI, JSON reporter, Git object access and tests. Identify extension points; do not assume file layout.
2. Compare actual output schema to `docs/angular-extension/01-current-state.md` and identify existing fields that must be preserved.
3. Write `docs/angular-extension/05-implementation-plan.md` with modules, interfaces, phased tasks, risks and a test matrix.
4. Run existing tests and report exact command/result. Do not modify functionality in this step except fixing a demonstrable test harness issue with approval.
5. Explicitly confirm that Cursor is for TOOL DEVELOPMENT only; actual review is offline and separate.

Deliver: architecture delta, baseline test results, plan and unresolved questions. No invented success claims.
