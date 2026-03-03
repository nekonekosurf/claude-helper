"""
RAG統合コーディングエージェント

ローカルLLM（GPT-OSS, Gemma）に宇宙分野RAGを統合した
コーディングエージェントの実装例。

2つの統合パターンを実装:
  Pattern A: 常時RAG注入（全てのクエリにRAGコンテキストを付与）
  Pattern B: ツールとしてのRAG（LLMが必要に応じてRAGを呼び出す）

実行方法:
    # Pattern A: 常時RAG
    uv run python coding_agent/agent_with_rag.py --mode always "LEO衛星のMLI設計について"

    # Pattern B: ツールRAG
    uv run python coding_agent/agent_with_rag.py --mode tool "TCSの設計要件を教えて"

    # インタラクティブモード
    uv run python coding_agent/agent_with_rag.py --interactive
"""

from __future__ import annotations

import os
import json
import sys
import logging
from pathlib import Path

# プロジェクトルートをPythonパスに追加
sys.path.insert(0, str(Path(__file__).parent.parent))

from space_rag.rag_engine import SpaceRAG
from space_rag.space_glossary import build_context_header, extract_abbreviations_from_text

logger = logging.getLogger(__name__)


# ============================================================
# LLMクライアント設定
# ============================================================

def get_llm_client():
    """
    Cerebras API（OpenAI互換）クライアントを返す。
    ローカルのOllamaや他のOpenAI互換サーバーにも使える。
    """
    from openai import OpenAI
    from dotenv import load_dotenv
    load_dotenv()

    base_url = os.getenv("LLM_BASE_URL", "https://api.cerebras.ai/v1")
    api_key = os.getenv("CEREBRAS_API_KEY", os.getenv("OPENAI_API_KEY", "dummy"))
    model = os.getenv("LLM_MODEL", "gpt-oss-120b")

    client = OpenAI(base_url=base_url, api_key=api_key)
    return client, model


# ============================================================
# Pattern A: 常時RAG注入
# ============================================================

def ask_with_rag_context(
    question: str,
    rag: SpaceRAG,
    client,
    model: str,
    system_prompt: str = "",
    history: list[dict] | None = None,
    max_context_tokens: int = 3000,
) -> str:
    """
    パターンA: 全クエリにRAGコンテキストを注入するシンプルな統合。

    フロー:
      1. RAGでクエリに関連するチャンクを検索
      2. 検索結果をシステムプロンプトに注入
      3. LLMに送信して回答を生成

    適するケース:
    - クエリが常に宇宙分野に関連することがわかっている場合
    - シンプルさを優先する場合
    - レイテンシより精度を優先する場合
    """
    # RAG検索
    result = rag.retrieve(question)
    rag_context = rag.build_prompt_context(result, max_tokens=max_context_tokens)

    # システムプロンプトの構築
    if not system_prompt:
        system_prompt = (
            "あなたは宇宙・航空宇宙工学の専門家AIアシスタントです。\n"
            "JAXA、NASA、ESAの技術文書と宇宙工学の知識に基づいて回答します。\n"
            "不確かな情報は明示し、推測と事実を区別して回答してください。"
        )

    if rag_context:
        system_with_rag = (
            f"{system_prompt}\n\n"
            f"以下は参照すべき技術文書の内容です（検索結果）:\n\n"
            f"{rag_context}\n\n"
            f"上記の文書内容を参考に回答してください。"
        )
    else:
        system_with_rag = system_prompt

    # メッセージ構築
    messages = []
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": question})

    # LLM呼び出し
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": system_with_rag}] + messages,
        temperature=0.3,  # 技術文書の回答は低温で安定させる
        max_tokens=2048,
    )

    answer = response.choices[0].message.content

    # 検索メタ情報をログに残す
    if result.chunks:
        logger.debug(
            f"RAG: {len(result.chunks)} chunks, "
            f"methods={result.methods_used}, "
            f"elapsed={result.elapsed_ms:.0f}ms"
        )

    return answer


# ============================================================
# Pattern B: ツールとしてのRAG
# ============================================================

