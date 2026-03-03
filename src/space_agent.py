"""
宇宙分野特化 マルチエージェントシステム - 統合エントリーポイント

全コンポーネントを組み合わせた Claude Code クローン:
  router.py       → モデルルーティング
  agent_team.py   → マルチエージェントチーム
  thinking.py     → Extended Thinking
  planner.py      → Plan-Execute パターン
  session_manager.py → セッション管理
  long_memory.py  → 長期記憶

## 使い方

### 対話モード
    python -m src.space_agent

### 1問だけ実行
    python -m src.space_agent --query "衛星熱制御の設計手順を教えて"

### チームモード
    python -m src.space_agent --team --query "..."

### Extended Thinking
    python -m src.space_agent --think cot --query "..."
"""

from __future__ import annotations

import os
import sys
import json
import time
import asyncio
import argparse
from pathlib import Path
from openai import OpenAI

from src.router import ModelRouter, ModelRole, RoutingResult
from src.thinking import think, ThinkingMode
from src.planner import Planner
from src.session_manager import (
    save_session, load_session, list_sessions,
    generate_session_id, get_latest_session_id, SessionSnapshot,
)
from src.long_memory import MemorySystem, seed_space_knowledge


# ---------------------------------------------------------------------------
# 設定
# ---------------------------------------------------------------------------

# ローカルモデル設定: 環境変数でオーバーライド
_DEFAULT_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.cerebras.ai/v1")
_DEFAULT_MODEL    = os.getenv("LLM_MODEL",    "gpt-oss-120b")
_DEFAULT_API_KEY  = os.getenv("CEREBRAS_API_KEY", os.getenv("LLM_API_KEY", "dummy"))

# マルチモデル環境向け設定例
# (実際のローカルモデル環境では以下を .env で設定する)
# MODEL_CODING_PRIMARY=codegemma:7b
# VLLM_URL_CODING=http://localhost:8001/v1
# MODEL_SPACE=gemma2:27b-space-ft
# VLLM_URL_SPACE=http://localhost:8002/v1


# ---------------------------------------------------------------------------
# SpaceAgent
# ---------------------------------------------------------------------------

