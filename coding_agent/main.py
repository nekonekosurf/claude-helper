"""
main.py - CLI エントリーポイント

コーディングエージェントの対話型 CLI。
シングルターン実行・マルチターン会話・計画モードに対応。
"""

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

# パッケージとして実行される場合と、スクリプトとして実行される場合の両方に対応
if __name__ == "__main__" and __package__ is None:
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from coding_agent.agent_core import AgentCore, AgentMode
    from coding_agent.config import Config, AgentConfig, LLMConfig, get_config, reset_config
    from coding_agent.sub_agent import SubAgentTask, SubAgentManager
else:
    from .agent_core import AgentCore, AgentMode
    from .config import Config, AgentConfig, LLMConfig, get_config, reset_config
    from .sub_agent import SubAgentTask, SubAgentManager


# ANSI カラーコード
class Color:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    GREEN = "\033[32m"
    BLUE = "\033[34m"
    YELLOW = "\033[33m"
    CYAN = "\033[36m"
    RED = "\033[31m"
    DIM = "\033[2m"
    MAGENTA = "\033[35m"


def colorize(text: str, color: str) -> str:
    """テキストに色を付ける（TTY でない場合はそのまま）"""
    if not sys.stdout.isatty():
        return text
    return f"{color}{text}{Color.RESET}"


class CLI:
    """対話型 CLI クラス"""

    def __init__(self, agent: AgentCore, show_thinking: bool = False):
        self.agent = agent
        self.show_thinking = show_thinking
        self._tool_calls_this_turn = 0

    def on_tool_call(self, tool_name: str, args: dict) -> None:
        """ツール呼び出し時のコールバック"""
        args_preview = json.dumps(args, ensure_ascii=False)
        if len(args_preview) > 80:
            args_preview = args_preview[:77] + "..."
        print(colorize(f"  [{tool_name}] {args_preview}", Color.CYAN))
        self._tool_calls_this_turn += 1

    def on_tool_result(self, tool_name: str, result: str) -> None:
        """ツール結果受け取り時のコールバック"""
        lines = result.split("\n")
        preview = lines[0] if lines else ""
        if len(preview) > 80:
            preview = preview[:77] + "..."
        if len(lines) > 1:
            print(colorize(f"    -> {preview} ... ({len(lines)} 行)", Color.DIM))
        else:
            print(colorize(f"    -> {preview}", Color.DIM))

    def on_thinking(self, thinking: str) -> None:
        """思考過程のコールバック"""
        if self.show_thinking:
            print(colorize("\n--- 思考過程 ---", Color.MAGENTA))
            for line in thinking.split("\n")[:10]:  # 最大10行表示
                print(colorize(f"  {line}", Color.DIM))
            print(colorize("--- 思考終了 ---\n", Color.MAGENTA))

    async def run_interactive(self) -> None:
        """対話モードを実行する"""
        print(colorize("\nコーディングエージェント起動", Color.BOLD + Color.GREEN))
        print(colorize(f"モデル: {self.agent.config.llm.model}", Color.DIM))
        print(colorize(f"エンドポイント: {self.agent.config.llm.base_url}", Color.DIM))
        print(colorize("\nコマンド:", Color.BOLD))
        print("  /plan   - 計画モードに切り替え（実行前に計画を確認）")
        print("  /clear  - 会話をリセット")
        print("  /save   - セッションを保存")
        print("  /load <file>  - セッションを読み込む")
        print("  /debug  - デバッグモード切り替え")
        print("  /thinking - 思考過程の表示切り替え")
        print("  /help   - ヘルプを表示")
        print("  /quit または Ctrl+D で終了\n")

        while True:
            try:
                # プロンプトの色はモードに応じて変える
                mode_label = "(PLAN)" if self.agent.is_plan_mode else ""
                prompt_str = colorize(f"あなた{mode_label}> ", Color.BOLD + Color.GREEN)

                # input() は同期的だが readline 統合のために使う
                user_input = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: input(prompt_str)
                )
                user_input = user_input.strip()

            except (EOFError, KeyboardInterrupt):
                print(colorize("\n終了します。", Color.YELLOW))
                break

            if not user_input:
                continue

            # スラッシュコマンドの処理
            if user_input.startswith("/"):
                await self._handle_command(user_input)
                continue

            # エージェントに送信
            self._tool_calls_this_turn = 0
            print()

            try:
                start = time.monotonic()
                response = await self.agent.chat(
                    user_input,
                    on_tool_call=self.on_tool_call,
                    on_tool_result=self.on_tool_result,
                    on_thinking=self.on_thinking,
                )
                elapsed = time.monotonic() - start

                # 結果を表示
                print(colorize("\nエージェント:", Color.BOLD + Color.BLUE))
                print(response.content)
                print(colorize(
                    f"\n[{response.tool_calls_count}ツール, {elapsed:.1f}秒]",
                    Color.DIM
                ))

            except KeyboardInterrupt:
                print(colorize("\n処理を中断しました。", Color.YELLOW))
            except Exception as e:
                print(colorize(f"\nエラー: {e}", Color.RED))
                if self.agent.config.agent.debug:
                    import traceback
                    traceback.print_exc()

    async def _handle_command(self, cmd: str) -> None:
        """スラッシュコマンドを処理する"""
        parts = cmd.split()
        command = parts[0].lower()

        if command == "/plan":
            if self.agent.is_plan_mode:
                self.agent.exit_plan_mode()
                print(colorize("計画モードを終了しました。", Color.YELLOW))
            else:
                self.agent.enter_plan_mode()
                print(colorize("計画モードに切り替えました。（/plan で終了）", Color.YELLOW))

        elif command == "/clear":
            self.agent.clear_context()
            print(colorize("会話をリセットしました。", Color.YELLOW))

        elif command == "/save":
            filepath = self.agent.save_session()
            print(colorize(f"セッションを保存しました: {filepath}", Color.GREEN))

        elif command == "/load":
            if len(parts) < 2:
                print(colorize("使い方: /load <ファイルパス>", Color.RED))
                return
            filepath = parts[1]
            if self.agent.load_session(filepath):
                print(colorize(f"セッションを読み込みました: {filepath}", Color.GREEN))
            else:
                print(colorize(f"セッションの読み込みに失敗しました: {filepath}", Color.RED))

        elif command == "/debug":
            self.agent.config.agent.debug = not self.agent.config.agent.debug
            state = "ON" if self.agent.config.agent.debug else "OFF"
            print(colorize(f"デバッグモード: {state}", Color.YELLOW))

        elif command == "/thinking":
            self.show_thinking = not self.show_thinking
            state = "ON" if self.show_thinking else "OFF"
            print(colorize(f"思考過程の表示: {state}", Color.YELLOW))

        elif command in ("/quit", "/exit", "/q"):
            print(colorize("終了します。", Color.YELLOW))
            sys.exit(0)

        elif command == "/help":
            print(colorize("\nコマンド一覧:", Color.BOLD))
            print("  /plan      - 計画モードの切り替え")
            print("  /clear     - 会話履歴をリセット")
            print("  /save      - セッションを保存")
            print("  /load <f>  - セッションを読み込む")
            print("  /debug     - デバッグログの切り替え")
            print("  /thinking  - 思考過程表示の切り替え")
            print("  /quit      - 終了")

        else:
            print(colorize(f"未知のコマンド: {command}。/help でヘルプを表示。", Color.RED))


