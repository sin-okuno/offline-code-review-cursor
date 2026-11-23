# Cursor実装プロンプト｜Phase 17 コンテキスト抽出品質改善

## 1. 役割と目的

あなたはPython 3.13、TypeScript、Angular 22、NgRx、Git解析、静的解析、テスト自動化に精通したシニアエンジニアです。

既存の `offline_review` プロジェクトを調査し、Phase 16で発見されたコンテキスト抽出品質の課題を **Phase 17** として改善してください。

最終目的は、Git差分からオフラインAIコードレビューに必要な関連コードを、正確かつ効率的に抽出することです。**AIによるバグ検出そのものは本フェーズの評価対象外**です。

## 2. 前提・制約

- 対象：Angular 22 / TypeScript / NgRx / Reactive Forms、Python 3.13 / Django REST Framework
- 既存ツール：`offline_review 0.4.0`（実際のリポジトリを確認すること）
- 実行：Windows 11 / WSL2、CPUのみ、完全オフライン
- ローカルAIへの入力：最大3ファイル、各20MB以内
- コンテキスト選択：原則2,000行以内。超過時は既存のレビュー単位分割機能を検討する
- 新規パッケージをネットワーク経由でインストールしない。外部APIに接続しない
- 既存の実装・CLI・JSONスキーマとの互換性を優先する
- 既存の未コミット変更を上書きしない
- `git reset --hard`、`git push`、自動 `git commit` は禁止する
- フィクスチャのGit履歴を勝手に変更しない

## 3. Phase 16初回ベースライン

| ケース | 検証内容 | 必須コンテキスト充足率 | 初回判定 |
|---|---|---:|---|
| ANG-001 | EffectのAPIエラー時に失敗Actionが発行されない | 66.67% | FAIL |
| ANG-002 | ReducerがStateを直接変更する | 100% | PASS |
| ANG-003 | Selectorが誤ったStateプロパティを参照する | 66.67% | FAIL |
| ANG-004 | 更新API成功前に画面遷移する | 50% | FAIL |
| ANG-005 | 403エラーを一般的な通信エラーとして扱う | 50% | FAIL |
| ANG-006 | フォームが不正でも保存処理を実行する | 50% | FAIL |
| ANG-007 | 選択IDと取得した詳細データのIDが一致しない | 100% | PASS |
| ANG-008 | 更新後に一覧が再取得されない | 66.67% | FAIL |
| ANG-009 | 手動Subscriptionの解除漏れ | 100% | PASS |
| ANG-010 | HTMLのボタン制御と保存処理の条件が不一致 | 100% | PASS |

初回合格率：4/10（40%）。これは**初回の履歴データ**であり、現在の状態ではない。ANG-001は後続の再評価で合格した実績がある。ANG-002では別ケースのGitコミットが指定される問題が発生した。必ず現行リポジトリの状態を確認すること。

## 4. Phase 17の完了条件

1. ANG-001〜010のテストケース定義とGitリビジョンが整合する。
2. 初回不合格6ケースの原因を、抽出器・評価器・テスト設定に切り分ける。
3. 有効な10ケースすべてで変更行収録率100%、必須コンテキスト評価PASSを目指す。
4. 2,000行制限とブロッキング警告0件を維持する。
5. 既存合格ケース（ANG-002、007、009、010）を回帰させない。
6. 評価器の偽陽性・偽陰性・リビジョン不一致を自動テストする。
7. 10ケースを一括実行・集計できる。
8. 完全オフラインで実行でき、既存Python解析のテストが通る。

**正解データを緩める、評価条件を無効化する、無関係なコードを大量に抽出する、といった方法で見かけ上の合格率を上げないこと。**

## 5. Step 0：リポジトリとGit状態の調査（最初に必須）

README、CLI、Git差分解析、TypeScript/Angular抽出、NgRx解析、評価器、Phase 16フィクスチャ、正解データ、既存テストを確認する。実際のディレクトリとCLIオプションを調べ、存在しないコマンドやファイルパスを推測しない。

`git status` を確認し、既存の作業ツリーを保護する。