def ask_with_rag_as_tool(
    question: str,
    rag: SpaceRAG,
    client,
    model: str,
    system_prompt: str = "",
    history: list[dict] | None = None,
    max_tool_calls: int = 3,
) -> str:
    """
    パターンB: LLMがRAGをツールとして自律的に呼び出す統合。

    フロー:
      1. LLMにRAGツールを定義して送信
      2. LLMが必要と判断した場合にRAG検索ツールを呼び出す
      3. 検索結果を受け取り、最終回答を生成

    適するケース:
    - 全てのクエリが宇宙分野とは限らない汎用エージェント
    - LLMに何を検索するか判断させたい場合
    - ツールチェーン（複数ツールの組み合わせ）を使う場合

    注意: ツール呼び出し対応のモデルが必要。
          GPT-OSS-120b はFunctionCallingに対応している。
    """
    if not system_prompt:
        system_prompt = (
            "あなたは宇宙・航空宇宙工学の専門家AIアシスタントです。\n"
            "宇宙分野の専門知識が必要な場合は search_space_knowledge ツールを呼び出してください。\n"
            "一般的な質問には知識から直接回答し、専門文書が必要な場合のみツールを使ってください。"
        )

    # RAGをツールとして定義
    tools = [rag.as_tool()]

    messages = []
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": question})

    # ツール呼び出しループ
    tool_calls_count = 0

    while tool_calls_count < max_tool_calls:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": system_prompt}] + messages,
            tools=tools,
            tool_choice="auto",
            temperature=0.3,
            max_tokens=2048,
        )

        choice = response.choices[0]

        # ツール呼び出しがない場合 → 最終回答
        if choice.finish_reason != "tool_calls":
            return choice.message.content or ""

        # ツール呼び出しを処理
        tool_calls_count += 1
        messages.append({"role": "assistant", "content": None, "tool_calls": [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.function.name, "arguments": tc.function.arguments},
            }
            for tc in choice.message.tool_calls
        ]})

        for tool_call in choice.message.tool_calls:
            if tool_call.function.name == "search_space_knowledge":
                try:
                    args = json.loads(tool_call.function.arguments)
                    # RAG検索を実行
                    tool_result = rag.search_as_tool(
                        query=args["query"],
                        doc_filter=args.get("doc_filter"),
                    )
                except Exception as e:
                    tool_result = f"検索エラー: {e}"

                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": tool_result,
                })

                logger.debug(f"Tool call: search_space_knowledge({args.get('query', '')})")

    # 最大ツール呼び出し回数に達した場合は通常回答を要求
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": system_prompt}] + messages,
        temperature=0.3,
        max_tokens=2048,
    )
    return response.choices[0].message.content or ""


# ============================================================
# インタラクティブエージェント
# ============================================================

class SpaceAgent:
    """
    宇宙分野RAG統合コーディングエージェント

    会話履歴を保持しながら質問に回答する。
    パターンA（常時RAG）とパターンB（ツールRAG）を切り替え可能。
    """

    def __init__(self, mode: str = "always"):
        """
        Args:
            mode: "always" (パターンA) または "tool" (パターンB)
        """
        self.mode = mode
        self.rag = SpaceRAG(top_k=5, use_vector=True, use_domain_filter=True)
        self.client, self.model = get_llm_client()
        self.history: list[dict] = []
        self.system_prompt = (
            "あなたは宇宙・航空宇宙工学の専門家AIアシスタントです。\n"
            "JAXA JERG文書、NASA技術報告書、ESA文書などの技術文書と\n"
            "宇宙工学の知識に基づいて正確に回答します。\n"
            "略語は展開し、専門用語は簡潔に説明してください。\n"
            "不確かな情報には必ずその旨を明示してください。"
        )

    def ask(self, question: str) -> str:
        """質問に回答する"""
        if self.mode == "tool":
            answer = ask_with_rag_as_tool(
                question=question,
                rag=self.rag,
                client=self.client,
                model=self.model,
                system_prompt=self.system_prompt,
                history=self.history,
            )
        else:
            answer = ask_with_rag_context(
                question=question,
                rag=self.rag,
                client=self.client,
                model=self.model,
                system_prompt=self.system_prompt,
                history=self.history,
            )

        # 会話履歴を更新
        self.history.append({"role": "user", "content": question})
        self.history.append({"role": "assistant", "content": answer})

        # 履歴が長くなりすぎたら古いものを削除（直近10ターン保持）
        if len(self.history) > 20:
            self.history = self.history[-20:]

        return answer

    def interactive(self):
        """インタラクティブセッションを開始する"""
        print(f"宇宙AIエージェント (mode={self.mode})")
        print("終了するには 'q' または 'exit' を入力\n")

        while True:
            try:
                question = input("You: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n終了します")
                break

            if not question:
                continue
            if question.lower() in ("q", "exit", "quit"):
                print("終了します")
                break

            print("Agent: ", end="", flush=True)
            try:
                answer = self.ask(question)
                print(answer)
            except Exception as e:
                print(f"エラー: {e}")
            print()


# ============================================================
# CLI
# ============================================================

def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    mode = "always"
    interactive = False
    question = ""

    args = sys.argv[1:]
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--mode" and i + 1 < len(args):
            mode = args[i + 1]
            i += 2
        elif arg == "--interactive":
            interactive = True
            i += 1
        else:
            question = arg
            i += 1

    agent = SpaceAgent(mode=mode)

    if interactive:
        agent.interactive()
    elif question:
        print(f"Q: {question}")
        print(f"A: {agent.ask(question)}")
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
