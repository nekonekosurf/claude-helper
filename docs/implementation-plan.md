# ローカル版 Claude Code 実装計画

## ゴール

JERG文書（JAXA技術文書96件）に対して質問すると、文書を検索して回答してくれる
**Claude Code 風のコーディングエージェント**をラズパイ上に構築する。

---

## 完成イメージ

```
$ uv run python3 agent.py

🤖 Agent ready (gpt-oss-120b via Cerebras)
> JERGのソフトウェア開発標準で、テスト工程について教えて

📌 検索中... 3件の関連文書を発見
  - JERG-0-049D ソフトウェア開発標準
  - JERG-2-610C 宇宙機ソフトウェア開発標準
  - JERG-1-008A ロケット搭載ソフトウェア開発標準

JERG-0-049D「ソフトウェア開発標準」によると、テスト工程は以下の通りです...
（文書の内容に基づいた回答）

> このファイルを読んで → read_file ツール実行
> このコマンドを実行して → bash ツール実行
> 終了
```

---

## 全体アーキテクチャ

```
┌─────────────────────────────────────────┐
│              CLI（ターミナル）             │
│         ユーザーの入力 / 応答表示          │
├─────────────────────────────────────────┤
│            エージェントループ              │
│  入力 → プロンプト組立 → LLM → ツール     │
│  → 結果注入 → LLM → ... → 最終回答       │
├─────────────────────────────────────────┤
│              ツール群                     │
│  read_file │ write_file │ edit_file      │
│  bash │ glob │ grep │ search_docs        │
├─────────────────────────────────────────┤
│           文書検索エンジン                 │
│  JERG PDF → テキスト → チャンク → 検索     │
│  BM25（キーワード）+ ベクトル検索          │
├─────────────────────────────────────────┤
│            LLM（API）                     │
│  Cerebras: gpt-oss-120b                  │
│  → 将来 vLLM に base_url 変更するだけ     │
├─────────────────────────────────────────┤
│          永続化                           │
│  セッション履歴 │ 記憶ファイル │ 設定      │
└─────────────────────────────────────────┘
```

---

## 実装ステップ

### Phase 1: 最小エージェント（Claude Code の骨格）

**やること:** LLMとツールを使って対話できるエージェントの最小版

**作るファイル:**
```
src/
├── agent.py          # メイン（エージェントループ + CLI）
├── llm_client.py     # LLM接続（Cerebras / vLLM 切替）
├── tools.py          # ツール定義と実行
└── config.py         # 設定管理
```

**実装内容:**
1. `llm_client.py` - OpenAI互換APIクライアント
   - Cerebras / vLLM を base_url で切替
   - ツール定義をLLMに渡す
   - レスポンスからツール呼び出しを解析

2. `tools.py` - 4つの基本ツール
   - `read_file` - ファイル読み取り（行番号付き）
   - `write_file` - ファイル作成/上書き
   - `edit_file` - テキスト置換（完全一致 → フォールバック）
   - `bash` - シェルコマンド実行

3. `agent.py` - エージェントループ
   - ユーザー入力を受け取る
   - システムプロンプト + ツール定義 + 履歴を組み立て
   - LLMに送信 → ツール呼び出し or テキスト応答
   - ツール呼び出しなら実行して結果をループに戻す
   - テキスト応答ならユーザーに表示

4. `config.py` - 設定
   - .env からAPIキー読み込み
   - プロバイダ切替（cerebras / vllm）

**完成時にできること:**
- 「このファイルを読んで」→ ファイルを読んで内容を教えてくれる
- 「hello.py を作って」→ ファイルを作成してくれる
- 「このコードのバグを直して」→ edit_file で修正してくれる
- 「ls -la を実行して」→ コマンドを実行して結果を教えてくれる

---

### Phase 2: JERG文書検索

**やること:** JERG PDFをテキスト化し、検索可能にする

**作るファイル:**
```
src/
├── indexer.py        # PDF → テキスト → チャンク → インデックス
├── searcher.py       # BM25 検索エンジン
└── tools.py          # search_docs ツール追加
data/
├── jerg/             # ダウンロード済みPDF（96件）
└── index/            # 検索インデックス
```

**実装内容:**
1. `indexer.py` - 文書インデックス作成（ラズパイで実行）
   - PDFからテキスト抽出（pypdf or pdfminer）
   - 日本語チャンク分割（「。」「\n」区切り、800文字/チャンク）
   - メタデータ付与（文書番号、タイトル、カテゴリ）
   - BM25インデックス構築（fugashi で日本語トークン化）
   - インデックスをファイルに保存（pickle or JSON）

2. `searcher.py` - 検索（ラズパイで実行、LLM不要）
   - BM25 キーワード検索
   - メタデータフィルタ（文書番号、カテゴリ）
   - 上位N件を返す

3. `tools.py` に追加
   - `search_docs` ツール: クエリを受け取り、関連チャンクを返す
   - LLMが自分で検索クエリを考えて呼び出す

**完成時にできること:**
- 「ソフトウェア開発標準のテスト要件を教えて」
  → LLMが search_docs("ソフトウェア テスト 要件") を呼ぶ
  → 関連するJERG文書のチャンクが返る
  → LLMがチャンクを読んで回答を生成

