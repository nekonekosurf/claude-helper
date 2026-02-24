# ローカル版 Claude Code を vLLM で構築する

## 概要

Claude Code と同等のコーディングエージェントを、**vLLM + ローカルLLM**（中国系モデル除外）でゼロから構築するための設計書。

---

## 1. vLLM 基盤

### vLLM とは

高性能な LLM 推論サーバー。OpenAI 互換 API を提供し、ツール呼び出し（Function Calling）に対応。

### なぜ vLLM か

| 特性 | 内容 |
|---|---|
| 並列処理 | 100+ 同時リクエスト対応（サブエージェント並列に最適） |
| PagedAttention | VRAMの断片化を50%以上削減 |
| 構造化出力 | `guided_json` / `guided_grammar` で有効なJSON出力を強制 |
| ツール呼び出し | `--enable-auto-tool-choice` でネイティブ対応 |
| OpenAI互換 | 標準的な `openai` Python SDKがそのまま使える |

### vLLM 起動コマンド

```bash
# 基本起動（ツール呼び出し有効）
vllm serve meta-llama/Llama-3.3-70B-Instruct \
  --enable-auto-tool-choice \
  --tool-call-parser hermes \
  --max-model-len 131072 \
  --gpu-memory-utilization 0.90

# Mistral モデルの場合
vllm serve mistralai/Devstral-Small-2501 \
  --enable-auto-tool-choice \
  --tool-call-parser mistral \
  --max-model-len 262144

# Gemma モデルの場合
vllm serve google/gemma-3-27b-it \
  --enable-auto-tool-choice \
  --tool-call-parser hermes \
  --max-model-len 131072

# 量子化モデル（VRAM節約）
vllm serve TheBloke/Llama-3.3-70B-Instruct-AWQ \
  --quantization awq \
  --enable-auto-tool-choice \
  --tool-call-parser hermes
```

### ツール呼び出しパーサー

| パーサー | 対応モデル |
|---|---|
| `hermes` | Llama 3.x, Gemma 3, 汎用 |
| `mistral` | Mistral / Devstral / Codestral |
| `llama3_json` | Llama 3.1+ 専用 |
| `jamba` | Jamba モデル |

### vLLM での API 呼び出し

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8000/v1",
    api_key="not-needed"  # ローカルなので不要
)

# ツール定義
tools = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "ファイルの内容を読む",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "ファイルパス"},
                    "offset": {"type": "integer", "description": "開始行"},
                    "limit": {"type": "integer", "description": "行数"}
                },
                "required": ["path"]
            }
        }
    }
]

# ツール呼び出し付きリクエスト
response = client.chat.completions.create(
    model="meta-llama/Llama-3.3-70B-Instruct",
    messages=[{"role": "user", "content": "main.py を読んで"}],
    tools=tools,
    tool_choice="auto",
    temperature=0  # ツール呼び出しは決定論的に
)

# ツール呼び出しの解析
if response.choices[0].message.tool_calls:
    tool_call = response.choices[0].message.tool_calls[0]
    print(f"ツール: {tool_call.function.name}")
    print(f"引数: {tool_call.function.arguments}")
```

### 構造化出力（JSON強制）

```python
from pydantic import BaseModel

class ToolCall(BaseModel):
    tool_name: str
    arguments: dict

# guided_json で出力を強制
response = client.chat.completions.create(
    model="meta-llama/Llama-3.3-70B-Instruct",
    messages=[...],
    extra_body={
        "guided_json": ToolCall.model_json_schema()
    }
)
```

### 複数モデルの同時起動（用途別）

```bash
# メインモデル（生成・推論用）ポート8000
vllm serve mistralai/Devstral-Small-2501 \
  --port 8000 \
  --enable-auto-tool-choice \
  --tool-call-parser mistral

# 軽量モデル（ルーティング・分類用）ポート8001
vllm serve google/gemma-3-4b-it \
  --port 8001 \
  --max-model-len 8192

# Embedding モデル ポート8002
vllm serve intfloat/multilingual-e5-large \
  --port 8002 \
  --task embed
