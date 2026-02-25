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
from src.task_planner import (
    should_use_planner, create_plan_prompt, parse_plan_response,
    create_verify_prompt, parse_verify_response, create_synthesis_prompt,
    TaskPlan, TaskStep, TaskStatus,
)


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


def run_planned_execution(question: str, client, model: str, max_replan: int = 2) -> str:
    """Plan-Verify-Execute パターンでの実行

    1. LLMに計画を作成させる
    2. 各ステップを実行し、検証する
    3. 全ステップ完了後、結果を統合して最終回答を生成

    Args:
        question: ユーザーの質問
        client: LLMクライアント
        model: モデル名
        max_replan: 再計画の最大回数

    Returns:
        最終回答テキスト
    """
    # --- Phase 1: Plan ---
    print("  📋 計画を作成中...")
    plan_prompt = create_plan_prompt(question)
    plan_response = chat(
        client, model,
        [{"role": "user", "content": plan_prompt}],
        tools=None,
    )
    plan = parse_plan_response(plan_response.content or "", question)

    if not plan.steps:
        # 計画作成に失敗した場合、直接回答にフォールバック
        print("  ⚠️  計画作成失敗、直接回答にフォールバック")
        return None

    print(f"  📋 計画: {plan.goal}")
    print(f"     ステップ数: {len(plan.steps)}")
    plan.status = TaskStatus.IN_PROGRESS

    # --- Phase 2: Execute & Verify ---
    replan_count = 0
    step_index = 0

    while step_index < len(plan.steps):
        step = plan.steps[step_index]
        step.status = TaskStatus.IN_PROGRESS
        print(f"  ▶ ステップ {step_index + 1}/{len(plan.steps)}: {step.description}")

        # Execute: search or direct LLM query
        step_result = _execute_step(step, question, client, model)
        step.result = step_result

        if not step_result:
            print(f"    ⚠️  結果なし、スキップ")
            step.status = TaskStatus.FAILED
            step_index += 1
            continue

        # Verify
        verify_prompt = create_verify_prompt(plan, step_index)
        verify_response = chat(
            client, model,
            [{"role": "user", "content": verify_prompt}],
            tools=None,
        )
        verification = parse_verify_response(verify_response.content or "")
        step.verification = verification.get("reason", "")

        if verification["proceed"] == "replan" and replan_count < max_replan:
            # Re-plan: create a new plan incorporating what we learned
            print(f"    🔄 再計画 ({replan_count + 1}/{max_replan})")
            replan_count += 1
            completed_info = _gather_completed_results(plan)
            replan_prompt = create_plan_prompt(
                f"{question}\n\n[これまでに得た情報]\n{completed_info}\n\n"
                f"[再計画の理由] {verification.get('reason', '不明')}"
            )
            replan_response = chat(
                client, model,
                [{"role": "user", "content": replan_prompt}],
                tools=None,
            )
            new_plan = parse_plan_response(replan_response.content or "", question)
            if new_plan.steps:
                # Keep completed steps, replace remaining
                completed_steps = [s for s in plan.steps if s.status == TaskStatus.COMPLETED]
                plan.goal = new_plan.goal or plan.goal
                plan.success_criteria = new_plan.success_criteria or plan.success_criteria
                plan.steps = completed_steps + new_plan.steps
                step_index = len(completed_steps)
                print(f"    📋 新しい計画: {len(new_plan.steps)} ステップ追加")
                continue
            else:
                # Re-plan failed, continue with original
                step.status = TaskStatus.COMPLETED
                step_index += 1
        elif verification["proceed"] == "no":
            print(f"    ⏭️  不要な結果、スキップ: {verification.get('reason', '')}")
            step.status = TaskStatus.FAILED
            step_index += 1
        else:
            # proceed == "yes"
            print(f"    ✅ 検証OK")
            step.status = TaskStatus.COMPLETED
            step_index += 1

    # --- Phase 3: Synthesize ---
    completed_count = sum(1 for s in plan.steps if s.status == TaskStatus.COMPLETED)
    if completed_count == 0:
        print("  ⚠️  有効な結果なし、直接回答にフォールバック")
        return None

    print("  📝 最終回答を統合中...")
    synthesis_prompt = create_synthesis_prompt(plan)
    synthesis_response = chat(
        client, model,
        [{"role": "user", "content": synthesis_prompt}],
        tools=None,
    )
    plan.status = TaskStatus.COMPLETED
    return synthesis_response.content or ""


def _execute_step(step: TaskStep, original_question: str, client, model) -> str | None:
    """1ステップを実行して結果を返す"""
    if step.search_query:
        # Search using guided_retrieval if available, otherwise hybrid_search
        try:
            from src.guided_retrieval import guided_search
            search_result = guided_search(
                step.search_query,
                top_k=3,
                client=client,
                model=model,
            )
            results = search_result.get("results", [])
        except Exception:
            try:
                from src.hybrid_search import hybrid_search
                results, _ = hybrid_search(
                    step.search_query,
                    top_k=3,
                    doc_filter=step.doc_filter,
                    client=client,
                    model=model,
                )
            except Exception:
                results = []

        if results:
            # Format search results as text
            parts = []
            for r in results[:3]:
                doc_id = r.get("doc_id", "?")
                text = r.get("text", "")[:500]
                score = r.get("score", 0)
                parts.append(f"[{doc_id}] (score={score:.2f})\n{text}")
            return "\n---\n".join(parts)
        else:
            return None
    else:
        # No search needed - ask LLM directly for this step
        step_prompt = (
            f"元の質問: {original_question}\n\n"
            f"以下のステップを実行してください:\n{step.description}\n\n"
            f"期待する出力: {step.expected_output}\n\n"
            f"簡潔に回答してください（200文字以内）。"
        )
        response = chat(
            client, model,
            [{"role": "user", "content": step_prompt}],
            tools=None,
        )
        return response.content or None


def _gather_completed_results(plan: TaskPlan) -> str:
    """完了済みステップの結果を収集"""
    parts = []
    for i, step in enumerate(plan.steps):
        if step.status == TaskStatus.COMPLETED and step.result:
            parts.append(f"ステップ{i+1}: {step.description}\n結果: {step.result[:200]}")
    return "\n\n".join(parts) if parts else "なし"


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

            # Plan-Verify-Execute パターンの判定
            if should_use_planner(user_input):
                answer = run_planned_execution(user_input, client, model)
                if answer:
                    # 計画実行の結果をメッセージ履歴に追加
                    messages.append({"role": "assistant", "content": answer})
                    print(f"\n{answer}\n")
                    continue
                # フォールバック: 計画失敗時は通常のエージェントループへ

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
