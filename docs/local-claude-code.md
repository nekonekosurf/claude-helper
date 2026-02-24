# ローカル版 Claude Code を作る方法

## 概要

Claude Code と同等のコーディングエージェントを、ローカルLLM（中国系モデル除外）で構築するための調査結果。

---

## 1. 既存のオープンソース代替ツール

### 比較表

| 機能 | Claude Code | Aider | Goose | OpenCode | Vibe CLI | Open Interpreter |
|---|---|---|---|---|---|---|
| CLI対話 | ○ | ○ | ○ | ○ | ○ | ○ |
| ファイル読み書き編集 | ○ | ○ | ○ | ○ | ○ | ○ |
| Bash実行 | ○ | × | ○ | ○ | ○ | ○ |
| コード検索 | ○ | △ | MCP経由 | ○ | ○ | ○ |
| ツール呼び出し | ○ | 暗黙的 | ○(MCP) | ○ | ○ | ○ |
| サブエージェント | ○ | × | × | マルチセッション | ○ | × |
| 記憶の永続化 | CLAUDE.md | × | × | × | × | × |
| プランモード | ○ | × | Deep mode | × | × | × |
| Git連携 | ○ | 最強 | ○ | ○ | ○ | △ |
| ローカルLLM | × | ○ | ○ | ○ | ○ | ○ |
| MCP対応 | ○ | × | ○(ネイティブ) | △ | × | × |
| オープンソース | × | ○ | ○ | ○ | ○ | ○ |

### 各ツールの詳細

#### Aider（コード編集精度が最高）
- **GitHub:** github.com/paul-gauthier/aider（39K+ stars）
- **特徴:**
  - tree-sitter でAST解析 → PageRank でリポジトリマップを生成
  - search/replaceブロック方式の編集（最も信頼性が高い）
  - Git統合が最も充実（自動コミット、メッセージ生成）
  - フォールバックマッチング: 完全一致 → 空白無視 → ファジーLevenshtein距離
- **ローカルLLM:** `aider --model ollama_chat/<model-name>`
- **弱点:** サブエージェントなし、プランモードなし、Bash実行なし

#### Goose（MCP拡張性が最強）
- **GitHub:** github.com/block/goose（30K+ stars、Apache 2.0）
- **特徴:**
  - MCP ネイティブ統合（BlockがAnthropicと共同設計）
  - 「Deep mode」で自律的リサーチ
  - Linux Foundation の Agentic AI Foundation に参加
- **ローカルLLM:** Ollama, Ramalama, Docker Model Runner 対応
- **弱点:** コード編集精度はAiderより劣る

#### OpenCode（TUIが最も美しい）
- **GitHub:** github.com/opencode-ai/opencode（100K+ stars）
- **特徴:**
  - Go製、Bubble Tea によるリッチなTUI
  - LSP連携（LLMに言語サーバー情報を提供）
  - マルチセッション対応（複数エージェント並列実行）
- **ローカルLLM:** Ollama対応（64K以上のコンテキスト必要）
- **弱点:** Ollamaでのツール呼び出しに問題報告あり

#### Mistral Vibe CLI（Claude Codeに最も近いアーキテクチャ）
- **GitHub:** github.com/mistralai/mistral-vibe（Apache 2.0）
- **特徴:**
  - サブエージェントでタスク委任（コンテキスト汚染防止）
  - プロジェクト全体のコンテキストスキャン
  - ツール実行の自動承認トグル
  - `@` でファイル参照、`!` でシェルコマンド
- **ローカルLLM:** Devstral Small 2 (24B) 向けに設計
- **弱点:** 新しいプロジェクト、成熟度はまだ低い

### 結論：どれを使うか

| 目的 | 推奨ツール |
|---|---|
| Claude Codeに最も近い体験 | **Mistral Vibe CLI**（サブエージェント、ツール権限、プロジェクトコンテキスト） |
| 最高のコード編集精度 | **Aider**（Git統合、search/replace方式） |
| 最大の拡張性 | **Goose**（MCP経由で何でも追加可能） |
| 美しいTUI + マルチセッション | **OpenCode** |

---

## 2. 使えるモデル（中国系除外）

> **注意:** DeepSeekは中国企業（杭州、High-Flyer傘下）のため除外。Qwen（Alibaba）も除外。

### コーディング用モデル

| モデル | 開発元 | サイズ | コンテキスト | コーディング力 | VRAM(Q4) | ツール呼出 |
|---|---|---|---|---|---|---|
| **Devstral Small 2** | Mistral(仏) | 24B | 256K | とても良い | ~16GB | ネイティブ |
| **Llama 3.3 70B** | Meta(米) | 70B | 128K | 優秀 | ~42GB | ネイティブ |
| **Codestral 22B** | Mistral(仏) | 22B | 256K | とても良い | ~14GB | Instruct版 |
| **Gemma 3 27B** | Google(米) | 27B | 128K | 良い | ~18GB | ネイティブ |
| **Gemma 3 12B** | Google(米) | 12B | 128K | 良い | ~8GB | ネイティブ |
| Llama 3.1 Swallow 8B | 東工大 | 8B | 128K | 日本語特化 | ~6GB | ネイティブ |
| StarCoder2 15B | BigCode | 15B | 16K | コード特化 | ~10GB | なし |

