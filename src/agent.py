"""メインエージェントループ + CLI - 全Phase統合"""

import sys
import json
from src.llm_client import create_client, chat
from src.tools import TOOL_DEFINITIONS, execute_tool
from src.config import MAX_TURNS
from src.prompt_builder import build_system_prompt
from src.session import (
    generate_session_id, save_session, load_session,
    list_sessions, get_latest_session_id,
)
from src.context import compress_context, estimate_messages_tokens
from src.memory import append_memory
from src.meta_agent import process_teach
from src.validator import run_validation
from src.knowledge import get_all_knowledge_summary


def run_agent_loop(client, model, messages: list) -> str | None:
    """エージェントループ: LLM呼び出し → ツール実行 → 繰り返し"""
    for turn in range(MAX_TURNS):
        # コンテキスト圧縮チェック
        messages = compress_context(client, model, messages)

        response = chat(client, model, messages, tools=TOOL_DEFINITIONS)

        # ツール呼び出しがある場合
        if response.tool_calls:
            messages.append(response.model_dump())

            for tc in response.tool_calls:
                fn_name = tc.function.name
                fn_args = tc.function.arguments
                print(f"  🔧 {fn_name}({_summarize_args(fn_args)})")

                result = execute_tool(fn_name, fn_args)

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                })

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


def _handle_command(cmd: str, client, model) -> str | None:
    """スラッシュコマンドを処理。処理した場合は結果文字列を返す"""
    if cmd.startswith("/teach "):
        instruction = cmd[7:].strip()
        if not instruction:
            return "使い方: /teach <指示>"
        return process_teach(client, model, instruction)

    elif cmd == "/validate":
        return run_validation()

    elif cmd == "/knowledge":
        return get_all_knowledge_summary()

    elif cmd == "/sessions":
        sessions = list_sessions()
        if not sessions:
            return "保存されたセッションはありません"
        lines = ["最近のセッション:"]
        for s in sessions:
            lines.append(f"  {s['session_id']} ({s['message_count']} messages, {s['saved_at']})")
        return "\n".join(lines)

    elif cmd.startswith("/remember "):
        entry = cmd[10:].strip()
        if entry:
            append_memory(f"- {entry}")
            return f"📝 記憶しました: {entry}"
        return "使い方: /remember <記憶する内容>"

    elif cmd == "/help":
        return (
            "コマンド一覧:\n"
            "  /teach <指示>  - ドメイン知識・ルーティングルールを追加\n"
            "  /validate      - 文書・ナレッジの整合性チェック\n"
            "  /knowledge     - 登録済みナレッジの一覧表示\n"
            "  /sessions      - 保存済みセッション一覧\n"
            "  /remember <内容> - 記憶に追記\n"
            "  /help          - このヘルプ\n"
            "  exit           - 終了"
        )

    return None


def run_single(question: str) -> str:
    """1つの質問を処理して回答を返す（テスト用）"""
    client, model = create_client()
    system_prompt = build_system_prompt(question)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": question},
    ]
    return run_agent_loop(client, model, messages)


def main():
    """CLI エントリーポイント"""
    import argparse
    parser = argparse.ArgumentParser(description="Claude Helper Agent")
    parser.add_argument("--continue", dest="continue_session", action="store_true",
                        help="前回のセッションを復元")
    parser.add_argument("--resume", type=str, help="指定セッションを復元")
    args = parser.parse_args()

    client, model = create_client()
    session_id = generate_session_id()
    messages = []

    # セッション復元
    if args.continue_session:
        last_id = get_latest_session_id()
        if last_id:
            messages, meta = load_session(last_id)
            session_id = last_id
            print(f"📂 セッション復元: {last_id} ({len(messages)} messages)")
        else:
            print("⚠️  復元可能なセッションがありません")
    elif args.resume:
        try:
            messages, meta = load_session(args.resume)
            session_id = args.resume
            print(f"📂 セッション復元: {args.resume} ({len(messages)} messages)")
        except FileNotFoundError:
            print(f"⚠️  セッションが見つかりません: {args.resume}")

    # システムプロンプトがなければ追加
    if not messages or messages[0].get("role") != "system":
        system_prompt = build_system_prompt()
        messages.insert(0, {"role": "system", "content": system_prompt})

    print(f"🤖 Agent ready ({model})")
    print(f"   Session: {session_id}")
    print("   '/help' でコマンド一覧, 'exit' で終了\n")

    try:
        while True:
            try:
                user_input = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n👋 終了します")
                break

            if not user_input:
                continue
            if user_input.lower() in ("exit", "quit", "終了"):
                break

            # スラッシュコマンド処理
            if user_input.startswith("/"):
                result = _handle_command(user_input, client, model)
                if result is not None:
                    print(f"\n{result}\n")
                    continue

            # 質問に応じてシステムプロンプトを動的に更新
            new_system = build_system_prompt(user_input)
            messages[0] = {"role": "system", "content": new_system}

            messages.append({"role": "user", "content": user_input})
            print()

            answer = run_agent_loop(client, model, messages)
            if answer:
                print(f"\n{answer}\n")

    finally:
        # セッション保存
        if len(messages) > 1:
            save_session(session_id, messages)
            print(f"💾 セッション保存: {session_id}")
        print("👋 終了します")


if __name__ == "__main__":
    main()