class SpaceAgent:
    """
    宇宙分野特化の統合エージェント。

    Args:
        use_router:     タスクに応じてモデルを切り替えるか
        use_planner:    複雑なタスクに Plan-Execute を使うか
        thinking_mode:  拡張思考モード (auto/cot/reflection/tot/bon/direct)
        use_team:       マルチエージェントチームを使うか
        use_memory:     長期記憶を使うか
        verbose:        詳細ログを出力するか
        team_config:    agent_team.py の設定辞書 (None なら単一エンドポイント)
    """

    def __init__(
        self,
        *,
        use_router:     bool = False,
        use_planner:    bool = True,
        thinking_mode:  str  = "auto",
        use_team:       bool = False,
        use_memory:     bool = True,
        verbose:        bool = True,
        team_config:    dict | None = None,
    ):
        self.verbose       = verbose
        self.use_router    = use_router
        self.use_planner   = use_planner
        self.use_team      = use_team
        self.use_memory    = use_memory
        self.thinking_mode = ThinkingMode(thinking_mode) if thinking_mode != "auto" else ThinkingMode.AUTO

        # デフォルトクライアント
        self._default_client = OpenAI(
            base_url=_DEFAULT_BASE_URL,
            api_key=_DEFAULT_API_KEY,
        )
        self._default_model = _DEFAULT_MODEL

        # ルーター (オプション)
        self._router = ModelRouter(verbose=verbose) if use_router else None

        # 長期記憶
        self._memory = MemorySystem() if use_memory else None
        if self._memory:
            added = seed_space_knowledge(self._memory.semantic)
            if added > 0:
                self._log(f"初期知識シード: {added} 件")

        # チーム設定 (オプション)
        self._team_config = team_config
        if use_team and team_config is None:
            from src.agent_team import make_single_endpoint_config
            self._team_config = make_single_endpoint_config(
                _DEFAULT_BASE_URL, _DEFAULT_MODEL, _DEFAULT_API_KEY
            )

        # プランナーは実行時に生成 (クライアントに依存するため)
        self._planner_cache: dict[str, Planner] = {}

        self._log("SpaceAgent 初期化完了")
        self._log(f"  モデル: {_DEFAULT_MODEL}")
        self._log(f"  ルーター: {'有効' if use_router else '無効'}")
        self._log(f"  思考モード: {thinking_mode}")
        self._log(f"  チーム: {'有効' if use_team else '無効'}")
        self._log(f"  記憶: {'有効' if use_memory else '無効'}")

    # ------------------------------------------------------------------
    # パブリックインターフェース
    # ------------------------------------------------------------------

    def query(
        self,
        user_input: str,
        *,
        messages: list[dict] | None = None,
        session_id: str | None = None,
    ) -> str:
        """
        ユーザー入力を処理して回答を返す。

        Args:
            user_input:  ユーザーの入力テキスト
            messages:    既存の会話履歴 (None なら空)
            session_id:  ロギング用セッションID

        Returns:
            回答文字列
        """
        t0 = time.perf_counter()

        # --- 1. モデル選択 ---
        if self._router:
            client, model, routing = self._router.route(user_input)
            self._log(f"ルーティング: {routing.role.value} → {model} "
                      f"(conf={routing.confidence:.2f})")
        else:
            client, model = self._default_client, self._default_model
            routing = None

        # --- 2. メモリコンテキスト取得 ---
        memory_context = ""
        if self._memory:
            memory_context = self._memory.get_context(user_input, max_total_chars=2000)
            if memory_context and self.verbose:
                self._log(f"記憶コンテキスト: {len(memory_context)} 文字")

        # --- 3. チームモード ---
        if self.use_team:
            return self._run_team(user_input, memory_context)

        # --- 4. Planner モード ---
        if self.use_planner and self._should_use_planner(user_input):
            self._log("Plan-Execute モードで実行")
            result = self._run_planner(client, model, user_input, memory_context)
            if result:
                elapsed = time.perf_counter() - t0
                self._log(f"完了: {elapsed:.1f}s")
                return result

        # --- 5. Extended Thinking ---
        self._log(f"思考モード: {self.thinking_mode.value}")
        prompt = user_input
        if memory_context:
            prompt = f"{memory_context}\n\n## 質問\n{user_input}"

        result = think(
            client, model, prompt,
            mode=self.thinking_mode,
            max_tokens=2500,
            verbose=self.verbose,
        )

        elapsed = time.perf_counter() - t0
        self._log(f"完了: {elapsed:.1f}s, 手法={result.method}")
        return result.answer

    def chat(
        self,
        session_id: str | None = None,
        *,
        continue_last: bool = False,
    ):
        """
        インタラクティブな対話ループを起動。

        Args:
            session_id:    復元するセッションID
            continue_last: 最後のセッションを続けるか
        """
        if continue_last:
            session_id = get_latest_session_id()
            if session_id:
                self._log(f"最後のセッションを復元: {session_id}")

        sid = session_id or generate_session_id()
        messages: list[dict] = []

        # セッション復元
        if session_id:
            snapshot = load_session(session_id)
            if snapshot:
                messages = snapshot.messages
                self._log(f"セッション復元: {len(messages)} メッセージ")

        print(f"\n宇宙分野特化エージェント")
        print(f"セッション: {sid}")
        print("'exit' で終了, '/help' でコマンド一覧\n")

        try:
            while True:
                try:
                    user_input = input("> ").strip()
                except (EOFError, KeyboardInterrupt):
                    break

                if not user_input:
                    continue
                if user_input.lower() in ("exit", "quit", "終了"):
                    break
                if user_input.startswith("/"):
                    out = self._handle_command(user_input, messages, sid)
                    if out:
                        print(f"\n{out}\n")
                    continue

                messages.append({"role": "user", "content": user_input})
                print()

                answer = self.query(user_input, messages=messages, session_id=sid)
                messages.append({"role": "assistant", "content": answer})
                print(f"\n{answer}\n")

        finally:
            if messages:
                save_session(sid, messages, model=_DEFAULT_MODEL)
                if self._memory:
                    self._memory.auto_extract_and_store(
                        messages, client=self._default_client,
                        model=_DEFAULT_MODEL, session_id=sid,
                    )
                self._log(f"セッション保存: {sid}")

    # ------------------------------------------------------------------
    # 内部メソッド
    # ------------------------------------------------------------------

    def _run_team(self, user_input: str, context: str) -> str:
        """チームモードで実行"""
        from src.agent_team import run_team_sync
        self._log("チームモードで実行中...")
        result = run_team_sync(user_input, self._team_config, context)
        self._log(f"チーム完了: {result.total_elapsed_sec:.1f}s, 戦略={result.strategy}")
        return result.final_answer

    def _run_planner(self, client, model, user_input: str, context: str) -> str | None:
        """プランナーモードで実行"""
        key = f"{client.base_url}:{model}"
        if key not in self._planner_cache:
            self._planner_cache[key] = Planner(
                client, model,
                verbose=self.verbose,
                search_fn=self._search if self._has_rag() else None,
            )
        planner = self._planner_cache[key]
        result = planner.run_sync(user_input, context=context)
        return result.final_answer if result.final_answer else None

    def _has_rag(self) -> bool:
        """RAG検索が利用可能か確認"""
        try:
            from src.hybrid_search import hybrid_search
            return True
        except ImportError:
            return False

    def _search(self, query: str) -> str:
        """RAG検索（Plannerから呼ばれる）"""
        try:
            from src.hybrid_search import hybrid_search
            results, _ = hybrid_search(query, top_k=3)
            parts = []
            for r in results[:3]:
                doc_id = r.get("doc_id", "?")
                text = r.get("text", "")[:400]
                score = r.get("score", 0)
                parts.append(f"[{doc_id}] score={score:.2f}\n{text}")
            return "\n---\n".join(parts) if parts else "検索結果なし"
        except Exception as e:
            return f"検索エラー: {e}"

    def _should_use_planner(self, question: str) -> bool:
        """Plannerを使うべきか判定"""
        if len(question) > 120:
            return True
        complex_patterns = [
            "どうやって", "手順", "方法", "設計", "比較", "分析",
            "トレードオフ", "なぜ", "問題", "how to", "design",
        ]
        return sum(1 for p in complex_patterns if p in question.lower()) >= 2

    def _handle_command(
        self, cmd: str, messages: list[dict], session_id: str
    ) -> str | None:
        """スラッシュコマンドを処理"""
        if cmd == "/help":
            return (
                "コマンド一覧:\n"
                "  /sessions         - セッション一覧\n"
                "  /memory           - 記憶内容を表示\n"
                "  /remember <内容>  - 作業記憶に追加\n"
                "  /stats            - ルーター統計\n"
                "  /mode <mode>      - 思考モード変更 (auto/cot/reflection/tot/bon/direct)\n"
                "  /team             - チームモード切り替え\n"
                "  /help             - このヘルプ"
            )
        elif cmd == "/sessions":
            sessions = list_sessions(limit=10)
            if not sessions:
                return "セッションなし"
            lines = ["最近のセッション:"]
            for s in sessions:
                lines.append(f"  {s.session_id}: {s.title!r} ({s.message_count}件)")
            return "\n".join(lines)

        elif cmd == "/memory":
            if not self._memory:
                return "記憶機能が無効です"
            context = self._memory.get_context("", max_total_chars=2000)
            self._memory.print_stats()
            return context or "記憶なし"

        elif cmd.startswith("/remember "):
            content = cmd[10:].strip()
            if self._memory:
                self._memory.working.add(content, category="user_note", importance=2.0)
                return f"記憶しました: {content}"
            return "記憶機能が無効です"

        elif cmd == "/stats":
            if self._router:
                self._router.print_stats()
            else:
                return "ルーターが無効です"

        elif cmd.startswith("/mode "):
            mode_str = cmd[6:].strip()
            try:
                if mode_str == "auto":
                    self.thinking_mode = ThinkingMode.AUTO
                else:
                    self.thinking_mode = ThinkingMode(mode_str)
                return f"思考モードを {mode_str} に変更しました"
            except ValueError:
                return f"不明なモード: {mode_str}. 使用可能: auto/cot/reflection/tot/bon/direct"

        elif cmd == "/team":
            self.use_team = not self.use_team
            return f"チームモード: {'有効' if self.use_team else '無効'}"

        return None

    def _log(self, msg: str):
        if self.verbose:
            print(f"[SpaceAgent] {msg}")


