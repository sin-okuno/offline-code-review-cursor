# Phase 12：Angular・NgRx影響範囲解析とレビューコンテキスト最適化

## 1. 役割
あなたはPython 3.13、Angular 22、TypeScript、NgRx、Git解析、静的解析ツール開発に精通したシニアソフトウェアエンジニアです。

既存の `offline_review` パッケージを拡張し、Git差分からレビューに必要なAngular関連コードを自動抽出する機能を改善してください。既存機能を破壊せず、段階的に実装してください。

## 2. 開発上の制約
- Cursorで扱うのは公開可能なサンプルコードのみ。会社のソースコード、Git差分、認証情報をCursorに送信しない。
- 実際のレビュー対象コードの解析はローカル環境で実行する。
- 解析対象のソースコードを実行しない。Gitリポジトリを変更しない。
- 解析対象の依存パッケージをインストールしない。ネットワーク通信を行わない。
- Gitのbase/headを混同しない。
- 既存CLI、JSON出力、Python解析機能との後方互換性を維持する。
- 解析できない依存関係を推測で `confirmed` にしない。
- TypeScriptの完全な意味解析を実装したと主張しない。
- 既存の `.cursor/rules/` と `docs/` を確認してから実装する。
- 初期実装ではPython標準ライブラリと既存の依存関係を優先する。新しい外部ライブラリが必要な場合は実装前に理由と代替案を提示する。

## 3. 現状の問題
前回の解析結果：
```json
{
  "analysis_method": "conservative_lexical",
  "coverage": {
    "template_bindings": "unsupported",
    "truncated": true,
    "typescript_semantic": "partial"
  }
}
```
課題：
1. HTMLとComponentメソッドの関係を追跡できない。
2. NgRx Action、Reducer、Effect、Selectorの関係を十分に抽出できない。
3. `max_files` により関連コードが除外される。
4. base/headの両方のコードが抽出予算を消費する。
5. import解決失敗の原因が十分に分類されていない。
6. 変更された処理と関係の薄いファイルが優先される可能性がある。

## 4. 実装対象

### FR-01：Angularテンプレート依存関係解析
`@Component` の `templateUrl` とHTMLファイルを関連付ける。対象：
- `templateUrl` と静的なインライン `template`
- イベントバインディング `(click)` など
- プロパティバインディング `[value]` など
- `@if`、`@for`、`{{ expression }}`、`async` パイプ
- 子Componentのセレクター

例：
```html
<button (click)="toggleRisk(level)">{{ level }}</button>
```
```typescript
toggleRisk(level: RiskLevel): void {
  this.store.dispatch(SetFilterAction({ riskLevels: [level] }));
}
```
期待関係： `HTML → toggleRisk() → SetFilterAction`。

注意：単純な文字列検索だけで確定的な依存関係と判定しない。コメント、文字列リテラル、複雑な式による誤検出を抑制する。静的に解決できない式は `heuristic` または `unresolved` に分類する。HTMLとComponentの対応が確認できた場合に限り対応関係を `confirmed` とする。テンプレート式の完全な意味解析は対象外。

### FR-02：NgRx依存関係解析
次の経路を追跡する：
```text
Component → dispatch → Action
                    ├→ Reducer → Selector → Component
                    └→ Effect → Service → API
```
対象：`store.dispatch(...)`、`store.select(...)`、`store.selectSignal(...)`、`createAction(...)`、`createActionGroup(...)`、`on(...)`、`createReducer(...)`、`createFeature(...)`、`createSelector(...)`、`createEffect(...)`、`ofType(...)`、Effect内のServiceメソッド呼び出し。

関数・クラス・Actionの名前を可能な範囲で解決し、importだけでなく実際の呼び出し関係を証拠付きで記録する。

### FR-03：変更シンボルの抽出
Git差分から変更されたクラス、メソッド、関数、Action、Selectorなどを可能な範囲で特定する。特定できない場合は変更行と周辺コードを使用。base/head両方のリビジョンで処理する。

### FR-04：双方向の影響範囲探索
- Forward：変更コードが参照するコード
- Reverse：変更コードを参照するコード

例：`ProductTreeFilterComponent.toggleRisk() → SetFilterAction → product-tree.reducer.ts → selectRiskFilter → ProductTreeViewComponent`。

循環参照で無限ループしない。探索深度・件数に上限を設ける。baseとheadのグラフは分離し、リビジョンをまたぐエッジを作成しない。

### FR-05：関連コードの優先順位付け
初期スコア例：

