# Claude Code マスターガイド

> Claude Code の使い方・設定・Tips を網羅した包括的リファレンス（2026年2月版）

---

## 目次

- [1. はじめに](#1-はじめに)
- [2. CLAUDE.md の書き方と活用](#2-claudemd-の書き方と活用)
- [3. カスタムエージェント (.claude/agents/)](#3-カスタムエージェント-claudeagents)
- [4. スキル (.claude/skills/)](#4-スキル-claudeskills)
- [5. フック (.claude/hooks/)](#5-フック-claudehooks)
- [6. MCP サーバー連携](#6-mcp-サーバー連携)
- [7. 権限・セキュリティ設定](#7-権限セキュリティ設定)
- [8. ワークフロー・使い方 Tips](#8-ワークフロー使い方-tips)
- [9. パフォーマンス・コスト最適化](#9-パフォーマンスコスト最適化)
- [10. IDE 連携](#10-ide-連携)
- [11. トラブルシューティング](#11-トラブルシューティング)
- [12. 高度な使い方](#12-高度な使い方)
- [13. 実際のユースケース・事例](#13-実際のユースケース事例)
- [14. 情報源・参考リンク](#14-情報源参考リンク)

---

## 1. はじめに

Claude Code は Anthropic が提供するエージェント型コーディング環境である。従来のチャットボットとは異なり、ファイルの読み書き、コマンド実行、コード変更を自律的に行うことができる。ユーザーは「何を作りたいか」を記述するだけで、Claude がコードベースの探索、計画立案、実装まで一貫して行う。

### Claude Code の核心的な原則

Claude Code を効果的に使いこなす上で最も重要な原則は **コンテキストウィンドウの管理** である。Claude のコンテキストウィンドウには会話全体、読み込んだファイル、コマンド出力が全て含まれ、これが埋まるにつれてパフォーマンスが低下する。このガイドで紹介するベストプラクティスの多くは、この制約を踏まえたものである [^1]。

---

## 2. CLAUDE.md の書き方と活用

### 2.1 CLAUDE.md とは

CLAUDE.md は Claude Code が毎回の会話の開始時に読み込む特別なファイルであり、ビルドコマンド、コードスタイル、ワークフロールールなど、コードだけからは推測できない永続的なコンテキストを Claude に提供する [^1][^2]。

### 2.2 CLAUDE.md の階層構造

CLAUDE.md は以下の場所に配置でき、それぞれスコープが異なる：

| 配置場所 | スコープ | 用途 |
|---------|---------|------|
| `~/.claude/CLAUDE.md` | 全プロジェクト共通 | 個人の開発スタイル、グローバル設定 |
| `./CLAUDE.md`（プロジェクトルート） | プロジェクト全体 | git にコミットしてチームで共有 |
| `./CLAUDE.local.md` | プロジェクトローカル | `.gitignore` に追加して個人用に |
| 親ディレクトリの `CLAUDE.md` | モノレポ用 | `root/CLAUDE.md` と `root/foo/CLAUDE.md` の両方が読み込まれる |
| 子ディレクトリの `CLAUDE.md` | ディレクトリ固有 | そのディレクトリのファイル作業時にオンデマンドで読み込み |

### 2.3 効果的な CLAUDE.md の書き方

#### 基本原則

- **50〜100行に収める**：ルートの CLAUDE.md は簡潔に。詳細は `@import` で分割する [^2]
- **毎セッション読み込まれる**ことを意識し、広く適用される内容のみ記載
- **各行について「この行がなければ Claude はミスをするか？」と自問**し、不要なら削除
- 強調が必要な場合は `IMPORTANT` や `YOU MUST` で優先度を上げる

#### 書くべき内容と書くべきでない内容

| 書くべき内容 | 書くべきでない内容 |
|-------------|-------------------|
| Claude が推測できない Bash コマンド | コードを読めばわかること |
| デフォルトと異なるコードスタイルルール | 標準的な言語規約（Claude は既に知っている） |
| テスト手順とテストランナーの指定 | 詳細な API ドキュメント（リンクで代替） |
| リポジトリの作法（ブランチ命名、PR 規約） | 頻繁に変わる情報 |
| プロジェクト固有のアーキテクチャ判断 | 長い説明やチュートリアル |
| 開発環境の癖（必要な環境変数など） | ファイルごとのコードベース説明 |
| よくあるゴッチャや非自明な挙動 | 「きれいなコードを書け」のような自明な指示 |

#### 実践的な CLAUDE.md の例

```markdown
# Code style
- Use ES modules (import/export) syntax, not CommonJS (require)
- Destructure imports when possible (eg. import { foo } from 'bar')

# Workflow
- Be sure to typecheck when you're done making a series of code changes
- Prefer running single tests, and not the whole test suite, for performance

# Build commands
- Test: `npm run test -- --watch=false`
- Lint: `npm run lint`
- Typecheck: `npx tsc --noEmit`

# Architecture
- Backend: Express.js with TypeScript
- Database: PostgreSQL with Prisma ORM
- Frontend: React 19 with Vite

# IMPORTANT
- NEVER commit directly to main. Always create a feature branch.
- YOU MUST run tests before creating a PR.
```

#### @import による外部ファイルの読み込み

```markdown
See @README.md for project overview and @package.json for available npm commands.

# Additional Instructions
- Git workflow: @docs/git-instructions.md
- Personal overrides: @~/.claude/my-project-instructions.md
```

### 2.4 CLAUDE.md のアンチパターン

1. **巨大すぎる CLAUDE.md**：長すぎるとルールが埋もれて無視される。Claude が何度も同じミスをする場合、ファイルが長すぎてルールが見落とされている可能性がある
2. **自明な内容の記載**：Claude が既に知っている標準規約を繰り返し書くのは無駄
3. **頻繁に変わる情報の記載**：バージョン番号やデプロイURL等は環境変数やスクリプトで管理すべき
4. **CLAUDE.md をフック代わりに使う**：毎回必ず実行されるべき処理はフックで保証する [^1]

#### `/init` コマンドの活用

`/init` を実行すると、現在のプロジェクト構造を分析してスターターの CLAUDE.md を自動生成してくれる。日本語で生成したい場合は `/init "日本語で作成してください"` とパラメータを追加する [^3]。

---

## 3. カスタムエージェント (.claude/agents/)

### 3.1 サブエージェントとは

サブエージェントは特定のタスクを処理する専門AIアシスタントである。各サブエージェントは独自のコンテキストウィンドウ、カスタムシステムプロンプト、特定のツールアクセス、独立した権限を持つ [^4][^5]。

サブエージェントのメリット：
- **コンテキストの保持**：探索や実装をメイン会話から分離
- **制約の強制**：使用可能なツールを限定
- **設定の再利用**：ユーザーレベルのエージェントで全プロジェクトに適用
- **動作の特化**：特定ドメインに集中したシステムプロンプト
- **コスト制御**：Haiku 等の安価なモデルにタスクをルーティング

### 3.2 ビルトインサブエージェント

| エージェント | モデル | ツール | 用途 |
|------------|-------|--------|------|
| **Explore** | Haiku（高速） | 読み取り専用 | ファイル検索、コード分析、コードベース探索 |
| **Plan** | 継承 | 読み取り専用 | プランモード時のコードベースリサーチ |
| **General-purpose** | 継承 | 全ツール | 複雑な調査、マルチステップ操作、コード変更 |
| **Bash** | 継承 | - | 別コンテキストでのターミナルコマンド実行 |

### 3.3 YAML Frontmatter の全オプション

サブエージェントは Markdown ファイルに YAML frontmatter を付けて定義する。

```yaml
---
name: code-reviewer           # 必須: 一意の識別子（小文字・ハイフン）
description: Reviews code     # 必須: いつ委譲すべきかの説明
tools: Read, Glob, Grep       # 任意: 使用可能ツール（省略時は全ツール継承）
disallowedTools: Write, Edit  # 任意: 拒否ツール
model: sonnet                 # 任意: sonnet / opus / haiku / inherit
permissionMode: default       # 任意: default / acceptEdits / dontAsk / bypassPermissions / plan
maxTurns: 50                  # 任意: 最大ターン数
skills:                       # 任意: 起動時にロードするスキル
  - api-conventions
mcpServers:                   # 任意: 利用可能な MCP サーバー
  - slack
hooks:                        # 任意: ライフサイクルフック
  PreToolUse:
    - matcher: "Bash"
      hooks:
        - type: command
          command: "./scripts/validate.sh"
memory: user                  # 任意: user / project / local（永続メモリ）
background: false             # 任意: バックグラウンド実行
isolation: worktree           # 任意: worktree で隔離実行
---

You are a code reviewer. Analyze code and provide actionable feedback.
```

### 3.4 エージェントの配置場所と優先度

| 配置場所 | スコープ | 優先度 |
|---------|---------|--------|
| `--agents` CLI フラグ | 現在のセッション | 1（最高） |
| `.claude/agents/` | プロジェクト | 2 |
| `~/.claude/agents/` | 全プロジェクト | 3 |
| プラグインの `agents/` | プラグイン有効時 | 4（最低） |

### 3.5 実践的なエージェント例

#### セキュリティレビューアー

```markdown
---
name: security-reviewer
description: Reviews code for security vulnerabilities
tools: Read, Grep, Glob, Bash
model: opus
---

You are a senior security engineer. Review code for:
- Injection vulnerabilities (SQL, XSS, command injection)
- Authentication and authorization flaws
- Secrets or credentials in code
- Insecure data handling

Provide specific line references and suggested fixes.
```

#### デバッガー

```markdown
---
name: debugger
description: Debugging specialist for errors, test failures, and unexpected behavior
tools: Read, Edit, Bash, Grep, Glob
---

You are an expert debugger specializing in root cause analysis.

When invoked:
1. Capture error message and stack trace
2. Identify reproduction steps
3. Isolate the failure location
4. Implement minimal fix
5. Verify solution works
```

#### CLI からの一時的なエージェント定義

```bash
claude --agents '{
  "code-reviewer": {
    "description": "Expert code reviewer. Use proactively after code changes.",
    "prompt": "You are a senior code reviewer.",
    "tools": ["Read", "Grep", "Glob", "Bash"],
    "model": "sonnet"
  }
}'
```

### 3.6 エージェントの管理

- `/agents` コマンドでインタラクティブに管理（表示、作成、編集、削除）
- `claude agents` でコマンドラインから一覧表示
- セッション開始時にロードされるため、手動追加後はセッション再起動が必要

---

## 4. スキル (.claude/skills/)

### 4.1 スキルとは

スキルは Claude の能力を拡張するツールである。`SKILL.md` ファイルに指示を書くだけで Claude のツールキットに追加される。関連するタスクのとき Claude が自動的に使用するか、`/skill-name` で直接呼び出せる [^6]。

> **注意**: 以前の `.claude/commands/` は skills に統合された。既存のコマンドファイルはそのまま動作するが、スキルにはディレクトリサポート、frontmatter、自動ロード等の追加機能がある。

### 4.2 スキルの配置場所

| レベル | パス | スコープ |
|-------|------|---------|
| エンタープライズ | マネージド設定 | 組織全体 |
| 個人 | `~/.claude/skills/<name>/SKILL.md` | 全プロジェクト |
| プロジェクト | `.claude/skills/<name>/SKILL.md` | このプロジェクトのみ |
| プラグイン | `<plugin>/skills/<name>/SKILL.md` | プラグイン有効時 |

### 4.3 SKILL.md の書き方

```yaml
---
name: fix-issue                    # 任意: /コマンド名（省略時はディレクトリ名）
description: Fix a GitHub issue    # 推奨: 使用タイミングの説明
argument-hint: "[issue-number]"    # 任意: 引数のヒント
disable-model-invocation: true     # 任意: Claude の自動呼び出しを無効化
user-invocable: true               # 任意: /メニューから非表示にする場合 false
allowed-tools: Read, Grep          # 任意: 許可ツール
model: sonnet                      # 任意: 使用モデル
context: fork                      # 任意: fork でサブエージェント実行
agent: Explore                     # 任意: context: fork 時のエージェント型
hooks:                             # 任意: スキルスコープのフック
  PreToolUse:
    - matcher: "Bash"
      hooks:
        - type: command
          command: "./scripts/check.sh"
---

Fix GitHub issue $ARGUMENTS following our coding standards.

1. Read the issue description with `gh issue view $ARGUMENTS`
2. Understand the requirements
3. Implement the fix
4. Write tests
5. Create a commit
```

### 4.4 文字列置換

| 変数 | 説明 |
|------|------|
| `$ARGUMENTS` | スキル呼び出し時に渡された全引数 |
| `$ARGUMENTS[N]` / `$N` | N番目の引数（0始まり） |
| `${CLAUDE_SESSION_ID}` | 現在のセッションID |

### 4.5 動的コンテキスト注入

`` !`command` `` 構文でシェルコマンドを事前実行し、結果をスキルに埋め込める：

```yaml
---
name: pr-summary
description: Summarize changes in a pull request
context: fork
agent: Explore
---

## Pull request context
- PR diff: !`gh pr diff`
- PR comments: !`gh pr view --comments`
- Changed files: !`gh pr diff --name-only`

## Your task
Summarize this pull request...
```

### 4.6 呼び出し制御

| frontmatter | ユーザー | Claude | コンテキスト |
|-------------|---------|--------|-------------|
| （デフォルト） | 呼び出し可 | 呼び出し可 | 説明文は常にコンテキスト内、本文は呼び出し時にロード |
| `disable-model-invocation: true` | 呼び出し可 | 呼び出し不可 | 説明文もコンテキスト外 |
| `user-invocable: false` | 呼び出し不可 | 呼び出し可 | 説明文は常にコンテキスト内 |

### 4.7 実用的なスキル例

#### API 規約スキル（参照型）

```yaml
---
name: api-conventions
description: REST API design conventions for our services
---

# API Conventions
- Use kebab-case for URL paths
- Use camelCase for JSON properties
- Always include pagination for list endpoints
- Version APIs in the URL path (/v1/, /v2/)
```

#### デプロイスキル（タスク型）

```yaml
---
name: deploy
description: Deploy the application to production
context: fork
disable-model-invocation: true
---

Deploy the application:
1. Run the test suite
2. Build the application
3. Push to the deployment target
4. Verify the deployment succeeded
```

#### コードベース可視化スキル

スキルディレクトリにスクリプトをバンドルし、インタラクティブなHTMLビジュアライゼーションを生成する高度なパターンも可能 [^6]。

---

## 5. フック (.claude/hooks/)

### 5.1 フックとは

フックは Claude Code のライフサイクルの特定ポイントで自動的にシェルコマンドや LLM プロンプトを実行するユーザー定義のトリガーである。CLAUDE.md の指示が「推奨」なのに対し、フックは **決定論的で確実に実行される** [^7][^8]。

### 5.2 フックイベント一覧

| イベント | 発火タイミング | ブロック可能 |
|---------|--------------|-------------|
| `SessionStart` | セッション開始・再開時 | No |
| `UserPromptSubmit` | プロンプト送信時（処理前） | Yes |
| `PreToolUse` | ツール呼び出し実行前 | Yes |
| `PermissionRequest` | 権限ダイアログ表示時 | Yes |
| `PostToolUse` | ツール呼び出し成功後 | No |
| `PostToolUseFailure` | ツール呼び出し失敗後 | No |
| `Notification` | 通知送信時 | No |
| `SubagentStart` | サブエージェント起動時 | No |
| `SubagentStop` | サブエージェント完了時 | Yes |
| `Stop` | Claude の応答完了時 | Yes |
| `TeammateIdle` | チームメイトがアイドルになる時 | Yes |
| `TaskCompleted` | タスク完了マーク時 | Yes |
| `ConfigChange` | 設定ファイル変更時 | Yes |
| `WorktreeCreate` | ワークツリー作成時 | Yes |
| `WorktreeRemove` | ワークツリー削除時 | No |
| `PreCompact` | コンテキスト圧縮前 | No |
| `SessionEnd` | セッション終了時 | No |

### 5.3 設定場所

| 場所 | スコープ | 共有可能 |
|-----|---------|---------|
| `~/.claude/settings.json` | 全プロジェクト | No |
| `.claude/settings.json` | プロジェクト | Yes（git コミット） |
| `.claude/settings.local.json` | プロジェクト（ローカル） | No（gitignore） |
| マネージドポリシー | 組織全体 | Yes |
| プラグインの `hooks/hooks.json` | プラグイン有効時 | Yes |
| スキル/エージェントの frontmatter | コンポーネント実行中 | Yes |

### 5.4 設定構造

フック設定は3階層のネスト構造：

```json
{
  "hooks": {
    "PostToolUse": [           // 1. フックイベント
      {
        "matcher": "Write|Edit",  // 2. マッチャーグループ（正規表現）
        "hooks": [                // 3. フックハンドラー
          {
            "type": "command",
            "command": "npx prettier --write $(jq -r '.tool_input.file_path')"
          }
        ]
      }
    ]
  }
}
```

### 5.5 フックハンドラーの種類

| タイプ | 説明 | デフォルトタイムアウト |
|-------|------|---------------------|
| `command` | シェルコマンドを実行 | 600秒 |
| `prompt` | LLM に単発評価を依頼 | 30秒 |
| `agent` | ツール付きサブエージェントを起動 | 60秒 |

### 5.6 終了コードの意味

| 終了コード | 意味 |
|-----------|------|
| **0** | 成功。stdout の JSON を解析 |
| **2** | ブロッキングエラー。stderr がエラーメッセージとしてフィードバック |
| **その他** | 非ブロッキングエラー。verbose モードで表示 |

### 5.7 実用的なフック例

#### ファイル編集後に自動フォーマット

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Write|Edit",
        "hooks": [
          {
            "type": "command",
            "command": "jq -r '.tool_input.file_path' | xargs npx prettier --write 2>/dev/null; exit 0"
          }
        ]
      }
    ]
  }
}
```

#### 危険なコマンドのブロック

```bash
#!/bin/bash
# .claude/hooks/block-rm.sh
COMMAND=$(jq -r '.tool_input.command')

if echo "$COMMAND" | grep -q 'rm -rf'; then
  jq -n '{
    hookSpecificOutput: {
      hookEventName: "PreToolUse",
      permissionDecision: "deny",
      permissionDecisionReason: "Destructive command blocked by hook"
    }
  }'
else
  exit 0
fi
```

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": ".claude/hooks/block-rm.sh"
          }
        ]
      }
    ]
  }
}
```

#### ESLint の自動実行

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Write|Edit",
        "hooks": [
          {
            "type": "command",
            "command": "jq -r '.tool_input.file_path' | xargs npx eslint --fix 2>/dev/null; exit 0"
          }
        ]
      }
    ]
  }
}
```

#### 停止前の品質チェック（プロンプトフック）

```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "prompt",
            "prompt": "Evaluate if Claude should stop: $ARGUMENTS. Check if all tasks are complete and tests pass.",
            "timeout": 30
          }
        ]
      }
    ]
  }
}
```

#### 非同期テスト実行

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Write|Edit",
        "hooks": [
          {
            "type": "command",
            "command": "\"$CLAUDE_PROJECT_DIR\"/.claude/hooks/run-tests-async.sh",
            "async": true,
            "timeout": 300
          }
        ]
      }
    ]
  }
}
```

