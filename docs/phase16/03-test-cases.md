# Phase 16 test cases

The fixture is a fictional beacon ledger. It is an NgModule around one feature: actions, reducer, selectors, effects, an HTTP service, a component, and a template. No case id or answer label appears in those files.

Baseline commit: `1da882dfca3c24824625282bfead94b4dbdb8c1e`.

Each case below is a branch from that baseline. The ground-truth files in `fixtures/phase16/cases/` hold the commit ids and line numbers. Rebuild with:

```text
python -m offline_review.evaluation.fixture_builder \
  --repo fixtures/phase16/angular-ngrx \
  --cases fixtures/phase16/cases
```

The destination directories must not already contain a repository or ground-truth files. Fixed author dates keep the commit ids stable.

| ID | Defect | Head commit | Evidence that must be read together |
|---|---|---|---|
| ANG-001 | The load effect no longer emits a failure action | `345362e208ffc852e070d5deee361e7523bd3397` | `loadBeaconsEffect`, `BeaconService.loadBeacons`, `beaconReducer` |
| ANG-002 | The save reducer pushes into the existing array | `1a98ab8eadb772267e9cb929048d1d2611bf1ba8` | `saveBeaconSuccess`, `selectBeacons` |
| ANG-003 | The name selector returns `notice` | `c62df61973335c51d5b0266b5f09aa5f2611a21d` | `selectBeaconName`, reducer `selectedId`, component `selectBeaconName` |
| ANG-004 | Navigation listens for the save request instead of its success | `06e7fedb5a38d770da965a18ce6b54a1cc062a07` | `navigateAfterSaveEffect`, `BeaconService.saveBeacon` |
| ANG-005 | Save failures store status 0 instead of the HTTP status | `17057b42c2b792c7a0b537245a4fb21c9b849e09` | `saveBeaconEffect`, `BeaconService.saveBeacon` |
| ANG-006 | Save no longer returns when the form is invalid | `6ebffb7f5c7d898134819482e2d6acc60e2444ea` | `Validators`, template `required` |
| ANG-007 | Detail load uses `fallbackId` instead of the selected id | `56218f40f5b3f3ce40bb9e2db2c0586ab4f27354` | `choose`, `loadBeaconDetailEffect` |
| ANG-008 | A successful save dispatches navigation and skips the list update | `24426a5095677570a39d8c5a66ca10884d096d07` | `saveBeaconEffect`, reducer `saveBeaconSuccess`, `selectBeacons` |
| ANG-009 | The name subscription has no `ngOnDestroy` | `edaff7ea867d7c352281e5e558f6fb366d7762ac` | `subscribe`, `selectBeaconName` |
| ANG-010 | The button is disabled while locked, but `save` no longer checks `locked` | `746cf77da2808bc1099248a00ea6068bc8d4eb21` | template `disabled`, `save` |

Clean cases, not counted as defects:

| ID | Change | Head commit |
|---|---|---|
| CLEAN-001 | Heading text only | `a657a176d6810bc052bcdf6f2b037d2482bfb2a6` |
| CLEAN-002 | `READY_LABEL` text only | `5c5a94541db679b2018f171150f9da23c33a7606` |

`ng build` and `tsc` were not run. Creating an Angular project would need a package download. The lexical scanner accepts every TypeScript file at every revision. `beacon.module.ts` still reports `TS_NGMODULE_UNKNOWN` because `StoreModule.forFeature(...)` is not a bare identifier. That warning is a scanner limit, not one of the ten defects.