---

### Phase 3: セッション管理と記憶

**やること:** 会話の保存/復元、記憶の永続化

**作るファイル:**
```
src/
├── session.py        # セッション保存/復元
├── memory.py         # 記憶ファイル管理
└── context.py        # コンテキスト圧縮
```

**実装内容:**
1. `session.py` - セッション管理
   - 会話履歴をJSONLで保存
   - `--continue` で直前のセッション復元
   - `--resume <id>` で特定セッション復元

2. `memory.py` - 記憶（MEMORY.md パターン）
   - 重要な情報を記憶ファイルに保存
   - セッション開始時に自動読み込み

3. `context.py` - コンテキスト圧縮
   - トークン数推定
   - 70%超過で古い会話を要約
   - システムプロンプト + 直近ターンは保持

**完成時にできること:**
- セッションを閉じても前回の会話を引き継げる
- 重要な決定事項がセッション間で記憶される
- 長い会話でもコンテキストが溢れない

---

### Phase 4: 拡張ツールと改善

**やること:** Claude Code に近づける追加機能

**作るファイル/変更:**
```
src/
├── tools.py          # glob, grep ツール追加
├── agent.py          # サブエージェント対応
└── config.py         # 設定ファイル（.agent.md）対応
```

**実装内容:**
1. `glob` ツール - ファイルパターン検索
2. `grep` ツール - 正規表現でファイル内容検索（ripgrep）
3. サブエージェント - 独立コンテキストでタスク実行
4. 設定ファイル - プロジェクト固有の指示書読み込み
5. 権限システム - ツール実行前の確認（allow/deny/ask）

**完成時にできること:**
- 「src/ の中で "error" を含むファイルを探して」→ grep
- 「テストを実行して結果を分析して」→ サブエージェント
- 危険なコマンドは実行前に確認を求める

---

## ファイル構成（最終形）

```
claude-helper/
├── pyproject.toml              # UV プロジェクト設定
├── .env                        # APIキー（gitignore）
├── .gitignore
├── CLAUDE.md                   # プロジェクト指示書
├── docs/
│   ├── claude-code-architecture.md  # Claude Code 解析
│   ├── local-claude-code.md         # vLLM設計書
│   └── implementation-plan.md       # この文書
├── src/
│   ├── agent.py                # メインエージェントループ + CLI
│   ├── llm_client.py           # LLM接続（Cerebras / vLLM）
│   ├── tools.py                # ツール定義と実行
│   ├── config.py               # 設定管理
│   ├── indexer.py              # JERG PDF → インデックス
│   ├── searcher.py             # BM25 文書検索
│   ├── session.py              # セッション保存/復元
│   ├── memory.py               # 記憶管理
│   └── context.py              # コンテキスト圧縮
├── data/
│   ├── jerg/                   # JERG PDF（96件、336MB）
│   └── index/                  # 検索インデックス
└── sessions/                   # セッション履歴
```

---

## 技術スタック

| コンポーネント | 技術 | 備考 |
|---|---|---|
| 言語 | Python 3.11 | ラズパイ標準 |
| パッケージ管理 | UV | 高速、ロックファイル対応 |
| LLM API | OpenAI SDK | Cerebras / vLLM 共通 |
| LLMモデル | gpt-oss-120b | Cerebras（テスト）→ vLLM（本番） |
| PDF処理 | pypdf | 軽量、ラズパイで動く |
| 日本語処理 | fugashi (MeCab) | BM25用トークン化 |
| キーワード検索 | rank-bm25 | 軽量、GPU不要 |
| CLI | 標準入出力 | Phase1は最小限 |

---

## 各 Phase の依存パッケージ

```bash
# Phase 1（最小エージェント）
uv add openai python-dotenv

# Phase 2（文書検索）
uv add pypdf fugashi unidic-lite rank-bm25

# Phase 3（セッション管理）
# 追加パッケージなし（標準ライブラリで実装）

# Phase 4（拡張）
# 追加パッケージなし（ripgrep はシステムコマンド）
```

---

## 本番移行（将来）

```python
# テスト環境（今）
LLM_BASE_URL = "https://api.cerebras.ai/v1"
LLM_API_KEY  = "cerebras-key"
LLM_MODEL    = "gpt-oss-120b"

# 本番環境（将来）→ この3行だけ変える
LLM_BASE_URL = "http://gpu-server:8000/v1"
LLM_API_KEY  = "dummy"
LLM_MODEL    = "gpt-oss-120b"  # 同じモデル
```

BM25検索 → 本番では Qdrant（ベクトル検索）に拡張可能。
検索部分は `searcher.py` を差し替えるだけ。

---

## スケジュール目安

| Phase | 内容 | 作業量 |
|---|---|---|
| **Phase 1** | 最小エージェント | src/ に4ファイル |
| **Phase 2** | JERG文書検索 | src/ に2ファイル + インデックス構築 |
| **Phase 3** | セッション管理 | src/ に3ファイル |
| **Phase 4** | 拡張ツール | 既存ファイルに追加 |
