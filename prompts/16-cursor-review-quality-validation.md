# Phase 16 — AIコードレビュー品質検証基盤の構築（Cursor実装プロンプト）

## 0. Cursorへの指示

あなたは Python 3.13、Angular 22、TypeScript、NgRx、Django REST Framework、静的解析、ソフトウェアテスト、セキュリティに精通したシニアエンジニアです。

既存プロジェクト `offline_review` に対して、**Phase 16：レビュー品質検証基盤**を追加実装してください。今回の目的は、新しいコードレビューAIを実装することではありません。既存の「Git差分から関連コードを抽出するツール」と、別途利用する「ローカルAIレビュー」の品質を、正解付きの合成コードで再現可能に評価することです。

**最重要制約**：既存機能を壊さないこと。まずリポジトリの現状を監査し、実在する構成・CLI・JSONスキーマ・テスト・既存ドキュメントを確認してください。ここに示すファイル名やCLIは提案です。現状と衝突する場合は既存規約に合わせ、変更理由を記録してください。

## 1. 背景・現状

- 対象の実プロジェクトは Angular 22 + NgRx + Django REST/Python 3.13 で、全体約10万行規模。
- `offline_review` は Git diff と関連コードを抽出し、Markdown/JSONで出力する。
- ローカルAIへ渡すレビューコンテキストは約2,000行を目安とする。長い差分は複数レビュー単位へ分割する。
- 直近の評価では変更行1,978行を収録し、変更行の欠落は0行。ただし周辺コンテキストの省略やAngular解析の部分的な未解決が残る。
- ローカルAIは独立したチャット環境で動作し、入力は最大3ファイル。ネットワークアクセスを前提としない。
- Cursorに社内・顧客のソースコード、機密情報、実データを投入してはならない。Cursor上では架空の合成コードのみを扱う。

## 2. Phase 16の到達目標

1. **意図的な不具合10件**を含むAngular 22/NgRxの合成テストリポジトリを作る。
2. 各不具合に一意のID、正解箇所、期待する指摘内容、検出に必要な関連ファイルを定義する。
3. 既存抽出ツールで作成したレビューコンテキストに、正解を判断するのに必要な情報が含まれるかを自動評価する。
4. ローカルAIのレビュー結果を、構造化された共通形式に変換して正解と比較する。
5. **抽出の失敗**と**AI判断の失敗**を別々に測定する。
6. 再現可能なテスト・評価レポート・運用手順を提供する。
7. 既存の `analyze` コマンドと既存JSON/Markdownの後方互換性を維持する。

## 3. 最初に行う監査（実装前に必須）

次の内容を調べ、簡潔な監査レポートを作成してください。

- `src/offline_review/` の構成、CLI引数、Git比較方式、レビュー単位の分割仕様。
- Python AST、TypeScript/Angular/NgRxの解析範囲と限界。
- 出力されるJSONとMarkdownのスキーマ、coverage、warnings、review unitsの構造。
- テストフレームワーク、fixture、既存テスト、依存ライブラリ。
- `docs/requirements.md`、`docs/architecture.md`、既存の `.cursor/rules/` と `prompts/` の命名規則。
- 既存機能を変更せずに評価基盤を追加する最小構成。

監査結果を `docs/phase16/01-current-state.md` に記録し、既存コードに存在しない関数・パス・CLI引数を推測で呼び出さないでください。

## 4. 合成テストケース（Angular 22 / NgRx：10件）

以下を初期の検証セットとします。すべて**架空の製品・ユーザー・API・データ**で構成してください。Angular 22でもNgModule方式を採用し、NgRxのAction、Effect、Reducer、Selector、Service、Component、HTMLを必要に応じて配置してください。