### 5.8 フック管理

- `/hooks` でインタラクティブに管理
- 設定の直接編集は次回セッション開始時に反映
- `"disableAllHooks": true` で一時的に全フックを無効化

---

## 6. MCP サーバー連携

### 6.1 MCP とは

MCP（Model Context Protocol）は AI ツール統合のためのオープンソース標準である。MCP サーバーを接続することで、Claude Code から外部ツール、データベース、API にアクセスできる [^9]。

### 6.2 MCP サーバーの追加方法

#### HTTP サーバー（推奨）

```bash
# 基本構文
claude mcp add --transport http <name> <url>

# 例: Notion に接続
claude mcp add --transport http notion https://mcp.notion.com/mcp

# Bearer トークン付き
claude mcp add --transport http secure-api https://api.example.com/mcp \
  --header "Authorization: Bearer your-token"
```

#### SSE サーバー（非推奨、HTTP を推奨）

```bash
claude mcp add --transport sse asana https://mcp.asana.com/sse
```

#### ローカル stdio サーバー

```bash
claude mcp add --transport stdio --env AIRTABLE_API_KEY=YOUR_KEY airtable \
  -- npx -y airtable-mcp-server
```

### 6.3 人気の MCP サーバー

| サーバー | 用途 |
|---------|------|
| **GitHub** | PR管理、イシュー操作、コードレビュー |
| **Sentry** | エラートラッキング、パフォーマンス監視 |
| **Notion** | ドキュメント管理、ナレッジベース |
| **PostgreSQL (dbhub)** | データベースクエリ |
| **Figma** | デザインアセット取得 |
| **Slack** | チーム連携、通知 |
| **Playwright** | ブラウザテスト自動化 |
| **Perplexity** | ウェブリサーチ |
| **Sequential Thinking** | 複雑な問題分解 |
| **Context7** | 最新ドキュメント参照 |

