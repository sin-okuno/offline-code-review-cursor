# Phase 11｜Angularレビューコンテキスト抽出の品質改善 — Cursor実装プロンプト

## 役割

あなたは Python 3.13、TypeScript、Angular 22、NgRx、Git 差分解析、静的解析、セキュアなオフライン開発に精通したシニアエンジニアです。

既存の `offline_review` CLI に対し、**レビュー対象コードの取りこぼし削減、依存関係解析の精度向上、レポートの整合性確保**を行ってください。

## 前提と守るべき制約

- 対象は約10万行の Angular 22 + NgRx + Django REST / Python 3.13 の既存リポジトリ。
- 出力はローカルLLMによるレビュー用の Markdown / JSON。1回のレビューの目安は約2,000行。
- **企業のソースコードを Cursor、クラウドAI、外部APIに送信しないこと。** Cursorでの開発・検証には合成したサンプルコードと公開可能なテストデータだけを使う。
- 実行時は完全オフライン。ネットワークアクセス、外部API、テレメトリ、リモートLLMを追加しない。
- 既存の Git 差分解析、Python AST 解析、CLI 引数、既存JSONスキーマ、既存テストを壊さない。
- 既存の `.cursor/rules/`、`docs/`、`prompts/`、`README.md` の内容を削除・上書きしない。変更が必要な場合は差分と理由を明示し、後方互換性を優先する。
- Angular 22 でも NgModule ベース構成をサポートする。Standalone 前提にしない。

## 現状の課題（既存の review-context 出力から確認）

1. `angular_context.coverage.lines_selected=1994`、`lines_omitted=197`、`truncated=true`。変更コードの一部がレビュー対象から除外されている。
2. `CHANGED_CONTEXT_OMITTED` が複数ファイルで発生している。重要な変更箇所を最優先に出力できていない。
3. `tsconfig.json` が JSONC（コメント付きJSON）である場合に `TSCONFIG_UNREADABLE` が発生する。
4. `RiskDetailComponent` の相対importに `target_not_found` が出ている。実際の欠落と解析器の誤判定を区別する必要がある。
5. `angular_context` にNgRx等の解析結果がある一方、旧 `coverage` に `angular_semantic_out_of_scope` / `ANGULAR_SEMANTIC_OUT_OF_SCOPE` が残り、レポートが矛盾している。
6. `ngrx`、`template_bindings`、`typescript_semantic` が `partial`。依存関係の確認済み／推定／未解決を区別し、レビューで追跡可能にしたい。
7. `WORKTREE_DIRTY` が発生する場合、コミット間差分に未コミット変更が含まれないことを明確にする必要がある。

## 作業手順

### Step 1：現状調査と実装計画

まず既存コード、テスト、ドキュメント、CLI、JSONスキーマを調査してください。いきなり大規模な改修を行わず、以下を示してください。

- 現在のモジュール構成、差分抽出・TS解析・依存グラフ・行数予算・レポート生成の処理経路
- 各課題の再現条件と根本原因の仮説
- 変更対象ファイル、変更内容、互換性への影響
- 実装順序、テスト計画、受け入れ基準

その後、影響の小さい単位に分けて実装してください。

### Step 2：変更コードを優先するコンテキスト選定

- **変更された行とその周辺文脈**を、未変更の依存コードより優先する。
- 優先順位は原則として「変更行 → 変更された関数・クラスの必要部分 → 直接依存 → 間接依存」とする。
- 各変更ファイルについて `changed_lines_total`、`changed_lines_included`、`changed_lines_omitted` を集計する。
- 省略した変更箇所はファイルパス、行範囲、理由をレポートに明示する。
- 2,000行に収まらない場合は、変更箇所を黙って捨てず、複数のレビュー単位へ分割する設計を検討・実装する。各単位はレビューに必要な最小限の共通コンテキストを含める。
- 分割後もファイル間の参照関係とレビュー単位の識別子を追跡可能にする。
- 行数予算はCLIの既存デフォルトと互換性を維持し、必要な設定追加は明示する。

### Step 3：TypeScript import解決の改善

- 相対import、拡張子省略、`index.ts`、ディレクトリ解決を扱う。
- `tsconfig.json` / `tsconfig.*.json` の `baseUrl`、`paths`、必要な `extends` を可能な範囲で安全に解決する。
- TypeScript標準的なJSONC（コメント、末尾カンマ）を安全に読み込む。文字列中の `//` をコメントと誤認しない。
- 解決結果を `resolved` / `unresolved` / `ambiguous` / `unsupported` などに分類し、根拠を保持する。
- 未解決importを即座に「アプリケーションのビルドエラー」と断定しない。解析対象外、パスエイリアス、生成ファイル等の可能性を区別する。
- ファイルシステム走査はリポジトリ内に限定し、パストラバーサルやシンボリックリンクによる領域外アクセスを防止する。

