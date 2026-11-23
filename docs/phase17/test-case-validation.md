# Phase 17 test-case validation

Checked on 2026-11-23. No fixture commit was created or rewritten. Source code was not changed.

## Repository

The tool directory is `offline-code-review-cursor` inside Git root `C:/work/00_src`. The work tree already has uncommitted tool changes from Phases 12–16. Those files were left untouched.

The synthetic fixture is `fixtures/phase16/angular-ngrx`. Its Git directory is ignored by the tool repository. Ground truth is `fixtures/phase16/cases/`.

Baseline recorded in `manifest.json`:

`1da882dfca3c24824625282bfead94b4dbdb8c1e`

Every ANG head commit exists, has type `commit`, and has that baseline as its only parent. No case stacks another case's change.

## Case check

| Case | Head | Changed path | What the diff actually does | Setup |
|---|---|---|---|---|
| ANG-001 | `345362e208ffc852e070d5deee361e7523bd3397` | `beacon.effects.ts` | Deletes the `catchError` that emits `loadBeaconsFailure` inside `loadBeaconsEffect`. One deletion, no insertion. | valid |
| ANG-002 | `1a98ab8eadb772267e9cb929048d1d2611bf1ba8` | `beacon.reducer.ts` | Replaces the immutable `saveBeaconSuccess` handler with `state.beacons.push(beacon)`. | valid |
| ANG-003 | `c62df61973335c51d5b0266b5f09aa5f2611a21d` | `beacon.selectors.ts` | `selectBeaconName` returns `state.notice` instead of the selected beacon name. | valid |
| ANG-004 | `06e7fedb5a38d770da965a18ce6b54a1cc062a07` | `beacon.effects.ts` | `navigateAfterSaveEffect` listens for `saveBeacon` instead of `saveBeaconSuccess`. | valid |
| ANG-005 | `17057b42c2b792c7a0b537245a4fb21c9b849e09` | `beacon.effects.ts` | `saveBeaconEffect` stores status `0` and message `network`. | valid |
| ANG-006 | `6ebffb7f5c7d898134819482e2d6acc60e2444ea` | `beacon.component.ts` | `save` checks `this.locked` and no longer checks `this.form.invalid`. | valid |
| ANG-007 | `56218f40f5b3f3ce40bb9e2db2c0586ab4f27354` | `beacon.component.ts` | `choose` loads `this.fallbackId` instead of the selected id. | valid |
| ANG-008 | `24426a5095677570a39d8c5a66ca10884d096d07` | `beacon.effects.ts` | `saveBeaconEffect` maps success to `navigateAfterSave()` instead of `saveBeaconSuccess`. | valid |
| ANG-009 | `edaff7ea867d7c352281e5e558f6fb366d7762ac` | `beacon.component.ts` | Removes `ngOnDestroy` and the `OnDestroy` implement clause. | valid |
| ANG-010 | `746cf77da2808bc1099248a00ea6068bc8d4eb21` | `beacon.component.ts` | `save` checks `this.form.invalid` and no longer checks `this.locked`. | valid |

ANG-002 does not touch `beacon.effects.ts` and does not delete `catchError`. The current ground truth is not `INVALID_TEST_SETUP`.

`git diff` was `direct` two-dot from the shared baseline to each head. Ground-truth `base_revision` and `buggy_revision` match those commits.
