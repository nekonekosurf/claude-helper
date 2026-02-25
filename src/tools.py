"""ツール定義と実行 - read_file, write_file, edit_file, bash, glob, grep, search_docs"""

import json
import subprocess
import fnmatch
import re
from pathlib import Path
from src.config import MAX_OUTPUT_CHARS, WORKING_DIR


# --- ツール定義（LLMに渡す JSON Schema）---

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "ファイルを読み取る。行番号付きで内容を返す。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "読み取るファイルのパス（絶対パスまたは作業ディレクトリからの相対パス）",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "ファイルを作成または上書きする。親ディレクトリが存在しない場合は自動作成する。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "書き込むファイルのパス",
                    },
                    "content": {
                        "type": "string",
                        "description": "ファイルに書き込む内容",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "ファイル内のテキストを置換する。old_stringに完全一致する箇所をnew_stringで置き換える。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "編集するファイルのパス",
                    },
                    "old_string": {
                        "type": "string",
                        "description": "置換対象のテキスト（完全一致）",
                    },
                    "new_string": {
                        "type": "string",
                        "description": "置換後のテキスト",
                    },
                },
                "required": ["path", "old_string", "new_string"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "シェルコマンドを実行する。結果（stdout + stderr）を返す。",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "実行するbashコマンド",
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "glob",
            "description": "ファイルパターンで検索する。例: '**/*.py', 'src/**/*.ts'",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "globパターン（例: '**/*.py'）",
                    },
                    "path": {
                        "type": "string",
                        "description": "検索開始ディレクトリ（省略時: 作業ディレクトリ）",
                    },
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep",
            "description": "正規表現でファイル内容を検索する。マッチした行を返す。",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "検索する正規表現パターン",
                    },
                    "path": {
                        "type": "string",
                        "description": "検索対象のファイルまたはディレクトリ（省略時: 作業ディレクトリ）",
                    },
                    "file_pattern": {
                        "type": "string",
                        "description": "対象ファイルのglobパターン（例: '*.py'）",
                    },
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_docs",
            "description": "JERG技術文書をキーワード検索する。関連するチャンクを返す。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "検索クエリ（日本語）",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "返す件数（デフォルト: 5）",
                    },
                    "doc_filter": {
                        "type": "string",
                        "description": "文書番号フィルタ（部分一致、例: 'JERG-2-200'）",
                    },
                },
                "required": ["query"],
            },
        },
    },
]


# --- ツール実行 ---

def _resolve_path(path: str) -> Path:
    """パスを解決する（相対パスは作業ディレクトリ基準）"""
    p = Path(path)
    if not p.is_absolute():
        p = Path(WORKING_DIR) / p
    return p


def tool_read_file(path: str) -> str:
    """ファイルを行番号付きで読み取る"""
    p = _resolve_path(path)
    if not p.exists():
        return f"Error: ファイルが見つかりません: {p}"
    if not p.is_file():
        return f"Error: ファイルではありません: {p}"
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        numbered = [f"{i+1:>4} | {line}" for i, line in enumerate(lines)]
        result = "\n".join(numbered)
        if len(result) > MAX_OUTPUT_CHARS:
            result = result[:MAX_OUTPUT_CHARS] + f"\n... (truncated, total {len(lines)} lines)"
        return result
    except Exception as e:
        return f"Error: {e}"


def tool_write_file(path: str, content: str) -> str:
    """ファイルを作成/上書きする"""
    p = _resolve_path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"OK: {p} に書き込みました（{len(content)} bytes）"
    except Exception as e:
        return f"Error: {e}"


def tool_edit_file(path: str, old_string: str, new_string: str) -> str:
    """ファイル内のテキストを置換する"""
    p = _resolve_path(path)
    if not p.exists():
        return f"Error: ファイルが見つかりません: {p}"
    try:
        text = p.read_text(encoding="utf-8")
        count = text.count(old_string)
        if count == 0:
            return f"Error: old_string が見つかりません。ファイル内容を確認してください。"
        if count > 1:
            return f"Error: old_string が {count} 箇所見つかりました。一意になるよう範囲を広げてください。"
        new_text = text.replace(old_string, new_string, 1)
        p.write_text(new_text, encoding="utf-8")
        return f"OK: {p} を編集しました（1箇所置換）"
    except Exception as e:
        return f"Error: {e}"


def tool_bash(command: str) -> str:
    """シェルコマンドを実行する"""
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=WORKING_DIR,
        )
        output = ""
        if result.stdout:
            output += result.stdout
        if result.stderr:
            output += result.stderr
        if not output:
            output = "(no output)"
        if result.returncode != 0:
            output += f"\n[exit code: {result.returncode}]"
        if len(output) > MAX_OUTPUT_CHARS:
            output = output[:MAX_OUTPUT_CHARS] + "\n... (truncated)"
        return output
    except subprocess.TimeoutExpired:
        return "Error: コマンドがタイムアウトしました（30秒）"
    except Exception as e:
        return f"Error: {e}"


