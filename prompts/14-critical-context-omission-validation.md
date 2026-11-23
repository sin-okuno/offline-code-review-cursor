# Phase 13：重要な関連コードの抽出漏れ検証

あなたは Python 3.13、Angular 22（NgModule）、NgRx、Git差分解析、静的解析、ソフトウェアテストに精通したエンジニアです。

既存の `offline_review` プロジェクトに対し、**重要な関連コードの抽出漏れを検証する仕組み**を段階的に追加してください。実装前に現状調査と変更計画を提示し、承認を得てからコードを変更してください。

## 0. 守るべき制約

- 既存機能、Python AST解析、Angular解析、Git差分解析、CLI、JSON/Markdown出力を破壊しない。既存の `.cursor/rules`、ドキュメント、プロンプトは削除・上書きしない。
- Python 3.13 / WSL2 / CPUのみで動作し、実行時のネットワークアクセスや外部AI APIを必要としない。
- 解析対象は `base` と `head` のGitコミット内容とし、未コミットの作業ツリーを混入させない。
- Cursorには公開・合成サンプルコードのみを渡す。会社の非公開コードは入力しない。
- 不確かな依存関係を `confirmed` として扱わない。証拠のない「抽出漏れなし」判定は禁止。
- 新規ライブラリが必要なら、導入前に名称、目的、ライセンス、オフライン配布方法を報告する。原則は標準ライブラリを優先する。

## 1. まず既存実装と出力を監査する（この段階では変更しない）

既存の `analyze` コマンド、依存グラフ生成、候補ランキング、選択予算、`angular_context.selected / omitted / unresolved / warnings`、`coverage` の生成処理とテストを調査してください。

検証対象の公開サンプルは、存在する場合、以下を使用します。

- リポジトリ：`/mnt/c/work/00_src/ai-driven/tree-sample`
- base：`7e85d3ecf4e2e584a04a2a3fc930ab75a3f9a205`
- head：`c034d66c1a28543160ae6e0f432f0c47c33ad8a1`
- 比較方式：`direct`
- 対象API：`src/app/product-tree/api/product-api.service.ts` の `ProductApiService.getProductTree`
- 契約変更：`Observable<readonly ProductTreeNode[]>` → `Observable<ProductTreeNode>`

前回出力では次の事象が観測されています。**現行コードと再生成結果で再現するか確認**してください。

1. 2,000行予算に対して約1,994行が選択され、省略が発生。
2. `src/app/app.module.ts` の変更行が `max_total_lines` により省略。
3. `src/app/product-tree/api/mock-product-api.interceptor.ts` の変更行が `max_total_lines` により省略。
4. `src/app/product-tree/product-tree.module.ts` のbase側が省略。
5. `RiskDetailComponent` のimportが `target_not_found`（`product-tree.module.ts` と `product-tree.component.spec.ts`）。
6. `angular_context.analysis_method` は `conservative_lexical`。静的解析の限界を踏まえて評価すること。

対象リポジトリや指定コミットが存在しない場合は、推測で検証せず、不足している入力を報告してください。既存出力を使う場合もコミットと設定が一致することを検証してください。

## 2. 正解データ（ゴールドセット）を定義する

公開サンプルのAPI契約変更について、人が確認可能な**期待関連箇所**をテストデータとして定義してください。最低限、次の候補を調査し、実際に関連があるものだけをゴールドセットに採用します。

- API Serviceの変更メソッドと型定義
- API Serviceを呼び出すEffect / Service
- Effectがdispatchする成功・失敗Actionとpayload型
- Actionを処理するReducerおよびState型
- Selectorと、その値を参照するComponent
- Componentに対応するHTMLテンプレート
- 関連するテストとMock API / Interceptor

各期待項目に `id`、`path`、`symbol`（特定できる場合）、`revision`、`criticality`（critical/high/normal）、`why_required`、根拠となる参照元と行番号を記録してください。ファイルが実際に存在しない場合は、期待項目に追加せず別途「未解決候補」に記録します。

**重要：**単なるimport、未使用シンボル、偶然の同名一致を、影響ありと決めつけないでください。`base` と `head` の双方を必要とする契約比較では、片方だけの抽出を成功扱いしないでください。

## 3. 抽出漏れ検証コマンドを追加する

既存CLIを維持したうえで、可能なら次のようなサブコマンドを追加してください（既存CLI設計と整合する名称に調整可）。

```bash
python -m offline_review validate-context \
  --context ./output-angular/review-context.json \
  --golden ./tests/fixtures/angular-context-golden.json \
  --output ./output-angular/context-validation.json
```

要件：

- 既存JSONの`selected`、`omitted`、`unresolved`、`warnings`を読み、ゴールドセットと照合する。
- `path + revision + symbol/line range`を使い、単に同一ファイルが含まれるだけでは成功としない。必要なコード範囲の存在を判定する。
- `selected`にあるが必要行が欠けている場合は `partial` とする。
- `omitted`にある場合は `omitted_by_budget` 等の理由を記録する。
- 依存解決不能は `unresolved`、ゴールドセット自体の根拠不足は `unknown` と区別する。
- ゴールドセットのコミット、比較方式、対象リポジトリが出力と一致しない場合は検証を失敗させる。
- 元のコンテキストJSONを破壊・上書きしない。
- JSONと人間が読めるMarkdownの検証レポートを生成する。

