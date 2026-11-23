"""Synthetic beacon-ledger sources. The names are fictional and contain no case ids."""

from __future__ import annotations

from dataclasses import dataclass

BASE_FILES: dict[str, str] = {
    "tsconfig.json": """\
{
  "compilerOptions": {
    "strict": true,
    "baseUrl": "."
  }
}
""",
    "src/app/beacon/beacon.models.ts": """\
export interface Beacon {
  id: string;
  name: string;
  status: string;
}

export const READY_LABEL = 'Ready';
""",
    "src/app/beacon/beacon.actions.ts": """\
import { createAction, props } from '@ngrx/store';
import { Beacon } from './beacon.models';

export const loadBeacons = createAction('[Beacon] Load');
export const loadBeaconsSuccess = createAction(
  '[Beacon] Load Success',
  props<{ beacons: Beacon[] }>(),
);
export const loadBeaconsFailure = createAction(
  '[Beacon] Load Failure',
  props<{ message: string }>(),
);
export const selectBeacon = createAction('[Beacon] Select', props<{ beaconId: string }>());
export const loadBeaconDetail = createAction(
  '[Beacon] Load Detail',
  props<{ beaconId: string }>(),
);
export const loadBeaconDetailSuccess = createAction(
  '[Beacon] Load Detail Success',
  props<{ beacon: Beacon }>(),
);
export const saveBeacon = createAction('[Beacon] Save', props<{ beacon: Beacon }>());
export const saveBeaconSuccess = createAction(
  '[Beacon] Save Success',
  props<{ beacon: Beacon }>(),
);
export const saveBeaconFailure = createAction(
  '[Beacon] Save Failure',
  props<{ status: number; message: string }>(),
);
export const navigateAfterSave = createAction('[Beacon] Navigate After Save');
""",
    "src/app/beacon/beacon.reducer.ts": """\
import { createReducer, on } from '@ngrx/store';
import { Beacon } from './beacon.models';
import {
  loadBeaconDetailSuccess,
  loadBeaconsSuccess,
  saveBeaconSuccess,
  selectBeacon,
} from './beacon.actions';

export interface BeaconState {
  beacons: Beacon[];
  selectedId: string;
  notice: string;
}

export const initialBeaconState: BeaconState = {
  beacons: [],
  selectedId: '',
  notice: '',
};

export const beaconReducer = createReducer(
  initialBeaconState,
  on(loadBeaconsSuccess, (state, { beacons }) => ({
    beacons: beacons,
    selectedId: state.selectedId,
    notice: state.notice,
  })),
  on(selectBeacon, (state, { beaconId }) => ({
    beacons: state.beacons,
    selectedId: beaconId,
    notice: state.notice,
  })),
  on(loadBeaconDetailSuccess, (state, { beacon }) => ({
    beacons: state.beacons.map((item) => (item.id === beacon.id ? beacon : item)),
    selectedId: beacon.id,
    notice: state.notice,
  })),
  on(saveBeaconSuccess, (state, { beacon }) => ({
    beacons: state.beacons.map((item) => (item.id === beacon.id ? beacon : item)),
    selectedId: state.selectedId,
    notice: state.notice,
  })),
);
""",
    "src/app/beacon/beacon.selectors.ts": """\
import { createSelector } from '@ngrx/store';
import { BeaconState } from './beacon.reducer';

export interface BeaconFeature {
  beacon: BeaconState;
}

export const selectBeaconState = (state: BeaconFeature) => state.beacon;

export const selectBeacons = createSelector(selectBeaconState, (state) => state.beacons);

export const selectBeaconName = createSelector(selectBeaconState, (state) => {
  const selected = state.beacons.find((item) => item.id === state.selectedId);
  return selected ? selected.name : '';
});
""",
    "src/app/beacon/beacon.service.ts": """\
import { Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';
import { Beacon } from './beacon.models';

@Injectable({ providedIn: 'root' })
export class BeaconService {
  constructor(private http: HttpClient) {}

  loadBeacons(): Observable<Beacon[]> {
    return this.http.get<Beacon[]>('/api/beacons');
  }

  loadBeacon(beaconId: string): Observable<Beacon> {
    return this.http.get<Beacon>('/api/beacons/' + beaconId);
  }

  saveBeacon(beacon: Beacon): Observable<Beacon> {
    return this.http.put<Beacon>('/api/beacons/' + beacon.id, beacon);
  }
}
""",
    "src/app/beacon/beacon.effects.ts": """\
import { inject } from '@angular/core';
import { Actions, createEffect, ofType } from '@ngrx/effects';
import { Router } from '@angular/router';
import { of } from 'rxjs';
import { catchError, map, switchMap, tap } from 'rxjs/operators';
import {
  loadBeaconDetail,
  loadBeaconDetailSuccess,
  loadBeacons,
  loadBeaconsFailure,
  loadBeaconsSuccess,
  navigateAfterSave,
  saveBeacon,
  saveBeaconFailure,
  saveBeaconSuccess,
} from './beacon.actions';
import { BeaconService } from './beacon.service';

export const loadBeaconsEffect = createEffect(
  (actions$ = inject(Actions), api = inject(BeaconService)) =>
    actions$.pipe(
      ofType(loadBeacons),
      switchMap(() =>
        api.loadBeacons().pipe(
          map((beacons) => loadBeaconsSuccess({ beacons: beacons })),
          catchError((error) => of(loadBeaconsFailure({ message: error.message }))),
        ),
      ),
    ),
  { functional: true },
);

export const loadBeaconDetailEffect = createEffect(
  (actions$ = inject(Actions), api = inject(BeaconService)) =>
    actions$.pipe(
      ofType(loadBeaconDetail),
      switchMap((action) =>
        api.loadBeacon(action.beaconId).pipe(
          map((beacon) => loadBeaconDetailSuccess({ beacon: beacon })),
        ),
      ),
    ),
  { functional: true },
);

export const saveBeaconEffect = createEffect(
  (actions$ = inject(Actions), api = inject(BeaconService)) =>
    actions$.pipe(
      ofType(saveBeacon),
      switchMap((action) =>
        api.saveBeacon(action.beacon).pipe(
          map((beacon) => saveBeaconSuccess({ beacon: beacon })),
          catchError((error) =>
            of(saveBeaconFailure({ status: error.status, message: error.message })),
          ),
        ),
      ),
    ),
  { functional: true },
);

export const navigateAfterSaveEffect = createEffect(
  (actions$ = inject(Actions), router = inject(Router)) =>
    actions$.pipe(
      ofType(saveBeaconSuccess),
      tap(() => router.navigateByUrl('/beacons')),
    ),
  { functional: true, dispatch: false },
);
""",
    "src/app/beacon/beacon.component.ts": """\
import { Component, OnDestroy, OnInit } from '@angular/core';
import { FormBuilder, FormGroup, Validators } from '@angular/forms';
import { Store } from '@ngrx/store';
import { Subscription } from 'rxjs';
import { Beacon } from './beacon.models';
import { loadBeaconDetail, loadBeacons, saveBeacon, selectBeacon } from './beacon.actions';
import { selectBeaconName } from './beacon.selectors';

@Component({
  selector: 'app-beacon-ledger',
  templateUrl: './beacon.component.html',
})
export class BeaconLedgerComponent implements OnInit, OnDestroy {
  form: FormGroup;
  locked = false;
  name = '';
  fallbackId = 'other-beacon';
  private subscription = new Subscription();

  constructor(
    private store: Store,
    private formBuilder: FormBuilder,
  ) {
    this.form = this.formBuilder.group({
      name: ['', Validators.required],
    });
  }

  ngOnInit(): void {
    this.store.dispatch(loadBeacons());
    this.subscription.add(
      this.store.select(selectBeaconName).subscribe((value) => {
        this.name = value;
      }),
    );
  }

  ngOnDestroy(): void {
    this.subscription.unsubscribe();
  }

  choose(beaconId: string): void {
    this.store.dispatch(selectBeacon({ beaconId: beaconId }));
    this.store.dispatch(loadBeaconDetail({ beaconId: beaconId }));
  }

  save(): void {
    if (this.locked || this.form.invalid) {
      return;
    }
    const beacon: Beacon = {
      id: 'beacon-1',
      name: String(this.form.value.name),
      status: 'ready',
    };
    this.store.dispatch(saveBeacon({ beacon: beacon }));
  }
}
""",
    "src/app/beacon/beacon.component.html": """\
<h1>Beacon ledger</h1>
<p>{{ name }}</p>
<form>
  <input formControlName="name" required />
  <button type="button" [disabled]="locked" (click)="save()">Save</button>
</form>
""",
    "src/app/beacon/beacon.module.ts": """\
import { NgModule } from '@angular/core';
import { ReactiveFormsModule } from '@angular/forms';
import { EffectsModule } from '@ngrx/effects';
import { StoreModule } from '@ngrx/store';
import { BeaconLedgerComponent } from './beacon.component';
import { beaconReducer } from './beacon.reducer';

@NgModule({
  declarations: [BeaconLedgerComponent],
  imports: [
    ReactiveFormsModule,
    StoreModule.forFeature('beacon', beaconReducer),
    EffectsModule.forFeature([]),
  ],
})
export class BeaconModule {}
""",
    "src/app/app.module.ts": """\
import { NgModule } from '@angular/core';
import { BrowserModule } from '@angular/platform-browser';
import { HttpClientModule } from '@angular/common/http';
import { BeaconModule } from './beacon/beacon.module';
import { BeaconLedgerComponent } from './beacon/beacon.component';

@NgModule({
  declarations: [],
  imports: [BrowserModule, HttpClientModule, BeaconModule],
  bootstrap: [BeaconLedgerComponent],
})
export class AppModule {}
""",
}


