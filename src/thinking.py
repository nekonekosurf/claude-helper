"""
Extended Thinking (深い推論) - ローカルモデルでの実装

## 実装パターン

1. Chain-of-Thought (CoT)
   - <thinking>...</thinking> タグで推論を強制
   - ステップバイステップの思考を明示

2. Self-Reflection (自己反省)
   - 生成 → 批判的レビュー → 修正
   - 誤りを自分で発見して修正する

3. Tree-of-Thought (ToT)
   - 複数の候補解を並列生成
   - 各候補を評価して最良を選択

4. Best-of-N サンプリング
   - N個の回答を生成
   - スコアリングして最良を返す
"""

from __future__ import annotations

import asyncio
import re
import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
from openai import OpenAI, AsyncOpenAI


# ---------------------------------------------------------------------------
# データ型
# ---------------------------------------------------------------------------

@dataclass
class ThinkingResult:
    """思考プロセスの結果"""
    answer: str                           # 最終回答
    thinking: str = ""                    # 推論過程
    method: str = "direct"               # 使用した手法
    iterations: int = 1                   # 繰り返し回数
    candidates: list[str] = field(default_factory=list)  # BestOfN の候補
    scores: list[float] = field(default_factory=list)    # 候補のスコア
    elapsed_sec: float = 0.0
    token_estimate: int = 0


# ---------------------------------------------------------------------------
# 1. Chain-of-Thought
# ---------------------------------------------------------------------------

COT_SYSTEM_PROMPT = """あなたは段階的に考える問題解決エージェントです。
回答する前に、必ず <thinking> タグ内で思考プロセスを記述してください。

形式:
<thinking>
ステップ1: [考えること]
ステップ2: [次に考えること]
...
結論: [最終的な判断]
</thinking>

[ここに最終回答を記述]
"""


def chain_of_thought(
    client: OpenAI,
    model: str,
    prompt: str,
    *,
    system_override: Optional[str] = None,
    max_tokens: int = 3000,
    extract_thinking: bool = True,
) -> ThinkingResult:
    """
    Chain-of-Thought: <thinking> タグで推論を強制する。

    Args:
        client: OpenAI互換クライアント
        model: モデル名
        prompt: ユーザープロンプト
        system_override: システムプロンプトをカスタマイズ
        max_tokens: 最大トークン数
        extract_thinking: 思考部分を分離するか

    Returns:
        ThinkingResult
    """
    t0 = time.perf_counter()
    system = system_override or COT_SYSTEM_PROMPT

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        max_tokens=max_tokens,
    )

    content = response.choices[0].message.content or ""
    thinking, answer = _extract_thinking_tag(content) if extract_thinking else ("", content)

    return ThinkingResult(
        answer=answer.strip(),
        thinking=thinking.strip(),
        method="chain_of_thought",
        elapsed_sec=time.perf_counter() - t0,
    )


def _extract_thinking_tag(text: str) -> tuple[str, str]:
    """<thinking>...</thinking> タグを抽出して (thinking, answer) を返す"""
    match = re.search(r"<thinking>([\s\S]*?)</thinking>", text, re.IGNORECASE)
    if match:
        thinking = match.group(1)
        answer = text[:match.start()] + text[match.end():]
        answer = answer.strip()
        return thinking, answer
    return "", text


# ---------------------------------------------------------------------------
# 2. Self-Reflection
# ---------------------------------------------------------------------------

REFLECTION_CRITIQUE_PROMPT = """以下の回答を批判的にレビューしてください。

## 元の質問
{question}

## 生成された回答
{answer}

## レビュー観点
- 回答は質問に正確に答えているか？
- 論理的な誤り・矛盾はないか？
- 重要な情報の抜け漏れはないか？
- 宇宙工学の観点から技術的に正確か？
- 改善すべき点を具体的に指摘してください

## 出力形式
ISSUES:
- [問題点1]
- [問題点2]
...
SEVERITY: [high/medium/low]  ← 全体的な問題の深刻さ
NEEDS_REVISION: [yes/no]
"""

REFLECTION_REVISE_PROMPT = """以下のレビュー指摘を踏まえて回答を改善してください。

## 元の質問
{question}

## 元の回答
{original_answer}

## レビュー指摘
{critique}

## 指示
指摘された問題点を全て修正した、改善版の回答を作成してください。
改善した点を冒頭に1行で示してください: [改善点: ...]
"""