```

---

## 2. 使えるモデル（中国系除外）

> **除外:** DeepSeek（中国・杭州）、Qwen（Alibaba）、BGE（中国科学院）

### コーディング用モデル

| モデル | 開発元 | サイズ | コンテキスト | コーディング力 | VRAM(Q4) | vLLM ツールパーサー |
|---|---|---|---|---|---|---|
| **Devstral Small 2** | Mistral(仏) | 24B | 256K | とても良い | ~16GB | `mistral` |
| **Llama 3.3 70B** | Meta(米) | 70B | 128K | 優秀 | ~42GB | `hermes` or `llama3_json` |
| **Codestral 22B** | Mistral(仏) | 22B | 256K | とても良い | ~14GB | `mistral` |
| **Gemma 3 27B** | Google(米) | 27B | 128K | 良い | ~18GB | `hermes` |
| **Gemma 3 12B** | Google(米) | 12B | 128K | 良い | ~8GB | `hermes` |
| Llama 3.1 Swallow 8B | 東工大 | 8B | 128K | 日本語特化 | ~6GB | `hermes` |

### Embedding モデル（中国系除外）

| モデル | 開発元 | パラメータ | 次元 | 日本語 |
|---|---|---|---|---|
| **multilingual-e5-large** | Microsoft(米) | 560M | 1024 | 優秀 |
| multilingual-e5-base | Microsoft(米) | 278M | 768 | とても良い |
| nomic-embed-text-v1.5 | Nomic(米) | 137M | 768 | 良い |

### ハードウェア別の推奨構成

| ハードウェア | 推奨モデル | vLLM 設定 |
|---|---|---|
| **16GB VRAM** (RTX 4070 Ti) | Devstral Small 2 24B (AWQ量子化) | `--quantization awq` |
| **24GB VRAM** (RTX 4090) | Devstral Small 2 24B (FP16) | デフォルト |
| **32GB VRAM** (RTX 5090) | Llama 3.3 70B (AWQ量子化) | `--quantization awq` |
| **48GB+ VRAM** (2x 3090) | Llama 3.3 70B (FP16) | `--tensor-parallel-size 2` |
| **80GB+ VRAM** (A100) | Llama 3.3 70B (FP16) | フルスペック |

---

## 3. アーキテクチャ設計

### 全体構成

```
┌─────────────────────────────────────────┐
│           CLI / TUI レイヤー              │
│  (Python: Rich+Textual / Go: Bubble Tea) │
├─────────────────────────────────────────┤
│            エージェントループ              │
│  入力 → プロンプト組立 → vLLM呼出 →     │
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
│           vLLM サーバー群                  │
│  メイン(8000) | ルーター(8001) | Embed(8002)│
├─────────────────────────────────────────┤
│            永続化レイヤー                  │
│  セッションDB | 記憶ファイル | 設定         │
└─────────────────────────────────────────┘
```

### エージェントループ（vLLM版）

```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:8000/v1", api_key="dummy")

SYSTEM_PROMPT = """あなたはコーディングエージェントです。
ユーザーのタスクを完了するために、ツールを使ってファイルの読み書き、コマンド実行を行います。
タスクが完了したらテキストで回答してください。"""

def agent_loop(user_message: str, tools: list, model: str):
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_message}
    ]

    while True:
        # vLLM にリクエスト
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=tools,
            tool_choice="auto",
            temperature=0,
        )

        choice = response.choices[0]

        # ツール呼び出しがあるか
        if choice.message.tool_calls:
            # アシスタントのメッセージを記録
            messages.append(choice.message)

            for tool_call in choice.message.tool_calls:
                # ツール実行
                result = execute_tool(
                    tool_call.function.name,
                    json.loads(tool_call.function.arguments)
                )

                # 結果をメッセージに追加
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": str(result)
                })
        else:
            # テキスト応答 → ループ終了
            return choice.message.content
```

### ツール実行の実装

```python
import subprocess
import os
import json

def execute_tool(name: str, args: dict) -> str:
    if name == "read_file":
        return _read_file(args["path"], args.get("offset"), args.get("limit"))
    elif name == "write_file":
        return _write_file(args["path"], args["content"])
    elif name == "edit_file":
        return _edit_file(args["path"], args["old_text"], args["new_text"])
    elif name == "bash":
        return _bash(args["command"], args.get("timeout_ms", 120000))
    elif name == "glob":
        return _glob(args["pattern"], args.get("path", "."))
    elif name == "grep":
        return _grep(args["pattern"], args.get("path", "."))
    else:
        return f"Unknown tool: {name}"

def _read_file(path: str, offset: int = None, limit: int = None) -> str:
    with open(path, "r") as f:
        lines = f.readlines()
    start = (offset or 1) - 1
    end = start + (limit or 2000)
    numbered = [f"{i+start+1}\t{line}" for i, line in enumerate(lines[start:end])]
    return "".join(numbered)