### 6.4 スコープ管理

| スコープ | 保存場所 | 共有 |
|---------|---------|------|
| `local`（デフォルト） | `~/.claude.json` | 個人・プロジェクト固有 |
| `project` | `.mcp.json`（プロジェクトルート） | git でチーム共有 |
| `user` | `~/.claude.json` | 個人・全プロジェクト |

```bash
# プロジェクトスコープで追加
claude mcp add --transport http paypal --scope project https://mcp.paypal.com/mcp

# ユーザースコープで追加
claude mcp add --transport http hubspot --scope user https://mcp.hubspot.com/anthropic
```

### 6.5 MCP 管理コマンド

```bash
claude mcp list              # 一覧表示
claude mcp get github        # 詳細確認
claude mcp remove github     # 削除
/mcp                         # Claude Code 内でステータス確認・OAuth認証
```

### 6.6 `.mcp.json` での環境変数展開

```json
{
  "mcpServers": {
    "api-server": {
      "type": "http",
      "url": "${API_BASE_URL:-https://api.example.com}/mcp",
      "headers": {
        "Authorization": "Bearer ${API_KEY}"
      }
    }
  }
}
```

### 6.7 Claude Code 自体を MCP サーバーとして使う

```bash
claude mcp serve
```

Claude Desktop から接続する設定：