| 対象 | 優先度 |
|---|---:|
| Git変更箇所 | 100 |
| 変更メソッド・関数 | 95 |
| 直接呼び出されるメソッド | 90 |
| 関連Action / Reducer / Effect | 85 |
| 関連Selector | 80 |
| 関連Service | 80 |
| 関連HTML | 75 |
| 関連テスト | 70 |
| 間接依存 | 50 |
| 共通Module | 20 |

既存の選択ロジックと整合するよう調整し、同点時は安定した順序で選択する。共通Moduleなど参照数の多いファイルが重要な変更箇所を押し出さないこと。

### FR-06：抽出予算の最適化
レビュー用コードの目標は約2,000行。
1. Git差分を最優先。
2. 変更シンボルの周辺コードを抽出。
3. 直接依存する処理を優先。
4. 重要なNgRx処理経路を優先。
5. 関連テストを適切に含める。
6. 関係の薄いファイルを除外。
7. 除外理由を記録。

変更箇所のbase/head比較を維持しつつ、未変更コードの重複収録を抑制する。予算超過時は重要な変更コードを黙って削除せず、未収録の変更箇所と不足行数を明示する。

### FR-07：import解決失敗の診断
`unresolved` の理由を分類する：
- `target_not_found`
- `unsupported_import`
- `ambiguous_resolution`
- `parse_failed`
- `budget_exceeded`

例：
```json
{
  "kind": "ts_import",
  "confidence": "unresolved",
  "resolution_reason": "target_not_found",
  "from_path": "src/app/product-tree/product-tree.module.ts",
  "import_path": "./risk-detail/risk-detail.component"
}
```
解決不能なimportがあっても解析全体を停止しない。

### FR-08：レポート改善
既存の `angular_context` を後方互換性を維持して拡張する。追加情報：変更シンボル、依存関係の種類、選択スコア・理由、影響範囲、未解決依存、解析対象外、抽出予算の使用状況、未収録変更箇所。

Markdownレポートの章：
1. Git変更概要
2. 変更シンボル
3. Angular影響範囲
4. NgRx処理経路
5. 優先レビュー対象
6. 関連コード
7. 未解決の依存関係
8. 解析上の制約

## 5. テスト要件
**Angular**：HTMLイベントとComponentメソッド、`templateUrl`、インラインテンプレート、`@if`/`@for`、HTMLコメントの誤検出防止、存在しないComponentメソッド。

**NgRx**：Component→Action、Action→Reducer、Action→Effect、Effect→Service、Selector→Component、同名Action、循環参照。

**Git**：base/headで異なる依存関係、追加・削除・リネームファイル、未変更の関連ファイル。

**抽出予算**：2,000行以内、予算超過、優先順位の安定性、重要変更箇所の欠落検知、重複抑制。

**セキュリティ**：パストラバーサル防止、Git引数の安全な処理、対象コード非実行、ネットワーク不要、リポジトリ読み取り専用。

## 6. 実装手順
1. **既存実装の調査**：CLI、Git差分、TypeScriptインデックス、Angular依存グラフ、コード選択、JSON/Markdown、既存テストを確認。再実装を避ける。
2. **設計書更新**：`docs/06-angular-impact-analysis.md` と `docs/07-context-selection.md` を作成または更新。データモデル、解析方式、優先順位、制約、テスト方針を記載。
3. **Angularテンプレート解析**：HTMLとComponentの依存関係を実装。
4. **NgRx解析**：Action、Reducer、Effect、Selector、Serviceの関係を実装。
5. **影響範囲探索**：変更シンボルを起点とした双方向探索。
6. **コード選択最適化**：約2,000行の予算で選択。
7. **レポート改善**：JSONとMarkdownを拡張。
8. **テスト**：追加テストと既存回帰テストを実行。

## 7. 完了条件
- 既存CLIが引き続き動作する。
- AngularテンプレートとComponentを関連付けられる。
- 主要なNgRx処理経路を追跡できる。
- 変更箇所から関連コードを優先抽出できる。
- 約2,000行の予算を管理できる。
- 未収録の変更箇所を検出できる。
- 未解決依存関係を分類できる。
- base/headが混在しない。
- JSON/Markdownに選択理由と解析制約が表示される。
- 既存テストと追加テストが成功する。

完全な意味解析ができない場合は、その制約をレポートに明示する。

## 8. 最終報告
実装後、変更ファイル一覧、実装機能、未対応機能、テスト件数と結果、既存機能への影響、実行コマンド、解析結果サンプル、次の改善候補を報告する。

**まず既存コードの確認から開始してください。**
