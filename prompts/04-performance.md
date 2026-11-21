# Cursor Prompt 04 — 100k+ LOC optimization

Only after acceptance tests pass: profile on a generated synthetic repository with at least 100k lines. Add a reverse-import index and optional cache keyed by commit SHA, parser version and import-root configuration; avoid N_changed x N_files repeated parsing. Keep memory bounded and output deterministic. Measure wall-clock runtime and peak RSS where available; distinguish measured results from estimates. Ensure cache invalidation is correct and tests still pass. Do not add dependencies without explicit approval.
