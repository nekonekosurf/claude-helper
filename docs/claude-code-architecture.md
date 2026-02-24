# Claude Code 内部アーキテクチャ完全解析

## 目次

1. [エージェントループ](#1-エージェントループ)
2. [システムプロンプト](#2-システムプロンプト)
3. [ツールシステム](#3-ツールシステム)
4. [コンテキスト管理](#4-コンテキスト管理)
5. [サブエージェント](#5-サブエージェント)
6. [Edit ツールの仕組み](#6-edit-ツールの仕組み)
7. [プランモード](#7-プランモード)
8. [セッション管理](#8-セッション管理)
9. [CLAUDE.md と設定](#9-claudemd-と設定)
10. [カスタムエージェント](#10-カスタムエージェント)
11. [Agent Teams](#11-agent-teams)
12. [MCP 連携](#12-mcp-連携)
13. [Git 連携](#13-git-連携)
14. [スキルシステム](#14-スキルシステム)
15. [フックシステム](#15-フックシステム)
16. [自動メモリ](#16-自動メモリ)
17. [権限システム](#17-権限システム)
18. [ローカル再現に必要なもの](#18-ローカル再現に必要なもの)

---

## 1. エージェントループ

### メインループの流れ

```
ユーザー入力
    │
    ▼
コンテキスト組み立て
  システムプロンプト + CLAUDE.md + ツール定義 + 会話履歴
    │
    ▼
LLM呼び出し
    │
    ├── テキスト応答 → ユーザーに表示 → 待機
    │
    └── ツール呼び出し → ツール実行 → 結果をコンテキストに追加 → ループ
```

### 具体的な動作

1. ユーザーがプロンプトを送信
2. Claude Code がシステムプロンプト + コンテキストを組み立てて LLM に送信
3. LLM が「ツール使用」か「テキスト応答」かを自分で判断
4. ツール使用の場合：ツールを実行 → 結果を新しいコンテキストとして追加 → 再度LLMに送信
5. テキスト応答の場合：ユーザーに表示して終了
6. ユーザーはいつでも Escape で中断可能

### 利用可能なモデル

| エイリアス | モデル | 用途 |
|---|---|---|
| `opus` | Claude Opus 4.6 | 最強の推論力 |
| `sonnet` | Claude Sonnet 4.6 | バランス型 |
| `haiku` | Claude Haiku 4.5 | 高速・低コスト |

---

## 2. システムプロンプト

### 含まれる内容

Claude Code は毎回の LLM 呼び出しに以下を含むシステムプロンプトを注入する：

```
1. ツール定義（各ツールのパラメータスキーマ）
2. 行動指針（安全性、権限、ファイル操作のルール）
3. CLAUDE.md の内容（プロジェクト固有の指示）
4. スキルの説明文
5. MCP サーバーのツール定義
6. セッション情報（作業ディレクトリ、環境）
7. 権限ルール（allow/deny/ask）
```

### カスタマイズ

```bash
# システムプロンプトを完全に置き換え
claude --system-prompt "あなたは..."

# 追加
claude --append-system-prompt "追加ルール"

# ファイルから読み込み
claude --system-prompt-file ./prompt.txt
```

---

## 3. ツールシステム

### 全ビルトインツール一覧

| ツール | パラメータ | 動作 |
|---|---|---|
| **Read** | `file_path`, `line_offset?`, `limit?` | ファイル読み取り（最大2000行）。行番号付きで返す |
| **Write** | `file_path`, `file_contents` | ファイル作成/上書き |
| **Edit** | `file_path`, `old_str`, `new_str` | 完全一致の文字列置換 |
| **Bash** | `command` | シェルコマンド実行。stdout/stderr/exit code を返す |
| **Glob** | `pattern`, `path?` | ファイルパターン検索。更新日時順で返す |
| **Grep** | `pattern`, `path?`, `type?`, `glob?`, `output_mode?`, `-A`, `-B`, `-C`, `-n`, `-i`, `multiline?` | ripgrep ラッパー。3つの出力モード |
| **WebFetch** | `url`, `prompt` | URL取得 + AI要約。HTML→Markdown変換。15分キャッシュ |
| **WebSearch** | `query`, `allowed_domains?`, `blocked_domains?` | Web検索。ドメインフィルタ対応 |
| **Task** | `subagent_type`, `prompt`, `description` | サブエージェント生成。独立コンテキスト |
| **AskUserQuestion** | `question`, `options?` | ユーザーに質問。選択肢 or 自由入力 |
| **Skill** | `skill_name`, `arguments?` | スキル/スラッシュコマンドの実行 |
| **MCP ツール** | （サーバーごとに異なる） | MCP サーバーから動的にロード |

### ツール定義のフォーマット（LLMに渡される形式）

```json
{
  "name": "Read",
  "description": "Read file contents from the local filesystem...",
  "input_schema": {
    "type": "object",
    "properties": {
      "file_path": {
        "type": "string",
        "description": "The absolute path to the file to read"
      },
      "line_offset": {
        "type": "number",
        "description": "The line number to start reading from"
      },
      "limit": {
        "type": "number",
        "description": "The number of lines to read"
      }
    },
    "required": ["file_path"]
  }
}
```

### 権限の仕組み

```
ツール呼び出し発生
    │
    ▼
deny ルールに一致？ → はい → ブロック（実行しない）
    │ いいえ
    ▼
ask ルールに一致？ → はい → ユーザーに確認
    │ いいえ
    ▼
allow ルールに一致？ → はい → 自動実行
    │ いいえ
    ▼
デフォルト → ユーザーに確認
```

---

## 4. コンテキスト管理

### LLM呼び出し時のコンテキスト構造

```
┌──────────────────────────────┐
│ システムプロンプト              │ ← 固定（ツール定義含む）
│   ├── CLAUDE.md              │
│   ├── ツール定義（各200-500トークン）│
│   ├── スキル説明              │
│   ├── MCP ツール定義          │
│   └── 権限ルール              │
├──────────────────────────────┤
│ 自動メモリ（MEMORY.md 先頭200行）│ ← セッション開始時にロード
├──────────────────────────────┤
│ 会話履歴                      │ ← ユーザーメッセージ + ツール結果
│   ├── Turn 1: ユーザー + 応答  │
│   ├── Turn 2: ユーザー + ツール呼出 + 結果 + 応答
│   ├── ...                    │
│   └── Turn N: 現在のターン     │
├──────────────────────────────┤
│ 残りトークン予算               │ ← LLMの応答用
└──────────────────────────────┘
```

### 自動圧縮（コンパクション）

**トリガー条件:**
- コンテキストウィンドウの **95%** に到達（`CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` で変更可能）

**圧縮の順番:**
1. まず古いツール出力を削除
2. 次に会話を要約（ファイルパス、関数名、エラーメッセージ、決定事項は保持）
3. システムプロンプト + ツール定義は常に保持
4. 直近のターンはそのまま保持

**手動操作:**
```
/compact focus: TypeScriptの型定義に注目して圧縮
/context    # コンテキスト使用量を確認
```

### CLAUDE.md のロード階層

```
1. 管理ポリシー CLAUDE.md（組織レベル、最優先）
2. .claude/CLAUDE.md（プロジェクト）
3. ./CLAUDE.md（プロジェクトルート）
4. 親ディレクトリの CLAUDE.md（ルートまで再帰）
5. ~/.claude/CLAUDE.md（ユーザーレベル）
6. ./CLAUDE.local.md（個人用、gitignore推奨）
```

**インポート構文:**
```markdown
@README.md の内容も参考にしてください
@docs/api-standards.md の規約に従ってください
@~/shared-rules.md を読んでください
```

---

## 5. サブエージェント（Task ツール）

### ビルトインサブエージェント

| エージェント | モデル | 使えるツール | 用途 |
|---|---|---|---|
| **Explore** | Haiku | 読み取り専用（Read, Glob, Grep, Bash(git)） | コード探索・調査 |
| **Plan** | 親と同じ | 読み取り専用 | 設計・計画 |
| **General-purpose** | 親と同じ | 全ツール | 複雑なマルチステップ作業 |
| **Bash** | 親と同じ | Bash のみ | ターミナル操作 |

### サブエージェントの定義ファイル（.claude/agents/xxx.md）

```yaml
---
name: code-reviewer
description: コードの品質レビュー。編集後に自動的に使用。
model: sonnet
tools: Read, Grep, Glob, Bash
disallowedTools: Write, Edit
maxTurns: 10
memory: project
background: true
isolation: worktree
permissionMode: plan
skills:
  - code-conventions
mcpServers:
  - github
hooks:
  PreToolUse:
    - matcher: "Bash"
      hooks:
        - type: command
          command: "./validate.sh"
---

あなたはシニアコードレビュアーです。品質、セキュリティ、ベストプラクティスに焦点を当ててください。
```

### サブエージェントの動作原理

```
メインエージェント
    │
    │ Task ツールで生成
    ▼
サブエージェント（独立コンテキスト）
    ├── 同じ CLAUDE.md をロード
    ├── 独自の会話履歴
    ├── 指定されたツールのみ使用可能
    ├── 他のサブエージェントは生成できない（ネスト不可）
    │
    │ 作業完了
    ▼
結果をメインに返却（要約されて圧縮）
```

### 保存場所

```
~/.claude/projects/{project}/{sessionId}/subagents/
  ├── agent-{id1}.jsonl
  └── agent-{id2}.jsonl
```

---

## 6. Edit ツールの仕組み

### 動作

```json
{
  "file_path": "/absolute/path/file.ts",
  "old_str": "完全一致する文字列",
  "new_str": "置換後の文字列"
}
```

### マッチングアルゴリズム

```
1. old_str でファイル内を検索（大文字小文字区別、完全一致）
2. 一致が1つだけ → 置換実行 → 前後5行を返す
3. 一致が0 → エラー: "Could not find..."
4. 一致が2以上 → エラー: "Found N matches..."
```

### ファイルチェックポイント

- **全ての編集の前に**ファイル全体のスナップショットを保存
- Escape 2回 で巻き戻し可能
- 「undo last edit」で取り消し可能
- git commit とは独立

---

## 7. プランモード

### 動作

```
プランモード有効化
    │
    ▼
Write / Edit / Bash → 拒否（読み取り専用）
Read / Glob / Grep / WebFetch / WebSearch → 許可
    │
    ▼
Explore サブエージェント（Haiku、読み取り専用）で調査
    │
    ▼
計画を作成 → ユーザー承認 → プランモード解除 → 実装
```

### 有効化方法

```bash
claude --permission-mode plan       # 起動時
# または Shift+Tab 2回               # セッション中
```

---

## 8. セッション管理

### 保存構造

```
~/.claude/projects/{project-path-hash}/
  ├── {sessionId}.jsonl          # 会話全体（ユーザー + ツール + 結果）
  ├── {sessionId}/
  │   └── subagents/
  │       ├── agent-{id1}.jsonl
  │       └── agent-{id2}.jsonl
  └── memory/
      └── MEMORY.md
```

### 再開方法

```bash
claude --continue          # 直近のセッション（同じディレクトリ）
claude --resume            # 対話的に選択
claude --resume auth-work  # 名前で指定
claude --resume {id} --fork-session  # フォーク（元を残す）
```

---

## 9. CLAUDE.md と設定

### settings.json の構造

```json
{
  "$schema": "https://json.schemastore.org/claude-code-settings.json",

  "model": "sonnet",
  "availableModels": ["sonnet", "haiku"],

  "permissions": {
    "allow": ["Bash(npm test)", "Read(src/**)"],
    "deny": ["Bash(rm -rf)"],
    "ask": ["Bash(git push)"],
    "defaultMode": "plan"
  },

  "env": {
    "NODE_ENV": "development",
    "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1"
  },

  "hooks": {
    "PostToolUse": [{
      "matcher": "Edit|Write",
      "hooks": [{"type": "command", "command": "prettier --write"}]
    }]
  },

  "outputStyle": "Explanatory"
}
```

### 設定の優先順位（上が最優先）

```
1. 管理ポリシー（admin、上書き不可）
2. CLI フラグ
3. .claude/settings.local.json（個人、gitignore）
4. .claude/settings.json（チーム共有）
5. ~/.claude/settings.json（ユーザーグローバル）
```

### Rules システム

```
.claude/rules/
  ├── code-style.md          # 全ファイルに適用
  ├── typescript.md          # 無条件適用
  └── frontend/
      └── react.md           # パスフィルタ可能
```

パスフィルタ付きルール:
```yaml
---
paths:
  - "src/**/*.ts"
  - "lib/**/*.{ts,tsx}"
---
TypeScript のルール...
```

---

## 10. カスタムエージェント

### 作成方法

```bash
# CLI で対話的に作成
/agents  →  "Create new agent"  →  スコープ選択  →  自然言語で説明  →  保存

# 手動でファイル作成
~/.claude/agents/my-agent.md      # ユーザーレベル
.claude/agents/my-agent.md        # プロジェクトレベル
```

### 配置場所（優先順）

```
1. CLI フラグ: --agents '{"name": {...}}'  （セッション限定、最優先）
2. プロジェクト: .claude/agents/           （git 共有可能）
3. ユーザー: ~/.claude/agents/             （全プロジェクト）
4. プラグイン: plugin/agents/
```

---

## 11. Agent Teams（実験的）

### 有効化

```json
{ "env": { "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1" } }
```

### アーキテクチャ

```
チームリーダー（メインセッション）
  ├── チームメイト A（独立セッション、独自コンテキスト）
  ├── チームメイト B（独立セッション）
  └── チームメイト C（独立セッション）

共有リソース:
  ├── タスクリスト: ~/.claude/tasks/{team-name}/
  ├── チーム設定: ~/.claude/teams/{team-name}/config.json
  └── メッセージボックス（相互通信）
```

### サブエージェントとの違い

| | サブエージェント | Agent Teams |
|---|---|---|
| コンテキスト | 独立（結果のみ返却） | 完全独立 |
| 通信 | メインに報告のみ | メンバー間で直接通信 |
| コスト | 低い（結果要約） | 高い（各メンバーがフルセッション） |
| 用途 | 単発タスク | 複雑な協調作業 |

---

## 12. MCP 連携

### 設定方法

```bash
# HTTP/SSE リモートサーバー
claude mcp add --transport http github https://api.githubcopilot.com/mcp/

# ローカル stdio サーバー
claude mcp add --transport stdio myserver -- npx -y server-package

# JSON 直接指定
claude mcp add-json fs '{"type":"stdio","command":"mcp-filesystem-server"}'
```

### スコープ

```
local（デフォルト）: ~/.claude.json（プロジェクト単位）
project:            .mcp.json（git 共有）
user:               ~/.claude.json（グローバル）
```

### ツール検索（Tool Search）

MCP ツールが10%以上のコンテキストを消費する場合、自動的に **MCPSearch ツール** が有効になり、必要なツールだけをオンデマンドでロードする。

---

## 13. Git 連携

### 基本操作

Claude は Bash ツール経由で git コマンドを実行:
- `git status`, `git diff`, `git log`, `git commit` 等
- 権限ルールで制御可能

### ワークツリー

```bash
claude --worktree feature-auth
# → .claude/worktrees/feature-auth/ にプロジェクトコピーを作成
# → worktree-feature-auth ブランチで作業
# → 独立したセッション、独立したメモリ
```

---

## 14. スキルシステム

### SKILL.md フォーマット

```yaml
---
name: deploy
description: 本番環境へのデプロイ
disable-model-invocation: true    # ユーザーのみ起動可（自動起動なし）
user-invocable: true              # /deploy コマンドとして表示
allowed-tools: Bash,Edit
context: fork                     # サブエージェントで実行
agent: Explore
model: sonnet
---

$ARGUMENTS に基づいてデプロイを実行:
1. テスト実行
2. ビルド
3. デプロイ
4. 検証
```

### 変数

| 変数 | 内容 |
|---|---|
| `$ARGUMENTS` | 全引数 |
| `$0`, `$1`, `$2` | インデックス指定 |
| `${CLAUDE_SESSION_ID}` | セッションID |
| `` !`command` `` | コマンド実行結果を注入 |

### 配置場所

```
.claude/skills/{name}/SKILL.md    # プロジェクト（最優先）
~/.claude/skills/{name}/SKILL.md  # ユーザー
plugin/skills/{name}/SKILL.md     # プラグイン
```

---

## 15. フックシステム

### イベントライフサイクル

```
SessionStart
  └→ UserPromptSubmit
       └→ PreToolUse（ブロック可能）
            └→ [ツール実行]
                 └→ PostToolUse（ブロック可能）/ PostToolUseFailure
                      └→ （PreToolUse に戻る）
       └→ PermissionRequest（自動承認可能）
       └→ Notification
  └→ Stop（ブロック可能）
SessionEnd
```

### フック定義

```json
{
  "hooks": {
    "PostToolUse": [{
      "matcher": "Edit|Write",
      "hooks": [
        { "type": "command", "command": "prettier --write $FILE" }
      ]
    }],
    "PreToolUse": [{
      "matcher": "Bash",
      "hooks": [
        { "type": "command", "command": "./validate-command.sh" }
      ]
    }]
  }
}
```

### 入出力（JSON via stdin/stdout）

```json
// フックへの入力:
{
  "session_id": "abc123",
  "cwd": "/path/to/project",
  "hook_event_name": "PreToolUse",
  "tool_name": "Bash",
  "tool_input": { "command": "npm test" }
}

// フックの出力:
// exit 0 → 許可（stdoutにJSON可）
// exit 2 → ブロック（stderrに理由）
```

---

## 16. 自動メモリ

### 保存場所

```
~/.claude/projects/{project}/memory/
  ├── MEMORY.md              # 索引（先頭200行がセッション開始時に自動ロード）
  ├── debugging.md           # トピック別（オンデマンドロード）
  └── architecture.md
```

### 動作原理

1. セッション開始時に `MEMORY.md` の先頭200行をシステムプロンプトに注入
2. Claude はファイルツールで記憶ファイルを読み書き可能
3. 重要な発見・決定事項を自動的に記録
4. セッション間で永続化（プロジェクト固有）

---

## 17. 権限システム

### ルール構文

```json
{
  "permissions": {
    "allow": [
      "Read",
      "Bash(npm run test)",
      "Bash(npm run *)",
      "WebFetch(domain:github.com)"
    ],
    "deny": [
      "Bash(rm -rf)",
      "Read(.env)",
      "Read(./secrets/**)"
    ],
    "ask": [
      "Bash(git push *)"
    ]
  }
}
```

### 権限モード

| モード | 動作 |
|---|---|
| `default` | 許可されていないものはユーザーに確認 |
| `acceptEdits` | ファイル変更は自動承認 |
| `dontAsk` | 未許可のツールは黙って拒否 |
| `bypassPermissions` | 全チェックをスキップ（危険） |
| `plan` | 読み取り専用を強制 |

---

## 18. ローカル再現に必要なもの

### 実装すべきコンポーネント

```
┌─────────────────────────────────────────────────────┐
│ 1. LLMインターフェース                                │
│    ツール定義付きでローカルLLMにリクエスト送信            │
│    vLLM の OpenAI互換 API を使用                      │
├─────────────────────────────────────────────────────┤
│ 2. エージェントループ                                  │
│    LLM応答を解析 → ツール呼出 or テキスト応答を判定      │
│    ツール実行 → 結果をコンテキストに追加 → 再送信         │
│    ～400行のPythonで最小実装可能                        │
├─────────────────────────────────────────────────────┤
│ 3. ツール層（最低4つ + 拡張4つ）                       │
│    read / write / edit / bash                        │
│    glob / grep / git / spawn_agent                   │
│    各ツールの入出力スキーマを正確に実装                  │
├─────────────────────────────────────────────────────┤
│ 4. Edit のフォールバックマッチング                      │
│    完全一致 → 空白無視 → ファジー(Levenshtein)          │
│    失敗時は詳細エラーをLLMに返して再試行させる           │
├─────────────────────────────────────────────────────┤
│ 5. ファイルチェックポイント                             │
│    編集前のスナップショット保存                         │
│    巻き戻し機能                                      │
├─────────────────────────────────────────────────────┤
│ 6. コンテキスト管理                                   │
│    トークンカウント                                   │
│    95%到達で自動圧縮                                  │
│    古いツール出力削除 → 会話要約                       │
├─────────────────────────────────────────────────────┤
│ 7. セッション永続化                                   │
│    JSONL形式で会話を保存                              │
│    --continue / --resume で復元                      │
├─────────────────────────────────────────────────────┤
│ 8. 記憶システム                                      │
│    MEMORY.md（先頭200行を自動ロード）                  │
│    トピック別ファイル                                 │
│    セッション間で永続化                               │
├─────────────────────────────────────────────────────┤
│ 9. 設定ファイル階層                                   │
│    CLAUDE.md 相当の指示書ロード                       │
│    settings.json 相当の設定マージ                     │
│    deny → ask → allow の権限評価                     │
├─────────────────────────────────────────────────────┤
│ 10. フックシステム                                    │
│     PreToolUse / PostToolUse イベントディスパッチ      │
│     外部コマンド実行（JSON stdin/stdout）             │
│     exit 0 = 許可 / exit 2 = ブロック                │
├─────────────────────────────────────────────────────┤
│ 11. サブエージェント                                  │
│     独立コンテキストで Task を実行                     │
│     結果を要約してメインに返却                        │
│     ネスト不可                                       │
├─────────────────────────────────────────────────────┤
│ 12. TUI（ターミナルUI）                               │
│     入力、出力表示、ツール実行表示                     │
│     Escape で中断                                    │
│     Rich/Textual(Python) or Bubble Tea(Go)           │
└─────────────────────────────────────────────────────┘
```

### 実装の優先順位

```
Phase 1（MVP - 最小動作版）
  ├── エージェントループ（ReActパターン、～400行）
  ├── 4つの基本ツール（read, write, edit, bash）
  ├── vLLM 連携（OpenAI互換API + ツール呼び出し）
  └── 基本的な CLI 入出力

Phase 2（実用版）
  ├── glob, grep ツール追加
  ├── コンテキスト圧縮
  ├── セッション保存/復元
  ├── 設定ファイル（.agent.md）ロード
  └── Edit のフォールバックマッチング

Phase 3（Claude Code 互換）
  ├── サブエージェント
  ├── 記憶システム（MEMORY.md パターン）
  ├── フックシステム
  ├── 権限システム（deny/ask/allow）
  ├── ファイルチェックポイント
  └── TUI の改善

Phase 4（フル機能）
  ├── スキルシステム
  ├── MCP 連携
  ├── Agent Teams
  ├── プランモード
  └── プラグインシステム
```