各ケースについて以下を照合する：

- `case_id`、`base_commit`、`head_commit`
- 期待する変更ファイル・変更シンボル・不具合内容
- `git diff <base> <head>` の実際の変更
- 正解データの必須コンテキスト

特にANG-002の対象はReducerのState直接変更である。`beacon.effects.ts` の `catchError` 削除が出た場合は、別ケースのコミット混入を疑い、**INVALID_TEST_SETUP** として停止・報告する。

無効なテスト設定を抽出器のFAILとして集計しない。必要なGitコミットが存在しない場合、フィクスチャを勝手に作り直さず報告する。

成果物：`docs/phase17/test-case-validation.md`

## 6. Step 1：初回不合格6ケースの原因分析

対象：ANG-001、003、004、005、006、008。

各ケースについてGit差分、`review-context.json` の `changed_symbols` と選択コード、`review-context.md` の実際の抜粋、評価器の `required_items`、`missing_required`、`revision_match`、`case_context_sufficient` を比較する。

原因分類：

- `EXTRACTION_MISSING`：必要なコードが抽出されない
- `SYMBOL_DETECTION_FAILURE`：変更シンボル認識失敗
- `DEPENDENCY_MISSING`：依存関係の追跡不足
- `EVALUATOR_FALSE_NEGATIVE`：コードが揃っているのに不合格
- `EVALUATOR_FALSE_POSITIVE`：コードが欠落しているのに合格
- `REVISION_MISMATCH`：比較コミット不一致
- `TEST_DATA_ERROR`：正解データ・ケース定義不正
- `OTHER`：その他

複数原因を許容し、各判断にファイルパス・行番号・抽出結果などの証拠を添える。`missing_required` の存在だけで抽出不足と断定しない。

成果物：`docs/phase17/root-cause-analysis.md`

## 7. Step 2：TypeScript変更シンボル・範囲抽出の改善（P0）

対象：ANG-001、003、004、005、008。

既存解析器を拡張し、`export const`、関数、クラス、メソッド、Arrow Function、複数行宣言、`createEffect`、`createSelector`、`createReducer`、`createAction`、ネストした呼び出しを認識する。

変更行がコールバック内部にある場合も、外側の宣言シンボル名と宣言全体の範囲を認識する。文字列やコメント内の括弧を誤認しない。

削除のみの変更ではbaseの削除前シンボルとheadの対応シンボルを両方取得する。headにシンボルが存在するのに `Lines: 0-0` になる問題を修正する。シンボル自体が消えた場合のみhead側の不在を明示する。

選択結果にはシンボル名・開始/終了行・revision・選択理由を保持する。構文解析が不確実なら推測で「確定」とせず警告・保守的な抽出を行う。

## 8. Step 3：NgRx依存関係抽出の改善（P1）

対象：ANG-001、004、005、008。

追跡対象：

```text
Component → dispatch(Action) → ofType(Action) → Effect → Service → HTTP API
Effect → success/failure Action → Reducer → State → Selector → Component
```

- Actionの定義と使用箇所
- Effectのトリガー、Service呼び出し、成功/失敗Action
- Actionを処理するReducer
- SelectorとComponentの参照関係

ANG-004：保存Effect、API、成功Action、画面遷移Effectの順序判断に必要なコードを取得する。

ANG-005：保存Effect全体、HTTP呼び出し、`catchError`、403分岐、失敗Actionを取得する。

ANG-008：保存成功Actionと一覧再取得Action/Effectの関係を取得する。存在しない処理は補完せず、その不在を確認できる範囲を提示する。

## 9. Step 4：Reactive Formsの依存関係抽出（P1）

対象：ANG-006。

`FormBuilder.group` → `Validators` → `FormGroup` → `form.invalid` / `form.valid` → `save()` → `dispatch(saveAction)` の関連コードを抽出する。HTMLの `[disabled]` 条件と保存メソッドの条件も関連付ける。

`Validators` という文字列が存在するだけでは十分とみなさず、対象フォームと保存処理の関係を確認する。

## 10. Step 5：評価器の誤判定防止（P0）