def tool_glob(pattern: str, path: str | None = None) -> str:
    """ファイルパターンで検索する"""
    base = _resolve_path(path) if path else Path(WORKING_DIR)
    if not base.exists():
        return f"Error: ディレクトリが見つかりません: {base}"
    try:
        matches = sorted(base.glob(pattern))
        if not matches:
            return f"パターン '{pattern}' に一致するファイルはありません"
        lines = [str(m) for m in matches[:100]]
        result = "\n".join(lines)
        if len(matches) > 100:
            result += f"\n... (他 {len(matches) - 100} 件)"
        return result
    except Exception as e:
        return f"Error: {e}"


def tool_grep(pattern: str, path: str | None = None, file_pattern: str | None = None) -> str:
    """正規表現でファイル内容を検索する"""
    base = _resolve_path(path) if path else Path(WORKING_DIR)
    try:
        regex = re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        return f"Error: 無効な正規表現: {e}"

    results = []
    try:
        if base.is_file():
            files = [base]
        else:
            glob_pat = file_pattern or "**/*"
            files = [f for f in base.glob(glob_pat) if f.is_file()]

        for filepath in files[:200]:
            try:
                text = filepath.read_text(encoding="utf-8", errors="replace")
                for i, line in enumerate(text.splitlines(), 1):
                    if regex.search(line):
                        results.append(f"{filepath}:{i}: {line.strip()}")
                        if len(results) >= 50:
                            break
            except Exception:
                continue
            if len(results) >= 50:
                break

        if not results:
            return f"パターン '{pattern}' に一致する箇所はありません"
        return "\n".join(results)
    except Exception as e:
        return f"Error: {e}"


def tool_search_docs(query: str, top_k: int = 5, doc_filter: str | None = None) -> str:
    """JERG文書をガイド付き2段階検索する（ドメイン検出→ハイブリッド検索）"""
    try:
        from src.guided_retrieval import guided_search
        from src.llm_client import create_client

        client, model = create_client()

        # doc_filterが明示指定された場合はガイド検索のフィルタを上書き
        search_result = guided_search(
            query=query,
            top_k=top_k,
            client=client,
            model=model,
        )

        # 明示的なdoc_filterが指定された場合は、再検索
        if doc_filter:
            from src.hybrid_search import hybrid_search
            results, methods = hybrid_search(
                query=query,
                top_k=top_k,
                doc_filter=doc_filter,
                client=client,
                model=model,
            )
            search_result["results"] = results
            search_result["methods_used"] = methods
            search_result["doc_filter"] = doc_filter

        results = search_result["results"]
        domains = search_result["domains"]
        procedure = search_result["procedure"]
        expert_notes = search_result["expert_notes"]
        methods_used = search_result["methods_used"]
        applied_filter = search_result["doc_filter"]

        if not results:
            return "検索結果がありません"

        parts = []

        # ドメイン検出情報
        if domains:
            top = domains[0]
            confidence = "高" if top["score"] >= 5 else "中" if top["score"] >= 3 else "低"
            parts.append(f"📌 ドメイン検出: {top['name']} (確信度: {confidence})")

        # 専門家ノート
        for note in expert_notes:
            parts.append(f"💡 専門家ノート: {note}")

        # 文書フィルタ
        if applied_filter:
            filter_docs = applied_filter.replace("|", ", ")
            parts.append(f"📄 文書フィルタ: {filter_docs}")

        # 検索手法
        parts.append(f"🔍 検索手法: {' + '.join(methods_used)}")

        # 手順情報
        if procedure:
            parts.append(f"\n📋 推奨手順 ({procedure['description']}):")
            for i, step in enumerate(procedure["steps"], 1):
                parts.append(f"   {i}. {step}")

        parts.append("")  # 空行

        # 検索結果
        for r in results:
            methods_str = "+".join(r.get("methods", []))
            parts.append(
                f"📄 {r['doc_id']} (score: {r['score']:.4f}, via: {methods_str})\n"
                f"   {r['text'][:400]}"
            )

        return "\n".join(parts)
    except FileNotFoundError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error: 検索エラー: {e}"


# --- ツール実行ディスパッチ ---

_TOOL_MAP = {
    "read_file": lambda args: tool_read_file(**args),
    "write_file": lambda args: tool_write_file(**args),
    "edit_file": lambda args: tool_edit_file(**args),
    "bash": lambda args: tool_bash(**args),
    "glob": lambda args: tool_glob(**args),
    "grep": lambda args: tool_grep(**args),
    "search_docs": lambda args: tool_search_docs(**args),
}


def execute_tool(name: str, arguments: str) -> str:
    """ツール名と引数JSONからツールを実行して結果を返す"""
    fn = _TOOL_MAP.get(name)
    if fn is None:
        return f"Error: 未知のツール: {name}"
    try:
        args = json.loads(arguments)
    except json.JSONDecodeError as e:
        return f"Error: 引数のJSON解析に失敗: {e}"
    return fn(args)