def self_reflection(
    client: OpenAI,
    model: str,
    prompt: str,
    *,
    max_iterations: int = 2,
    revise_if_severity: str = "medium",  # "high"/"medium"/"low"
    max_tokens: int = 2000,
    verbose: bool = False,
) -> ThinkingResult:
    """
    Self-Reflection: 生成 → 批判 → 修正 を繰り返す。

    Args:
        client: OpenAI互換クライアント
        model: モデル名
        prompt: ユーザープロンプト
        max_iterations: 最大繰り返し回数 (推奨: 2)
        revise_if_severity: この深刻度以上なら修正する
        max_tokens: 各ステップの最大トークン数
        verbose: 中間結果を表示するか

    Returns:
        ThinkingResult
    """
    t0 = time.perf_counter()
    severity_order = {"low": 0, "medium": 1, "high": 2}
    revise_threshold = severity_order.get(revise_if_severity, 1)

    # Step 1: 初期回答生成 (CoT付き)
    initial = chain_of_thought(client, model, prompt, max_tokens=max_tokens)
    current_answer = initial.answer
    thoughts = [initial.thinking]

    if verbose:
        print(f"[Reflection] 初期回答生成完了 ({initial.elapsed_sec:.1f}s)")

    revision_count = 0
    for i in range(max_iterations):
        # Step 2: 批判的レビュー
        critique_prompt = REFLECTION_CRITIQUE_PROMPT.format(
            question=prompt,
            answer=current_answer,
        )
        critique_response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": critique_prompt}],
            max_tokens=500,
        )
        critique = critique_response.choices[0].message.content or ""

        # 深刻度を判定
        severity = "low"
        needs_revision = False
        for line in critique.splitlines():
            if line.upper().startswith("SEVERITY:"):
                severity = line.split(":", 1)[1].strip().lower()
            elif line.upper().startswith("NEEDS_REVISION:"):
                needs_revision = "YES" in line.upper()

        if verbose:
            print(f"[Reflection] イテレーション {i+1}: severity={severity}, "
                  f"needs_revision={needs_revision}")

        # 修正が不要なら終了
        if not needs_revision or severity_order.get(severity, 0) < revise_threshold:
            if verbose:
                print(f"[Reflection] 修正不要 - 終了")
            break

        # Step 3: 修正
        revise_prompt = REFLECTION_REVISE_PROMPT.format(
            question=prompt,
            original_answer=current_answer,
            critique=critique,
        )
        revise_response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": revise_prompt}],
            max_tokens=max_tokens,
        )
        current_answer = revise_response.choices[0].message.content or current_answer
        thoughts.append(f"[修正{i+1}]\n{critique}\n→ 修正済み")
        revision_count += 1

        if verbose:
            print(f"[Reflection] 修正完了 ({revision_count}回目)")

    return ThinkingResult(
        answer=current_answer,
        thinking="\n\n".join(thoughts),
        method="self_reflection",
        iterations=revision_count + 1,
        elapsed_sec=time.perf_counter() - t0,
    )


# ---------------------------------------------------------------------------
# 3. Tree-of-Thought
# ---------------------------------------------------------------------------

TOT_EXPAND_PROMPT = """以下の問題に対して、異なる3つのアプローチを提案してください。

## 問題
{question}

## 指示
各アプローチを100文字程度で説明してください。

APPROACH_1: [アプローチ1の説明]
APPROACH_2: [アプローチ2の説明]
APPROACH_3: [アプローチ3の説明]
"""

TOT_EVALUATE_PROMPT = """以下のアプローチを評価してください。

## 問題
{question}

## アプローチ
{approach}

## 評価
- 実現可能性 (1-5):
- 完全性 (1-5):
- 宇宙工学的妥当性 (1-5):

SCORE: [合計 / 15]  ← 例: "SCORE: 12/15"
REASONING: [評価理由を1文で]
"""

TOT_ELABORATE_PROMPT = """以下のアプローチを詳しく展開して、完全な回答を作成してください。

## 問題
{question}

## 採用アプローチ
{approach}

## 詳細な回答
"""


async def _evaluate_approach(
    client: AsyncOpenAI,
    model: str,
    question: str,
    approach: str,
    max_tokens: int = 200,
) -> float:
    """アプローチをスコアリング (非同期)"""
    prompt = TOT_EVALUATE_PROMPT.format(question=question, approach=approach)
    try:
        response = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
        )
        content = response.choices[0].message.content or ""
        for line in content.splitlines():
            if line.upper().startswith("SCORE:"):
                score_str = line.split(":", 1)[1].strip()
                match = re.search(r"(\d+)\s*/\s*15", score_str)
                if match:
                    return float(match.group(1)) / 15.0
    except Exception:
        pass
    return 0.5  # デフォルト


