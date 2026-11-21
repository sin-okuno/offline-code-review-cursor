# Offline Code Review CLI — Requirements v1.0

## Purpose
Build a real, executable local code review context extractor for a >100k LOC Angular 22 + Django REST/Python 3.13 repository. The reviewed source stays local. Cursor AI is for implementation using synthetic data only.

## Environment
Windows 11 and WSL2, CPU only, 16GB available memory, Python 3.13 and Git. Any additional OSS requires company approval. Runtime is offline.

## Authentication boundary
Company Git remote uses HTTPS client certificates (mTLS). Authentication, fetch and pull happen separately via the existing approved Git client. This tool accepts a local repository and uses no remote authentication or network Git operations.

## MVP requirements
- CLI `python -m offline_review analyze --repo PATH --base REF --head REF --output DIR --comparison merge-base|direct [--python-root REL ...] [--cache DIR]`. `--python-root` is optional, repeatable, and is the only import root; omitting it parses Python files without assigning module names. `--cache` is an optional directory outside the repository. Omitting it parses on every run.
- Resolve refs to immutable commit SHAs and record them in output.
- Detect additions, modifications, deletions, renames and copy-status if supported; preserve status and old/new path.
- Extract changed old/new line ranges from unified diffs; deletion-only changes must be represented.
- Analyze Python source from the exact Git object for each revision, not the worktree.
- Extract affected classes/functions (including nested and async) and their source ranges; record top-level changes and syntax failures.
- Build file-level Python import candidate relationships with configurable source roots; distinguish direct matches from unresolved imports.
- For TS/HTML, preserve diff and changed line ranges; Angular semantic analysis is explicitly out of scope in MVP.
- Generate `review-context.json`, `review-context.md`, and warnings/coverage metadata. Output schema versioned.
- Read-only behavior. No network operations, no executing reviewed project scripts.
- Standard library only in runtime; unittest + temporary local Git repositories for automated tests.

## Acceptance criteria
- `python -m unittest discover -s tests -v` passes.
- Integration tests cover add/modify/delete/rename, deletion-only changes, unicode and space paths, uncommitted worktree differing from HEAD, nested functions/classes, syntax errors, non-main default branches, and missing refs.
- No source repository files modified by the tool.
- CLI runs successfully on a synthetic Angular+Django fixture and generates deterministic Markdown/JSON.
- Output clearly labels unsupported cases and incomplete dependency coverage.
- The tool never invokes `git fetch`, `git pull`, `git push`, `git clone`, remote APIs or model downloads.

## Explicit non-goals for MVP
- AI inference, static analysis runners, full Django framework relation discovery, full Angular/NgRx AST graph, uncommitted changes, automatic fixes, executing tests in reviewed repo, CI integration.

## Definition of done
Code, CLI help, docs, fixture, unit and integration tests, observed test outputs, limitations and troubleshooting are committed. Never declare completion based only on generated code.
