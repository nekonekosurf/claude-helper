"""
拡張版 Plan-Execute パターン

既存の task_planner.py を拡張:
- より柔軟なステップ定義
- 非同期並列実行
- ロールバック対応
- 実行ログの詳細化
- セッション間での計画保存・復元

## 実行フロー
1. Plan    : タスクを依存関係付きのステップに分解
2. Execute : 各ステップを実行 (可能なら並列)
3. Verify  : 各ステップの結果を検証
4. Re-plan : 失敗した場合に計画を修正
5. Synthesize : 全結果を統合して最終回答を生成
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Optional, Callable, Any
from openai import OpenAI, AsyncOpenAI


# ---------------------------------------------------------------------------
# データ型
# ---------------------------------------------------------------------------

class StepStatus(Enum):
    PENDING    = "pending"
    RUNNING    = "running"
    COMPLETED  = "completed"
    FAILED     = "failed"
    SKIPPED    = "skipped"


class StepType(Enum):
    SEARCH     = "search"      # RAG検索
    LLM        = "llm"         # LLM直接呼び出し
    CODE       = "code"        # コード実行 (将来拡張)
    PARALLEL   = "parallel"    # 並列実行グループ


@dataclass
class PlanStep:
    """計画の1ステップ"""
    step_id: str
    step_type: StepType
    description: str
    instruction: str                    # LLM/検索への具体的な指示
    expected_output: str
    depends_on: list[str] = field(default_factory=list)
    status: StepStatus = StepStatus.PENDING
    result: Optional[str] = None
    error: Optional[str] = None
    elapsed_sec: float = 0.0
    retries: int = 0
    max_retries: int = 1


@dataclass
class ExecutionPlan:
    """実行計画全体"""
    plan_id: str
    original_question: str
    goal: str
    success_criteria: str
    steps: list[PlanStep] = field(default_factory=list)
    status: StepStatus = StepStatus.PENDING
    replan_count: int = 0
    created_at: float = field(default_factory=time.time)
    context_budget: int = 4000     # 各ステップのトークン予算

    def to_dict(self) -> dict:
        d = asdict(self)
        # Enum を文字列に変換
        d["status"] = self.status.value
        for step in d["steps"]:
            step["status"] = StepStatus(step["status"]).value
            step["step_type"] = StepType(step["step_type"]).value
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "ExecutionPlan":
        steps = [
            PlanStep(
                **{**s, "status": StepStatus(s["status"]),
                   "step_type": StepType(s["step_type"])}
            )
            for s in d.pop("steps", [])
        ]
        d["status"] = StepStatus(d["status"])
        return cls(**d, steps=steps)


@dataclass
class PlannerResult:
    """Planner 実行結果"""
    final_answer: str
    plan: ExecutionPlan
    total_elapsed_sec: float
    replan_count: int = 0


# ---------------------------------------------------------------------------
# プロンプト生成
# ---------------------------------------------------------------------------

def _make_plan_prompt(question: str, context: str = "") -> str:
    return f"""以下の質問に答えるための実行計画を JSON 形式で作成してください。

## 質問
{question}

{f"## 追加コンテキスト{chr(10)}{context}" if context else ""}

## 出力形式
```json
{{
  "goal": "最終的に達成すること（1文）",
  "success_criteria": "成功の判断基準（1文）",
  "steps": [
    {{
      "step_id": "s1",
      "step_type": "search",
      "description": "ステップの説明",
      "instruction": "検索クエリ or LLMへの具体的な指示",
      "expected_output": "このステップで得るべき情報",
      "depends_on": []
    }},
    {{
      "step_id": "s2",
      "step_type": "llm",
      "description": "分析ステップ",
      "instruction": "s1の結果を踏まえて〇〇を分析してください",
      "expected_output": "分析結果",
      "depends_on": ["s1"]
    }}
  ]
}}
```

## ルール
- ステップは最大6つまで
- step_type: "search"（RAG検索）/ "llm"（LLM直接）のどちらか
- 独立したステップの depends_on は []（並列実行される）
- 依存がある場合のみ depends_on に step_id を記載
- instruction は具体的に（抽象的な指示は避ける）
"""


def _make_verify_prompt(question: str, step: PlanStep) -> str:
    return f"""以下のステップ結果を検証してください。

## 元の質問
{question}

## ステップ
説明: {step.description}
期待する出力: {step.expected_output}

## 実際の結果
{step.result}