### Embedding モデル（中国系除外）

| モデル | 開発元 | パラメータ | 次元 | 日本語 |
|---|---|---|---|---|
| **multilingual-e5-large** | Microsoft(米) | 560M | 1024 | 優秀 |
| multilingual-e5-base | Microsoft(米) | 278M | 768 | とても良い |
| nomic-embed-text-v1.5 | Nomic(米) | 137M | 768 | 良い |
| e5-mistral-7b-instruct | Microsoft(米) | 7B | 4096 | 優秀(重い) |

### ハードウェア別の推奨構成

| ハードウェア | 推奨モデル | 体験品質 |
|---|---|---|
| **8GB VRAM** (RTX 4060) | Gemma 3 12B Q4 | 基本的な編集は可能 |
| **16GB VRAM** (RTX 4070 Ti) | Devstral Small 2 24B Q4 | 良い（実用レベル） |
| **24GB VRAM** (RTX 4090) | Devstral Small 2 24B Q5/Q6 | とても良い |
| **32GB VRAM** (RTX 5090) | Llama 3.3 70B Q4 | 優秀 |
| **Mac 32GB+** | Devstral Small 2 24B | 良い（やや遅い） |

---

## 3. アーキテクチャ設計

### 全体構成

```
┌─────────────────────────────────────────┐
│           CLI / TUI レイヤー              │
│  (Python: Rich+Textual / Go: Bubble Tea) │
├─────────────────────────────────────────┤
│            エージェントループ              │
│  入力 → プロンプト組立 → LLM呼出 →       │
│  ツール解析 → ツール実行 → 結果注入 →     │
│  ループ or 最終回答                       │
├─────────────────────────────────────────┤
│            ツールレジストリ                │
│  read | write | edit | bash |            │
│  glob | grep | git | spawn_agent         │
├─────────────────────────────────────────┤
│           コンテキストマネージャ           │
│  リポマップ(tree-sitter) | ファイルキャッシュ│
│  会話履歴 | 記憶ファイル                   │
├─────────────────────────────────────────┤
│           LLMプロバイダ層                  │
│  Ollama | llama.cpp | vLLM               │
├─────────────────────────────────────────┤
│            永続化レイヤー                  │
│  セッションDB | 記憶ファイル | 設定         │
└─────────────────────────────────────────┘
```

### エージェントループ（ReActパターン）

最小実装は **約400行のPython** で可能:

```python
def agent_loop(user_message, tools, model):
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_message}
    ]

    while True:
        # 1. THINK: LLMに送信
        response = llm_call(model, messages, tools)

        # 2. ツール呼び出しがあるか確認 (ACT)
        if response.tool_calls:
            for tool_call in response.tool_calls:
                # 3. ツール実行
                result = execute_tool(tool_call.name, tool_call.arguments)

                # 4. OBSERVE: 結果をメッセージに追加
                messages.append({"role": "assistant", "content": response})
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": str(result)
                })
        else:
            # 最終回答 → ループ終了
            return response.content
```

### 最小限のツール定義（4つ）

```python
TOOLS = {
    "read_file": {
        "description": "ファイルの内容を読む",
        "parameters": {
            "path": "string (必須)",
            "offset": "integer (任意、開始行)",
            "limit": "integer (任意、行数)"
        }
    },
    "write_file": {
        "description": "ファイルを作成/上書き。ディレクトリは自動作成",
        "parameters": {
            "path": "string (必須)",
            "content": "string (必須)"
        }
    },
    "edit_file": {
        "description": "ファイル内のテキストを置換。old_textは完全一致が必要",
        "parameters": {
            "path": "string (必須)",
            "old_text": "string (必須)",
            "new_text": "string (必須)"
        }
    },
    "bash": {
        "description": "シェルコマンドを実行。stdout, stderr, exit codeを返す",
        "parameters": {
            "command": "string (必須)",
            "timeout_ms": "integer (任意、デフォルト120000)"
        }
    }
}
```

### Claude Code完全互換の拡張ツール（+4つ）

| ツール | 用途 |
|---|---|
| `glob` | パターンでファイル検索 |
| `grep` | 正規表現でファイル内容検索 |
| `git_operations` | status, diff, commit, log |
| `spawn_agent` | サブエージェント生成（独立コンテキスト） |

---

## 4. コンテキスト管理（最重要の設計判断）

### リポジトリマップ（Aiderのアプローチ）

1. **tree-sitter** で全ソースファイルを解析 → 関数/クラス定義を抽出
2. 依存関係グラフを構築（どのファイルがどの定義を参照）
3. **PageRank** で会話中のファイルに関連する定義をランク付け
4. トークン予算内（デフォルト~1Kトークン）で上位の定義をプロンプトに含める
5. 詳細が必要なら `read_file` で取得

### 会話履歴の圧縮