| ID | 意図的な不具合 | 正解の判定基準 | 主な依存関係 |
|---|---|---|---|
| ANG-001 | Effectが失敗Actionを発行せず、エラー時に処理が途切れる | API失敗時のAction/状態更新が欠落 | Effect→Service→API、Reducer |
| ANG-002 | Reducerが状態オブジェクトを直接変更する | 不変性違反を具体的に指摘 | Action→Reducer→Selector |
| ANG-003 | Selectorが誤ったstateプロパティを参照する | 誤参照による画面値の不整合 | Reducer→Selector→Component |
| ANG-004 | 更新API成功前に成功画面へ遷移する | 非同期完了前の遷移を指摘 | Component→Action→Effect→Service |
| ANG-005 | APIの403応答を一般的な通信エラーとして処理する | 権限エラーの扱いが仕様と不一致 | Service→Effect→Component |
| ANG-006 | Reactive Formsの必須入力検証が保存処理で無視される | invalidなフォームでも保存可能 | Component→HTML→Service |
| ANG-007 | 一覧の選択IDと詳細取得IDが異なる | 選択した対象以外の詳細を取得 | Component→Action→Effect |
| ANG-008 | 更新成功後に古い一覧データが残る | Storeの更新または再取得が欠落 | Effect→Reducer→Selector→Component |
| ANG-009 | コンポーネント破棄後も手動購読が残る | unsubscribe等の適切な終了処理欠落 | Component→Observable/Service |
| ANG-010 | HTMLテンプレートの操作可否と実行処理の条件が不整合 | UI上の無効化だけでは実行制御できない | HTML→Component→Action |

### テストケース作成条件

- 1ケースにつき、**バグありコミット**と**比較対象コミット**を用意し、既存の `--base` / `--head` で差分抽出できるようにする。Git履歴を壊さない。
- バグのないベースラインはビルド・型チェック・可能な単体テストが通ること。
- バグあり版は**対象の論理的不具合だけ**を導入し、無関係な構文エラーやビルド不能を混入させない。
- 各ケースに、単一ファイルだけでは判定できない**関連コード依存**を少なくとも1つ含める。
- 意図的なバグと関係のない変更、過剰なコメント、正解を漏らす変数名（`buggy`等）は避ける。
- 各ケースに正解の検証テストまたは実行可能な期待動作の確認方法を付ける。意図的な不具合が存在することを再現可能に示す。
- 評価用fixtureにのみ不具合IDを記録し、AIへ渡すレビューコンテキストには正解・ラベル・ヒントを含めない。
- 可能なら正常ケースを2件以上追加し、誤検出評価にも使う。正常ケースは10件のバグ件数に算入しない。

## 5. Ground Truth（正解データ）

機械可読な `ground-truth.json` または同等の既存規約に沿った形式を用意してください。最低限、以下を保持します。

```json
{
  "schema_version": "1.0",
  "case_id": "ANG-001",
  "title": "Effect does not handle API failure",
  "severity": "medium",
  "buggy_revision": "<実際のGitコミットID>",
  "base_revision": "<実際のGitコミットID>",
  "primary_locations": [
    { "path": "src/app/example/example.effects.ts", "start_line": 1, "end_line": 1 }
  ],
  "required_context": [
    { "path": "src/app/example/example.effects.ts", "symbol": "loadExample$", "reason": "failure path" },
    { "path": "src/app/example/example.service.ts", "symbol": "loadExample", "reason": "API failure behavior" }
  ],
  "expected_finding": "API failure path does not emit a failure action",
  "expected_behavior": "Failure action is emitted and handled",
  "reproduction": "<再現用テストまたはコマンド>",
  "tags": ["angular", "ngrx", "effect", "error-handling"]
}
```

上記のパス・行番号は**形式例**です。実際のfixtureに一致する値を生成し、存在確認を行ってください。行番号は変更でずれるため、可能ならシンボルとGitリビジョンも併記してください。

## 6. コンテキスト抽出品質の評価

既存の `offline_review analyze` を利用し、各ケースのMarkdown/JSONを生成してください。評価器は次を判定します。

