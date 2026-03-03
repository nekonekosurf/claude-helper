"""
context_manager.py - コンテキストウィンドウ管理モジュール

会話履歴のトークン数を管理し、コンテキストが溢れそうになったら
古いメッセージを圧縮・要約して新しい会話に引き継ぐ。
"""

import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Optional

from openai import AsyncOpenAI


@dataclass
class Message:
    """会話メッセージ"""
    role: str  # "system" | "user" | "assistant" | "tool"
    content: str | list  # テキスト、またはtool_callsのリスト
    tool_call_id: Optional[str] = None  # tool メッセージの場合
    tool_calls: Optional[list] = None  # assistant メッセージのツール呼び出し
    tokens: int = 0  # 推定トークン数

    def to_openai_format(self) -> dict:
        """OpenAI API 形式に変換する"""
        msg: dict = {"role": self.role}

        if self.role == "tool":
            msg["content"] = self.content if isinstance(self.content, str) else str(self.content)
            if self.tool_call_id:
                msg["tool_call_id"] = self.tool_call_id
        elif self.tool_calls:
            msg["content"] = self.content if isinstance(self.content, str) else ""
            msg["tool_calls"] = self.tool_calls
        else:
            msg["content"] = self.content if isinstance(self.content, str) else json.dumps(self.content, ensure_ascii=False)

        return msg


class TokenCounter:
    """トークン数の推定（tiktoken が無い場合の簡易実装）"""

    @staticmethod
    def estimate(text: str) -> int:
        """テキストのトークン数を推定する（1トークン ≈ 4文字）"""
        if not text:
            return 0
        # 日本語は1文字≒2トークン、英語は1文字≒0.25トークンとして混合推定
        jp_count = sum(1 for c in text if "\u3000" <= c <= "\u9fff" or "\uff00" <= c <= "\uffef")
        en_count = len(text) - jp_count
        return int(jp_count * 2 + en_count * 0.25) + 4  # +4はオーバーヘッド

    @classmethod
    def count_message(cls, msg: Message) -> int:
        """メッセージのトークン数を推定する"""
        total = 4  # メッセージオーバーヘッド
        content = msg.content

        if isinstance(content, str):
            total += cls.estimate(content)
        elif isinstance(content, list):
            for item in content:
                if isinstance(item, dict):
                    total += cls.estimate(json.dumps(item, ensure_ascii=False))

        if msg.tool_calls:
            total += cls.estimate(json.dumps(msg.tool_calls, ensure_ascii=False))

        return total


