"""ツール定義と実行 - read_file, write_file, edit_file, bash"""

import json
import subprocess
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


# --- ツール実行ディスパッチ ---

_TOOL_MAP = {
    "read_file": lambda args: tool_read_file(**args),
    "write_file": lambda args: tool_write_file(**args),
    "edit_file": lambda args: tool_edit_file(**args),
    "bash": lambda args: tool_bash(**args),
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
