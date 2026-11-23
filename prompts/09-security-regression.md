# Additive-only execution contract (mandatory)
- Preserve every existing file under `.cursor/rules/`, `docs/`, and `prompts/`. Do not delete, rename, overwrite, or replace any existing instructions or documents.
- This prompt is an extension to original prompts `01-scaffold.md`, `02-python-analysis.md`, `03-hardening.md`, `04-performance.md`; do not rerun or replace them.
- Inspect real source and tests first; preserve working Git diff, Python AST, CLI, and output schema. Add features and tests only, with minimal necessary edits to implementation files.
- Before changes, show `git status --short` and identify affected files. Do not discard user modifications. If conflict arises with existing instructions, stop and ask for approval.
- Cursor is for development with synthetic fixtures only; offline review execution never uses Cursor, remote AI, or external network.

# Prompt 05 — Security, accuracy, regression

Audit implementation against every rule and acceptance criterion.

- Verify no remote Git commands, network calls, target repo writes, code execution, `shell=True`, unsafe path handling, arbitrary plugin loading or secret collection.
- Validate revision provenance and deterministic outputs, Unicode/Windows paths, binary/symlink/submodule handling, rename/deletion, large files, cycles and budget truncation.
- Add adversarial fixtures with fake imports in comments/strings and malformed TS.
- Run full unit/integration/regression tests, including preexisting Python analysis.
- Document confirmed functionality vs limitations in `docs/06-validation-report.md` with exact test commands and results.

Fix failures and rerun. Do not claim tests were executed unless actually run.