async def run_single_query(
    query: str,
    config: Config,
    show_thinking: bool = False,
    plan_mode: bool = False,
) -> None:
    """シングルクエリを実行して結果を出力する"""
    agent = AgentCore(config)

    tool_calls_log = []

    def on_tool_call(name: str, args: dict) -> None:
        preview = json.dumps(args, ensure_ascii=False)[:60]
        print(colorize(f"  [ツール] {name}: {preview}", Color.CYAN), file=sys.stderr)
        tool_calls_log.append((name, args))

    def on_thinking(thinking: str) -> None:
        if show_thinking:
            print(colorize("\n<thinking>", Color.MAGENTA), file=sys.stderr)
            print(colorize(thinking, Color.DIM), file=sys.stderr)
            print(colorize("</thinking>\n", Color.MAGENTA), file=sys.stderr)

    if plan_mode:
        agent.enter_plan_mode()

    response = await agent.chat(
        query,
        on_tool_call=on_tool_call,
        on_thinking=on_thinking,
    )

    print(response.content)

    if show_thinking and response.thinking:
        print(colorize("\n--- 思考過程 ---", Color.MAGENTA), file=sys.stderr)
        print(colorize(response.thinking, Color.DIM), file=sys.stderr)


async def run_parallel_demo(config: Config) -> None:
    """並列サブエージェントのデモを実行する"""
    manager = SubAgentManager(
        client=__import__("openai").AsyncOpenAI(
            base_url=config.llm.base_url,
            api_key=config.llm.api_key,
        ),
        config=config,
    )

    tasks = [
        SubAgentTask(
            id="task1",
            description="Pythonファイルを列挙",
            instructions="カレントディレクトリのPythonファイルを全て列挙し、ファイル数を報告してください。",
            tools=["Glob", "Bash"],
        ),
        SubAgentTask(
            id="task2",
            description="Git状態を確認",
            instructions="Gitリポジトリの現在の状態（ブランチ名、変更ファイル数）を確認して報告してください。",
            tools=["Bash"],
        ),
        SubAgentTask(
            id="task3",
            description="依存関係を確認",
            instructions="pyproject.toml または requirements.txt を読んで、主要な依存ライブラリを列挙してください。",
            tools=["Read", "Glob"],
        ),
    ]

    print(colorize(f"並列サブエージェント {len(tasks)} 個を起動...\n", Color.BOLD))

    def progress_cb(task_id: str, msg: str) -> None:
        print(colorize(f"  [{task_id}] {msg}", Color.CYAN))

    results = await manager.run_parallel(tasks, progress_cb)
    formatted = manager.format_results(results)
    print("\n" + formatted)


