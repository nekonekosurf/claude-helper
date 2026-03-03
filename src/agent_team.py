"""
マルチエージェントチーム - Claude Code の Agent Teams に相当する実装

## アーキテクチャ
  Orchestrator (調整役)
     ├── CoderAgent     (コーディング専門)
     ├── SpaceAgent     (宇宙工学専門)
     ├── ReasoningAgent (推論・分析専門)
     └── SummaryAgent   (統合・要約専門)

## 実行モデル
- asyncio で並列実行
- 各エージェントは独立した会話コンテキストを持つ
- Orchestrator が結果を統合して最終回答を生成
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Callable
from openai import AsyncOpenAI, OpenAI


# ---------------------------------------------------------------------------
# データ型
# ---------------------------------------------------------------------------

class AgentRole(Enum):
    ORCHESTRATOR = "orchestrator"
    CODER        = "coder"
    SPACE_EXPERT = "space_expert"
    REASONER     = "reasoner"
    SUMMARIZER   = "summarizer"
    CRITIC       = "critic"     # レビュー・批判的検討


@dataclass
class SubTask:
    """サブエージェントへの指示"""
    task_id: str
    agent_role: AgentRole
    instruction: str                   # エージェントへの指示
    context: str = ""                  # 参考情報
    depends_on: list[str] = field(default_factory=list)  # task_id の依存関係
    timeout_sec: float = 60.0


@dataclass
class SubTaskResult:
    """サブエージェントの実行結果"""
    task_id: str
    agent_role: AgentRole
    output: str
    elapsed_sec: float
    success: bool
    error: Optional[str] = None
    token_usage: int = 0


@dataclass
class TeamResult:
    """チーム全体の実行結果"""
    final_answer: str
    subtask_results: list[SubTaskResult]
    total_elapsed_sec: float
    strategy: str = ""


# ---------------------------------------------------------------------------
# エージェント設定
# ---------------------------------------------------------------------------

# 各ロールのシステムプロンプト
AGENT_SYSTEM_PROMPTS: dict[AgentRole, str] = {
    AgentRole.ORCHESTRATOR: """あなたはタスク調整エージェントです。
複雑なタスクを専門家チームに分配し、結果を統合して最終回答を作成します。
回答は具体的・簡潔に。不確実な場合は明示してください。""",

    AgentRole.CODER: """あなたはコーディング専門エージェントです。
- 動作するコードを書く
- エラーハンドリングを含める
- 型ヒントと簡潔なコメントを付ける
- 宇宙分野のPythonコード（CCSDS, SpacePy等）に詳しい""",

    AgentRole.SPACE_EXPERT: """あなたは宇宙工学の専門家エージェントです。
- 衛星システム設計（電力・熱・構造・姿勢制御）
- JAXA/NASA規格・JERG文書
- 宇宙環境（放射線・真空・熱サイクル）
- 軌道力学・推進系
の質問に専門的に回答してください。""",

    AgentRole.REASONER: """あなたは分析・推論専門エージェントです。
- 複数の観点からトレードオフを分析する
- 証拠に基づいて論理的に推論する
- 仮定を明示する
- 結論と根拠を分けて記述する""",

    AgentRole.SUMMARIZER: """あなたは情報統合・要約専門エージェントです。
複数のエージェントの回答を統合し、矛盾を解消して一貫した最終回答を作成します。
冗長な部分を除いて簡潔にまとめてください。""",

    AgentRole.CRITIC: """あなたはクリティカルレビューエージェントです。