```json
{
  "mcpServers": {
    "claude-code": {
      "type": "stdio",
      "command": "claude",
      "args": ["mcp", "serve"],
      "env": {}
    }
  }
}
```

---

## 7. 権限・セキュリティ設定

### 7.1 権限モード

| モード | 動作 |
|-------|------|
| `default` | 標準的な権限チェック（プロンプト表示） |
| `acceptEdits` | ファイル編集を自動承認 |
| `plan` | 読み取り専用（プランモード） |
| `dontAsk` | 権限プロンプトを自動拒否（明示許可ツールは動作） |
| `bypassPermissions` | 全権限チェックをスキップ |

### 7.2 allowedTools の設定

`/permissions` コマンドで許可するツールをホワイトリスト設定できる：

```json
{
  "permissions": {
    "allow": [
      "Bash(npm run lint)",
      "Bash(npm run test *)",
      "Bash(git commit *)",
      "Bash(git push *)",
      "Read",
      "Glob",
      "Grep"
    ],
    "deny": [
      "Bash(rm -rf *)",
      "Task(Explore)"
    ]
  }
}
```

パーミッションルール構文：
- `Bash(npm test)` - 完全一致
- `Bash(git diff *)` - プレフィックスマッチ（`*` の前にスペースが必要）
- `Skill(commit)` - スキルの制御

### 7.3 --dangerously-skip-permissions

```bash
claude --dangerously-skip-permissions
```

**用途**: CI/CD パイプライン、リンティング修正、ボイラープレート生成など、承認なしで連続実行したい場合 [^10]。

**重要な注意事項**:
- 任意のコマンドが実行可能になるため、**データ損失、システム破損、データ流出のリスク**がある
- **インターネットアクセスのないサンドボックス環境でのみ使用すべき**
- Docker コンテナ等の隔離環境が推奨
- 本番の開発マシンでの使用は避ける

**安全な代替アプローチ**:

```bash
# allowedTools でツールを制限して使う
claude -p "Fix lint errors" \
  --allowedTools "Read,Edit,Bash(npm run lint *)"
```

### 7.4 サンドボックス

`/sandbox` でOS レベルの分離を有効化。ファイルシステムとネットワークアクセスを制限しつつ、定義された境界内で自由に作業できる。

---

## 8. ワークフロー・使い方 Tips

### 8.1 効果的なプロンプトの書き方