以下を検証する。

- 必須シンボルの存在だけでなく、必要な処理範囲が収録されているか
- base/headリビジョンが正しいか
- 必須コードが同一レビュー単位、または結合可能な単位にあるか
- コードが揃っているのにFAILになる偽陰性
- シンボル名だけあるのにPASSになる偽陽性
- 別ケースのGit差分を評価するリビジョン不一致

リビジョン不一致は `INVALID_TEST_SETUP` として識別し、抽出器の性能集計から除外する。既存の `changed_line_coverage`、`required_context_coverage`、`case_context_sufficient`、`combined_units_sufficient`、`revision_match`、`missing_required`、`blocking_warnings` を維持する。スキーマ変更が必要なら互換性を検討し文書化する。

## 11. Step 6：ANG-001〜010の一括実行と集計

既存CLIを調査した上で、以下を自動化する。

1. ケース定義の読込
2. Gitコミット・期待差分の検証
3. `offline_review analyze` 相当の既存処理
4. `evaluate-context` 相当の既存処理
5. ケース別結果の保存
6. 全体結果の集計

想定出力（既存の出力規約があれば優先）：

```text
reports/phase17/
  summary.md
  summary.json
  ANG-001/
    review-context.md
    review-context.json
    context-evaluation.md
    context-evaluation.json
  ...
  ANG-010/
    review-context.md
    review-context.json
    context-evaluation.md
    context-evaluation.json
```

集計項目：case_id、変更ファイル、base/headコミット、変更行収録率、必須コンテキスト充足率、revision_match、PASS/FAIL/INVALID_TEST_SETUP、不足項目、初回との比較。

**ケースIDとGitリビジョンの整合性を検証できない場合、評価結果をPASSにしない。**

## 12. Step 7：回帰テスト・大規模変更への備え

- 初回合格4ケース（ANG-002、007、009、010）の回帰防止
- 初回不合格6ケースの原因解消
- 変更行収録率100%、行数制限、警告0件
- 既存Python解析の回帰防止
- 完全オフライン実行
- 10万行規模のリポジトリ、2,000行程度の差分、多数の変更ファイル、複数NgRx経路への拡張性を検討
- 行数制限超過時は既存のレビュー単位分割機能を優先利用し、コンテキスト欠落を報告する

## 13. 実行順序と停止条件

1. リポジトリ・Git状態調査
2. テストケースとコミットの整合性確認
3. 初回不合格6ケースの根本原因分析
4. 修正対象と影響範囲の特定
5. 既存テスト実行
6. 最小限の実装修正
7. 単体・結合テスト追加
8. 10ケース一括実行
9. 回帰テスト
10. レポート作成

テストデータが不正で正しいコミットを確定できない場合は、無理に修正・集計せず停止し、必要な情報を報告する。既存の未コミット変更との衝突がある場合も停止する。

## 14. 最終報告

次をMarkdownで報告する。

### A. 実施概要
実装した改善、変更ファイル、追加テスト。

### B. 原因分析
| ケース | 原因分類 | 根拠 | 修正内容 |
|---|---|---|---|

### C. 評価結果
| ケース | 初回結果 | 今回の結果 | リビジョン一致 | 判定 |
|---|---|---|---|---|

### D. 品質指標
変更行収録率、必須コンテキスト合格率、行数超過件数、ブロッキング警告件数、INVALID_TEST_SETUP件数。

### E. テスト結果
単体・結合・一括・回帰テストの実行コマンドと結果。

### F. 残課題
未完了項目、既知の制約、次の改善候補。

### G. Git状態
開始時・終了時の状態、変更ファイル一覧、コミット未実行の確認。

## 15. 重要な最終指示

Phase 17の目標は、テストを形式的に10/10合格させることではなく、実際のAngular/NgRxプロジェクトでもAIレビューに必要なコンテキストを選べるようにすることです。

まず **Step 0〜1だけ実施し、コードを変更せず原因分析を報告**してください。その結果を確認してからStep 2以降の実装に進んでください。Gitコミットは実行しないでください。
