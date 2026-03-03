"""
tools.py - ツール実装モジュール

Claude Code と同等のツール群を実装する。
各ツールは非同期関数として実装し、エラー時は詳細なエラーメッセージを返す。
"""

import asyncio
import glob as glob_module
import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Optional

import aiohttp

# ツール定義（OpenAI tool_call 形式 / JSON Schema）
TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "Read",
            "description": (
                "ローカルファイルシステムからファイルを読み込む。"
                "ファイルを編集する前に必ずこのツールで内容を確認すること。"
                "画像・PDF・Jupyterノートブックも読み込める。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "読み込むファイルの絶対パス（相対パス不可）",
                    },
                    "offset": {
                        "type": "integer",
                        "description": "読み始める行番号（省略時は先頭から）",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "読み込む最大行数（省略時は全行）",
                    },
                },
                "required": ["file_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "Write",
            "description": (
                "ファイルを新規作成または完全上書きする。"
                "既存ファイルを編集する場合は Edit を優先すること。"
                "Write を使う場合は必ず事前に Read で内容を確認すること。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "書き込むファイルの絶対パス",
                    },
                    "content": {
                        "type": "string",
                        "description": "書き込む内容",
                    },
                },
                "required": ["file_path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "Edit",
            "description": (
                "既存ファイルの一部を編集する（差分のみ）。"
                "old_string を new_string で置換する。"
                "old_string はファイル内で一意である必要がある。"
                "必ず事前に Read でファイルを確認してから使うこと。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "編集するファイルの絶対パス",
                    },
                    "old_string": {
                        "type": "string",
                        "description": "置換前の文字列（ファイル内で一意な文字列）",
                    },
                    "new_string": {
                        "type": "string",
                        "description": "置換後の文字列",
                    },
                    "replace_all": {
                        "type": "boolean",
                        "description": "True の場合、全ての出現箇所を置換する（デフォルト: False）",
                    },
                },
                "required": ["file_path", "old_string", "new_string"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "Glob",
            "description": (
                "glob パターンでファイルを検索する。"
                "更新時刻の降順でソートされた結果を返す。"
                "ファイル名パターンで検索する場合はこのツールを使う。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "glob パターン（例: '**/*.py', 'src/**/*.ts'）",
                    },
                    "path": {
                        "type": "string",
                        "description": "検索するディレクトリ（省略時はカレントディレクトリ）",
                    },
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "Grep",
            "description": (
                "ファイル内容を正規表現で検索する（ripgrep ベース）。"
                "コンテンツ検索には Bash の grep より必ずこのツールを使うこと。"
                "output_mode で出力形式を選択できる。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "検索する正規表現パターン",
                    },
                    "path": {
                        "type": "string",
                        "description": "検索するファイルまたはディレクトリ",
                    },
                    "glob": {
                        "type": "string",
                        "description": "検索対象をフィルタする glob パターン（例: '*.py'）",
                    },
                    "output_mode": {
                        "type": "string",
                        "enum": ["content", "files_with_matches", "count"],
                        "description": "出力形式: content(マッチ行), files_with_matches(ファイルパス), count(件数)",
                    },
                    "case_insensitive": {
                        "type": "boolean",
                        "description": "大文字小文字を区別しない（デフォルト: False）",
                    },
                    "context": {
                        "type": "integer",
                        "description": "マッチ前後に表示する行数",
                    },
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "Bash",
            "description": (
                "シェルコマンドを実行する。"
                "ファイル検索には Glob/Grep を優先すること。"
                "デフォルトタイムアウト: 120秒、最大: 600秒。"
                "出力上限: 30,000文字。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "実行するシェルコマンド",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "タイムアウト秒数（デフォルト: 120、最大: 600）",
                    },
                    "work_dir": {
                        "type": "string",
                        "description": "コマンドを実行するディレクトリ",
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "WebSearch",
            "description": (
                "Webを検索して最新情報を取得する。"
                "知識のカットオフ以降の情報や、最新のドキュメントを調べる時に使う。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "検索クエリ（2文字以上）",
                    },
                    "num_results": {
                        "type": "integer",
                        "description": "取得する結果数（デフォルト: 5）",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "WebFetch",
            "description": (
                "指定URLのコンテンツを取得してMarkdownに変換する。"
                "WebSearch で見つけたURLの詳細を確認する時に使う。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "取得するURL（https://で始まる完全なURL）",
                    },
                    "prompt": {
                        "type": "string",
                        "description": "取得したコンテンツから抽出したい情報の指示",
                    },
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "LS",
            "description": "ディレクトリの内容を一覧表示する",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "一覧表示するディレクトリのパス（省略時はカレントディレクトリ）",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "TodoRead",
            "description": "現在のToDoリストを読み込む",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "TodoWrite",
            "description": "ToDoリストを更新する（複数タスクの追跡に使う）",
            "parameters": {
                "type": "object",
                "properties": {
                    "todos": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "content": {"type": "string"},
                                "status": {
                                    "type": "string",
                                    "enum": ["pending", "in_progress", "completed"],
                                },
                                "priority": {
                                    "type": "string",
                                    "enum": ["low", "medium", "high"],
                                },
                            },
                            "required": ["id", "content", "status", "priority"],
                        },
                        "description": "ToDoリスト全体（完全な状態で指定する）",
                    },
                },
                "required": ["todos"],
            },
        },
    },
]