#### 自己検証手段を与える（最も効果的）

Claude に自分の作業を検証する手段を与えることが、最も効果の高い方法である [^1]。

| 戦略 | Before | After |
|------|--------|-------|
| **検証基準を提供** | "メール検証関数を実装して" | "validateEmail 関数を書いて。テストケース: user@example.com は true、invalid は false。実装後にテストを実行して" |
| **UI を視覚的に検証** | "ダッシュボードを良くして" | "[スクショ貼付] このデザインを実装して。結果のスクショを撮って元と比較し、差分を修正して" |
| **根本原因に対処** | "ビルドが失敗している" | "このエラーでビルドが失敗する: [エラー貼付]。修正してビルド成功を確認して。エラーを抑制せず根本原因に対処して" |

#### 具体的なコンテキストを与える

```
# Bad
"テストを追加して"

# Good
"foo.py のユーザーがログアウトしたケースのテストを書いて。モックは避けて。"

# Bad
"カレンダーウィジェットを追加して"

# Good
"ホームページの既存ウィジェットの実装を見てパターンを理解して。
HotDogWidget.php が良い例。そのパターンに従って新しいカレンダーウィジェットを実装して。"
```

#### リッチコンテンツの提供方法

- **`@` でファイル参照**：`@src/auth/login.ts` で直接参照
- **画像を貼り付け**：コピー&ペーストまたはドラッグ&ドロップ
- **URL を提供**：ドキュメントや API リファレンスのリンク
- **パイプでデータ入力**：`cat error.log | claude`

#### 思考の深さを制御するトリガーワード

| ワード | 思考トークン数 | 用途 |
|-------|--------------|------|
| `"think"` | 約4K | 簡単な問題 |
| `"think hard"` / `"megathink"` | 約10K | 中程度の問題 |
| `"ultrathink"` | 約32K | 複雑なアーキテクチャ決定、深い分析 |

> **注意**: 2026年現在、extended thinking はデフォルトで有効になっており、`/effort` コマンドで低/中/高/最大を制御できる [^11]。

### 8.2 探索 → 計画 → 実装 → コミットの4フェーズ

```
1. 【探索】プランモードで読み取り専用調査
   > read /src/auth and understand how we handle sessions

2. 【計画】実装計画の作成
   > I want to add Google OAuth. What files need to change? Create a plan.
   (Ctrl+G で計画をエディタで編集可能)

3. 【実装】ノーマルモードで実装
   > implement the OAuth flow from your plan. Write tests and fix failures.

4. 【コミット】
   > commit with a descriptive message and open a PR
```

### 8.3 Claude にインタビューさせる

大きな機能の場合、最初にClaudeにインタビューさせると効果的：

```
I want to build [brief description]. Interview me in detail using the
AskUserQuestion tool.

Ask about technical implementation, UI/UX, edge cases, concerns, and
tradeoffs. Don't ask obvious questions, dig into the hard parts.

Keep interviewing until we've covered everything, then write a complete
spec to SPEC.md.
```

仕様完成後、新しいセッションで実装すると、クリーンなコンテキストで作業できる。

### 8.4 セッション管理

#### 早めの軌道修正

- **`Esc`**: Claude を途中で停止（コンテキストは保持）
- **`Esc + Esc`** / **`/rewind`**: リワインドメニューで前の状態に復元
- **`"Undo that"`**: Claude に変更を取り消させる
- **`/clear`**: 無関係なタスク間でコンテキストをリセット

**2回同じ問題を修正しても直らなければ `/clear` して、学んだことを含むより良いプロンプトで再スタート** [^1]。

#### コンテキストの積極的な管理

```bash
/clear                        # タスク間でコンテキスト全リセット
/compact                      # 会話を要約して圧縮
/compact Focus on API changes # 指示付き圧縮
```

- `/rewind` → メッセージチェックポイントを選択 → 「Summarize from here」で部分圧縮
- CLAUDE.md に圧縮指示を追加：`"When compacting, always preserve the full list of modified files and any test commands"`

#### セッションの再開

```bash
claude --continue    # 最新の会話を再開
claude --resume      # 最近のセッションから選択
/rename              # セッションに名前を付ける（例: "oauth-migration"）
```

### 8.5 サブエージェントの活用

コンテキストが最も重要なリソースであるため、サブエージェントの活用が強力：

```
# 調査の委譲（メインコンテキストを汚さない）
Use subagents to investigate how our auth system handles token refresh

# 並行リサーチ
Research the authentication, database, and API modules in parallel
using separate subagents

# 検証
Use a subagent to review this code for edge cases
```

### 8.6 Git ワークフロー（ワークツリー・並行作業）

Git Worktree を使うと、同じリポジトリの複数ブランチを同時にチェックアウトし、各ブランチで独立した Claude セッションを実行できる [^12]。

#### ワークツリーの作成と使用

```bash
# --worktree フラグでワークツリーを作成して Claude を起動
claude --worktree feature-auth

# サブエージェントでワークツリー隔離
# エージェントの frontmatter に isolation: worktree を追加
```

#### 並行開発パターン

1. **Session A**: Feature X を実装
2. **Session B**: Bug fix Y を修正
3. **Session C**: テスト追加

各セッションは独立したファイルとブランチで作業し、互いに干渉しない。

#### Writer/Reviewer パターン

| Session A（Writer） | Session B（Reviewer） |
|--------------------|----------------------|
| `Implement a rate limiter` | |
| | `Review the rate limiter in @src/middleware/rateLimiter.ts` |
| `Here's the review feedback: [output]. Address these issues.` | |

### 8.7 CI/CD 統合（GitHub Actions）

```yaml
name: Claude Code Review
on:
  pull_request:
    types: [opened, synchronize]

jobs:
  review:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Install Claude Code
        run: npm install -g @anthropic-ai/claude-code
      - name: Review PR
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
        run: |
          claude -p "Review the changes in this PR for code quality,
          security issues, and best practices. Provide actionable feedback." \
          --output-format json \
          --allowedTools "Read,Grep,Glob,Bash(git diff *)"
```

### 8.8 テスト駆動開発との組み合わせ

```
1. "Write failing tests for the new user registration feature"
2. "Now implement the code to make all tests pass"
3. "Refactor the implementation while keeping tests green"
```

別セッションでテストを書き、別セッションで実装するパターンも効果的。

