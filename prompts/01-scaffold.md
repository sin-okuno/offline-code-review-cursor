# Cursor Prompt 01 — Scaffold and tests first

Read all `.cursor/rules/*.mdc`, `docs/requirements.md`, and `docs/architecture.md` before editing. Implement a real Python 3.13 package under `src/offline_review` with a CLI and tests, using Python standard library only at runtime. Start with a minimal working end-to-end pipeline, then incrementally implement requirements. Do not merely produce pseudocode or a plan.

First implement: `git_adapter.py` with immutable commit resolution, merge-base, read-only diff/status, Git object reading; `diff_parser.py` supporting old and new line ranges; CLI; JSON/Markdown output. Include a synthetic temporary Git repository integration test that verifies modified and deleted lines. Avoid network operations and external diff/textconv. Ensure `PYTHONPATH=src python -m unittest discover -s tests -v` runs and report observed results. If a test fails, fix it before proceeding. Do not read real company repositories.
