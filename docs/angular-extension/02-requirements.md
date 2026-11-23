# Angular context extraction requirements v1.0

## Objective
Given an existing offline `offline_review analyze --repo ... --base ... --head ... --output ...` invocation, produce a bounded, evidence-backed review context for Angular 22, TypeScript, HTML and NgRx without sending code to Cursor or any network service.

## Functional requirements
- FR-01: Preserve existing Git diff results and Python support.
- FR-02: Index `.ts` and `.html` files at BOTH immutable revisions; skip generated, binary, submodule and oversized files with explicit warnings.
- FR-03: Identify unambiguous changed TS classes/functions/methods, or mark symbol identification unknown. Include line ranges and evidence.
- FR-04: Resolve relative imports and safe `tsconfig` aliases where possible. Classify unresolved/ambiguous imports.
- FR-05: Link `@Component` to external HTML via explicit `templateUrl`; link NgModule declarations and explicit NgRx Action/Effect/Reducer/Selector/Service references as evidence allows.
- FR-06: Traverse forward and reverse dependencies with deterministic ranking and limits.
- FR-07: Output `review-context.md` and an additive versioned JSON section `angular_context` with nodes, edges, selections, omitted, warnings and coverage.
- FR-08: Include changed code excerpts plus nearby context, dependency excerpts and old-side excerpts for deletions; annotate commit SHA, file path, line range, confidence and reason for inclusion.
- FR-09: Keep review extraction separate from local AI inference; do not claim the extractor detected bugs.
- FR-10: Support synthetic Angular 22 NgModule + NgRx fixtures and regression tests.

## Nonfunctional / security
Offline, read-only, no target code execution, no network, no Cursor use during review, no credentials handled, deterministic outputs, bounded runtime/memory/output. Keep runtime Python stdlib + Git initially; do not add npm/Node/parser dependencies without approval.

## Explicit limitations
A Python-only lexical extractor cannot provide full TypeScript AST, type resolution, template binding validation or runtime call graph. Report conservative coverage. For full semantics, propose a separate opt-in TypeScript Compiler API/ts-morph phase after company approval and offline packaging review.

## Acceptance criteria
1. Existing tests and JSON fields remain valid.
2. Synthetic fixture: changing component `.ts` selects its linked `.html`, imported service and confirmed reverse importers within limits.
3. Deleting an exported function retains base-revision evidence.
4. Ambiguous aliases or syntax produce warnings, not false confirmed edges.
5. `review-context.md` includes actual source excerpts and provenance, not only line-number arrays.
6. Deterministic output for identical base/head and options.
7. No network operations, execution of target source, or target repository writes.
8. `analysis_complete` remains false if any required analysis is unsupported/truncated.
