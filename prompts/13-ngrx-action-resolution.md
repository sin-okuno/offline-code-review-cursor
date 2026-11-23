# Cursor追加プロンプト 12 — NgRx Action参照解決と回帰テスト

## 役割・目的
あなたはPython製オフラインコードレビューCLI `offline_review` の保守担当です。既存のAngular 22 / TypeScript / NgRx向け保守的字句解析を改善し、`dispatch(new ActionClass(...))` を正しくActionクラスとして解決してください。**既存機能の破壊・大規模な書き換えは禁止**です。実装前にリポジトリを調査し、変更計画を簡潔に提示してから作業してください。

## 背景：実際の解析結果
比較対象はサンプルリポジトリ `tree-sample` の以下のGitコミットです。
- base: `7e85d3ecf4e2e584a04a2a3fc930ab75a3f9a205`
- head: `c034d66c1a28543160ae6e0f432f0c47c33ad8a1`
- comparison: `direct`（two-dot）

`review-context.json` の `angular_context.edges` に以下の誤検出があります。

```json
{
  "kind": "ngrx_action_reference",
  "evidence": "dispatch(new",
  "to_symbol": "new",
  "to_path": null,
  "confidence": "unresolved",
  "resolution_reason": "target_not_found"
}
```

発生位置：
- `src/app/product-tree/components/product-tree-filter/product-tree-filter.component.ts`、head、70行目
- `src/app/product-tree/components/product-tree-view/product-tree-view.component.ts`、head、92行目
- `src/app/product-tree/product-tree.component.ts`、head、29行目

一方、base側の `dispatch(ProductTreeActions...)` と、Selector参照は一定程度解決されています。これらの既存動作を維持してください。

直近レポートの `angular_context.coverage` は `ngrx: partial`、`template_bindings: partial`、`typescript_semantic: partial`、`lines_selected: 1994`、`line_budget: 2000`、`lines_omitted: 197`、`truncated: true` です。今回の主目的はAction解決です。行数上限を緩めて問題を隠さないでください。

## スコープと優先順位

### P0: Action式の正しい抽出
1. 次の式を区別して認識すること（変数名や空白・改行に依存しない）。
   - `this.store.dispatch(new LoadTreeAction())`
   - `store.dispatch(new SetFilterAction({ name: 'x' }))`
   - `this.store.dispatch(ProductTreeActions.loadTree())`
   - `this.store.dispatch(loadTree())`
   - `this.store.dispatch(actionVariable)`（静的解決できない場合は推測しない）
2. `new`、`this`、`store`、`dispatch` をAction名として登録しない。
3. コメントや文字列中の `dispatch(...)` を誤認しない。複数行、ネストした引数、型引数などの境界を可能な範囲で扱う。
4. 現在の `conservative_lexical` 方針を維持。曖昧な式を無理に `confirmed` にしない。必要なら `unresolved` と理由を出力。
5. Python標準ライブラリで実装可能な範囲を優先し、追加のオンライン依存や実行時Node必須化を行わない。既存パーサの利用可否を先に調査する。

### P0: importと宣言の解決
1. `new LoadTreeAction()` を、同じGitリビジョン内のimport／export／宣言から `src/app/product-tree/state/product-tree.actions.ts` に解決する。
2. 名前付きimport、alias import、namespace import、相対パス、拡張子省略を既存機能に沿って扱う。
3. base/headの参照を混同しない。Gitコミットの内容だけを読み、作業ツリーの未コミット変更を混入しない。
4. 複数候補や未対応構文は `unresolved` / `partial` として根拠を残す。単なる名前一致だけで `confirmed` にしない。
5. 既存の `edges` のフィールド（`kind`, `from_path`, `to_path`, `to_symbol`, `line`, `source_revision`, `confidence`, `resolution_reason`, `evidence`）を保持する。

### P1: NgRx経路の補強
1. 解決済みActionから、既存の対応範囲内でReducer／Effectの参照を追跡できるようにする。
2. クラスベースActionと `createAction` / `createActionGroup` ベースのActionを区別し、静的に確認できる参照だけを結ぶ。
3. `ofType(...)`、`on(...)` の参照は宣言やimportを確認して紐づける。単なる文字列一致は `confirmed` にしない。
4. Component → Action → Effect → Service、およびAction → Reducer → Selector → Componentを完全に解決できない場合は、確認済み部分と未対応部分を明示する。
5. 対象コードをレビュー用コンテキストへ追加する場合も、既存の優先順位と `max_total_lines=2000` を守る。