### Step 4：Angular / NgRx の関連コード抽出

次の依存関係を可能な範囲で追跡してください。

- Component → Action → Effect → Service → API呼び出し
- Action → Reducer → Selector → Component
- Component → HTMLテンプレート（`templateUrl`、必要に応じてinline template）
- NgModule → declarations / imports / providers
- Component → 注入Service、Service → 呼び出し先Service

条件：

- NgModule構成と `Effect → Service → API` の実装パターンを優先する。
- `dispatch`、`ofType`、`createAction`、`createEffect`、`createReducer`、`createSelector` 等を適切に検出する。
- 静的解析で**確認できた関係**と、命名・字句解析による**推定関係**を分ける。
- 参照元、参照先、ファイル、行番号、判定方法を可能な範囲で残す。
- 不明な関係を捏造せず、未解決として出力する。
- base/head の情報を混同しない。比較対象のコミットに対応する内容だけを解析する。

### Step 5：カバレッジ・警告の整合性

- `coverage` と `angular_context.coverage` の意味を明確化し、値が矛盾しないようにする。
- Angular解析を実施したのに `ANGULAR_SEMANTIC_OUT_OF_SCOPE` と表示するような古い警告を修正する。
- ただし、字句解析しか行っていない箇所を「完全なTypeScript意味解析済み」と誤表示しない。
- `complete` / `partial` / `unsupported` 等の状態と判定基準を定義する。
- 変更行の省略、import未解決、tsconfig未対応、予算超過、作業ツリー未コミットなどを区別して警告する。
- `WORKTREE_DIRTY` 時は、解析対象がコミット間差分であり未コミット変更を含まないことを明示する。
- JSONとMarkdownで同じカバレッジ情報・警告・レビュー単位を参照できるようにする。
- 既存のJSONフィールドは原則維持し、追加フィールドで拡張する。削除・意味変更が必要なら互換性対策を用意する。

### Step 6：回帰テスト

企業コードを使用せず、合成fixtureで以下を自動テストしてください。

1. 変更コードが2,000行を超える場合の分割と省略報告
2. 変更行が未変更の関連コードより優先されること
3. Component → Action → Effect → Service → API の抽出
4. Action → Reducer → Selector → Component の抽出
5. NgModule宣言・import・provider とHTMLテンプレートの関連づけ
6. JSONC、tsconfig paths、index.ts、相対importの解決
7. 存在しないimportの未解決報告と誤検知抑制
8. base/headが異なる場合の正しいコミット内容の解析
9. カバレッジと警告のJSON/Markdown整合性
10. 既存のPython AST・Git差分・CLI・セキュリティテストの回帰
11. 大規模リポジトリでの実行時間・メモリ・決定性
12. ネットワークアクセスなしで完結すること

### Step 7：ドキュメントと実行例

- 新機能、制限事項、警告の意味、レビュー分割時の使い方を追記する。
- 既存ドキュメントを保持し、必要に応じて新しい文書を追加する。
- 以下の既存CLIが引き続き利用できることを確認する。

```bash
cd /mnt/c/work/00_src/ai-driven/offline-code-review-cursor
export PYTHONPATH="$PWD/src"
python -m offline_review analyze \
  --repo /mnt/c/work/00_src/ai-driven/tree-sample \
  --base HEAD~1 --head HEAD --comparison direct --output ./output
```

## 完了条件

- 変更行の収録率を定量的に把握でき、予算超過時に無言で変更行を落とさない。
- Angular/NgRx依存関係が根拠つきで出力される。
- JSONCと主要なTypeScript import形式を安全に処理できる。
- 旧・新カバレッジ表示に矛盾がない。
- 既存Python/Git/CLIの回帰テストが通る。
- 合成fixtureのみで検証し、実行時にネットワークを使わない。

## 最終報告フォーマット

1. 現状調査で判明した原因
2. 変更したファイルと主要な実装内容
3. 追加・変更したテストと結果
4. 実行コマンドと出力ファイルの例
5. 残存する制限・誤検知の可能性
6. 既存仕様との互換性とセキュリティ確認

**注意：レビューコンテキスト抽出器の改善と、実アプリケーションに不具合があるという判定は別物です。根拠のない不具合断定はしないでください。**
