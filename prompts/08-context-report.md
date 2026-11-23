# Additive-only execution contract (mandatory)
- Preserve every existing file under `.cursor/rules/`, `docs/`, and `prompts/`. Do not delete, rename, overwrite, or replace any existing instructions or documents.
- This prompt is an extension to original prompts `01-scaffold.md`, `02-python-analysis.md`, `03-hardening.md`, `04-performance.md`; do not rerun or replace them.
- Inspect real source and tests first; preserve working Git diff, Python AST, CLI, and output schema. Add features and tests only, with minimal necessary edits to implementation files.
- Before changes, show `git status --short` and identify affected files. Do not discard user modifications. If conflict arises with existing instructions, stop and ask for approval.
- Cursor is for development with synthetic fixtures only; offline review execution never uses Cursor, remote AI, or external network.

# Prompt 04 — Bounded review context and report

Extend existing CLI without breaking arguments or old JSON keys.

- Use changed line ranges + graph to select actual source excerpts. Include base-side excerpts for deletions.
- Deterministic priority: changed files, confirmed direct deps, confirmed reverse deps, candidate deps.
- Add configurable max-depth/max-files/max-bytes/max-lines-per-excerpt with safe defaults and argument validation.
- Produce `review-context.md` with source SHA, path, line ranges, excerpt and selection reason. Escape Markdown code fences robustly.
- Add versioned `angular_context` JSON section with nodes, edges, selected, omitted, warnings, coverage.
- Update partial/analysis_complete without pretending full TS semantics.
- Protect existing JSON compatibility and verify report output on synthetic fixture.

Run all tests and show a short synthetic sample of Markdown/JSON output.