## 判定
USEFUL: [YES/NO]   ← 元の質問の回答に役立つか
COMPLETE: [YES/NO] ← 期待する出力と一致するか
PROCEED: [YES/NO/REPLAN] ← 次のステップに進むか
REASON: [理由を1文で]
"""


def _make_synthesis_prompt(question: str, plan: ExecutionPlan) -> str:
    completed = [s for s in plan.steps if s.status == StepStatus.COMPLETED and s.result]
    steps_text = "\n\n".join(
        f"### {s.step_id}: {s.description}\n{s.result}"
        for s in completed
    )
    return f"""以下の情報を統合して、元の質問への最終回答を作成してください。

## 元の質問
{question}

## ゴール
{plan.goal}

## 収集した情報
{steps_text if steps_text else "（情報収集なし）"}

## ルール
- 元の質問に直接・簡潔に答える
- 根拠がある場合は明記する
- 不確実な情報は「不確実」と明示する
"""


# ---------------------------------------------------------------------------
# Planner
# ---------------------------------------------------------------------------

class Planner:
    """
    Plan-Execute パターンの拡張実装。

    使い方:
        planner = Planner(client, model)
        result = await planner.run("衛星の熱設計について教えて")
        print(result.final_answer)
    """

    def __init__(
        self,
        client: OpenAI,
        model: str,
        *,
        max_replan: int = 2,
        max_parallel: int = 3,
        verbose: bool = True,
        # 外部ツール (オプション)
        search_fn: Optional[Callable[[str], str]] = None,
    ):
        self.client = client
        self.model = model
        self.max_replan = max_replan
        self.max_parallel = max_parallel
        self.verbose = verbose
        self.search_fn = search_fn  # RAG検索関数 (str -> str)

        self._async_client = AsyncOpenAI(
            base_url=client.base_url,
            api_key=client.api_key,
        )

    def run_sync(self, question: str, context: str = "") -> PlannerResult:
        """同期版エントリーポイント"""
        return asyncio.run(self.run(question, context))

    async def run(self, question: str, context: str = "") -> PlannerResult:
        """非同期版エントリーポイント"""
        t0 = time.perf_counter()
        plan_id = f"plan_{int(t0)}"

        # 1. Plan
        self._log("計画を作成中...")
        plan = await self._create_plan(plan_id, question, context)

        if not plan.steps:
            self._log("計画作成失敗 → 直接回答にフォールバック")
            answer = await self._direct_answer(question)
            dummy_plan = ExecutionPlan(
                plan_id=plan_id,
                original_question=question,
                goal="直接回答",
                success_criteria="",
            )
            return PlannerResult(
                final_answer=answer,
                plan=dummy_plan,
                total_elapsed_sec=time.perf_counter() - t0,
            )

        self._log(f"計画完了: {len(plan.steps)} ステップ")
        for s in plan.steps:
            deps = f" (deps: {s.depends_on})" if s.depends_on else ""
            self._log(f"  [{s.step_id}] {s.description}{deps}")

        # 2-4. Execute & Verify (with Re-plan)
        await self._execute_plan(plan)

        # 5. Synthesize
        completed_count = sum(1 for s in plan.steps if s.status == StepStatus.COMPLETED)
        if completed_count == 0:
            self._log("有効な結果なし → 直接回答にフォールバック")
            answer = await self._direct_answer(question)
        else:
            self._log("最終回答を統合中...")
            answer = await self._synthesize(question, plan)

        plan.status = StepStatus.COMPLETED
        return PlannerResult(
            final_answer=answer,
            plan=plan,
            total_elapsed_sec=time.perf_counter() - t0,
            replan_count=plan.replan_count,
        )

    async def _create_plan(self, plan_id: str, question: str, context: str) -> ExecutionPlan:
        """LLMに計画を作成させる"""
        prompt = _make_plan_prompt(question, context)
        try:
            response = await self._async_client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=1500,
            )
            content = response.choices[0].message.content or ""
            return self._parse_plan(plan_id, question, content)
        except Exception as e:
            self._log(f"計画作成エラー: {e}")
            return ExecutionPlan(
                plan_id=plan_id,
                original_question=question,
                goal="",
                success_criteria="",
            )

    def _parse_plan(self, plan_id: str, question: str, content: str) -> ExecutionPlan:
        """JSONレスポンスをパース"""
        match = re.search(r"```json\s*([\s\S]*?)\s*```", content)
        json_str = match.group(1) if match else content

        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            return ExecutionPlan(
                plan_id=plan_id, original_question=question, goal="", success_criteria=""
            )

        steps = []
        for item in data.get("steps", [])[:6]:
            step_type_str = item.get("step_type", "llm").lower()
            step_type = StepType.SEARCH if step_type_str == "search" else StepType.LLM

            steps.append(PlanStep(
                step_id=item.get("step_id", f"s{len(steps)+1}"),
                step_type=step_type,
                description=item.get("description", ""),
                instruction=item.get("instruction", ""),
                expected_output=item.get("expected_output", ""),
                depends_on=item.get("depends_on", []),
            ))

        return ExecutionPlan(
            plan_id=plan_id,
            original_question=question,
            goal=data.get("goal", ""),
            success_criteria=data.get("success_criteria", ""),
            steps=steps,
        )

    async def _execute_plan(self, plan: ExecutionPlan):
        """依存関係を考慮しながらステップを実行"""
        completed_ids: set[str] = set()
        pending = {s.step_id: s for s in plan.steps}

        while pending:
            # 実行可能なステップを抽出
            ready = [
                s for s in pending.values()
                if all(dep in completed_ids for dep in s.depends_on)
            ]
            if not ready:
                # 循環依存: 未完了ステップを強制実行
                ready = list(pending.values())[:1]
                self._log(f"警告: 循環依存を検出、強制実行: {ready[0].step_id}")

            # 依存ステップの結果をコンテキストとして注入
            for step in ready:
                if step.depends_on:
                    dep_context = "\n\n".join(
                        f"[{dep} の結果]\n{plan.steps[self._find_step_idx(plan, dep)].result or ''}"
                        for dep in step.depends_on
                        if dep in completed_ids
                    )
                    step.instruction = (
                        f"{step.instruction}\n\n[前ステップの結果]\n{dep_context}"
                        if dep_context else step.instruction
                    )

            # 並列実行
            batch_size = min(self.max_parallel, len(ready))
            for i in range(0, len(ready), batch_size):
                batch = ready[i:i + batch_size]
                self._log(f"並列実行: {[s.step_id for s in batch]}")
                await asyncio.gather(*[self._execute_step(s) for s in batch])

            # 検証 & 再計画
            for step in ready:
                if step.status == StepStatus.COMPLETED:
                    needs_replan = await self._verify_step(plan, step)
                    if needs_replan and plan.replan_count < self.max_replan:
                        await self._replan(plan, step)
                        # 再計画後は全て pending をリセット
                        pending = {s.step_id: s for s in plan.steps
                                   if s.status not in (StepStatus.COMPLETED, StepStatus.SKIPPED)}
                        break

                completed_ids.add(step.step_id)
                pending.pop(step.step_id, None)

    async def _execute_step(self, step: PlanStep):
        """1ステップを実行"""
        t0 = time.perf_counter()
        step.status = StepStatus.RUNNING

        try:
            if step.step_type == StepType.SEARCH and self.search_fn:
                result = await asyncio.get_event_loop().run_in_executor(
                    None, self.search_fn, step.instruction
                )
            else:
                response = await self._async_client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "user", "content": step.instruction}],
                    max_tokens=self.client.__dict__.get("max_tokens", 1500),
                )
                result = response.choices[0].message.content or ""

            step.result = result
            step.status = StepStatus.COMPLETED
            step.elapsed_sec = time.perf_counter() - t0
            self._log(f"  [{step.step_id}] 完了 ({step.elapsed_sec:.1f}s)")

        except Exception as e:
            step.error = str(e)
            step.retries += 1
            if step.retries <= step.max_retries:
                self._log(f"  [{step.step_id}] 再試行 ({step.retries}/{step.max_retries})")
                await self._execute_step(step)
            else:
                step.status = StepStatus.FAILED
                step.elapsed_sec = time.perf_counter() - t0
                self._log(f"  [{step.step_id}] 失敗: {e}")

    async def _verify_step(self, plan: ExecutionPlan, step: PlanStep) -> bool:
        """ステップの結果を検証。True を返すと再計画が必要"""
        if not step.result:
            return True

        prompt = _make_verify_prompt(plan.original_question, step)
        try:
            response = await self._async_client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=200,
            )
            content = response.choices[0].message.content or ""
            for line in content.splitlines():
                if line.upper().startswith("PROCEED:"):
                    val = line.split(":", 1)[1].strip().upper()
                    if "REPLAN" in val:
                        self._log(f"  [{step.step_id}] 再計画が必要")
                        return True
                    elif "NO" in val:
                        step.status = StepStatus.SKIPPED
                        self._log(f"  [{step.step_id}] スキップ")
                        return False
            return False
        except Exception:
            return False

    async def _replan(self, plan: ExecutionPlan, failed_step: PlanStep):
        """失敗ステップを踏まえて計画を再作成"""
        plan.replan_count += 1
        completed_info = "\n".join(
            f"- {s.step_id}: {s.description} → {(s.result or '')[:100]}"
            for s in plan.steps
            if s.status == StepStatus.COMPLETED
        )
        replan_question = (
            f"{plan.original_question}\n\n"
            f"[完了済み情報]\n{completed_info}\n\n"
            f"[失敗したステップ]\n{failed_step.description}: {failed_step.error or '結果が不十分'}"
        )
        new_plan = await self._create_plan(plan.plan_id, replan_question, "")
        if new_plan.steps:
            completed = [s for s in plan.steps if s.status == StepStatus.COMPLETED]
            plan.steps = completed + new_plan.steps
            self._log(f"再計画: {len(new_plan.steps)} 新ステップ追加")

    async def _synthesize(self, question: str, plan: ExecutionPlan) -> str:
        """全ステップの結果を統合"""
        prompt = _make_synthesis_prompt(question, plan)
        try:
            response = await self._async_client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=2000,
            )
            return response.choices[0].message.content or ""
        except Exception as e:
            return f"統合エラー: {e}"

    async def _direct_answer(self, question: str) -> str:
        """計画なしで直接回答"""
        try:
            response = await self._async_client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": question}],
                max_tokens=2000,
            )
            return response.choices[0].message.content or ""
        except Exception as e:
            return f"エラー: {e}"

    def _find_step_idx(self, plan: ExecutionPlan, step_id: str) -> int:
        for i, s in enumerate(plan.steps):
            if s.step_id == step_id:
                return i
        return 0

    def _log(self, msg: str):
        if self.verbose:
            print(f"[Planner] {msg}")


# ---------------------------------------------------------------------------
# 計画の保存・復元
# ---------------------------------------------------------------------------

PLAN_SAVE_DIR = Path(__file__).parent.parent / "sessions" / "plans"


def save_plan(plan: ExecutionPlan):
    """計画をJSONファイルに保存"""
    PLAN_SAVE_DIR.mkdir(parents=True, exist_ok=True)
    path = PLAN_SAVE_DIR / f"{plan.plan_id}.json"
    path.write_text(json.dumps(plan.to_dict(), ensure_ascii=False, indent=2))


def load_plan(plan_id: str) -> Optional[ExecutionPlan]:
    """保存済み計画を読み込む"""
    path = PLAN_SAVE_DIR / f"{plan_id}.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    return ExecutionPlan.from_dict(data)


def list_plans() -> list[dict]:
    """保存済み計画の一覧"""
    PLAN_SAVE_DIR.mkdir(parents=True, exist_ok=True)
    plans = []
    for p in sorted(PLAN_SAVE_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            data = json.loads(p.read_text())
            plans.append({
                "plan_id": data.get("plan_id", ""),
                "question": data.get("original_question", "")[:60],
                "status": data.get("status", ""),
                "steps": len(data.get("steps", [])),
            })
        except Exception:
            pass
    return plans


# ---------------------------------------------------------------------------
# クイックテスト
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import os
    from openai import OpenAI

    client = OpenAI(
        base_url=os.getenv("LLM_BASE_URL", "https://api.cerebras.ai/v1"),
        api_key=os.getenv("CEREBRAS_API_KEY", "dummy"),
    )
    model = os.getenv("LLM_MODEL", "gpt-oss-120b")

    planner = Planner(client, model, verbose=True)

    question = "宇宙機の電力収支設計の手順と、太陽電池パネルの必要面積の計算方法を教えてください"
    print(f"Question: {question}\n")

    result = planner.run_sync(question)
    print(f"\n=== 最終回答 ===\n{result.final_answer}")
    print(f"\n経過時間: {result.total_elapsed_sec:.1f}s")
    print(f"再計画回数: {result.replan_count}")
    print(f"ステップ数: {len(result.plan.steps)}")
    for s in result.plan.steps:
        print(f"  [{s.step_id}] {s.status.value}: {s.description[:50]}")