def tree_of_thought(
    client: OpenAI,
    model: str,
    prompt: str,
    *,
    n_branches: int = 3,       # 並列に探索するブランチ数
    max_tokens: int = 2000,
    verbose: bool = False,
) -> ThinkingResult:
    """
    Tree-of-Thought: 複数アプローチを探索して最良を選択。

    Args:
        client: OpenAI互換クライアント (同期版)
        model: モデル名
        prompt: ユーザープロンプト
        n_branches: 探索するアプローチ数 (推奨: 3)
        max_tokens: 最終回答の最大トークン数
        verbose: 中間結果を表示するか

    Returns:
        ThinkingResult
    """
    t0 = time.perf_counter()

    # Step 1: アプローチを展開
    expand_prompt = TOT_EXPAND_PROMPT.format(question=prompt)
    expand_response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": expand_prompt}],
        max_tokens=500,
    )
    expand_content = expand_response.choices[0].message.content or ""

    # アプローチを抽出
    approaches = []
    for line in expand_content.splitlines():
        for i in range(1, n_branches + 1):
            prefix = f"APPROACH_{i}:"
            if line.upper().startswith(prefix):
                approaches.append(line[len(prefix):].strip())
                break

    if not approaches:
        # 展開失敗: CoTにフォールバック
        return chain_of_thought(client, model, prompt, max_tokens=max_tokens)

    if verbose:
        print(f"[ToT] {len(approaches)} アプローチを展開")

    # Step 2: 非同期でアプローチを評価
    async def evaluate_all():
        async_client = AsyncOpenAI(
            base_url=client.base_url,
            api_key=client.api_key,
        )
        tasks = [
            _evaluate_approach(async_client, model, prompt, approach)
            for approach in approaches
        ]
        return await asyncio.gather(*tasks)

    scores = asyncio.run(evaluate_all())

    if verbose:
        for approach, score in zip(approaches, scores):
            print(f"[ToT] スコア {score:.2f}: {approach[:60]}...")

    # Step 3: 最良アプローチを詳細展開
    best_idx = max(range(len(scores)), key=lambda i: scores[i])
    best_approach = approaches[best_idx]
    best_score = scores[best_idx]

    elaborate_prompt = TOT_ELABORATE_PROMPT.format(
        question=prompt,
        approach=best_approach,
    )
    elaborate_response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": elaborate_prompt}],
        max_tokens=max_tokens,
    )
    final_answer = elaborate_response.choices[0].message.content or ""

    if verbose:
        print(f"[ToT] 最良アプローチ (score={best_score:.2f}): {best_approach[:60]}...")

    return ThinkingResult(
        answer=final_answer,
        thinking="\n".join(
            f"Approach {i+1} (score={s:.2f}): {a}"
            for i, (a, s) in enumerate(zip(approaches, scores))
        ),
        method="tree_of_thought",
        candidates=approaches,
        scores=list(scores),
        elapsed_sec=time.perf_counter() - t0,
    )


# ---------------------------------------------------------------------------
# 4. Best-of-N サンプリング
# ---------------------------------------------------------------------------

BON_SCORE_PROMPT = """以下の回答を評価してください (0-10点)。

## 質問
{question}

## 回答
{answer}

## 評価基準
- 正確性 (0-4): 事実に基づいているか
- 完全性 (0-3): 質問に完全に答えているか
- 明確性 (0-3): 分かりやすく書かれているか

SCORE: [合計点 / 10]
"""


async def _generate_and_score(
    client: AsyncOpenAI,
    model: str,
    prompt: str,
    max_tokens: int,
    temperature: float,
    question: str,
) -> tuple[str, float]:
    """1つの回答を生成してスコアリング (非同期)"""
    # 生成
    gen_response = await client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=max_tokens,
        temperature=temperature,
    )
    answer = gen_response.choices[0].message.content or ""

    # スコアリング
    score_prompt = BON_SCORE_PROMPT.format(question=question, answer=answer)
    score_response = await client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": score_prompt}],
        max_tokens=100,
        temperature=0.0,
    )
    score_content = score_response.choices[0].message.content or ""
    score = 5.0  # デフォルト
    for line in score_content.splitlines():
        if line.upper().startswith("SCORE:"):
            match = re.search(r"(\d+(?:\.\d+)?)\s*/\s*10", line)
            if match:
                score = float(match.group(1))
                break

    return answer, score