### P1: レポートとカバレッジ
1. `angular_context.edges` と `angular_context.unresolved` に同一参照の重複・矛盾がないようにする。
2. Action参照が解決できた場合、`evidence` には可能な限り `dispatch(new LoadTreeAction` のような識別可能な断片を残す。機密情報の過剰出力は避ける。
3. `angular_context.coverage.ngrx` を `complete` と断定しない。実際の対応範囲を反映する。
4. 既存のJSON/Markdownスキーマ、CLIオプション、終了コードとの後方互換性を維持する。スキーマ追加が必要なら加算的変更とする。
5. `CHANGED_CONTEXT_OMITTED` の警告、`lines_omitted`、`truncated` を隠さない。

## テスト要件（必須）
- `dispatch(new LoadTreeAction())` → `to_symbol == "LoadTreeAction"`、対象ファイル解決成功。
- `dispatch(new SetFilterAction({ ... }))` → 正しいAction名。
- 改行・ネストした括弧・コメント・文字列・同名の別モジュール・alias import。
- `dispatch(ProductTreeActions.loadTree())` の既存動作維持。
- `dispatch(loadTree())` はimport先が確定した場合のみ `confirmed`。
- `dispatch(actionVariable)` は根拠がない場合 `unresolved`。
- base/headでAction実装が異なる場合のリビジョン分離。
- `new` が `to_symbol` に出ないことを回帰テストで固定。
- Python解析、Git差分、HTML解析、NgModule、Selector、CLI、既存出力に対する回帰テスト。
- ネットワーク遮断状態でテストが成功すること。テスト用サンプルは公開可能な架空コードのみ。

## 検証手順
リポジトリ構成を調査し、既存のテストコマンドと実行方法を確認する。既存例が利用可能なら以下を実行する。

```bash
cd /mnt/c/work/00_src/ai-driven/offline-code-review-cursor
export PYTHONPATH="$PWD/src"
python -m unittest discover -s tests -v
python -m offline_review analyze \
  --repo /mnt/c/work/00_src/ai-driven/tree-sample \
  --base 7e85d3ecf4e2e584a04a2a3fc930ab75a3f9a205 \
  --head HEAD --comparison direct --output ./output-angular
```

注意：`HEAD` が上記headコミットを指しているか先に確認し、異なる場合は `--head c034d66c1a28543160ae6e0f432f0c47c33ad8a1` を使う。出力先を既存成果物から分離したい場合は別ディレクトリにする。

出力JSONの `angular_context.edges` / `unresolved` を調査し、修正前後の以下を表で報告する。
- `ngrx_action_reference` の `confirmed` / `unresolved` 件数
- `to_symbol == "new"` の件数（期待値0）
- 対象3箇所の解決先と根拠
- `lines_selected`、`lines_omitted`、`truncated`
- テスト実行件数・失敗件数

`RiskDetailComponent` のimport未解決、`tsconfig.json` のJSONC読込警告、トップレベルcoverageの不整合など、別の問題を発見した場合は記録するが、今回の修正を無制限に拡張しない。

## 実装上の禁止事項
- 既存の `.cursor/rules`、`docs`、`prompts` を削除・上書きしない。必要なら新しい文書を追加する。
- 既存のPython AST解析、Git比較方式、Angular出力の基本構造を破壊しない。
- 実行対象リポジトリのコードを実行しない。`eval`、任意スクリプト実行、外部API送信、ネットワークアクセスを導入しない。
- 社内ソース、認証情報、機密ファイルをCursorへ投入しない。
- 根拠のない依存関係を `confirmed` として出力しない。

## 作業完了時の回答形式
1. 原因分析（誤って `new` を抽出した理由）
2. 変更したファイル一覧と役割
3. 実装内容（対応構文／未対応構文）
4. テスト結果
5. サンプル比較の修正前後の定量評価
6. 残存する未解決参照と次フェーズの提案

まず既存コードを読み、原因と最小変更計画を示してください。その後、実装とテストまで実施してください。