- **changed_line_coverage**：差分中の変更行がレビュー単位のいずれかに含まれている割合。
- **required_context_coverage**：Ground Truthで指定した必須シンボル/関連箇所が抽出された割合。
- **case_context_sufficient**：そのケースの不具合判定に必要な情報がレビュー単位または明示的に結合した複数単位から得られるか。
- **cross_unit_dependency**：必要な情報が別レビュー単位に分散し、単独のAI入力では判断不能になっていないか。
- **resolution_warnings**：import未解決、Angular解析対象外、テンプレート未解析など、判断に影響する警告。
- **context_budget**：レビュー単位ごとの行数と上限超過の有無。

**重要**：`changed lines omitted = 0` と `context lines omitted > 0` は意味が違います。別々に記録してください。差分行がすべて収録されても、必須の関連コードが欠ければ抽出成功と判定しないでください。

レビュー単位を結合して評価する場合は、その操作を明示し、実際にローカルAIへ渡す入力条件（約2,000行・最大3ファイル）と区別してください。AIが一度に読めない情報を、読めたものとして評価してはいけません。

## 7. ローカルAIレビュー結果の評価

ローカルAIそのものをCursorから自動呼び出ししないでください。ローカルAIは別環境で動作します。評価器はユーザーが保存したレビュー結果を**ローカルファイルから読み込む**設計にします。

### 7.1 AIに渡す出力

既存ツールが生成する `review-context.md` と、別途用意するレビュー指示（既存のレビューポリシーを優先）を利用します。評価のための正解データはAIに渡しません。レビュー単位が複数の場合、各単位のIDを明示します。

### 7.2 AI結果の共通JSON形式

```json
{
  "schema_version": "1.0",
  "case_id": "ANG-001",
  "review_unit_id": "unit-001",
  "findings": [
    {
      "finding_id": "F-001",
      "path": "src/app/example/example.effects.ts",
      "line": 1,
      "severity": "medium",
      "title": "API failure is not handled",
      "description": "...",
      "evidence": "...",
      "suggestion": "..."
    }
  ]
}
```

この形式は評価用の**提案スキーマ**です。既存出力形式と衝突する場合は別スキーマとして独立させてください。MarkdownのみのAI結果を使う場合、手動変換の手順またはオフライン変換支援を用意してください。LLMを用いた自動変換を必須にしないでください。

### 7.3 正解との照合

- `path` と行番号の近さ**だけ**で正解と判定しない。指摘内容が不具合の原因・影響を捉えていることを必須にする。
- キーワード一致だけで自動的に正解とせず、機械的に判定できない場合は `needs_manual_review` とする。
- 1つの正解に対する重複指摘はTrue Positiveを1件だけ計上する。
- 同じ指摘を複数の正解に重複割当しない（1対1マッチング）。
- 判定理由と照合対象のIDを保存し、人手で修正・承認できるようにする。
- AI結果がないケースは `not_evaluated` とし、False Negativeと混同しない。評価済みで未検出の場合のみFNとする。
- 正常ケースに対する不当な指摘はFalse Positiveとする。

## 8. 評価指標と原因分類

以下の指標をレポートに出力してください。

- **Recall** = TP / (TP + FN)
- **Precision** = TP / (TP + FP)
- **F1** = 2 × Precision × Recall / (Precision + Recall)
- **Context Sufficiency Rate** = 判定に必要なコンテキストが揃った評価対象ケース数 / 評価対象ケース数
- **Changed Line Coverage** = 収録された変更行数 / 全変更行数
- **False Positives per Clean Case** = 正常ケースの誤検出数 / 評価した正常ケース数

分母0の場合は数値0とせず `N/A` 等で明示してください。暫定目標は Recall 80%以上、Precision 80%以上、Changed Line Coverage 100%、必須コンテキスト充足率90%以上とします。目標値は設定可能にしてください。少数サンプルの結果を実運用品質の証明として扱わないでください。