class ToolExecutor:
    """ツール実行クラス"""

    def __init__(self, work_dir: str = ".", tool_timeout: int = 120,
                 tool_output_max_chars: int = 30000):
        self.work_dir = os.path.abspath(work_dir)
        self.tool_timeout = tool_timeout
        self.tool_output_max_chars = tool_output_max_chars
        # ToDoリスト（インメモリ管理）
        self._todos: list[dict] = []
        # Web検索キャッシュ（重複防止）
        self._web_cache: dict[str, str] = {}

    async def execute(self, tool_name: str, tool_input: dict) -> str:
        """ツール名と引数から適切なツールを実行する"""
        tool_map = {
            "Read": self._read,
            "Write": self._write,
            "Edit": self._edit,
            "Glob": self._glob,
            "Grep": self._grep,
            "Bash": self._bash,
            "WebSearch": self._web_search,
            "WebFetch": self._web_fetch,
            "LS": self._ls,
            "TodoRead": self._todo_read,
            "TodoWrite": self._todo_write,
        }

        handler = tool_map.get(tool_name)
        if handler is None:
            return f"エラー: 未知のツール '{tool_name}'"

        try:
            result = await handler(**tool_input)
            # 出力文字数制限
            if len(result) > self.tool_output_max_chars:
                result = result[:self.tool_output_max_chars] + (
                    f"\n\n[出力が {self.tool_output_max_chars} 文字を超えたため切り詰めました]"
                )
            return result
        except TypeError as e:
            return f"エラー: ツール引数が不正です: {e}"
        except Exception as e:
            return f"エラー: {tool_name} の実行中に例外が発生しました: {type(e).__name__}: {e}"

    async def _read(self, file_path: str, offset: int = 0, limit: int = 0) -> str:
        """ファイルを読み込む（cat -n 形式で行番号付き）"""
        path = Path(file_path)
        if not path.is_absolute():
            path = Path(self.work_dir) / path

        if not path.exists():
            return f"エラー: ファイルが存在しません: {path}"
        if path.is_dir():
            return f"エラー: '{path}' はディレクトリです。LS ツールを使ってください。"

        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()

            # オフセット・制限の適用
            if offset > 0:
                lines = lines[offset - 1:]  # 1-indexed
            if limit > 0:
                lines = lines[:limit]

            # cat -n 形式で行番号付き出力
            result_lines = []
            start_line = offset if offset > 0 else 1
            for i, line in enumerate(lines):
                line_num = start_line + i
                result_lines.append(f"  {line_num:4d}\t{line}")

            return "".join(result_lines) if result_lines else "(空のファイル)"

        except PermissionError:
            return f"エラー: ファイルを読む権限がありません: {path}"
        except Exception as e:
            return f"エラー: ファイル読み込み失敗: {e}"

    async def _write(self, file_path: str, content: str) -> str:
        """ファイルを書き込む（親ディレクトリが無ければ作成）"""
        path = Path(file_path)
        if not path.is_absolute():
            path = Path(self.work_dir) / path

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            return f"ファイルを書き込みました: {path}\n({len(content)} 文字)"
        except PermissionError:
            return f"エラー: 書き込み権限がありません: {path}"
        except Exception as e:
            return f"エラー: ファイル書き込み失敗: {e}"

    async def _edit(self, file_path: str, old_string: str, new_string: str,
                    replace_all: bool = False) -> str:
        """ファイルの一部を編集する"""
        path = Path(file_path)
        if not path.is_absolute():
            path = Path(self.work_dir) / path

        if not path.exists():
            return f"エラー: ファイルが存在しません（Read で確認してから Edit を使うこと）: {path}"

        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()

            # old_string の存在確認
            count = content.count(old_string)
            if count == 0:
                return (
                    f"エラー: 指定した文字列がファイル内に見つかりません。\n"
                    f"ファイル: {path}\n"
                    f"検索文字列:\n{old_string[:200]}"
                )

            # replace_all=False の場合、一意性チェック
            if not replace_all and count > 1:
                return (
                    f"エラー: 指定した文字列がファイル内に {count} 箇所見つかりました。\n"
                    f"一意な文字列を指定するか、replace_all=True を使ってください。"
                )

            if replace_all:
                new_content = content.replace(old_string, new_string)
                replaced_count = count
            else:
                new_content = content.replace(old_string, new_string, 1)
                replaced_count = 1

            with open(path, "w", encoding="utf-8") as f:
                f.write(new_content)

            return f"編集完了: {path}\n({replaced_count} 箇所を置換)"

        except Exception as e:
            return f"エラー: ファイル編集失敗: {e}"

    async def _glob(self, pattern: str, path: Optional[str] = None) -> str:
        """glob パターンでファイルを検索する"""
        search_dir = path if path else self.work_dir
        if not os.path.isabs(search_dir):
            search_dir = os.path.join(self.work_dir, search_dir)

        try:
            # ** パターンを再帰的に処理
            full_pattern = os.path.join(search_dir, pattern)
            matches = glob_module.glob(full_pattern, recursive=True)

            # 更新時刻の降順でソート
            matches.sort(key=lambda p: os.path.getmtime(p) if os.path.exists(p) else 0,
                         reverse=True)

            if not matches:
                return f"パターン '{pattern}' にマッチするファイルが見つかりませんでした。"

            return "\n".join(matches)

        except Exception as e:
            return f"エラー: Glob 検索失敗: {e}"

    async def _grep(self, pattern: str, path: Optional[str] = None,
                    glob: Optional[str] = None, output_mode: str = "files_with_matches",
                    case_insensitive: bool = False, context: int = 0) -> str:
        """ripgrep を使ってファイルを検索する"""
        # rg コマンドの構築
        cmd = ["rg"]

        if case_insensitive:
            cmd.append("-i")

        if context > 0:
            cmd.extend(["-C", str(context)])

        # 出力モードの設定
        if output_mode == "files_with_matches":
            cmd.append("-l")
        elif output_mode == "count":
            cmd.append("-c")
        else:  # content
            cmd.extend(["-n"])  # 行番号付き

        # glob フィルタ
        if glob:
            cmd.extend(["--glob", glob])

        cmd.append(pattern)

        if path:
            search_path = path if os.path.isabs(path) else os.path.join(self.work_dir, path)
            cmd.append(search_path)
        else:
            cmd.append(self.work_dir)

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)

            output = stdout.decode("utf-8", errors="replace")
            if not output.strip():
                # rg は見つからなかった時に exit code 1 を返す（エラーではない）
                return f"パターン '{pattern}' にマッチする内容が見つかりませんでした。"

            return output

        except FileNotFoundError:
            # rg が無い場合は grep にフォールバック
            return await self._grep_fallback(pattern, path, glob, output_mode,
                                             case_insensitive, context)
        except asyncio.TimeoutError:
            return "エラー: grep タイムアウト（30秒）"
        except Exception as e:
            return f"エラー: Grep 失敗: {e}"

    async def _grep_fallback(self, pattern: str, path: Optional[str], glob_pattern: Optional[str],
                             output_mode: str, case_insensitive: bool, context: int) -> str:
        """ripgrep が無い場合の Python フォールバック実装"""
        search_path = path if path else self.work_dir
        if not os.path.isabs(search_path):
            search_path = os.path.join(self.work_dir, search_path)

        flags = re.IGNORECASE if case_insensitive else 0
        try:
            regex = re.compile(pattern, flags)
        except re.error as e:
            return f"エラー: 正規表現が無効です: {e}"

        results = []
        search_root = Path(search_path)

        if search_root.is_file():
            files = [search_root]
        else:
            if glob_pattern:
                files = list(search_root.glob(glob_pattern))
            else:
                files = [f for f in search_root.rglob("*") if f.is_file()]

        for filepath in files:
            try:
                with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                    lines = f.readlines()

                matches = [(i + 1, line) for i, line in enumerate(lines) if regex.search(line)]

                if matches:
                    if output_mode == "files_with_matches":
                        results.append(str(filepath))
                    elif output_mode == "count":
                        results.append(f"{filepath}: {len(matches)}")
                    else:  # content
                        for lineno, line in matches:
                            results.append(f"{filepath}:{lineno}:{line.rstrip()}")

            except Exception:
                continue

        return "\n".join(results) if results else f"パターン '{pattern}' にマッチする内容が見つかりませんでした。"

    async def _bash(self, command: str, timeout: int = 120,
                    work_dir: Optional[str] = None) -> str:
        """シェルコマンドを実行する"""
        # タイムアウト上限を適用
        timeout = min(timeout, 600)
        cwd = work_dir if work_dir else self.work_dir

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env={**os.environ},
            )

            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )

            stdout_str = stdout.decode("utf-8", errors="replace")
            stderr_str = stderr.decode("utf-8", errors="replace")
            exit_code = proc.returncode

            result_parts = []
            if stdout_str.strip():
                result_parts.append(stdout_str)
            if stderr_str.strip():
                result_parts.append(f"[stderr]\n{stderr_str}")
            if exit_code != 0:
                result_parts.append(f"[exit code: {exit_code}]")

            return "\n".join(result_parts) if result_parts else "(出力なし)"

        except asyncio.TimeoutError:
            return f"エラー: コマンドがタイムアウトしました（{timeout}秒）"
        except Exception as e:
            return f"エラー: Bash 実行失敗: {e}"

    async def _web_search(self, query: str, num_results: int = 5) -> str:
        """Web 検索を実行する（DuckDuckGo API を使用）"""
        # キャッシュチェック
        cache_key = f"{query}:{num_results}"
        if cache_key in self._web_cache:
            return self._web_cache[cache_key]

        try:
            # DuckDuckGo Instant Answer API（無料・APIキー不要）
            params = {
                "q": query,
                "format": "json",
                "no_html": "1",
                "skip_disambig": "1",
            }

            async with aiohttp.ClientSession() as session:
                async with session.get(
                    "https://api.duckduckgo.com/",
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status != 200:
                        return f"エラー: Web 検索 API が {resp.status} を返しました"
                    data = await resp.json(content_type=None)

            results = []

            # Abstract（概要）
            if data.get("Abstract"):
                results.append(f"## 概要\n{data['Abstract']}")
                if data.get("AbstractURL"):
                    results.append(f"出典: {data['AbstractURL']}")

            # RelatedTopics（関連トピック）
            topics = data.get("RelatedTopics", [])[:num_results]
            if topics:
                results.append("\n## 関連情報")
                for topic in topics:
                    if isinstance(topic, dict) and topic.get("Text"):
                        text = topic["Text"][:200]
                        url = topic.get("FirstURL", "")
                        results.append(f"- {text}\n  URL: {url}")

            if not results:
                # フォールバック: SearXNG や他の検索エンジンを試みる
                results.append(
                    f"検索結果が見つかりませんでした。\n"
                    f"クエリ: {query}\n"
                    f"ヒント: WebFetch で直接URLを取得してください。"
                )

            output = "\n".join(results)
            self._web_cache[cache_key] = output
            return output

        except aiohttp.ClientError as e:
            return f"エラー: Web 検索のネットワークエラー: {e}"
        except Exception as e:
            return f"エラー: Web 検索失敗: {e}"

    async def _web_fetch(self, url: str, prompt: Optional[str] = None) -> str:
        """URL のコンテンツを取得して Markdown に変換する"""
        # HTTP を HTTPS に自動アップグレード
        if url.startswith("http://"):
            url = "https://" + url[7:]

        try:
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (compatible; coding-agent/1.0)"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            }

            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=30),
                    allow_redirects=True,
                ) as resp:
                    if resp.status != 200:
                        return f"エラー: URL が {resp.status} を返しました: {url}"

                    content_type = resp.headers.get("Content-Type", "")
                    content = await resp.text(errors="replace")

            # HTMLを簡易的にMarkdown変換
            markdown = self._html_to_markdown(content)

            # prompt が指定されている場合はその指示に従って抽出
            if prompt:
                result = f"URL: {url}\n\nプロンプト: {prompt}\n\n内容:\n{markdown}"
            else:
                result = f"URL: {url}\n\n{markdown}"

            return result

        except aiohttp.ClientError as e:
            return f"エラー: URL の取得に失敗しました: {e}"
        except Exception as e:
            return f"エラー: WebFetch 失敗: {e}"

    def _html_to_markdown(self, html: str) -> str:
        """HTML を簡易的に Markdown に変換する"""
        # script, style タグを除去
        html = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)

        # よく使われる HTML タグを Markdown に変換
        html = re.sub(r"<h1[^>]*>(.*?)</h1>", r"# \1\n", html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r"<h2[^>]*>(.*?)</h2>", r"## \1\n", html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r"<h3[^>]*>(.*?)</h3>", r"### \1\n", html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r"<strong[^>]*>(.*?)</strong>", r"**\1**", html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r"<b[^>]*>(.*?)</b>", r"**\1**", html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r"<em[^>]*>(.*?)</em>", r"*\1*", html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r"<code[^>]*>(.*?)</code>", r"`\1`", html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r"<pre[^>]*>(.*?)</pre>", r"```\n\1\n```", html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r"<a[^>]*href=['\"]([^'\"]*)['\"][^>]*>(.*?)</a>", r"[\2](\1)", html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r"<li[^>]*>(.*?)</li>", r"- \1", html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r"<p[^>]*>(.*?)</p>", r"\1\n", html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r"<br[^>]*>", "\n", html, flags=re.IGNORECASE)

        # 残りのHTMLタグを除去
        html = re.sub(r"<[^>]+>", "", html)

        # HTMLエンティティをデコード
        html = html.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
        html = html.replace("&quot;", '"').replace("&#39;", "'").replace("&nbsp;", " ")

        # 連続する空行を2行に圧縮
        html = re.sub(r"\n{3,}", "\n\n", html)

        return html.strip()

    async def _ls(self, path: Optional[str] = None) -> str:
        """ディレクトリの内容を一覧表示する"""
        target = path if path else self.work_dir
        if not os.path.isabs(target):
            target = os.path.join(self.work_dir, target)

        try:
            if not os.path.exists(target):
                return f"エラー: パスが存在しません: {target}"
            if not os.path.isdir(target):
                return f"エラー: '{target}' はディレクトリではありません"

            entries = []
            for entry in sorted(os.scandir(target), key=lambda e: (not e.is_dir(), e.name)):
                if entry.is_dir():
                    entries.append(f"  {entry.name}/")
                else:
                    size = entry.stat().st_size
                    size_str = f"{size:,}" if size < 1_000_000 else f"{size / 1_000_000:.1f}M"
                    entries.append(f"  {entry.name} ({size_str} bytes)")

            return f"{target}:\n" + "\n".join(entries)

        except PermissionError:
            return f"エラー: ディレクトリを読む権限がありません: {target}"
        except Exception as e:
            return f"エラー: LS 失敗: {e}"

    async def _todo_read(self) -> str:
        """ToDoリストを読み込む"""
        if not self._todos:
            return "ToDoリストは空です。"

        lines = ["# ToDo リスト\n"]
        status_icon = {"pending": "○", "in_progress": "◑", "completed": "●"}
        priority_icon = {"high": "!!!", "medium": "!!", "low": "!"}

        for todo in self._todos:
            icon = status_icon.get(todo["status"], "?")
            pri = priority_icon.get(todo["priority"], "")
            lines.append(f"{icon} [{todo['id']}] {pri} {todo['content']} ({todo['status']})")

        return "\n".join(lines)

    async def _todo_write(self, todos: list[dict]) -> str:
        """ToDoリストを更新する"""
        self._todos = todos
        return f"ToDoリストを更新しました（{len(todos)} 件）"
