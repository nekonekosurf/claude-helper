"""
agent_core.py - メインのエージェントループ

Think → Tool Use → Observe → Think... のループを実装する。
Extended Thinking 相当の CoT（Chain of Thought）をプロンプト技法で実現する。
"""

import asyncio
import json
import re
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any, AsyncGenerator, Callable, Optional

from openai import AsyncOpenAI

from .config import (
    Config,
    PLAN_MODE_PROMPT,
    SYSTEM_PROMPT_BASE,
    SYSTEM_PROMPT_COT,
    SYSTEM_PROMPT_TOOLS,
    get_config,
)
from .context_manager import ContextManager
from .sub_agent import SubAgentManager, SubAgentTask
from .tools import TOOL_DEFINITIONS, ToolExecutor


class AgentMode(Enum):
    """エージェントの動作モード"""
    NORMAL = "normal"    # 通常の実行モード
    PLAN = "plan"        # 計画モード（実際には実行しない）
    SUBAGENT = "subagent"  # サブエージェントとして動作


@dataclass
class AgentResponse:
    """エージェントのレスポンス"""
    content: str              # 最終的なテキスト回答
    thinking: str = ""        # <thinking> タグ内の思考過程
    tool_calls_count: int = 0  # 実行したツール呼び出し数
    elapsed_seconds: float = 0.0
    mode: str = "normal"


