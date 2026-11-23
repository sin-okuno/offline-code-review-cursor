# Additive-only execution contract (mandatory)
- Preserve every existing file under `.cursor/rules/`, `docs/`, and `prompts/`. Do not delete, rename, overwrite, or replace any existing instructions or documents.
- This prompt is an extension to original prompts `01-scaffold.md`, `02-python-analysis.md`, `03-hardening.md`, `04-performance.md`; do not rerun or replace them.
- Inspect real source and tests first; preserve working Git diff, Python AST, CLI, and output schema. Add features and tests only, with minimal necessary edits to implementation files.
- Before changes, show `git status --short` and identify affected files. Do not discard user modifications. If conflict arises with existing instructions, stop and ask for approval.
- Cursor is for development with synthetic fixtures only; offline review execution never uses Cursor, remote AI, or external network.

# Prompt 02 — Conservative TypeScript index

Implement the TS indexing portion of `docs/angular-extension/02-requirements.md` according to the audited plan.

- Read base/head source only from immutable Git blobs using existing adapter; do not use working tree source.
- Build conservative scanner that ignores comments/string content correctly; recognize supported imports, class/function/method boundaries and component decorators, with source line ranges and evidence.
- If syntax is too complex to parse safely, mark `unknown` and emit a warning. Do not claim TypeScript AST completeness.
- Index both revisions and handle removed symbols and files.
- Provide typed public functions and tests using synthetic files. Add no runtime dependencies.
- Keep existing CLI/report behavior intact at this phase.

Run unit and regression tests. Return changed files, actual test results, known unsupported syntax.