def _write_file(path: str, content: str) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(content)
    return f"File written: {path}"

def _edit_file(path: str, old_text: str, new_text: str) -> str:
    with open(path, "r") as f:
        content = f.read()

    count = content.count(old_text)
    if count == 0:
        # フォールバック: 空白無視マッチ
        import re
        pattern = re.escape(old_text).replace(r"\ ", r"\s+")
        if re.search(pattern, content):
            content = re.sub(pattern, new_text, content, count=1)
        else:
            return f"Error: '{old_text[:50]}...' not found in {path}"
    elif count > 1:
        return f"Error: Found {count} matches. Provide more context."
    else:
        content = content.replace(old_text, new_text, 1)

    with open(path, "w") as f:
        f.write(content)
    return f"Edit applied to {path}"

def _bash(command: str, timeout_ms: int = 120000) -> str:
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True,
            timeout=timeout_ms / 1000
        )
        output = ""
        if result.stdout:
            output += f"stdout:\n{result.stdout}\n"
        if result.stderr:
            output += f"stderr:\n{result.stderr}\n"
        output += f"exit_code: {result.returncode}"
        return output
    except subprocess.TimeoutExpired:
        return "Error: Command timed out"

def _glob(pattern: str, path: str = ".") -> str:
    import glob as g
    matches = g.glob(os.path.join(path, pattern), recursive=True)
    return "\n".join(sorted(matches))

def _grep(pattern: str, path: str = ".") -> str:
    result = subprocess.run(
        ["rg", "--no-heading", "-n", pattern, path],
        capture_output=True, text=True
    )
    return result.stdout or "No matches found"
```

### サブエージェント（vLLM 並列の強み）

vLLM の高い並列処理能力を活かして、複数のサブエージェントを同時実行：

```python
import asyncio

async def spawn_agent(task: str, tools: list, model: str) -> str:
    """独立したコンテキストでサブエージェントを実行"""
    # サブエージェント用のクライアント（同じ vLLM サーバー）
    sub_client = AsyncOpenAI(
        base_url="http://localhost:8000/v1",
        api_key="dummy"
    )

    messages = [
        {"role": "system", "content": "あなたは専門サブエージェントです。"},
        {"role": "user", "content": task}
    ]

    max_turns = 20
    for _ in range(max_turns):
        response = await sub_client.chat.completions.create(
            model=model,
            messages=messages,
            tools=tools,
            temperature=0,
        )
        choice = response.choices[0]

        if choice.message.tool_calls:
            messages.append(choice.message)
            for tc in choice.message.tool_calls:
                result = execute_tool(tc.function.name,
                                      json.loads(tc.function.arguments))
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": str(result)
                })
        else:
            return choice.message.content

    return "Sub-agent reached max turns"

# 複数サブエージェントを並列実行
async def parallel_agents():
    results = await asyncio.gather(
        spawn_agent("src/ のテストを確認して", read_only_tools, model),
        spawn_agent("package.json の依存関係を分析して", read_only_tools, model),
        spawn_agent("README.md を読んでプロジェクト概要を教えて", read_only_tools, model),
    )
    return results
```

### コンテキスト圧縮

```python
def compress_context(messages: list, model: str, max_tokens: int) -> list:
    """コンテキストがmax_tokensの70%を超えたら圧縮"""
    total = estimate_tokens(messages)

    if total < max_tokens * 0.7:
        return messages  # 圧縮不要

    # システムプロンプト + 直近5ターンは保持
    system = messages[0]
    recent = messages[-10:]  # 直近5ターン（user + assistant で10メッセージ）
    old = messages[1:-10]

    # 古いメッセージを要約
    summary_prompt = f"""以下の会話を簡潔に要約してください。
必ず保持する情報: ファイルパス、関数名、エラーメッセージ、決定事項

{format_messages(old)}"""

    summary = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": summary_prompt}],
        max_tokens=500,
    ).choices[0].message.content

    return [
        system,
        {"role": "user", "content": f"[セッション要約]\n{summary}"},
        {"role": "assistant", "content": "了解しました。要約を把握しました。"},
        *recent
    ]