### 8.9 チーム開発での活用

- **CLAUDE.md を git にコミット**してチームで共有
- **プロジェクトスコープのスキルとエージェント**を `.claude/` に配置
- **`.mcp.json`** でMCPサーバー設定を共有
- **コードレビューのためのサブエージェント**を標準化

---

## 9. パフォーマンス・コスト最適化

### 9.1 モデル選択と切り替え

タクティカルなモデル切り替えでコストを60〜80%削減できる [^13]。

| モデル | 特徴 | コスト | 推奨用途 |
|-------|------|-------|---------|
| **Sonnet** | Opus の90%の能力、2倍の速度 | 中 | デフォルト。大半の開発タスク |
| **Haiku** | 最もコスト効率が良い | 低（Sonnet の約1/4） | 簡単なタスク、コードサーチ |
| **Opus** | 最高の推論能力 | 高（Sonnet の約5倍） | 複雑なアーキテクチャ決定、深い分析 |
| **OpusPlan** | ハイブリッド | 可変 | 計画にOpus、実行にSonnet |

#### モデル切り替えコマンド

```bash
/model haiku      # Haiku に切り替え
/model sonnet     # Sonnet に切り替え
/model opus       # Opus に切り替え
```

### 9.2 /compact の使い方

`/compact` は会話履歴を要約して圧縮し、各メッセージと共に送信されるトークン数を削減する。長いセッションでコストを40〜60%カットできる [^13]。

```bash
/compact                         # 標準圧縮
/compact Focus on the API changes  # 指示付き圧縮（何を残すか）
```

- 自動圧縮はコンテキスト制限に近づくと自動発火（約95%）
- `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` で閾値を調整可能（例: `50`）

### 9.3 トークン節約テクニック

1. **`/clear` を頻繁に使う**：無関係なタスク間でコンテキストをリセット
2. **サブエージェントで調査を分離**：大量のファイル読み込みをメインコンテキストに入れない
3. **プロンプトキャッシュ**：Claude Code は自動でプロンプトキャッシュを使用
4. **探索にはHaikuを使う**：`Explore` サブエージェントはデフォルトでHaikuを使用
5. **具体的なファイル指定**：`@src/auth/login.ts` のように直接参照し、不要な探索を減らす
6. **`/effort` で思考の深さを制御**：low/medium/high/max で調整

### 9.4 ステータスラインでのトークン使用量監視

カスタムステータスラインを設定してコンテキスト使用量を継続的に追跡できる。`/statusline` コマンドで設定。

---

## 10. IDE 連携

### 10.1 VS Code 連携

VS Code 拡張機能が最も成熟した IDE 統合を提供する [^14]。

#### インストール

1. `Cmd+Shift+X`（Mac）/ `Ctrl+Shift+X`（Windows/Linux）で拡張機能ビューを開く
2. "Claude Code" を検索してインストール

#### 主な機能

- ネイティブグラフィカルチャットパネル
- チェックポイントベースの Undo
- `@` メンションでファイル参照
- 並行会話
- 差分ビューでの変更提案
- 診断情報の共有

#### ターミナルでの使用

統合ターミナル（`Ctrl+`）から `claude` コマンドを直接実行することも可能。

### 10.2 JetBrains 連携

1. Settings > Plugins > Marketplace で "Claude Code" を検索してインストール
2. Claude Code CLI を IDE の統合ターミナル内で実行
3. 変更提案は IDE の差分ビューで表示

### 10.3 ターミナルでの使い方

```bash
# 基本的な起動
claude

# 特定のディレクトリで起動
claude --cwd /path/to/project

# 追加ディレクトリの参照
claude --add-dir /path/to/shared-libs

# プロンプトを直接渡して実行
claude -p "Explain this project"

# 前回の会話を再開
claude --continue
```

---

## 11. トラブルシューティング

### 11.1 よくある問題と解決策

#### インストール・セットアップ

| 問題 | 解決策 |
|------|--------|
| インストール失敗 | Node.js を 18.0 以上に更新。`npm cache clean --force` 後に再インストール |
| "Invalid API key" エラー | Anthropic コンソールでキーを確認。余分なスペースや文字がないか確認 |
| コマンドが見つからない | `npm install -g @anthropic-ai/claude-code` でグローバルインストール |

#### パフォーマンス問題

| 問題 | 解決策 |
|------|--------|
| 応答が遅い | `/clear` でコンテキストリセット。プロンプトを具体的にして不要なスキャンを減らす |
| コンテキスト超過 | `/compact` で圧縮。サブエージェントで調査を分離 |
| 同じミスを繰り返す | CLAUDE.md が長すぎないか確認。`/clear` して再スタート |

#### MCP 関連

| 問題 | 解決策 |
|------|--------|
| MCP 接続エラー | `claude --mcp-debug` でデバッグ。`/mcp` で状態確認 |
| OAuth 認証失敗 | `/mcp` から再認証。ブラウザが開かない場合はURLを手動コピー |
| MCP 起動タイムアウト | `MCP_TIMEOUT=10000 claude` でタイムアウトを延長 |

#### ファイル権限

| 問題 | 解決策 |
|------|--------|
| ファイルを変更できない | プロジェクトディレクトリの書き込み権限を確認 |
| 権限プロンプトが多すぎる | `/permissions` で安全なコマンドを許可リストに追加 |

### 11.2 デバッグ方法

```bash
# バージョン確認
claude --version

# デバッグモードで起動
claude --debug

# verbose モードの切り替え（セッション中）
Ctrl+O

# MCP デバッグ
claude --mcp-debug

# 接続テスト
ping claude.ai

# API キー確認
echo $ANTHROPIC_API_KEY
```

### 11.3 よくある失敗パターンと対策

| パターン | 問題 | 対策 |
|---------|------|------|
| **キッチンシンクセッション** | 1つのセッションで無関係なタスクを混ぜる | タスク間で `/clear` |
| **修正の連鎖** | 2回以上修正しても直らない | `/clear` して学んだことを含む新プロンプト |
| **肥大化した CLAUDE.md** | 重要なルールが埋もれる | 定期的に剪定。不要な行を削除 |
| **検証なしの信頼** | もっともらしいがエッジケースを扱わない実装 | テスト、スクリプト、スクショで検証 |
| **無限探索** | スコープなしの調査でコンテキスト消費 | 調査範囲を限定するかサブエージェントを使用 |