不具合未検出時は以下に分類します。

1. `extractor_missing_context`：必要な関連コードが抽出されなかった。
2. `cross_unit_context_gap`：必要な情報がAIへ渡した別単位に分散していた。
3. `ai_missed_with_sufficient_context`：十分な情報があるのにAIが指摘しなかった。
4. `ambiguous_ground_truth`：正解データや判定条件に曖昧さがある。
5. `not_evaluated`：AI結果未投入などで判定できない。

## 9. 実装構成（提案）

既存構成を調査したうえで、以下に相当する機能を追加してください。

```text
src/offline_review/
  evaluation/
    schemas.py             # 正解・AI結果・評価結果の検証
    context_evaluator.py   # 関連コード抽出の充足率
    finding_matcher.py     # 指摘と正解の照合（曖昧なものは人手確認）
    metrics.py             # TP/FP/FN、Recall、Precision、F1
    report.py              # Markdown/JSONレポート
    cli.py                 # 既存CLIと衝突しない評価用コマンド
fixtures/phase16/
  angular-ngrx/            # 架空のAngular 22/NgRxテストアプリ
  cases/                   # ケース定義、正解データ、正常ケース
tests/evaluation/
  test_schemas.py
  test_context_evaluator.py
  test_finding_matcher.py
  test_metrics.py
  test_report.py
docs/phase16/
  01-current-state.md
  02-design.md
  03-test-cases.md
  04-operation-guide.md
  05-results.md
```

実際のファイル配置は既存アーキテクチャに合わせてください。既存の `analyze` の出力JSONスキーマを破壊せず、評価結果は別ファイル・別コマンドにしてください。外部ライブラリの追加は必要最小限とし、追加時はライセンスと導入理由を記録してください。

## 10. CLIと運用例

既存CLIを維持し、**新規の評価コマンド**を設計してください。例：

```bash
# 既存コマンド：変更しない
python -m offline_review analyze \
  --repo ./fixtures/phase16/angular-ngrx \
  --base <base-commit> --head <buggy-commit> \
  --comparison direct --output ./output/phase16/ANG-001

# 提案する新規コマンド：実装後、実際の引数に合わせて文書化
python -m offline_review evaluate-context \
  --context ./output/phase16/ANG-001/review-context.json \
  --ground-truth ./fixtures/phase16/cases/ANG-001.json \
  --output ./output/phase16/ANG-001/context-evaluation.json

python -m offline_review evaluate-findings \
  --findings ./output/phase16/ANG-001/ai-findings.json \
  --ground-truth ./fixtures/phase16/cases/ANG-001.json \
  --output ./output/phase16/ANG-001/quality-report
```

`<base-commit>` 等はプレースホルダーです。実装後に**実際に動作確認済みのコマンド**と実Gitリビジョンを記載してください。`evaluate-context` / `evaluate-findings` の名称も提案であり、既存CLI設計を優先します。

## 11. セキュリティ・オフライン要件

- 評価機能の実行中にネットワークアクセス、外部API、クラウドAI、テレメトリーを使用しない。
- 社内ソースコード、顧客情報、認証情報、トークン、実データをfixtureやテストログに含めない。
- ファイルパスは許可されたリポジトリ・出力ディレクトリ内に限定し、パストラバーサル、シンボリックリンクによる逸脱を防ぐ。
- JSON/Markdownは信頼できない入力として扱い、危険なコード実行・任意コマンド実行・HTML埋め込みをしない。
- 既存の Git差分・Python解析・Angular解析・レビュー単位分割の挙動を変更しない。
- 依存パッケージの取得が必要な場合、ネットワークアクセスを勝手に開始せず、必要性と代替手段を報告する。
- 評価レポートに秘密情報が混入しないよう、入力ファイルの内容を無条件に丸ごとログへ複製しない。

## 12. 必須テスト

以下を自動テストとして実装してください。