def best_of_n(
    client: OpenAI,
    model: str,
    prompt: str,
    *,
    n: int = 3,
    temperature: float = 0.7,
    max_tokens: int = 2000,
    verbose: bool = False,
) -> ThinkingResult:
    """
    Best-of-N サンプリング: N個生成して最良を返す。

    Args:
        client: OpenAI互換クライアント
        model: モデル名
        prompt: ユーザープロンプト
        n: 生成する候補数 (推奨: 3-5)
        temperature: 生成温度 (多様性のため少し高め)
        max_tokens: 各候補の最大トークン数
        verbose: 中間結果を表示するか

    Returns:
        ThinkingResult
    """
    t0 = time.perf_counter()

    async def run_all():
        async_client = AsyncOpenAI(
            base_url=client.base_url,
            api_key=client.api_key,
        )
        tasks = [
            _generate_and_score(
                async_client, model, prompt, max_tokens, temperature, prompt
            )
            for _ in range(n)
        ]
        return await asyncio.gather(*tasks)

    results = asyncio.run(run_all())
    candidates = [r[0] for r in results]
    scores = [r[1] for r in results]

    best_idx = max(range(len(scores)), key=lambda i: scores[i])
    best_answer = candidates[best_idx]

    if verbose:
        for i, (ans, score) in enumerate(zip(candidates, scores)):
            print(f"[BoN] 候補{i+1} (score={score:.1f}): {ans[:60]}...")
        print(f"[BoN] 採用: 候補{best_idx+1} (score={scores[best_idx]:.1f})")

    return ThinkingResult(
        answer=best_answer,
        thinking=f"Best of {n} (scores: {[round(s, 1) for s in scores]})",
        method="best_of_n",
        candidates=candidates,
        scores=scores,
        iterations=n,
        elapsed_sec=time.perf_counter() - t0,
    )


# ---------------------------------------------------------------------------
# 統合インターフェース
# ---------------------------------------------------------------------------

class ThinkingMode(Enum):
    DIRECT         = "direct"           # 通常の生成
    COT            = "chain_of_thought" # Chain-of-Thought
    REFLECTION     = "self_reflection"  # Self-Reflection
    TOT            = "tree_of_thought"  # Tree-of-Thought
    BEST_OF_N      = "best_of_n"        # Best-of-N
    AUTO           = "auto"             # タスクに応じて自動選択


def think(
    client: OpenAI,
    model: str,
    prompt: str,
    mode: ThinkingMode = ThinkingMode.AUTO,
    *,
    max_tokens: int = 2000,
    verbose: bool = False,
    **kwargs,
) -> ThinkingResult:
    """
    思考モードを選択して実行する統合インターフェース。

    Args:
        client: OpenAI互換クライアント
        model: モデル名
        prompt: ユーザープロンプト
        mode: 思考モード
        max_tokens: 最大トークン数
        verbose: デバッグ出力を有効化
        **kwargs: 各思考モード固有のパラメータ

    Returns:
        ThinkingResult
    """
    if mode == ThinkingMode.AUTO:
        mode = _auto_select_mode(prompt)
        if verbose:
            print(f"[Think] 自動選択: {mode.value}")

    if mode == ThinkingMode.DIRECT:
        t0 = time.perf_counter()
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
        )
        return ThinkingResult(
            answer=response.choices[0].message.content or "",
            method="direct",
            elapsed_sec=time.perf_counter() - t0,
        )

    elif mode == ThinkingMode.COT:
        return chain_of_thought(client, model, prompt, max_tokens=max_tokens, **kwargs)

    elif mode == ThinkingMode.REFLECTION:
        return self_reflection(client, model, prompt, max_tokens=max_tokens,
                               verbose=verbose, **kwargs)

    elif mode == ThinkingMode.TOT:
        return tree_of_thought(client, model, prompt, max_tokens=max_tokens,
                               verbose=verbose, **kwargs)

    elif mode == ThinkingMode.BEST_OF_N:
        return best_of_n(client, model, prompt, max_tokens=max_tokens,
                         verbose=verbose, **kwargs)

    else:
        raise ValueError(f"Unknown mode: {mode}")


def _auto_select_mode(prompt: str) -> ThinkingMode:
    """プロンプトの特徴から思考モードを自動選択"""
    # コーディング → CoT (ステップバイステップが有効)
    code_patterns = [r"コード", r"実装", r"\bcode\b", r"バグ", r"デバッグ"]
    if any(re.search(p, prompt, re.I) for p in code_patterns):
        return ThinkingMode.COT

    # 評価・比較 → ToT (複数案の検討が有効)
    comparison_patterns = [r"比較", r"どちらが", r"最適", r"トレードオフ", r"選択"]
    if any(re.search(p, prompt, re.I) for p in comparison_patterns):
        return ThinkingMode.TOT

    # 長い複雑な質問 → Self-Reflection
    if len(prompt) > 200:
        return ThinkingMode.REFLECTION

    # デフォルト: CoT
    return ThinkingMode.COT


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

    tests = [
        ("衛星の熱制御システムの主な手法を3つ説明して", ThinkingMode.COT),
        ("Pythonのquicksortを実装して", ThinkingMode.COT),
        ("宇宙機の推進系の比較: 化学推進 vs 電気推進", ThinkingMode.TOT),
    ]

    for prompt, mode in tests:
        print(f"\n{'='*60}")
        print(f"Mode: {mode.value}")
        print(f"Prompt: {prompt}")
        result = think(client, model, prompt, mode=mode, verbose=True)
        print(f"\n--- 回答 ---")
        print(result.answer[:500])
        print(f"\n経過: {result.elapsed_sec:.1f}s, 手法: {result.method}")
