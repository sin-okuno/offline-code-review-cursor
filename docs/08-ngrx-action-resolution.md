# NgRx action callee resolution

`dispatch(new LoadTreeAction())` used to record the callee as `new`, because the extractor kept the first identifier after `(`. The callee is now the identifier after one leading `new`. `new`, `this`, `store`, `dispatch`, `select`, and `selectSignal` are never stored as `to_symbol`.

`ProductTreeActions.loadTree()` still uses the namespace identifier `ProductTreeActions`. `loadTree()` is confirmed only when that local name is an imported binding that resolves to one file. A bare variable such as `actionVariable` stays `unresolved` / `target_not_found`. Two imports of the same local name stay `ambiguous_resolution`.

Comments and string literals are already masked before tokenization, so a `dispatch` written there is not an edge. The TypeScript extractor version is `"3"` so cached scans that stored `new` are not reused.

This does not add a TypeScript type checker. `createActionGroup` and `createFeature` still have no dedicated edge. `coverage.ngrx` stays `partial`. The total line budget stays 2000.