提案・コード・分析を批判的に検討し:
- 問題点・抜け漏れを指摘する
- 改善案を提案する
- 宇宙システムの安全性・信頼性の観点からチェックする""",
}


# ---------------------------------------------------------------------------
# サブエージェント (非同期)
# ---------------------------------------------------------------------------

class SubAgent:
    """1つの専門エージェント"""

    def __init__(
        self,
        role: AgentRole,
        base_url: str,
        model_name: str,
        api_key: str = "dummy",
        max_tokens: int = 2048,
        system_prompt: Optional[str] = None,
    ):
        self.role = role
        self.model_name = model_name
        self.max_tokens = max_tokens
        self.client = AsyncOpenAI(base_url=base_url, api_key=api_key)
        self.system_prompt = system_prompt or AGENT_SYSTEM_PROMPTS.get(role, "")

    async def run(self, task: SubTask) -> SubTaskResult:
        """サブタスクを非同期で実行"""
        t0 = time.perf_counter()

        messages = [
            {"role": "system", "content": self.system_prompt},
        ]

        if task.context:
            messages.append({
                "role": "user",
                "content": f"[参考情報]\n{task.context}",
            })
            messages.append({
                "role": "assistant",
                "content": "参考情報を確認しました。",
            })

        messages.append({"role": "user", "content": task.instruction})

        try:
            response = await asyncio.wait_for(
                self.client.chat.completions.create(
                    model=self.model_name,
                    messages=messages,
                    max_tokens=self.max_tokens,
                ),
                timeout=task.timeout_sec,
            )
            output = response.choices[0].message.content or ""
            usage = getattr(response, "usage", None)
            token_usage = (usage.completion_tokens if usage else 0) if usage else 0

            return SubTaskResult(
                task_id=task.task_id,
                agent_role=self.role,
                output=output,
                elapsed_sec=time.perf_counter() - t0,
                success=True,
                token_usage=token_usage,
            )

        except asyncio.TimeoutError:
            return SubTaskResult(
                task_id=task.task_id,
                agent_role=self.role,
                output="",
                elapsed_sec=task.timeout_sec,
                success=False,
                error=f"Timeout after {task.timeout_sec}s",
            )
        except Exception as e:
            return SubTaskResult(
                task_id=task.task_id,
                agent_role=self.role,
                output="",
                elapsed_sec=time.perf_counter() - t0,
                success=False,
                error=str(e),
            )


# ---------------------------------------------------------------------------
# オーケストレーター
# ---------------------------------------------------------------------------

class Orchestrator:
    """
    マルチエージェントチームの調整役

    使い方:
        orch = Orchestrator.from_config(config_dict)
        result = await orch.run("衛星の熱制御システムとPythonシミュレーションコードを説明して")
        print(result.final_answer)
    """

    def __init__(
        self,
        agents: dict[AgentRole, SubAgent],
        orchestrator_base_url: str,
        orchestrator_model: str,
        orchestrator_api_key: str = "dummy",
        max_parallel: int = 3,
        verbose: bool = True,
    ):
        self.agents = agents
        self.max_parallel = max_parallel
        self.verbose = verbose
        self._orch_client = AsyncOpenAI(
            base_url=orchestrator_base_url,
            api_key=orchestrator_api_key,
        )
        self._orch_model = orchestrator_model

    @classmethod
    def from_config(cls, config: dict) -> "Orchestrator":
        """
        設定辞書から Orchestrator を構築。

        config 例:
        {
          "orchestrator": {"base_url": "http://localhost:8003/v1", "model": "llama3.1:70b"},
          "agents": {
            "coder":        {"base_url": "http://localhost:8001/v1", "model": "codegemma:7b"},
            "space_expert": {"base_url": "http://localhost:8002/v1", "model": "gemma2:27b-space-ft"},
            "reasoner":     {"base_url": "http://localhost:8003/v1", "model": "llama3.1:70b"},
            "summarizer":   {"base_url": "http://localhost:8004/v1", "model": "gemma2:9b"},
          }
        }
        """
        role_map = {
            "coder":        AgentRole.CODER,
            "space_expert": AgentRole.SPACE_EXPERT,
            "reasoner":     AgentRole.REASONER,
            "summarizer":   AgentRole.SUMMARIZER,
            "critic":       AgentRole.CRITIC,
        }
        agents = {}
        for role_str, agent_cfg in config.get("agents", {}).items():
            role = role_map.get(role_str)
            if role:
                agents[role] = SubAgent(
                    role=role,
                    base_url=agent_cfg["base_url"],
                    model_name=agent_cfg["model"],
                    api_key=agent_cfg.get("api_key", "dummy"),
                    max_tokens=agent_cfg.get("max_tokens", 2048),
                )

        orch_cfg = config.get("orchestrator", {})
        return cls(
            agents=agents,
            orchestrator_base_url=orch_cfg.get("base_url", "http://localhost:8003/v1"),
            orchestrator_model=orch_cfg.get("model", "llama3.1:70b"),
            orchestrator_api_key=orch_cfg.get("api_key", "dummy"),
            max_parallel=config.get("max_parallel", 3),
            verbose=config.get("verbose", True),
        )

    async def run(self, user_request: str, additional_context: str = "") -> TeamResult:
        """
        ユーザーリクエストをチームで処理する。

        手順:
        1. Orchestrator がタスクを分解
        2. サブエージェントを並列実行 (依存関係を考慮)
        3. Orchestrator が結果を統合して最終回答を生成
        """
        t0 = time.perf_counter()
        self._log("チームタスク開始...")

        # --- Step 1: タスク分解 ---
        subtasks = await self._decompose(user_request, additional_context)
        self._log(f"タスク分解完了: {len(subtasks)} サブタスク")

        if not subtasks:
            # 分解失敗: 直接 Orchestrator が回答
            answer = await self._direct_answer(user_request)
            return TeamResult(
                final_answer=answer,
                subtask_results=[],
                total_elapsed_sec=time.perf_counter() - t0,
                strategy="direct",
            )

        # --- Step 2: 並列実行 (依存関係考慮) ---
        results = await self._execute_with_dependencies(subtasks)

        # --- Step 3: 結果統合 ---
        self._log("結果を統合中...")
        final_answer = await self._synthesize(user_request, results)

        return TeamResult(
            final_answer=final_answer,
            subtask_results=results,
            total_elapsed_sec=time.perf_counter() - t0,
            strategy="team",
        )

    async def _decompose(self, request: str, context: str) -> list[SubTask]:
        """Orchestrator がタスクを分解"""
        available_roles = "\n".join(
            f"- {role.value}: {AGENT_SYSTEM_PROMPTS[role].splitlines()[0]}"
            for role in self.agents
        )

        prompt = f"""以下のリクエストを専門エージェントへのサブタスクに分解してください。