---

## 12. 高度な使い方

### 12.1 Agent Teams（並行エージェント）

サブエージェントが単一セッション内で動作するのに対し、Agent Teams は複数の独立したセッション間でエージェントを協調させる。共有タスク、メッセージング、チームリードによる自動調整が可能 [^1]。

### 12.2 Plan Mode

Plan Mode は Claude を読み取り専用モードに切り替え、コードベースの探索と計画立案を行う。コードの変更は行わない。

```
# Ctrl+G でプランモードに切り替え
# プランモード中はファイル読み取りと質問のみ

# 使用例
> Read the auth module and create a plan to add OAuth support
```

Plan Mode は以下の場合に特に有効：
- 不慣れなコードベースの理解
- 複数ファイルにまたがる変更の計画
- アプローチが不明確な場合

**スコープが明確で小さな変更（タイポ修正、ログ追加等）ではスキップしてよい。**

### 12.3 ヘッドレスモード（--print, -p）

非対話型で Claude Code を実行する。CI パイプライン、pre-commit フック、自動ワークフローに最適 [^15]。

```bash
# ワンショットクエリ
claude -p "Explain what this project does"

# JSON 出力
claude -p "List all API endpoints" --output-format json

# ストリーミング JSON
claude -p "Analyze this log file" --output-format stream-json

# 構造化出力（JSON Schema）
claude -p "Extract function names from auth.py" \
  --output-format json \
  --json-schema '{"type":"object","properties":{"functions":{"type":"array","items":{"type":"string"}}}}'

# ツール制限付き
claude -p "Run tests and fix failures" \
  --allowedTools "Bash,Read,Edit"

# 会話の継続
claude -p "Review this codebase" --output-format json
claude -p "Now focus on database queries" --continue

# セッション ID 指定で再開
session_id=$(claude -p "Start a review" --output-format json | jq -r '.session_id')
claude -p "Continue that review" --resume "$session_id"

# システムプロンプトの追加
gh pr diff "$1" | claude -p \
  --append-system-prompt "You are a security engineer." \
  --output-format json
```

### 12.4 SDK を使ったカスタム統合

Claude Code は Python と TypeScript の SDK を提供しており、プログラマティックな制御が可能：

- 構造化出力
- ツール承認コールバック
- ネイティブメッセージオブジェクト
- ストリーミングレスポンス

