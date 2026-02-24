"""メインエージェントループ + CLI"""

import sys
import json
from src.llm_client import create_client, chat
from src.tools import TOOL_DEFINITIONS, execute_tool
from src.config import MAX_TURNS

SYSTEM_PROMPT = """\
あなたはコーディングアシスタントです。ユーザーの指示に従い、ファイル操作やコマンド実行を行います。

利用可能なツール:
- read_file: ファイルを読み取る
- write_file: ファイルを作成/上書きする
- edit_file: ファイル内のテキストを置換する
- bash: シェルコマンドを実行する

ルール:
- ファイルを編集する前に、必ず read_file で内容を確認してください
- 危険なコマンド（rm -rf, etc）は実行前にユーザーに確認を取ってください
- 回答は簡潔に、日本語で行ってください
- ツールを使う必要がある場合は積極的にツールを使ってください
"""


def run_agent_loop(client, model, messages: list) -> str | None:
    """エージェントループ: LLM呼び出し → ツール実行 → 繰り返し"""
    for turn in range(MAX_TURNS):
        response = chat(client, model, messages, tools=TOOL_DEFINITIONS)

        # ツール呼び出しがある場合
        if response.tool_calls:
            # アシスタントメッセージを履歴に追加
            messages.append(response.model_dump())

            for tc in response.tool_calls:
                fn_name = tc.function.name
                fn_args = tc.function.arguments
                print(f"  🔧 {fn_name}({_summarize_args(fn_args)})")

                result = execute_tool(fn_name, fn_args)

                # ツール結果を履歴に追加
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                })

            # ツール実行後、次のターンへ（LLMに結果を見せる）
            continue

        # テキスト応答の場合 → ループ終了
        content = response.content or ""
        messages.append({"role": "assistant", "content": content})
        return content

    return "(最大ターン数に達しました)"


def _summarize_args(args_json: str) -> str:
    """ツール引数を短く表示用にまとめる"""
    try:
        args = json.loads(args_json)
        parts = []
        for k, v in args.items():
            s = str(v)
            if len(s) > 40:
                s = s[:37] + "..."
            parts.append(f"{k}={s}")
        return ", ".join(parts)
    except Exception:
        return args_json[:60]


def run_single(question: str) -> str:
    """1つの質問を処理して回答を返す（テスト用）"""
    client, model = create_client()
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": question},
    ]
    return run_agent_loop(client, model, messages)


def main():
    """CLI エントリーポイント"""
    client, model = create_client()
    print(f"🤖 Agent ready ({model})")
    print("   'exit' で終了\n")

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    while True:
        try:
            user_input = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n👋 終了します")
            break

        if not user_input:
            continue
        if user_input.lower() in ("exit", "quit", "終了"):
            print("👋 終了します")
            break

        messages.append({"role": "user", "content": user_input})
        print()

        answer = run_agent_loop(client, model, messages)
        if answer:
            print(f"\n{answer}\n")


if __name__ == "__main__":
    main()
