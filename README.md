# Cursor starter kit: Offline Code Review CLI

`src/offline_review` resolves commits and writes `review-context.json` and `review-context.md` with old and new line ranges, affected Python definitions, and file-level import candidates. Pass `--python-root` for each import root. `--cache` is optional. See `docs/usage.md`. Acceptance checks and the synthetic 100k-line measurement are recorded in `docs/validation.md`.

## Usage
1. Open this folder as a workspace in Cursor.
2. Review `.cursor/rules/` and `docs/`.
3. Prompts 01 through 04 are already implemented. They are the record of the scaffold, Python analysis, hardening, and cache milestones.
4. Run `PYTHONPATH=src python -m unittest discover -s tests -v` (PowerShell: `$env:PYTHONPATH='src'; python -m unittest discover -s tests -v`). On the Ubuntu 26.04 distro, run that command after `wsl -d Ubuntu-26.04`. `python` there is CPython 3.13.12.
5. Use only synthetic or public source in Cursor AI. Do not upload company code or mTLS secrets.

## Boundary
Git mTLS client authentication is handled by an existing approved Git client before review. The generated CLI must read local Git objects only. Offline-by-design is not equivalent to OS-level network blocking.