def build_config_from_args(args: argparse.Namespace) -> Config:
    """コマンドライン引数から設定を構築する"""
    config = Config(
        llm=LLMConfig(
            base_url=args.base_url or os.getenv("VLLM_BASE_URL", "http://localhost:8000/v1"),
            model=args.model or os.getenv("VLLM_MODEL", "Qwen/Qwen2.5-Coder-32B-Instruct"),
            max_tokens=args.max_tokens,
            temperature=args.temperature,
            use_native_tool_call=not args.no_native_tools,
        ),
        agent=AgentConfig(
            work_dir=args.work_dir or os.getcwd(),
            max_iterations=args.max_iterations,
            force_chain_of_thought=not args.no_cot,
            debug=args.debug,
        ),
    )
    return config


def main() -> None:
    """メインエントリーポイント"""
    parser = argparse.ArgumentParser(
        description="vLLM ベースのローカルコーディングエージェント",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用例:
  # 対話モード
  python -m coding_agent.main

  # シングルクエリ
  python -m coding_agent.main -q "main.py を読んで内容を要約して"

  # 計画モードで実行
  python -m coding_agent.main -q "テストを書いて" --plan

  # カスタムモデル・エンドポイント
  python -m coding_agent.main --model deepseek-coder --base-url http://localhost:8001/v1

  # 並列サブエージェントデモ
  python -m coding_agent.main --demo-parallel

  # 思考過程を表示
  python -m coding_agent.main --show-thinking
        """,
    )

    # 接続設定
    conn_group = parser.add_argument_group("接続設定")
    conn_group.add_argument(
        "--base-url", default=None,
        help="vLLM エンドポイント (デフォルト: http://localhost:8000/v1)",
    )
    conn_group.add_argument(
        "--model", default=None,
        help="使用するモデル名 (デフォルト: Qwen/Qwen2.5-Coder-32B-Instruct)",
    )

    # 実行モード
    mode_group = parser.add_argument_group("実行モード")
    mode_group.add_argument(
        "-q", "--query", default=None,
        help="シングルクエリを実行して終了",
    )
    mode_group.add_argument(
        "--plan", action="store_true",
        help="計画モードで実行（-q と組み合わせて使う）",
    )
    mode_group.add_argument(
        "--demo-parallel", action="store_true",
        help="並列サブエージェントのデモを実行",
    )

    # 表示設定
    display_group = parser.add_argument_group("表示設定")
    display_group.add_argument(
        "--show-thinking", action="store_true",
        help="思考過程 (<thinking> タグ) を表示する",
    )
    display_group.add_argument(
        "--debug", action="store_true",
        help="デバッグログを有効にする",
    )

    # 詳細設定
    detail_group = parser.add_argument_group("詳細設定")
    detail_group.add_argument(
        "--max-tokens", type=int, default=4096,
        help="最大生成トークン数 (デフォルト: 4096)",
    )
    detail_group.add_argument(
        "--temperature", type=float, default=0.1,
        help="生成温度 (デフォルト: 0.1)",
    )
    detail_group.add_argument(
        "--max-iterations", type=int, default=50,
        help="最大ループ回数 (デフォルト: 50)",
    )
    detail_group.add_argument(
        "--no-native-tools", action="store_true",
        help="ネイティブツールコールを無効にする（JSON フォールバックを使用）",
    )
    detail_group.add_argument(
        "--no-cot", action="store_true",
        help="Chain of Thought プロンプトを無効にする",
    )
    detail_group.add_argument(
        "--work-dir", default=None,
        help="作業ディレクトリ (デフォルト: カレントディレクトリ)",
    )
    detail_group.add_argument(
        "--session", default=None,
        help="読み込むセッションファイル",
    )

    args = parser.parse_args()
    config = build_config_from_args(args)

    # 実行モードの選択
    if args.demo_parallel:
        asyncio.run(run_parallel_demo(config))

    elif args.query:
        asyncio.run(run_single_query(
            args.query,
            config,
            show_thinking=args.show_thinking,
            plan_mode=args.plan,
        ))

    else:
        # 対話モード
        agent = AgentCore(config)

        if args.session:
            if agent.load_session(args.session):
                print(colorize(f"セッションを読み込みました: {args.session}", Color.GREEN))
            else:
                print(colorize(f"セッションの読み込みに失敗しました: {args.session}", Color.RED))

        cli = CLI(agent, show_thinking=args.show_thinking)
        asyncio.run(cli.run_interactive())


if __name__ == "__main__":
    main()