```

---

## 4. ツール呼び出しの信頼性（vLLM特有）

### vLLM の構造化出力オプション

| 手法 | 説明 | 使い方 |
|---|---|---|
| `guided_json` | JSONスキーマに沿った出力を強制 | `extra_body={"guided_json": schema}` |
| `guided_grammar` | EBNF文法で出力を制約 | `extra_body={"guided_grammar": grammar}` |
| `guided_regex` | 正規表現で出力を制約 | `extra_body={"guided_regex": pattern}` |
| ツールパーサー | モデル固有のツール呼出形式を自動解析 | `--enable-auto-tool-choice` |

### 推奨設定

```python
# 方法1: ツール呼び出し（推奨）
response = client.chat.completions.create(
    model=model,
    messages=messages,
    tools=tools,
    tool_choice="auto",
    temperature=0,
)

# 方法2: 構造化JSON出力（フォールバック）
from pydantic import BaseModel
from typing import Optional

class AgentResponse(BaseModel):
    thinking: str
    tool_name: Optional[str] = None
    tool_args: Optional[dict] = None
    final_answer: Optional[str] = None

response = client.chat.completions.create(
    model=model,
    messages=messages,
    extra_body={"guided_json": AgentResponse.model_json_schema()},
)
```

### ツール呼び出しが壊れる場合の対処

```
1. --tool-call-parser を変更（hermes → llama3_json → mistral）
2. guided_json でスキーマを強制
3. temperature=0 にする
4. ツール説明を短くする（全ツール合計 ~150トークン）
5. モデルを大きくする（12B未満はツール呼び出しが不安定）
```

---

## 5. 記憶・セッション管理

### セッション保存（JSONL形式）

```python
import json
from datetime import datetime

def save_session(session_id: str, messages: list):
    path = f".agent/sessions/{session_id}.jsonl"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a") as f:
        for msg in messages:
            f.write(json.dumps({
                "timestamp": datetime.now().isoformat(),
                "message": msg
            }, ensure_ascii=False) + "\n")

def load_session(session_id: str) -> list:
    path = f".agent/sessions/{session_id}.jsonl"
    messages = []
    with open(path, "r") as f:
        for line in f:
            data = json.loads(line)
            messages.append(data["message"])
    return messages
```

### 記憶ファイル（MEMORY.md パターン）

```
.agent/
  ├── memory/
  │   └── MEMORY.md          # 先頭200行を自動ロード
  ├── sessions/
  │   └── {id}.jsonl         # 会話履歴
  ├── config.md              # プロジェクト指示書（≒ CLAUDE.md）
  └── config.local.md        # 個人設定（gitignore）
```

---

## 6. Claude Code との差分（現実的な限界）

| 項目 | Claude Code | vLLM ローカル版 |
|---|---|---|
| コンテキスト | 200K（劣化なし） | 128-256K（後半で劣化あり） |
| 推論品質 | Opus/Sonnet級 | 70Bでも劣る（特に複雑な推論） |
| 速度 | API高速 | 24B@RTX4090で25-35 tok/s |
| 並列処理 | 制限あり | **vLLMで100+並列**（ここは勝てる） |
| 編集自己修復 | 非常に強い | モデル依存 |
| サブエージェント | 強い推論力 | 並列性能で補う |
| ツール呼び出し | 100%安定 | モデル・パーサー依存 |

---

## 7. 実装ステップ

```
Phase 1（MVP - 最小動作版）
  ├── vLLM サーバー起動（Devstral or Llama 3.3）
  ├── エージェントループ（ReAct、～400行 Python）
  ├── 4つの基本ツール（read, write, edit, bash）
  ├── OpenAI SDK で vLLM と通信
  └── 基本的な CLI 入出力

Phase 2（実用版）
  ├── glob, grep ツール追加
  ├── コンテキスト圧縮（トークンカウント + 自動要約）
  ├── セッション保存/復元（JSONL）
  ├── 設定ファイル（config.md）ロード
  └── Edit のフォールバックマッチング（完全一致→空白無視→ファジー）

Phase 3（Claude Code 互換）
  ├── サブエージェント（asyncio で並列実行）
  ├── 記憶システム（MEMORY.md パターン）
  ├── フックシステム（PreToolUse / PostToolUse）
  ├── 権限システム（deny/ask/allow）
  ├── ファイルチェックポイント（編集前スナップショット）
  └── TUI の改善（Rich/Textual）

Phase 4（フル機能）
  ├── 複数モデル同時起動（メイン + ルーター + Embedding）
  ├── スキルシステム
  ├── MCP 連携
  ├── プランモード（読み取り専用モード）
  └── tree-sitter リポマップ
```
