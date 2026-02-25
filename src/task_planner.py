"""
Plan-Verify-Execute パターン

複雑なタスクを安全に処理するための3段階制御：
1. Plan: タスクを分解し、成功基準を明文化
2. Verify: 各ステップ完了後、元の問いと照合
3. Execute: 検証済みの計画に沿って実行

コンテキスト長が限定的なローカルLLMで、
間違った方向に深掘りすることを防ぐ。
"""

from dataclasses import dataclass, field
from typing import Optional
from enum import Enum


class TaskStatus(Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    NEEDS_REPLANNING = "needs_replanning"


@dataclass
class TaskStep:
    """タスクの1ステップ"""
    description: str
    expected_output: str  # 期待する出力の説明
    search_query: Optional[str] = None  # 検索が必要な場合のクエリ
    doc_filter: Optional[str] = None  # 文書フィルタ
    status: TaskStatus = TaskStatus.PENDING
    result: Optional[str] = None
    verification: Optional[str] = None  # 検証結果


@dataclass
class TaskPlan:
    """タスク計画"""
    original_question: str  # 元の質問（常に保持=アンカー）
    goal: str  # 最終的なゴール
    success_criteria: str  # 成功基準
    steps: list[TaskStep] = field(default_factory=list)
    current_step: int = 0
    status: TaskStatus = TaskStatus.PENDING
    context_budget: int = 4000  # ステップごとのトークン予算


def create_plan_prompt(question: str) -> str:
    """
    LLMにタスク計画を作成させるプロンプトを生成
    """
    return f"""以下の質問に答えるための計画を立ててください。

## 質問
{question}

## 出力形式（必ずこの形式で回答してください）
GOAL: [最終的に何を回答すべきか、1文で]
SUCCESS_CRITERIA: [回答が成功と言える条件、1文で]
STEPS:
1. [ステップの説明] | SEARCH: [検索クエリ（不要ならNONE）] | EXPECT: [このステップで得られるべき情報]
2. [ステップの説明] | SEARCH: [検索クエリ] | EXPECT: [期待する情報]
3. ...

## ルール
- ステップは最大5つまで（コンテキスト節約のため）
- 各ステップは独立して実行可能にする
- 検索クエリは具体的に（曖昧な検索は避ける）
- 「調べる」「確認する」だけでなく、何をどう調べるか明記
"""


def parse_plan_response(response: str, original_question: str) -> TaskPlan:
    """
    LLMの応答をTaskPlanにパース
    """
    plan = TaskPlan(
        original_question=original_question,
        goal="",
        success_criteria="",
    )

    lines = response.strip().split("\n")
    current_section = None

    for line in lines:
        line = line.strip()
        if not line:
            continue

        if line.startswith("GOAL:"):
            plan.goal = line[5:].strip()
        elif line.startswith("SUCCESS_CRITERIA:"):
            plan.success_criteria = line[17:].strip()
        elif line.startswith("STEPS:"):
            current_section = "steps"
        elif current_section == "steps" and (line[0].isdigit() or line.startswith("-")):
            # Parse step: "1. description | SEARCH: query | EXPECT: expected"
            # Remove leading number/bullet
            step_text = line.lstrip("0123456789.-) ").strip()

            parts = step_text.split("|")
            description = parts[0].strip()
            search_query = None
            expected_output = ""

            for part in parts[1:]:
                part = part.strip()
                if part.upper().startswith("SEARCH:"):
                    sq = part[7:].strip()
                    if sq.upper() != "NONE":
                        search_query = sq
                elif part.upper().startswith("EXPECT:"):
                    expected_output = part[7:].strip()

            plan.steps.append(TaskStep(
                description=description,
                expected_output=expected_output,
                search_query=search_query,
            ))

    plan.status = TaskStatus.PENDING
    return plan


def create_verify_prompt(plan: TaskPlan, step_index: int) -> str:
    """
    ステップの結果を検証するプロンプトを生成
    """
    step = plan.steps[step_index]

    return f"""以下のタスクのステップ結果を検証してください。

## 元の質問（アンカー）
{plan.original_question}

## ゴール
{plan.goal}

## 現在のステップ（{step_index + 1}/{len(plan.steps)}）
説明: {step.description}
期待する出力: {step.expected_output}

## ステップの結果
{step.result}

## 検証してください
1. この結果は元の質問の回答に役立つか？ (YES/NO)
2. 期待する出力と一致するか？ (YES/NO)
3. 次のステップに進んでよいか？ (YES/NO/REPLAN)

回答形式:
USEFUL: [YES/NO]
MATCHES: [YES/NO]
PROCEED: [YES/NO/REPLAN]
REASON: [1文で理由]
"""


def parse_verify_response(response: str) -> dict:
    """検証結果をパース"""
    result = {
        "useful": False,
        "matches": False,
        "proceed": "no",
        "reason": "",
    }

    for line in response.strip().split("\n"):
        line = line.strip()
        if line.upper().startswith("USEFUL:"):
            result["useful"] = "YES" in line.upper()
        elif line.upper().startswith("MATCHES:"):
            result["matches"] = "YES" in line.upper()
        elif line.upper().startswith("PROCEED:"):
            val = line[8:].strip().upper()
            if "REPLAN" in val:
                result["proceed"] = "replan"
            elif "YES" in val:
                result["proceed"] = "yes"
            else:
                result["proceed"] = "no"
        elif line.upper().startswith("REASON:"):
            result["reason"] = line[7:].strip()

    return result


def create_synthesis_prompt(plan: TaskPlan) -> str:
    """
    全ステップの結果を統合して最終回答を生成するプロンプト
    """
    steps_summary = ""
    for i, step in enumerate(plan.steps):
        if step.status == TaskStatus.COMPLETED and step.result:
            steps_summary += f"\n### ステップ{i+1}: {step.description}\n{step.result}\n"

    return f"""以下の情報を統合して、元の質問に対する最終回答を作成してください。

## 元の質問
{plan.original_question}

## ゴール
{plan.goal}

## 成功基準
{plan.success_criteria}

## 収集した情報
{steps_summary}

## ルール
- 元の質問に直接答える
- 根拠となる文書番号があれば明記
- 情報が不足している場合はその旨を述べる
- 簡潔に（コンテキスト節約）
"""


def should_use_planner(question: str) -> bool:
    """
    質問がPlan-Verify-Executeパターンを使うべきか判定
    シンプルな質問には使わない（オーバーヘッド削減）
    """
    # Long questions or complex patterns → use planner
    if len(question) > 100:
        return True

    complex_patterns = [
        "どうやって", "どのように", "手順", "方法",
        "比較", "違い", "それぞれ",
        "設計", "解析", "分析",
        "問題", "トラブル", "不具合",
        "レビュー", "審査", "チェック",
    ]

    match_count = sum(1 for p in complex_patterns if p in question)
    return match_count >= 2