詳細: [Agent SDK Documentation](https://platform.claude.com/docs/en/agent-sdk/overview)

### 12.5 ファンアウトパターン（大規模バッチ処理）

```bash
# 1. タスクリストを生成
claude -p "List all Python files needing migration" > files.txt

# 2. 並行実行
for file in $(cat files.txt); do
  claude -p "Migrate $file from React to Vue. Return OK or FAIL." \
    --allowedTools "Edit,Bash(git commit *)" &
done
wait
```

### 12.6 チェックポイントとリワインド

Claude の全アクションはチェックポイントを作成する。`Esc + Esc` または `/rewind` でリワインドメニューを開き：

- 会話のみ復元
- コードのみ復元
- 両方復元
- 選択したメッセージから要約

チェックポイントはセッション間で永続化されるため、ターミナルを閉じても後でリワインドできる。

---

## 13. 実際のユースケース・事例

### 13.1 OSS 開発での活用

- **Anthropic 社内**: 約12名のエンジニアチームで1日60〜100の内部リリース、エンジニア1人あたり1日約5つの PR [^16]
- **claude-code-infrastructure-showcase**: 6ヶ月以上の実環境テストを経たインフラ。40%以上の効率改善を報告
- **Hugging Face**: Claude Code Skills を使って1日1,000以上のML実験を実行 [^6]

### 13.2 大規模プロジェクトでの実践

#### 大規模リファクタリング

```
1. Plan Mode でコードベースを分析
   > Analyze the entire /src directory and identify all deprecated API usages

2. サブエージェントで並行調査
   > Use subagents to investigate each module independently

3. ファンアウトで並行修正
   for file in $(cat deprecated-files.txt); do
     claude -p "Update $file to use the new API" --allowedTools "Edit"
   done

4. テスト実行で検証
   > Run the full test suite and fix any regressions
```

#### 新規機能開発（Claude にインタビューさせるパターン）

```
1. 要件インタビュー → SPEC.md 生成
2. /clear で新セッション
3. Plan Mode で設計
4. 実装 → テスト → レビュー → コミット
```

### 13.3 一人開発での活用

#### 効果的なワークフロー

1. **コードベースの理解**: `claude` を起動して質問。「このプロジェクトのアーキテクチャを説明して」
2. **機能実装**: 具体的なプロンプトで実装依頼。テストケースを必ず含める
3. **コードレビュー**: セキュリティレビューアーエージェントでセルフレビュー
4. **ドキュメント生成**: コードベースを分析してREADME等を自動生成
5. **バグ修正**: エラーメッセージとスタックトレースを貼り付けて修正依頼

#### データサイエンスでの活用

```
# Jupyter Notebook の変換
> Transform this exploratory notebook into a production pipeline

# データ分析
> Analyze the data in data/sales.csv and create visualizations
```

### 13.4 チーム開発のベストプラクティス

1. **CLAUDE.md をバージョン管理**：チーム全員が同じ規約を共有
2. **プロジェクトスキルを標準化**：`.claude/skills/` にチーム共通のワークフロー
3. **カスタムエージェントの共有**：`.claude/agents/` でコードレビューアー等を標準化
4. **MCP サーバーの共有**：`.mcp.json` でプロジェクトスコープのMCP設定
5. **CI/CD 統合**：PR レビュー、セキュリティ監査をヘッドレスモードで自動化

---

## 14. 情報源・参考リンク

### Anthropic 公式ドキュメント

- [Best Practices for Claude Code](https://code.claude.com/docs/en/best-practices)
- [Hooks Reference](https://code.claude.com/docs/en/hooks)
- [Create Custom Subagents](https://code.claude.com/docs/en/sub-agents)
- [Extend Claude with Skills](https://code.claude.com/docs/en/skills)
- [Connect Claude Code to tools via MCP](https://code.claude.com/docs/en/mcp)
- [Configure Permissions](https://code.claude.com/docs/en/permissions)
- [Run Claude Code Programmatically (Headless)](https://code.claude.com/docs/en/headless)
- [Common Workflows](https://code.claude.com/docs/en/common-workflows)
- [Model Configuration](https://code.claude.com/docs/en/model-config)
- [Use Claude Code in VS Code](https://code.claude.com/docs/en/vs-code)
- [Troubleshooting](https://code.claude.com/docs/en/troubleshooting)
- [Claude Code のベストプラクティス（日本語）](https://code.claude.com/docs/ja/best-practices)

### コミュニティリソース

- [Writing a Good CLAUDE.md - HumanLayer Blog](https://www.humanlayer.dev/blog/writing-a-good-claude-md)
- [How to Write a Good CLAUDE.md File - Builder.io](https://www.builder.io/blog/claude-md-guide)
- [Claude Code's Custom Agent Framework - DEV Community](https://dev.to/therealmrmumba/claude-codes-custom-agent-framework-changes-everything-4o4m)
- [How I Split Claude Code Into 12 Specialized Sub-Agents - DEV Community](https://dev.to/matkarimov099/how-i-split-claude-code-into-12-specialized-sub-agents-for-my-react-project-3jh8)
- [Claude Code Hooks: Complete Guide with 20+ Examples - DEV Community](https://dev.to/lukaszfryc/claude-code-hooks-complete-guide-with-20-ready-to-use-examples-2026-dcg)
- [Mastering Git Worktrees with Claude Code - Medium](https://medium.com/@dtunai/mastering-git-worktrees-with-claude-code-for-parallel-development-workflow-41dc91e645fe)
- [Shipping Faster with Claude Code and Git Worktrees - incident.io](https://incident.io/blog/shipping-faster-with-claude-code-and-git-worktrees)
- [How I Use Every Claude Code Feature - Shrivu Shankar](https://blog.sshh.io/p/how-i-use-every-claude-code-feature)
- [awesome-claude-code - GitHub](https://github.com/hesreallyhim/awesome-claude-code)
- [shanraisshan/claude-code-best-practice - GitHub](https://github.com/shanraisshan/claude-code-best-practice)
- [Best MCP Servers for Claude Code - MCPcat](https://mcpcat.io/guides/best-mcp-servers-for-claude-code/)

### 日本語リソース

- [Claude Code を使いこなすためのベストプラクティス - ENECHANGE Developer Blog](https://tech.enechange.co.jp/entry/2026/02/16/195000)
- [Claude Code ベストプラクティスが公式ドキュメント化されたので日本語訳した](https://www.pnkts.net/2026/01/22/claude-code-best-practices-ja)
- [Claude Codeベストプラクティス2026 - Qiita](https://qiita.com/dai_chi/items/63b15050cc1280c45f86)
- [Claude Code公式ベストプラクティス完全解説 - note](https://note.com/samurai_worker/n/ncf736866aab6)
- [Claude Codeの使い方完全ガイド - カゴヤ](https://www.kagoya.jp/howto/engineer/hpc/use-claudecode/)

### 学習リソース

- [Claude Code Best Practices: The 2026 Guide - Morph](https://www.morphllm.com/claude-code-best-practices)
- [Claude Code Guide: Professional Setup - wmedia.es](https://wmedia.es/en/writing/claude-code-professional-guide-frontend-ai)
- [Claude Skills and CLAUDE.md: A Practical Guide - gend.co](https://www.gend.co/blog/claude-skills-claude-md-guide)
- [Claude Code Hooks: A Practical Guide - DataCamp](https://www.datacamp.com/tutorial/claude-code-hooks)
- [How Claude Code is built - Pragmatic Engineer](https://newsletter.pragmaticengineer.com/p/how-claude-code-is-built)
- [How to Reduce Claude Code Costs - thecaio.ai](https://www.thecaio.ai/blog/reduce-claude-code-costs)

---

## 脚注

[^1]: [Best Practices for Claude Code - Anthropic 公式](https://code.claude.com/docs/en/best-practices)
[^2]: [Writing a Good CLAUDE.md - HumanLayer Blog](https://www.humanlayer.dev/blog/writing-a-good-claude-md)
[^3]: [Claude Code のベストプラクティス - 日本語公式](https://code.claude.com/docs/ja/best-practices)
[^4]: [Create Custom Subagents - Anthropic 公式](https://code.claude.com/docs/en/sub-agents)
[^5]: [Claude Code's Custom Agent Framework - DEV Community](https://dev.to/therealmrmumba/claude-codes-custom-agent-framework-changes-everything-4o4m)
[^6]: [Extend Claude with Skills - Anthropic 公式](https://code.claude.com/docs/en/skills)
[^7]: [Hooks Reference - Anthropic 公式](https://code.claude.com/docs/en/hooks)
[^8]: [Claude Code Hooks: Complete Guide - DEV Community](https://dev.to/lukaszfryc/claude-code-hooks-complete-guide-with-20-ready-to-use-examples-2026-dcg)
[^9]: [Connect Claude Code to tools via MCP - Anthropic 公式](https://code.claude.com/docs/en/mcp)
[^10]: [Claude Code --dangerously-skip-permissions: Safe Usage Guide](https://www.ksred.com/claude-code-dangerously-skip-permissions-when-to-use-it-and-when-you-absolutely-shouldnt/)
[^11]: [What is UltraThink in Claude Code - ClaudeLog](https://claudelog.com/faqs/what-is-ultrathink/)
[^12]: [Mastering Git Worktrees with Claude Code - Medium](https://medium.com/@dtunai/mastering-git-worktrees-with-claude-code-for-parallel-development-workflow-41dc91e645fe)
[^13]: [Model Configuration - Anthropic 公式](https://code.claude.com/docs/en/model-config)
[^14]: [Use Claude Code in VS Code - Anthropic 公式](https://code.claude.com/docs/en/vs-code)
[^15]: [Run Claude Code Programmatically - Anthropic 公式](https://code.claude.com/docs/en/headless)
[^16]: [How Claude Code is built - Pragmatic Engineer](https://newsletter.pragmaticengineer.com/p/how-claude-code-is-built)

---

> このガイドは 2026年2月24日 時点の情報に基づいています。Claude Code は活発に開発が進められており、最新情報は [公式ドキュメント](https://code.claude.com/docs/) を参照してください。