@dataclass(frozen=True, slots=True)
class CasePatch:
    case_id: str
    case_kind: str
    title: str
    severity: str
    message: str
    path: str
    old: str
    new: str
    anchor: str
    symbol: str
    required: tuple[tuple[str, str, str], ...]
    expected_finding: str
    expected_behavior: str
    match_terms: tuple[str, ...]
    tags: tuple[str, ...]
    reproduction: str
    when: str
    also: tuple[tuple[str, str], ...] = ()


CASES: tuple[CasePatch, ...] = (
    CasePatch(
        "ANG-001",
        "defect",
        "Load effect drops API failures",
        "high",
        "adjust beacon load pipeline",
        "src/app/beacon/beacon.effects.ts",
        """\
          map((beacons) => loadBeaconsSuccess({ beacons: beacons })),
          catchError((error) => of(loadBeaconsFailure({ message: error.message }))),
""",
        """\
          map((beacons) => loadBeaconsSuccess({ beacons: beacons })),
""",
        "map((beacons) => loadBeaconsSuccess({ beacons: beacons }))",
        "loadBeaconsEffect",
        (
            ("src/app/beacon/beacon.effects.ts", "loadBeaconsEffect", "failure path was removed"),
            ("src/app/beacon/beacon.service.ts", "loadBeacons", "HTTP call can fail"),
            ("src/app/beacon/beacon.reducer.ts", "beaconReducer", "failure state is unchanged"),
        ),
        "API failure path does not emit a failure action",
        "A failure action is emitted when the load request fails",
        ("failure action", "api failure"),
        ("angular", "ngrx", "effect", "error-handling"),
        "Compare the load effect with the baseline. The baseline emits loadBeaconsFailure from catchError.",
        "2026-01-15T00:01:00+00:00",
    ),
    CasePatch(
        "ANG-002",
        "defect",
        "Save reducer mutates the beacon list",
        "high",
        "adjust beacon save state",
        "src/app/beacon/beacon.reducer.ts",
        """\
  on(saveBeaconSuccess, (state, { beacon }) => ({
    beacons: state.beacons.map((item) => (item.id === beacon.id ? beacon : item)),
    selectedId: state.selectedId,
    notice: state.notice,
  })),
""",
        """\
  on(saveBeaconSuccess, (state, { beacon }) => {
    state.beacons.push(beacon);
    return state;
  }),
""",
        "state.beacons.push(beacon)",
        "beaconReducer",
        (
            ("src/app/beacon/beacon.reducer.ts", "saveBeaconSuccess", "state is mutated"),
            ("src/app/beacon/beacon.selectors.ts", "selectBeacons", "the selector reads the same list"),
        ),
        "The reducer mutates the state list instead of returning a new state",
        "The saved beacon replaces the previous item without mutating the old array",
        ("mutates", "push"),
        ("angular", "ngrx", "reducer"),
        "The baseline save handler returns a new object. The changed handler calls push.",
        "2026-01-15T00:02:00+00:00",
    ),
    CasePatch(
        "ANG-003",
        "defect",
        "Selector reads the notice instead of the beacon name",
        "medium",
        "adjust beacon name projection",
        "src/app/beacon/beacon.selectors.ts",
        """\
export const selectBeaconName = createSelector(selectBeaconState, (state) => {
  const selected = state.beacons.find((item) => item.id === state.selectedId);
  return selected ? selected.name : '';
});
""",
        """\
export const selectBeaconName = createSelector(selectBeaconState, (state) => {
  return state.notice;
});
""",
        "return state.notice",
        "selectBeaconName",
        (
            ("src/app/beacon/beacon.selectors.ts", "selectBeaconName", "wrong property"),
            ("src/app/beacon/beacon.reducer.ts", "selectedId", "the selected beacon is stored here"),
            ("src/app/beacon/beacon.component.ts", "selectBeaconName", "the screen displays this selector"),
        ),
        "The selector returns notice instead of the selected beacon name",
        "The displayed name comes from the beacon whose id is selectedId",
        ("wrong property", "beacon name"),
        ("angular", "ngrx", "selector"),
        "The baseline selector finds the beacon by selectedId. The change returns notice.",
        "2026-01-15T00:03:00+00:00",
    ),
    CasePatch(
        "ANG-004",
        "defect",
        "Navigation starts before the save request succeeds",
        "high",
        "adjust beacon navigation",
        "src/app/beacon/beacon.effects.ts",
        "      ofType(saveBeaconSuccess),\n      tap(() => router.navigateByUrl('/beacons')),",
        "      ofType(saveBeacon),\n      tap(() => router.navigateByUrl('/beacons')),",
        "ofType(saveBeacon),\n      tap(() => router.navigateByUrl('/beacons'))",
        "navigateAfterSaveEffect",
        (
            ("src/app/beacon/beacon.effects.ts", "navigateAfterSaveEffect", "navigation listens to the start action"),
            ("src/app/beacon/beacon.service.ts", "saveBeacon", "the write is still asynchronous"),
        ),
        "Navigation runs when the save starts, before the API succeeds",
        "Navigation runs only after saveBeaconSuccess",
        ("before the api", "navigate"),
        ("angular", "ngrx", "effect"),
        "The baseline navigation effect listens for saveBeaconSuccess.",
        "2026-01-15T00:04:00+00:00",
    ),
    CasePatch(
        "ANG-005",
        "defect",
        "Save failures collapse HTTP 403 into a generic network error",
        "medium",
        "adjust beacon save errors",
        "src/app/beacon/beacon.effects.ts",
        "of(saveBeaconFailure({ status: error.status, message: error.message }))",
        "of(saveBeaconFailure({ status: 0, message: 'network' }))",
        "status: 0, message: 'network'",
        "saveBeaconEffect",
        (
            ("src/app/beacon/beacon.effects.ts", "saveBeaconEffect", "status is discarded"),
            ("src/app/beacon/beacon.service.ts", "saveBeacon", "the HTTP client preserves the status"),
        ),
        "HTTP 403 is reported as a generic network failure",
        "The failure action keeps status 403 for a permission error",
        ("403", "permission"),
        ("angular", "ngrx", "effect"),
        "The baseline copies error.status onto saveBeaconFailure.",
        "2026-01-15T00:05:00+00:00",
    ),
    CasePatch(
        "ANG-006",
        "defect",
        "Save ignores the required form control",
        "medium",
        "adjust beacon form save",
        "src/app/beacon/beacon.component.ts",
        "if (this.locked || this.form.invalid) {",
        "if (this.locked) {",
        "if (this.locked) {",
        "save",
        (
            ("src/app/beacon/beacon.component.ts", "Validators", "the name control is required"),
            ("src/app/beacon/beacon.component.html", "required", "the template marks the control required"),
        ),
        "An invalid form can still dispatch the save action",
        "Save returns while the form is invalid",
        ("form.invalid", "required"),
        ("angular", "forms"),
        "The baseline save method returns when the form is invalid.",
        "2026-01-15T00:06:00+00:00",
    ),
    CasePatch(
        "ANG-007",
        "defect",
        "Detail load uses a different id from the selection",
        "high",
        "adjust beacon detail request",
        "src/app/beacon/beacon.component.ts",
        """\
    this.store.dispatch(selectBeacon({ beaconId: beaconId }));
    this.store.dispatch(loadBeaconDetail({ beaconId: beaconId }));
""",
        """\
    this.store.dispatch(selectBeacon({ beaconId: beaconId }));
    this.store.dispatch(loadBeaconDetail({ beaconId: this.fallbackId }));
""",
        "loadBeaconDetail({ beaconId: this.fallbackId })",
        "choose",
        (
            ("src/app/beacon/beacon.component.ts", "choose", "selection and detail ids differ"),
            ("src/app/beacon/beacon.effects.ts", "loadBeaconDetailEffect", "the effect loads action.beaconId"),
        ),
        "The detail request uses a different id from the selected beacon",
        "The detail request uses the same id that was selected",
        ("different id", "selected"),
        ("angular", "ngrx", "effect"),
        "The baseline choose method sends the same beaconId to both actions.",
        "2026-01-15T00:07:00+00:00",
    ),
    CasePatch(
        "ANG-008",
        "defect",
        "Successful save does not update the stored list",
        "high",
        "adjust beacon save result",
        "src/app/beacon/beacon.effects.ts",
        "map((beacon) => saveBeaconSuccess({ beacon: beacon })),",
        "map(() => navigateAfterSave()),",
        "map(() => navigateAfterSave())",
        "saveBeaconEffect",
        (
            ("src/app/beacon/beacon.effects.ts", "saveBeaconEffect", "success action is not emitted"),
            ("src/app/beacon/beacon.reducer.ts", "saveBeaconSuccess", "the list update never runs"),
            ("src/app/beacon/beacon.selectors.ts", "selectBeacons", "the screen keeps the previous list"),
        ),
        "The stored list stays stale because save success is not dispatched",
        "saveBeaconSuccess replaces the saved beacon in the list",
        ("stale list", "saveBeaconSuccess"),
        ("angular", "ngrx", "effect", "reducer"),
        "The baseline maps a successful response to saveBeaconSuccess.",
        "2026-01-15T00:08:00+00:00",
    ),
    CasePatch(
        "ANG-009",
        "defect",
        "The name subscription outlives the component",
        "medium",
        "adjust beacon name subscription",
        "src/app/beacon/beacon.component.ts",
        """\
  ngOnDestroy(): void {
    this.subscription.unsubscribe();
  }

""",
        "",
        "this.subscription.add(",
        "ngOnInit",
        (
            ("src/app/beacon/beacon.component.ts", "subscribe", "the manual subscription remains"),
            ("src/app/beacon/beacon.selectors.ts", "selectBeaconName", "the observable stays active"),
        ),
        "The manual subscription is not released when the component is destroyed",
        "ngOnDestroy unsubscribes the name subscription",
        ("unsubscribe", "ngOnDestroy"),
        ("angular", "rxjs"),
        "The baseline component implements ngOnDestroy and unsubscribes.",
        "2026-01-15T00:09:00+00:00",
        (("implements OnInit, OnDestroy", "implements OnInit"),),
    ),
    CasePatch(
        "ANG-010",
        "defect",
        "The disabled control is not enforced by the save method",
        "medium",
        "adjust beacon save guard",
        "src/app/beacon/beacon.component.ts",
        "if (this.locked || this.form.invalid) {",
        "if (this.form.invalid) {",
        "if (this.form.invalid) {",
        "save",
        (
            ("src/app/beacon/beacon.component.html", "disabled", "the button is disabled while locked"),
            ("src/app/beacon/beacon.component.ts", "save", "the method no longer checks locked"),
        ),
        "The template disables the button, but save still runs while locked",
        "save returns while locked, matching the disabled button",
        ("disabled", "locked"),
        ("angular", "template"),
        "The baseline save method returns when locked. The template binds disabled to locked.",
        "2026-01-15T00:10:00+00:00",
    ),
    CasePatch(
        "CLEAN-001",
        "clean",
        "Rename the page heading",
        "low",
        "rename beacon heading",
        "src/app/beacon/beacon.component.html",
        "<h1>Beacon ledger</h1>",
        "<h1>Beacon register</h1>",
        "",
        "",
        (),
        "No defect is expected",
        "The heading text changes and the save behavior stays the same",
        (),
        ("angular", "clean"),
        "Only the heading text differs from the baseline.",
        "2026-01-15T00:11:00+00:00",
    ),
    CasePatch(
        "CLEAN-002",
        "clean",
        "Rename the ready label",
        "low",
        "rename beacon ready label",
        "src/app/beacon/beacon.models.ts",
        "export const READY_LABEL = 'Ready';",
        "export const READY_LABEL = 'Standing by';",
        "",
        "",
        (),
        "No defect is expected",
        "The display label changes and the store behavior stays the same",
        (),
        ("angular", "clean"),
        "Only READY_LABEL differs from the baseline.",
        "2026-01-15T00:12:00+00:00",
    ),
)