class AgentCore:
    """メインのコーディングエージェントクラス"""

    def __init__(self, config: Optional[Config] = None):
        self.config = config or get_config()

        # OpenAI 互換クライアント（vLLM に接続）
        self.client = AsyncOpenAI(
            base_url=self.config.llm.base_url,
            api_key=self.config.llm.api_key,
            timeout=self.config.llm.timeout,
        )

        # ツール実行クラス
        self.tool_executor = ToolExecutor(
            work_dir=self.config.agent.work_dir,
            tool_timeout=self.config.agent.tool_timeout,
            tool_output_max_chars=self.config.agent.tool_output_max_chars,
        )

        # コンテキスト管理
        self.context = ContextManager(
            client=self.client,
            model=self.config.llm.model,
            max_tokens=self.config.llm.max_tokens,
            compress_threshold=self.config.agent.context_compress_threshold,
        )

        # サブエージェント管理
        self.sub_agent_manager = SubAgentManager(self.client, self.config)

        # 動作モード
        self._mode = AgentMode.NORMAL

        # システムプロンプトを構築して設定
        self._setup_system_prompt()

    def _setup_system_prompt(self) -> None:
        """システムプロンプトを構築する"""
        parts = [SYSTEM_PROMPT_BASE, SYSTEM_PROMPT_TOOLS]

        # Extended Thinking 相当の CoT プロンプトを追加
        if self.config.agent.force_chain_of_thought:
            parts.append(SYSTEM_PROMPT_COT)

        # 計画モードの場合
        if self._mode == AgentMode.PLAN and self.config.agent.enable_plan_mode:
            parts.append(PLAN_MODE_PROMPT)

        self.context.set_system_message("\n\n".join(parts))

    def enter_plan_mode(self) -> None:
        """計画モードに切り替える"""
        self._mode = AgentMode.PLAN
        self._setup_system_prompt()

    def exit_plan_mode(self) -> None:
        """計画モードを終了する"""
        self._mode = AgentMode.NORMAL
        self._setup_system_prompt()

    @property
    def is_plan_mode(self) -> bool:
        return self._mode == AgentMode.PLAN

    async def chat(
        self,
        user_input: str,
        on_tool_call: Optional[Callable[[str, dict], None]] = None,
        on_tool_result: Optional[Callable[[str, str], None]] = None,
        on_thinking: Optional[Callable[[str], None]] = None,
    ) -> AgentResponse:
        """
        ユーザー入力に応答する（エージェントループを実行）

        Args:
            user_input: ユーザーからの入力
            on_tool_call: ツール呼び出し時のコールバック (tool_name, args)
            on_tool_result: ツール結果受け取り時のコールバック (tool_name, result)
            on_thinking: 思考過程を受け取った時のコールバック (thinking_text)

        Returns:
            AgentResponse: エージェントの最終回答
        """
        start_time = time.monotonic()
        tool_calls_count = 0
        all_thinking = []

        # ユーザーメッセージをコンテキストに追加
        self.context.add_message("user", user_input)

        # 計画モードは1回だけ実行
        max_iter = 1 if self.is_plan_mode else self.config.agent.max_iterations

        for iteration in range(max_iter):
            # コンテキスト圧縮チェック
            compressed = await self.context.compress_if_needed()
            if compressed:
                self._debug(f"コンテキストを圧縮しました（{self.context.total_tokens} トークン）")

            # LLM に問い合わせ
            messages = self.context.get_messages_for_api()
            response_message, finish_reason = await self._call_llm(messages)

            if response_message is None:
                break

            # アシスタントメッセージを記録
            self.context.add_message(
                "assistant",
                response_message.get("content") or "",
                tool_calls=response_message.get("tool_calls"),
            )

            # テキスト内容から<thinking>タグを抽出（CoT）
            content_text = response_message.get("content") or ""
            if content_text and "<thinking>" in content_text:
                thinking = self._extract_thinking(content_text)
                if thinking:
                    all_thinking.append(thinking)
                    if on_thinking:
                        on_thinking(thinking)
                    # thinking を除いた本文を再取得
                    content_text = self._remove_thinking(content_text)

            tool_calls = response_message.get("tool_calls")

            # ---- ツール呼び出しを処理 ----
            if tool_calls:
                # ネイティブツールコール形式
                tool_results = await self._execute_tool_calls(
                    tool_calls, on_tool_call, on_tool_result
                )
                tool_calls_count += len(tool_calls)

                # ツール結果をコンテキストに追加
                for tc_id, tool_name, result in tool_results:
                    self.context.add_message(
                        "tool",
                        result,
                        tool_call_id=tc_id,
                    )

                continue  # 次のイテレーションへ

            # ---- ネイティブコールなし → テキストからツール呼び出しをパース ----
            elif not self.config.llm.use_native_tool_call and content_text:
                tool_call = self._parse_tool_call_from_text(content_text)

                if tool_call:
                    tool_name, tool_args = tool_call
                    tool_call_id = f"fallback_{uuid.uuid4().hex[:8]}"

                    if on_tool_call:
                        on_tool_call(tool_name, tool_args)

                    result = await self.tool_executor.execute(tool_name, tool_args)

                    if on_tool_result:
                        on_tool_result(tool_name, result)

                    self.context.add_message(
                        "user",
                        f"[ツール結果 - {tool_name}]\n{result}"
                    )
                    tool_calls_count += 1
                    continue

            # ---- Task ツール（サブエージェント起動）の処理 ----
            if "Task" in content_text:
                sub_task = self._parse_sub_agent_task(content_text)
                if sub_task:
                    result = await self._run_sub_agent(sub_task, on_tool_call, on_tool_result)
                    self.context.add_message(
                        "user",
                        f"[サブエージェント結果]\n{result}"
                    )
                    tool_calls_count += 1
                    continue

            # ---- 終了条件（stop または生成完了） ----
            if finish_reason in ("stop", "length", "eos", None) and not tool_calls:
                final_content = content_text or (response_message.get("content") or "")
                return AgentResponse(
                    content=final_content,
                    thinking="\n---\n".join(all_thinking),
                    tool_calls_count=tool_calls_count,
                    elapsed_seconds=time.monotonic() - start_time,
                    mode="plan" if self.is_plan_mode else "normal",
                )

        # 最大イテレーション到達
        return AgentResponse(
            content=f"最大イテレーション数 ({self.config.agent.max_iterations}) に達しました。",
            thinking="\n---\n".join(all_thinking),
            tool_calls_count=tool_calls_count,
            elapsed_seconds=time.monotonic() - start_time,
        )

    async def _call_llm(self, messages: list[dict]) -> tuple[Optional[dict], Optional[str]]:
        """
        LLM を呼び出す

        Returns:
            (message_dict, finish_reason) or (None, None) on error
        """
        try:
            kwargs: dict[str, Any] = {
                "model": self.config.llm.model,
                "messages": messages,
                "max_tokens": self.config.llm.max_tokens,
                "temperature": self.config.llm.temperature,
                "top_p": self.config.llm.top_p,
            }

            # ネイティブツールコールが有効な場合
            if self.config.llm.use_native_tool_call and not self.is_plan_mode:
                kwargs["tools"] = TOOL_DEFINITIONS
                kwargs["tool_choice"] = "auto"

            response = await self.client.chat.completions.create(**kwargs)
            choice = response.choices[0]
            message = choice.message

            # レスポンスを辞書に変換
            message_dict: dict[str, Any] = {
                "role": "assistant",
                "content": message.content,
                "tool_calls": None,
            }

            if message.tool_calls:
                message_dict["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in message.tool_calls
                ]

            return message_dict, choice.finish_reason

        except Exception as e:
            self._debug(f"LLM 呼び出しエラー: {e}")
            # エラーをコンテキストに追加して終了
            error_msg = f"LLM API エラーが発生しました: {type(e).__name__}: {e}"
            return {"role": "assistant", "content": error_msg, "tool_calls": None}, "stop"

    async def _execute_tool_calls(
        self,
        tool_calls: list[dict],
        on_tool_call: Optional[Callable],
        on_tool_result: Optional[Callable],
    ) -> list[tuple[str, str, str]]:
        """
        複数のツール呼び出しを並列実行する

        Returns:
            [(tool_call_id, tool_name, result), ...]
        """
        async def execute_one(tc: dict) -> tuple[str, str, str]:
            tool_name = tc["function"]["name"]
            try:
                tool_args = json.loads(tc["function"]["arguments"])
            except json.JSONDecodeError:
                tool_args = {}

            if on_tool_call:
                on_tool_call(tool_name, tool_args)

            result = await self.tool_executor.execute(tool_name, tool_args)

            if on_tool_result:
                on_tool_result(tool_name, result)

            return tc["id"], tool_name, result

        # 全ツール呼び出しを並列実行
        results = await asyncio.gather(
            *[execute_one(tc) for tc in tool_calls],
            return_exceptions=True,
        )

        final_results = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                tc = tool_calls[i]
                final_results.append((
                    tc["id"],
                    tc["function"]["name"],
                    f"エラー: {result}",
                ))
            else:
                final_results.append(result)

        return final_results

    async def _run_sub_agent(
        self,
        task: SubAgentTask,
        on_tool_call: Optional[Callable],
        on_tool_result: Optional[Callable],
    ) -> str:
        """サブエージェントを実行する"""
        def progress_cb(task_id: str, msg: str) -> None:
            self._debug(f"[SubAgent {task_id}] {msg}")

        result = await self.sub_agent_manager.run_single(task, progress_cb)
        return result.output or f"エラー: {result.error}"

    def _extract_thinking(self, text: str) -> str:
        """<thinking> タグ内のテキストを抽出する"""
        matches = re.findall(r"<thinking>(.*?)</thinking>", text, re.DOTALL)
        return "\n".join(matches).strip()

    def _remove_thinking(self, text: str) -> str:
        """<thinking> タグとその内容をテキストから除去する"""
        return re.sub(r"<thinking>.*?</thinking>", "", text, flags=re.DOTALL).strip()

    def _parse_tool_call_from_text(self, text: str) -> Optional[tuple[str, dict]]:
        """
        テキストからツール呼び出しを解析する（フォールバック用）

        以下の形式をサポート:
        1. ```json {"tool": "...", "args": {...}} ```
        2. <tool_call>{"name": "...", "args": {...}}</tool_call>
        3. Action: ToolName\nActionInput: {...}
        """
        # JSON ブロック形式
        json_pattern = r"```(?:json)?\s*(\{.*?\})\s*```"
        for match in re.finditer(json_pattern, text, re.DOTALL):
            try:
                data = json.loads(match.group(1))
                tool_name = data.get("tool") or data.get("name")
                tool_args = data.get("args") or data.get("parameters") or data.get("arguments", {})
                if tool_name:
                    return tool_name, tool_args
            except json.JSONDecodeError:
                continue

        # タグ形式
        tag_pattern = r"<tool_call>(.*?)</tool_call>"
        for match in re.finditer(tag_pattern, text, re.DOTALL):
            try:
                data = json.loads(match.group(1).strip())
                tool_name = data.get("name") or data.get("tool")
                tool_args = data.get("args") or data.get("parameters") or data.get("arguments", {})
                if tool_name:
                    return tool_name, tool_args
            except json.JSONDecodeError:
                continue

        # ReAct 形式: Action: ToolName\nAction Input: {...}
        action_match = re.search(
            r"Action:\s*(\w+)\s*\nAction Input:\s*(\{.*?\})",
            text,
            re.DOTALL,
        )
        if action_match:
            tool_name = action_match.group(1)
            try:
                tool_args = json.loads(action_match.group(2))
                return tool_name, tool_args
            except json.JSONDecodeError:
                pass

        return None

    def _parse_sub_agent_task(self, text: str) -> Optional[SubAgentTask]:
        """テキストからサブエージェントタスクを解析する"""
        # <task> タグ形式
        task_match = re.search(r"<task>(.*?)</task>", text, re.DOTALL)
        if task_match:
            try:
                data = json.loads(task_match.group(1).strip())
                return SubAgentTask(
                    id=data.get("id", uuid.uuid4().hex[:8]),
                    description=data.get("description", "サブタスク"),
                    instructions=data.get("instructions", ""),
                    context=data.get("context"),
                    tools=data.get("tools"),
                )
            except json.JSONDecodeError:
                pass
        return None

    def clear_context(self) -> None:
        """会話コンテキストをクリアする"""
        self.context.clear()
        self._setup_system_prompt()

    def save_session(self, session_id: Optional[str] = None) -> str:
        """セッションを保存してファイルパスを返す"""
        import os
        sid = session_id or uuid.uuid4().hex[:8]
        session_file = os.path.join(self.config.agent.session_dir, f"session_{sid}.json")
        self.context.save_session(session_file)
        return session_file

    def load_session(self, session_file: str) -> bool:
        """セッションをファイルから復元する"""
        return self.context.load_session(session_file)

    def _debug(self, msg: str) -> None:
        """デバッグログを出力する"""
        if self.config.agent.debug:
            print(f"[DEBUG] {msg}")

    async def run_with_plan(self, user_input: str) -> AgentResponse:
        """
        計画モードで計画を立てた後、ユーザー確認を得て実行する

        計画 → 承認待ち → 実行 のフローを実装
        """
        # 計画フェーズ
        self.enter_plan_mode()
        plan_response = await self.chat(user_input)
        self.exit_plan_mode()

        # 計画を表示して確認を取る
        print("\n" + "=" * 60)
        print("計画:")
        print(plan_response.content)
        print("=" * 60)

        confirm = input("\nこの計画で実行しますか？ [y/N]: ").strip().lower()
        if confirm not in ("y", "yes"):
            return AgentResponse(
                content="実行をキャンセルしました。",
                elapsed_seconds=plan_response.elapsed_seconds,
            )

        # 実行フェーズ（新しいコンテキストで）
        # 計画の内容をシステムプロンプトに含めて実行
        execute_prompt = (
            f"以下の計画に従って実際に実行してください：\n\n"
            f"{plan_response.content}\n\n"
            f"元のタスク: {user_input}"
        )

        return await self.chat(execute_prompt)