```
[セッションコンテキスト]
作業中: /home/user/project/backend
変更ファイル: auth.py, middleware.py, tests/test_auth.py
決定事項: JWTトークン使用、リフレッシュトークン回転追加
現在のタスク: /api/users の401エラー修正
最後のエラー: KeyError 'user_id' in middleware.py line 45
[セッションコンテキスト終了]
```

**戦略:**
1. コンテキストウィンドウの70%に達したら圧縮を実行
2. システムプロンプト + ツール定義は常に保持
3. 直近3-5ターンはそのまま保持
4. それ以前は要約に圧縮（ファイルパス、関数名、エラーメッセージ、決定事項は必ず保持）

### 記憶の永続化（CLAUDE.mdパターン）

```
~/.local-agent/memory/              # グローバル設定
<project>/.agent.md                 # プロジェクト指示書
<project>/.agent.local.md           # 個人設定（gitignore）
<project>/.agent/memory/auto.md     # 自動記憶
```

---

## 5. ツール呼び出しの信頼性

### 問題
12B未満のモデルはJSONのツール呼び出しを頻繁に壊す

### 解決策

| 手法 | 説明 |
|---|---|
| Ollamaの`format`パラメータ | Pydanticスキーマで有効なJSONを強制 |
| llama.cppのGBNF文法 | 出力トークンを文法的に制約（どのモデルでも動く） |
| Lazy Grammar | 自然言語で思考 → ツール呼び出し部分だけ制約 |
| ツール説明を短く | 全ツール合計~150トークンに抑える |
| temperature=0 | 決定論的なツール呼び出し |

### 編集の信頼性（3段階フォールバック）

```
1. 完全一致マッチ
     ↓ 失敗
2. 空白無視マッチ
     ↓ 失敗
3. ファジーマッチ（Levenshtein距離）
     ↓ 失敗
4. 詳細エラー返却 → LLMに再試行させる
```

---

## 6. 実装の3つの選択肢

### 選択肢A: 既存ツールを使う（最速）

```bash
# Ollama + モデルをインストール
curl -fsSL https://ollama.com/install.sh | sh
ollama pull devstral-small:24b-q4_K_M

# Aider で使う（コード編集精度最高）
pip install aider-chat
aider --model ollama_chat/devstral-small:24b-q4_K_M

# または Goose で使う（MCP拡張性最高）
goose configure  # Ollamaをプロバイダに設定
goose session start
```

### 選択肢B: ゼロから作る（最大の制御）

**推奨スタック:**
- 言語: Python 3.12+
- LLM: Ollama（最も簡単）or llama-cpp-python（制御性高い）
- フレームワーク: Smolagents（HuggingFace）or 素のReActループ（~400行）
- TUI: Rich + Textual
- コード解析: tree-sitter（py-tree-sitter-languages）
- 検索: ripgrep（サブプロセス）
- 構造化出力: Pydantic + Ollama format
- 記憶: ファイルベース（.agent.mdパターン）

**参考チュートリアル:**
- together.ai/blog/how-to-build-a-coding-agent-from-scratch
- freecodecamp.org/news/build-an-ai-coding-agent-in-python
- github.com/ghuntley/how-to-build-a-coding-agent

### 選択肢C: Mistral Vibe CLIをフォーク（バランス最適）

1. github.com/mistralai/mistral-vibe をフォーク（Apache 2.0）
2. LLMプロバイダを Ollama に変更
3. 記憶の永続化を追加（ファイルベース、CLAUDE.mdパターン）
4. tree-sitter リポマップを追加（Aiderのアプローチ）
5. ツール定義をカスタマイズ

**利点:** サブエージェント、コンテキスト管理、ツール実行の仕組みが既にある。

---

## 7. Claude Codeとの差分（現実的な限界）

| 項目 | Claude Code | ローカル版 |
|---|---|---|
| コンテキスト | 200K（劣化なし） | 128K（後半で劣化） |
| 推論品質 | Opus/Sonnet級 | 70Bでも顕著に劣る |
| 速度 | API経由で高速 | ローカル推論は遅い |
| 編集自己修復 | 非常に強い | モデル依存、弱め |
| サブエージェント | 同じ強い推論力 | 小さいモデルに制限 |

---

## 8. 推奨実装ステップ

```
Phase 1: 環境構築（1週間）
  ├─ Ollama + Devstral Small 2 24B インストール
  ├─ 既存ツール（Aider or Goose）で動作確認
  └─ 基本的なコーディングタスクで品質評価

Phase 2: カスタマイズ（2週間）
  ├─ Vibe CLI をフォーク or 素のReActループ実装
  ├─ 4つの基本ツール実装
  ├─ tree-sitter リポマップ統合
  └─ 記憶ファイルの永続化

Phase 3: 拡張（2週間）
  ├─ サブエージェント機能追加
  ├─ コンテキスト圧縮の実装
  ├─ グラフRAG / ベクトル検索の統合
  └─ TUI の改善

Phase 4: 最適化（継続）
  ├─ 編集精度のフォールバック改善
  ├─ プランモードの実装
  ├─ MCP対応
  └─ 社内ドキュメント統合
```