## リクエスト
{request}

{f"## 追加コンテキスト{chr(10)}{context}" if context else ""}

## 利用可能なエージェント
{available_roles}

## 出力形式 (JSONで回答)
```json
[
  {{
    "task_id": "t1",
    "agent_role": "coder",
    "instruction": "エージェントへの具体的な指示",
    "depends_on": []
  }},
  {{
    "task_id": "t2",
    "agent_role": "space_expert",
    "instruction": "エージェントへの指示",
    "depends_on": []
  }}
]
```

## ルール
- サブタスクは最大4つまで
- 独立したタスクは depends_on を空にする（並列実行される）
- 依存関係がある場合のみ depends_on に task_id を記載
- agent_role は上記の利用可能なエージェント名から選ぶ
"""
        try:
            response = await self._orch_client.chat.completions.create(
                model=self._orch_model,
                messages=[
                    {"role": "system", "content": AGENT_SYSTEM_PROMPTS[AgentRole.ORCHESTRATOR]},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=1024,
            )
            content = response.choices[0].message.content or ""
            return self._parse_decomposition(content)
        except Exception as e:
            self._log(f"タスク分解エラー: {e}")
            return []

    def _parse_decomposition(self, content: str) -> list[SubTask]:
        """JSON形式のタスク分解をパース"""
        # ```json ... ``` ブロックを抽出
        import re
        match = re.search(r"```json\s*([\s\S]*?)\s*```", content)
        json_str = match.group(1) if match else content

        try:
            items = json.loads(json_str)
        except json.JSONDecodeError:
            # JSONパース失敗: 空リストを返す
            return []

        role_map = {
            "coder":        AgentRole.CODER,
            "space_expert": AgentRole.SPACE_EXPERT,
            "reasoner":     AgentRole.REASONER,
            "summarizer":   AgentRole.SUMMARIZER,
            "critic":       AgentRole.CRITIC,
        }

        tasks = []
        for item in items[:4]:  # 最大4つ
            role_str = item.get("agent_role", "reasoner")
            role = role_map.get(role_str, AgentRole.REASONER)

            # エージェントが存在しない場合は REASONER にフォールバック
            if role not in self.agents and AgentRole.REASONER in self.agents:
                role = AgentRole.REASONER

            tasks.append(SubTask(
                task_id=item.get("task_id", f"t{len(tasks)+1}"),
                agent_role=role,
                instruction=item.get("instruction", ""),
                depends_on=item.get("depends_on", []),
            ))
        return tasks

    async def _execute_with_dependencies(self, tasks: list[SubTask]) -> list[SubTaskResult]:
        """依存関係を考慮しながら並列実行"""
        pending = {t.task_id: t for t in tasks}
        completed: dict[str, SubTaskResult] = {}
        results: list[SubTaskResult] = []

        while pending:
            # 依存関係が全て完了しているタスクを抽出
            ready = [
                t for t in pending.values()
                if all(dep in completed for dep in t.depends_on)
            ]

            if not ready:
                # 循環依存: 強制実行
                ready = list(pending.values())[:1]

            # 依存するタスクの結果をコンテキストとして追加
            for task in ready:
                if task.depends_on:
                    dep_context = "\n\n".join(
                        f"[{completed[dep].agent_role.value}の結果]\n{completed[dep].output}"
                        for dep in task.depends_on
                        if dep in completed
                    )
                    task.context = dep_context

            # max_parallel 個ずつ並列実行
            for batch_start in range(0, len(ready), self.max_parallel):
                batch = ready[batch_start:batch_start + self.max_parallel]
                self._log(f"並列実行: {[t.task_id for t in batch]}")

                coros = []
                for task in batch:
                    agent = self.agents.get(task.agent_role)
                    if agent:
                        coros.append(agent.run(task))
                    else:
                        # エージェント不在: スキップ
                        self._log(f"  警告: {task.agent_role.value} エージェントが未設定")

                batch_results = await asyncio.gather(*coros, return_exceptions=True)
                for task, result in zip(batch, batch_results):
                    if isinstance(result, Exception):
                        result = SubTaskResult(
                            task_id=task.task_id,
                            agent_role=task.agent_role,
                            output="",
                            elapsed_sec=0,
                            success=False,
                            error=str(result),
                        )
                    completed[task.task_id] = result
                    results.append(result)
                    status = "OK" if result.success else f"NG({result.error})"
                    self._log(f"  {task.task_id} [{task.agent_role.value}]: {status} "
                              f"({result.elapsed_sec:.1f}s)")

            # 完了したものを pending から削除
            for task in ready:
                pending.pop(task.task_id, None)

        return results

    async def _synthesize(self, original_request: str, results: list[SubTaskResult]) -> str:
        """全エージェントの結果を統合"""
        if not results:
            return "エージェントから有効な結果が得られませんでした。"

        successful = [r for r in results if r.success and r.output]
        if not successful:
            return "全てのサブタスクが失敗しました。" + "\n".join(
                f"- {r.agent_role.value}: {r.error}" for r in results
            )

        results_text = "\n\n".join(
            f"=== {r.agent_role.value} の回答 ===\n{r.output}"
            for r in successful
        )

        # Summarizer エージェントがあれば使う、なければ Orchestrator が統合
        synthesizer = self.agents.get(AgentRole.SUMMARIZER)
        if synthesizer:
            task = SubTask(
                task_id="synthesis",
                agent_role=AgentRole.SUMMARIZER,
                instruction=(
                    f"以下の専門家チームの回答を統合して、元のリクエストに対する"
                    f"最終回答を作成してください。\n\n"
                    f"## 元のリクエスト\n{original_request}\n\n"
                    f"## 各エージェントの回答\n{results_text}"
                ),
                timeout_sec=90.0,
            )
            result = await synthesizer.run(task)
            if result.success:
                return result.output

        # フォールバック: Orchestrator が直接統合
        prompt = (
            f"以下の情報を統合して、元のリクエストへの最終回答を日本語で作成してください。\n\n"
            f"## 元のリクエスト\n{original_request}\n\n"
            f"## 各エージェントの回答\n{results_text}"
        )
        try:
            response = await self._orch_client.chat.completions.create(
                model=self._orch_model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=2048,
            )
            return response.choices[0].message.content or ""
        except Exception as e:
            return f"統合エラー: {e}\n\n{results_text}"

    async def _direct_answer(self, request: str) -> str:
        """タスク分解なしで直接回答"""
        try:
            response = await self._orch_client.chat.completions.create(
                model=self._orch_model,
                messages=[
                    {"role": "system", "content": AGENT_SYSTEM_PROMPTS[AgentRole.ORCHESTRATOR]},
                    {"role": "user", "content": request},
                ],
                max_tokens=2048,
            )
            return response.choices[0].message.content or ""
        except Exception as e:
            return f"エラー: {e}"

    def _log(self, msg: str):
        if self.verbose:
            print(f"[Orchestrator] {msg}")


# ---------------------------------------------------------------------------
# 同期ラッパー (既存の同期コードから呼び出しやすくする)
# ---------------------------------------------------------------------------

def run_team_sync(
    user_request: str,
    config: dict,
    additional_context: str = "",
) -> TeamResult:
    """
    同期版エントリーポイント。asyncio.run() でラップ。

    Args:
        user_request: ユーザーのリクエスト
        config: Orchestrator.from_config() に渡す設定辞書
        additional_context: 追加コンテキスト

    Returns:
        TeamResult
    """
    orch = Orchestrator.from_config(config)
    return asyncio.run(orch.run(user_request, additional_context))


# ---------------------------------------------------------------------------
# デモ用デフォルト設定 (全モデルが同一エンドポイントを使う簡易版)
# ---------------------------------------------------------------------------

def make_single_endpoint_config(
    base_url: str,
    model: str,
    api_key: str = "dummy",
) -> dict:
    """
    1つのエンドポイント/モデルで全エージェントを動かす設定（開発/テスト用）
    """
    agent_def = {"base_url": base_url, "model": model, "api_key": api_key}
    return {
        "orchestrator": agent_def,
        "agents": {
            "coder":        agent_def,
            "space_expert": agent_def,
            "reasoner":     agent_def,
            "summarizer":   agent_def,
        },
        "max_parallel": 2,
        "verbose": True,
    }


# ---------------------------------------------------------------------------
# クイックテスト
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import os

    # 既存の Cerebras 設定を使う
    base_url = os.getenv("LLM_BASE_URL", "https://api.cerebras.ai/v1")
    model    = os.getenv("LLM_MODEL", "gpt-oss-120b")
    api_key  = os.getenv("CEREBRAS_API_KEY", "dummy")

    config = make_single_endpoint_config(base_url, model, api_key)

    request = "衛星の熱制御システムの基本設計と、Pythonで熱モデルを計算するコード例を教えて"

    print(f"Request: {request}")
    print("=" * 60)
    result = run_team_sync(request, config)
    print("\n=== 最終回答 ===")
    print(result.final_answer)
    print(f"\n経過時間: {result.total_elapsed_sec:.1f}s")
    print(f"戦略: {result.strategy}")
    for r in result.subtask_results:
        status = "OK" if r.success else f"NG({r.error})"
        print(f"  {r.task_id} [{r.agent_role.value}]: {status} ({r.elapsed_sec:.1f}s)")