# ---------------------------------------------------------------------------
# CLI エントリーポイント
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="宇宙分野特化マルチエージェント")
    parser.add_argument("--query", "-q", type=str, help="1問だけ実行して終了")
    parser.add_argument("--team", action="store_true", help="チームモードを有効化")
    parser.add_argument(
        "--think",
        choices=["auto", "cot", "self_reflection", "tree_of_thought", "best_of_n", "direct"],
        default="auto",
        help="Extended Thinking モード",
    )
    parser.add_argument("--no-planner", action="store_true", help="Plannerを無効化")
    parser.add_argument("--router", action="store_true", help="モデルルーターを有効化")
    parser.add_argument("--no-memory", action="store_true", help="記憶機能を無効化")
    parser.add_argument("--continue", dest="continue_last", action="store_true",
                        help="最後のセッションを続ける")
    parser.add_argument("--resume", type=str, help="指定セッションを復元")
    parser.add_argument("--quiet", "-q2", action="store_true", help="ログを最小化")
    args = parser.parse_args()

    agent = SpaceAgent(
        use_router=args.router,
        use_planner=not args.no_planner,
        thinking_mode=args.think,
        use_team=args.team,
        use_memory=not args.no_memory,
        verbose=not args.quiet,
    )

    if args.query:
        # 1問実行モード
        print(f"\nQuery: {args.query}\n{'='*60}")
        answer = agent.query(args.query)
        print(f"\n{answer}\n")
    else:
        # 対話モード
        session_id = args.resume
        agent.chat(session_id=session_id, continue_last=args.continue_last)


if __name__ == "__main__":
    main()