## 4. 定量指標と判定基準

最低限、以下を算出してください。

1. `critical_recall`：critical期待項目のうち、必要範囲が完全に含まれる割合。
2. `high_recall`：high期待項目の完全包含割合。
3. `weighted_recall`：重要度別の重み付き再現率（重みを明示）。
4. `changed_line_coverage`：base/head別の変更行カバレッジ。
5. `budget_omission_count`：予算制約で省略された期待項目数。
6. `unresolved_critical_count`：criticalに関係する未解決依存数。
7. `context_efficiency`：選択行のうちゴールドセットの必要範囲に該当する行の割合（計測可能な場合のみ）。

ゼロ件の分母は100%とせず `not_applicable` と表示してください。`unknown` やゴールドセット未整備を成功として集計しないでください。重複する行範囲は一度だけ数えてください。

暫定合格ゲート（調整可能な設定値）：

- `critical_recall = 100%`
- `unresolved_critical_count = 0`
- 変更行の欠落がある場合は警告または不合格（設定可能）。
- highの抽出率と全体効率は数値を報告し、初回は基準値を勝手に確定しない。

合否だけでなく、**どの期待項目の何行が欠落し、なぜ欠落したか**を必ず報告してください。

## 5. 予算変更による比較実験

既存の引数名・上限仕様を確認したうえで、次の条件を比較してください。

- 現状の2,000行上限
- 3,000行上限
- 5,000行上限

上限を変更するCLIオプションがない場合は、既存の設定機構を調査して再現可能な方法を提示してください。設定方法が存在しないのにオプションを捏造しないでください。

各条件で `critical_recall`、`high_recall`、`changed_line_coverage`、`context_efficiency`、選択行数、省略理由、生成時間を比較してください。予算を増やすだけでなく、**重要コードを先に選択するランキング改善で解決できるか**を分析してください。

## 6. 原因分析と改善方針

検出した抽出漏れを以下に分類してください。

- `graph_missing_edge`：依存グラフに必要な辺がない
- `resolution_failure`：import/シンボル解決失敗
- `ranking_issue`：候補は存在するが優先順位が低い
- `budget_limit`：行数・ファイル数上限
- `range_too_narrow`：ファイルは選択されたが必要範囲がない
- `revision_mismatch`：base/head取り違え
- `unsupported_syntax`：未対応構文
- `golden_data_issue`：期待データ側の問題

**最初の実装は検証器とレポートを優先**してください。抽出器本体の改善が必要な場合は、検証結果を根拠に別の変更計画を提示し、既存の選択ロジックを無条件に書き換えないでください。

## 7. 必須テスト

- API戻り値の配列→単一オブジェクト変更で、実際の消費側を追跡できること。
- `selected`に同一ファイルがあっても必要な行範囲が欠ければ不合格となること。
- base/headの片側欠落を検出すること。
- 予算省略が `omitted_by_budget` として識別できること。
- `RiskDetailComponent` の未解決importを、実在確認なしに解決済みとしないこと。
- ゴールドセットと解析結果のコミット不一致を拒否すること。
- 重複行、空の期待データ、壊れたJSON、未知のステータスを安全に扱うこと。
- 既存テストがすべて通ること。

## 8. 成果物

プロジェクトの既存構成に合わせ、次を作成・更新してください。

1. 検証用CLIとロジック（既存機能を壊さず追加）
2. 公開サンプルに基づくゴールドセットJSON
3. 単体テストと回帰テスト
4. `docs/` 配下の検証方法・指標定義・制約説明
5. 比較実験のJSON/Markdownレポート
6. 抽出漏れ一覧（ファイル、revision、行、重要度、理由、推奨対応）

## 9. 作業の進め方

**Step A：調査・計画（コード変更なし）**

- 既存構成、実際のJSONスキーマ、関連実装、テストを調査する。
- API契約変更の影響経路をGitコミットから確認する。
- ゴールドセット候補と証拠を提示する。
- 変更予定ファイル、互換性、テスト計画、懸念点を報告する。
- **ここで停止し、承認を待つ。**

**Step B：検証器・ゴールドセット・テスト実装**

- 承認後に実装し、テストを実行する。

**Step C：実測と評価**

- 2,000/3,000/5,000行で比較し、抽出漏れと原因を定量化する。
- 抽出器の改善が必要なら、次Phase向けの変更案を提示する。

## 10. 最終報告の形式

- 実行環境・対象コミット・比較方式
- 変更したファイル一覧
- テスト結果（成功/失敗/未実施）
- 指標比較表（2,000/3,000/5,000行）
- critical/high抽出漏れの詳細
- `RiskDetailComponent` 未解決の調査結果
- 推奨する改善の優先順位
- 再現コマンド
- 未対応事項・残余リスク

まず **Step Aだけ** 実施してください。推測ではなく、既存コードとGit履歴に基づいて報告してください。