1. Ground Truthの必須項目欠落・不正なパス・不正な行番号を拒否する。
2. 10件の不具合ケースが存在し、Gitの比較コミットが有効である。
3. バグあり版が構文エラーを含まず、狙った不具合のみ導入されている。
4. 変更行の全収録と関連コード充足率を別々に測定する。
5. 関連コードが1ファイル欠けた場合、抽出不足として検出する。
6. 必須コードがレビュー単位間に分散した場合、cross-unitとして検出する。
7. 既知の正解指摘はTP、未検出はFN、無関係な指摘はFPになる。
8. 同じ正解への重複指摘はTPを二重計上しない。
9. 曖昧な指摘は自動でTPにせず人手確認に送る。
10. AI結果未提出は `not_evaluated` とする。
11. 正常ケースの誤検出を記録する。
12. 分母0の指標はN/A扱いにする。
13. MarkdownとJSONの集計結果が一致する。
14. 既存CLI・既存出力スキーマの回帰テストが通る。
15. オフライン実行可能で、外部APIアクセスがない。

テスト実行結果はコマンド、件数、成功・失敗、既知の制約を `docs/phase16/05-results.md` に記録してください。未実行テストを成功と書かないでください。

## 13. 実装順序

**Step 1 — Audit**：既存コード・仕様・テストを調査し、変更方針を文書化。

**Step 2 — Fixture & Ground Truth**：10件の合成バグ、正常ケース、比較コミット、正解データを用意。

**Step 3 — Context Evaluation**：既存抽出結果と正解データを照合し、コンテキスト充足率を計測。

**Step 4 — AI Finding Evaluation**：ローカルAIレビュー結果の読み込み、照合、人手確認、品質指標の計算。

**Step 5 — Report**：ケース別・全体のJSON/Markdownレポートを出力。

**Step 6 — Regression & Docs**：既存テストと追加テストを実行し、操作手順・制約・結果を記録。

各Stepの終了時に、変更ファイル、テスト結果、残課題を簡潔に報告してください。実装可能な範囲で作業を進め、未実装部分を完成済みと記載しないでください。

## 14. 受け入れ基準（Definition of Done）

- [ ] 合成Angular 22/NgRxの既知バグ10件と正常ケース2件以上がある。
- [ ] 全ケースに再現可能な比較GitリビジョンとGround Truthがある。
- [ ] 既存抽出ツールから全ケースのレビューコンテキストを生成できる。
- [ ] 変更行収録率・必須関連コード充足率・レビュー単位分断を測定できる。
- [ ] AI結果JSONの取り込みとTP/FP/FN/Precision/Recall/F1の計算ができる。
- [ ] 抽出不足とAI判断ミスを分離できる。
- [ ] 曖昧な照合結果を人手確認できる。
- [ ] 正解データがAI入力へ混入しない。
- [ ] JSON/Markdownの評価レポートを生成できる。
- [ ] 既存CLIと既存のPython/Angular解析が回帰テストを通過する。
- [ ] すべてオフラインで動作し、社内コードを使用していない。
- [ ] ドキュメントに実行確認済みのコマンド、実測値、未解決課題を記載した。

## 15. 最終報告の形式

実装終了後、次の順番で日本語の結果を報告してください。

1. **現状監査の要約**：既存仕様と追加ポイント。
2. **追加・変更ファイル一覧**：目的を1行ずつ。
3. **10件のテストケース一覧**：バグID、対象、必要な関連コード。
4. **実行したコマンドと結果**：テスト成功件数・失敗件数、ビルド結果。
5. **抽出品質の実測値**：変更行収録率、関連コード充足率、分断ケース数。
6. **AIレビュー品質の実測値**：AI結果が未投入なら「未評価」と明示し、数値を捏造しない。
7. **未解決課題とPhase 17への推奨改善項目**。

**着手してください。最初に監査を実施し、その結果を踏まえて既存機能を維持する最小の追加設計を確定してから実装してください。**
