# Additive-only execution contract (mandatory)
- Preserve every existing file under `.cursor/rules/`, `docs/`, and `prompts/`. Do not delete, rename, overwrite, or replace any existing instructions or documents.
- This prompt is an extension to original prompts `01-scaffold.md`, `02-python-analysis.md`, `03-hardening.md`, `04-performance.md`; do not rerun or replace them.
- Inspect real source and tests first; preserve working Git diff, Python AST, CLI, and output schema. Add features and tests only, with minimal necessary edits to implementation files.
- Before changes, show `git status --short` and identify affected files. Do not discard user modifications. If conflict arises with existing instructions, stop and ask for approval.
- Cursor is for development with synthetic fixtures only; offline review execution never uses Cursor, remote AI, or external network.

# Prompt 06 — Performance at repository scale

Use generated synthetic data only. Build a representative ~100k-line Angular/NgRx repository fixture and benchmark index build, graph traversal, report generation and memory usage.

- Bound memory/output; cache by commit SHA + blob ID + extractor version where safe; no stale cross-revision data.
- Avoid repeated Git subprocess calls where batch reading is possible, but preserve security.
- Measure actual results before/after; document machine and synthetic fixture limitations.
- Ensure deterministic outputs and all regression tests pass.
- Provide `docs/07-benchmark.md` with reproducible commands and measured values.