class ContextManager:
    """コンテキストウィンドウ管理クラス"""

    def __init__(
        self,
        client: AsyncOpenAI,
        model: str,
        max_tokens: int = 4096,
        compress_threshold: int = 30000,
    ):
        self.client = client
        self.model = model
        self.max_tokens = max_tokens
        self.compress_threshold = compress_threshold

        # メッセージ履歴
        self._messages: list[Message] = []
        # システムメッセージ（圧縮時も保持）
        self._system_message: Optional[Message] = None
        # 圧縮済みサマリー（コンテキスト継続性のため保持）
        self._summaries: list[str] = []
        # 現在のトークン数
        self._current_tokens: int = 0

    @property
    def messages(self) -> list[Message]:
        return self._messages

    @property
    def total_tokens(self) -> int:
        return self._current_tokens

    def set_system_message(self, content: str) -> None:
        """システムメッセージを設定する（圧縮時も保持される）"""
        self._system_message = Message(
            role="system",
            content=content,
            tokens=TokenCounter.estimate(content),
        )

    def add_message(self, role: str, content: str | list,
                    tool_call_id: Optional[str] = None,
                    tool_calls: Optional[list] = None) -> Message:
        """メッセージを追加する"""
        msg = Message(
            role=role,
            content=content,
            tool_call_id=tool_call_id,
            tool_calls=tool_calls,
        )
        msg.tokens = TokenCounter.count_message(msg)
        self._messages.append(msg)
        self._current_tokens += msg.tokens
        return msg

    def get_messages_for_api(self) -> list[dict]:
        """API 呼び出し用のメッセージリストを返す"""
        result = []

        # システムメッセージ（サマリーを含む場合は更新）
        if self._system_message:
            system_content = self._system_message.content
            if self._summaries:
                system_content = (
                    system_content
                    + "\n\n## 会話の要約（圧縮済み）\n"
                    + "\n---\n".join(self._summaries)
                )
            result.append({"role": "system", "content": system_content})

        # 通常メッセージ
        for msg in self._messages:
            result.append(msg.to_openai_format())

        return result

    async def compress_if_needed(self) -> bool:
        """必要に応じてコンテキストを圧縮する"""
        if self._current_tokens < self.compress_threshold:
            return False

        await self._compress_old_messages()
        return True

    async def _compress_old_messages(self) -> None:
        """古いメッセージを圧縮してサマリーを生成する"""
        if len(self._messages) < 4:
            return  # 少なすぎる場合は圧縮しない

        # 圧縮するメッセージ数（全体の半分）
        compress_count = len(self._messages) // 2
        messages_to_compress = self._messages[:compress_count]
        self._messages = self._messages[compress_count:]

        # 圧縮するメッセージのテキスト化
        text_parts = []
        for msg in messages_to_compress:
            if msg.role == "system":
                continue
            content = msg.content if isinstance(msg.content, str) else json.dumps(msg.content, ensure_ascii=False)
            text_parts.append(f"[{msg.role}]: {content[:500]}")

        conversation_text = "\n".join(text_parts)

        # LLM を使って要約を生成する
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": "あなたはコードエディタのアシスタントです。会話の要点を簡潔に要約してください。",
                    },
                    {
                        "role": "user",
                        "content": (
                            "以下の会話を3-5行で要約してください。"
                            "完了したタスク、決定事項、重要なファイルパスを含めてください：\n\n"
                            + conversation_text
                        ),
                    },
                ],
                max_tokens=500,
                temperature=0.1,
            )
            summary = response.choices[0].message.content or ""
        except Exception:
            # 要約に失敗した場合は機械的に切り詰める
            summary = conversation_text[:1000] + "...[圧縮]"

        self._summaries.append(summary)

        # トークン数を再計算
        self._current_tokens = sum(m.tokens for m in self._messages)
        if self._system_message:
            self._current_tokens += self._system_message.tokens

    def save_session(self, session_file: str) -> None:
        """セッションをファイルに保存する"""
        import os
        os.makedirs(os.path.dirname(session_file), exist_ok=True)

        session_data = {
            "messages": [
                {
                    "role": m.role,
                    "content": m.content,
                    "tool_call_id": m.tool_call_id,
                    "tool_calls": m.tool_calls,
                }
                for m in self._messages
            ],
            "summaries": self._summaries,
            "system_message": self._system_message.content if self._system_message else None,
        }

        with open(session_file, "w", encoding="utf-8") as f:
            json.dump(session_data, f, ensure_ascii=False, indent=2)

    def load_session(self, session_file: str) -> bool:
        """セッションをファイルから復元する"""
        try:
            with open(session_file, "r", encoding="utf-8") as f:
                session_data = json.load(f)

            self._messages = []
            for m in session_data.get("messages", []):
                self.add_message(
                    role=m["role"],
                    content=m["content"],
                    tool_call_id=m.get("tool_call_id"),
                    tool_calls=m.get("tool_calls"),
                )

            self._summaries = session_data.get("summaries", [])

            if session_data.get("system_message"):
                self.set_system_message(session_data["system_message"])

            return True
        except (FileNotFoundError, json.JSONDecodeError, KeyError):
            return False

    def clear(self) -> None:
        """会話履歴をクリアする"""
        self._messages = []
        self._summaries = []
        self._current_tokens = 0
