# Test and validation plan

## Fixture matrix
- TS import: single/multiline, aliases, type-only, re-export, index resolution, relative traversal, tsconfig paths, unresolved/ambiguous.
- Angular: NgModule, Component decorator, templateUrl, template containing binding, service injection, NgRx action/effect/reducer/selector explicit references.
- Diff: add/edit/delete, deletion-only, rename, old/new revisions, Unicode paths, CRLF, zero-line hunk.
- Safety: comments containing fake imports, strings containing fake decorators, malformed TS, huge file, cycles, max depth/files/bytes, binary file.
- Regression: existing Python AST and Git diff tests; no changes to existing JSON keys.

## Gates
Run `python -m unittest discover -s tests -v` using the repository's documented PYTHONPATH/install procedure. Run a synthetic integration command with immutable SHA refs. Compare output twice after normalizing any deliberately variable metadata. Record actual timings on generated ~100k-line synthetic repo; do not claim target performance based only on fixture.

## Manual inspection
Confirm 1) actual excerpts included, 2) source paths and commit provenance accurate, 3) linked HTML selected, 4) unresolved imports not treated as confirmed, 5) no network or repository modifications.
