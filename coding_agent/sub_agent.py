"""
sub_agent.py - サブエージェント管理モジュール

複数のサブエージェントを並列実行してタスクを効率化する。
各サブエージェントはメインエージェントから独立した会話コンテキストを持つ。
"""

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Coroutine, Optional

from openai import AsyncOpenAI

from .config import Config, SUB_AGENT_SYSTEM_PROMPT
from .context_manager import ContextManager
from .tools import TOOL_DEFINITIONS, ToolExecutor


class SubAgentStatus(Enum):
    """サブエージェントの実行状態"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class SubAgentTask:
    """サブエージェントに割り当てるタスク"""
    id: str
    description: str  # タスクの説明
    instructions: str  # 詳細な指示
    context: Optional[str] = None  # 追加コンテキスト（ファイル内容など）
    tools: Optional[list[str]] = None  # 使用を許可するツール（None=全て）


@dataclass
class SubAgentResult:
    """サブエージェントの実行結果"""
    task_id: str
    status: SubAgentStatus
    output: str  # 最終出力テキスト
    tool_calls_count: int = 0  # 実行したツール呼び出し数
    elapsed_seconds: float = 0.0
    error: Optional[str] = None


class SubAgent:
    """個別のサブエージェントインスタンス"""

    def __init__(
        self,
        task: SubAgentTask,
        client: AsyncOpenAI,
        config: Config,
        progress_callback: Optional[Callable[[str, str], None]] = None,
    ):
        self.task = task
        self.client = client
        self.config = config
        self.progress_callback = progress_callback
        self._tool_executor = ToolExecutor(
            work_dir=config.agent.work_dir,
            tool_timeout=config.agent.tool_timeout,
            tool_output_max_chars=config.agent.tool_output_max_chars,
        )
        self._context = ContextManager(
            client=client,
            model=config.llm.model,
            max_tokens=config.llm.max_tokens,
            compress_threshold=config.agent.context_compress_threshold,
        )

    async def run(self) -> SubAgentResult:
        """サブエージェントを実行してタスクを完了する"""
        start_time = time.monotonic()
        tool_calls_count = 0

        # ツール定義のフィルタリング（許可リストがある場合）
        allowed_tools = TOOL_DEFINITIONS
        if self.task.tools is not None:
            allowed_tools = [
                t for t in TOOL_DEFINITIONS
                if t["function"]["name"] in self.task.tools
            ]

        # システムメッセージを設定
        system_content = SUB_AGENT_SYSTEM_PROMPT
        if self.task.context:
            system_content += f"\n\n## 提供されたコンテキスト\n{self.task.context}"

        self._context.set_system_message(system_content)

        # タスク指示をユーザーメッセージとして追加
        user_content = f"## タスク: {self.task.description}\n\n{self.task.instructions}"
        self._context.add_message("user", user_content)

        # エージェントループ
        for iteration in range(self.config.agent.max_iterations):
            # コンテキスト圧縮チェック
            await self._context.compress_if_needed()

            # LLM に問い合わせ
            try:
                messages = self._context.get_messages_for_api()
                kwargs: dict[str, Any] = {
                    "model": self.config.llm.model,
                    "messages": messages,
                    "max_tokens": self.config.llm.max_tokens,
                    "temperature": self.config.llm.temperature,
                    "top_p": self.config.llm.top_p,
                }

                if self.config.llm.use_native_tool_call and allowed_tools:
                    kwargs["tools"] = allowed_tools
                    kwargs["tool_choice"] = "auto"

                response = await self.client.chat.completions.create(**kwargs)
                choice = response.choices[0]

            except Exception as e:
                return SubAgentResult(
                    task_id=self.task.id,
                    status=SubAgentStatus.FAILED,
                    output="",
                    error=f"API エラー: {e}",
                    elapsed_seconds=time.monotonic() - start_time,
                )

            message = choice.message
            finish_reason = choice.finish_reason

            # アシスタントメッセージを記録
            self._context.add_message(
                "assistant",
                message.content or "",
                tool_calls=message.tool_calls,
            )

            # ツール呼び出しの処理
            if message.tool_calls:
                # 全ツール呼び出しを並列実行
                tool_tasks = []
                for tc in message.tool_calls:
                    tool_name = tc.function.name
                    try:
                        tool_args = json.loads(tc.function.arguments)
                    except json.JSONDecodeError:
                        tool_args = {}
                    tool_tasks.append(self._execute_tool(tc.id, tool_name, tool_args))

                tool_results = await asyncio.gather(*tool_tasks, return_exceptions=True)
                tool_calls_count += len(tool_tasks)

                # ツール結果をコンテキストに追加
                for i, tc in enumerate(message.tool_calls):
                    result = tool_results[i]
                    if isinstance(result, Exception):
                        result_content = f"エラー: {result}"
                    else:
                        result_content = str(result)

                    self._context.add_message(
                        "tool",
                        result_content,
                        tool_call_id=tc.id,
                    )

                    if self.progress_callback:
                        self.progress_callback(
                            self.task.id,
                            f"ツール実行: {tc.function.name}"
                        )

                continue  # 次のイテレーションへ

            # finish_reason が stop または length の場合は終了
            elif finish_reason in ("stop", "length", "eos"):
                output = message.content or ""
                return SubAgentResult(
                    task_id=self.task.id,
                    status=SubAgentStatus.COMPLETED,
                    output=output,
                    tool_calls_count=tool_calls_count,
                    elapsed_seconds=time.monotonic() - start_time,
                )

            # ネイティブツールコールが無い場合のフォールバック（JSON パース）
            elif not self.config.llm.use_native_tool_call:
                content = message.content or ""
                tool_call = self._parse_tool_call_from_text(content)

                if tool_call:
                    tool_name, tool_args = tool_call
                    tool_call_id = f"fallback_{uuid.uuid4().hex[:8]}"
                    result = await self._execute_tool(tool_call_id, tool_name, tool_args)
                    self._context.add_message("user", f"[ツール結果 - {tool_name}]\n{result}")
                    tool_calls_count += 1
                    continue
                else:
                    # ツール呼び出しも無く、stop でもない場合は出力として扱う
                    return SubAgentResult(
                        task_id=self.task.id,
                        status=SubAgentStatus.COMPLETED,
                        output=content,
                        tool_calls_count=tool_calls_count,
                        elapsed_seconds=time.monotonic() - start_time,
                    )

            else:
                # 予期しない終了
                return SubAgentResult(
                    task_id=self.task.id,
                    status=SubAgentStatus.COMPLETED,
                    output=message.content or "",
                    tool_calls_count=tool_calls_count,
                    elapsed_seconds=time.monotonic() - start_time,
                )

        # 最大イテレーション到達
        return SubAgentResult(
            task_id=self.task.id,
            status=SubAgentStatus.FAILED,
            output="",
            error=f"最大イテレーション数 {self.config.agent.max_iterations} に達しました",
            elapsed_seconds=time.monotonic() - start_time,
        )

    async def _execute_tool(self, tool_call_id: str, tool_name: str, tool_args: dict) -> str:
        """ツールを実行する"""
        return await self._tool_executor.execute(tool_name, tool_args)

    def _parse_tool_call_from_text(self, text: str) -> Optional[tuple[str, dict]]:
        """
        テキストからツール呼び出しを解析する（ネイティブツールコール非対応モデル用）

        モデルが以下の形式で出力することを期待：
        ```json
        {
          "tool": "Bash",
          "args": {"command": "ls -la"}
        }
        ```
        """
        import re

        # JSON ブロックを探す
        json_pattern = r"```(?:json)?\s*(\{.*?\})\s*```"
        matches = re.findall(json_pattern, text, re.DOTALL)

        for match in matches:
            try:
                data = json.loads(match)
                if "tool" in data and "args" in data:
                    return data["tool"], data["args"]
                # 別の形式もサポート
                if "name" in data and "parameters" in data:
                    return data["name"], data["parameters"]
            except json.JSONDecodeError:
                continue

        # タグ形式も試す: <tool_call>{"name": "...", "args": {...}}</tool_call>
        tag_pattern = r"<tool_call>(.*?)</tool_call>"
        matches = re.findall(tag_pattern, text, re.DOTALL)
        for match in matches:
            try:
                data = json.loads(match.strip())
                name = data.get("name") or data.get("tool")
                args = data.get("args") or data.get("parameters") or data.get("arguments", {})
                if name:
                    return name, args
            except json.JSONDecodeError:
                continue

        return None


class SubAgentManager:
    """複数のサブエージェントを管理するクラス"""

    def __init__(self, client: AsyncOpenAI, config: Config):
        self.client = client
        self.config = config
        self._active_agents: dict[str, SubAgent] = {}
        self._results: dict[str, SubAgentResult] = {}

    async def run_parallel(
        self,
        tasks: list[SubAgentTask],
        progress_callback: Optional[Callable[[str, str], None]] = None,
    ) -> list[SubAgentResult]:
        """
        複数のタスクを並列で実行する

        Args:
            tasks: 実行するタスクのリスト
            progress_callback: 進捗報告コールバック (task_id, message) -> None

        Returns:
            各タスクの結果リスト（入力順）
        """
        # 最大並列数で制限
        semaphore = asyncio.Semaphore(self.config.agent.max_parallel_agents)

        async def run_with_semaphore(task: SubAgentTask) -> SubAgentResult:
            async with semaphore:
                agent = SubAgent(
                    task=task,
                    client=self.client,
                    config=self.config,
                    progress_callback=progress_callback,
                )
                self._active_agents[task.id] = agent

                if progress_callback:
                    progress_callback(task.id, f"開始: {task.description}")

                result = await agent.run()
                self._results[task.id] = result
                del self._active_agents[task.id]

                if progress_callback:
                    status_str = "完了" if result.status == SubAgentStatus.COMPLETED else "失敗"
                    progress_callback(
                        task.id,
                        f"{status_str}: {task.description} ({result.elapsed_seconds:.1f}秒)"
                    )

                return result

        # 全タスクを並列実行
        results = await asyncio.gather(
            *[run_with_semaphore(task) for task in tasks],
            return_exceptions=True,
        )

        # Exception を SubAgentResult に変換
        final_results = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                final_results.append(SubAgentResult(
                    task_id=tasks[i].id,
                    status=SubAgentStatus.FAILED,
                    output="",
                    error=f"予期しないエラー: {result}",
                ))
            else:
                final_results.append(result)

        return final_results

    async def run_single(
        self,
        task: SubAgentTask,
        progress_callback: Optional[Callable[[str, str], None]] = None,
    ) -> SubAgentResult:
        """単一のサブエージェントを実行する"""
        results = await self.run_parallel([task], progress_callback)
        return results[0]

    def format_results(self, results: list[SubAgentResult]) -> str:
        """複数のサブエージェント結果をフォーマットする"""
        lines = ["## サブエージェント実行結果\n"]

        for result in results:
            status_icon = {
                SubAgentStatus.COMPLETED: "OK",
                SubAgentStatus.FAILED: "NG",
                SubAgentStatus.CANCELLED: "--",
            }.get(result.status, "?")

            lines.append(f"### [{status_icon}] タスク: {result.task_id}")
            lines.append(f"- 実行時間: {result.elapsed_seconds:.1f}秒")
            lines.append(f"- ツール呼び出し: {result.tool_calls_count}回")

            if result.error:
                lines.append(f"- エラー: {result.error}")
            elif result.output:
                lines.append(f"\n{result.output}")

            lines.append("")

        return "\n".join(lines)
