"""
モデルルーティング - タスク分類に基づいて適切なローカルモデルを選択

## 対応モデル（OpenAI互換APIで提供されることを前提）
- CodeGemma 7B      : コーディング専用（コード生成・デバッグ・補完）
- StarCoder2 15B    : コーディング補助（多言語対応・コードレビュー）
- Gemma 2 27B       : 宇宙専門知識・推論（ファインチューニング済みを推奨）
- Llama 3.1 70B     : 複雑な一般推論・長文生成
- Gemma 2 9B        : 軽量タスク（要約・分類・翻訳）
- Phi-3 Mini 3.8B   : 超軽量タスク（即答・単純変換）

## ルーティング戦略
1. キーワードスコアリング（低レイテンシ・ルールベース）
2. オプション: 分類モデル（より高精度、別途起動が必要）
3. オプション: LLM自己判定（最高精度、コスト高）
"""

from __future__ import annotations

import re
import os
import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
from openai import OpenAI


# ---------------------------------------------------------------------------
# モデル定義
# ---------------------------------------------------------------------------

class ModelRole(Enum):
    CODING      = "coding"       # コード生成・デバッグ
    SPACE       = "space"        # 宇宙専門知識
    REASONING   = "reasoning"    # 複雑推論・長文
    LIGHTWEIGHT = "lightweight"  # 要約・分類・軽量タスク
    ULTRALIGHT  = "ultralight"   # 即答・単純変換


@dataclass
class ModelConfig:
    """1モデルの設定"""
    name: str                        # OpenAI API に渡すモデル名
    role: ModelRole
    base_url: str                    # vLLM / Ollama などのエンドポイント
    api_key: str = "dummy"
    max_tokens: int = 4096
    context_window: int = 8192       # モデルの最大コンテキスト長
    gpu_memory_gb: float = 0.0       # 参考: 必要 VRAM
    description: str = ""
    # 優先度 (低いほど優先、同一 role 内で複数候補がある場合)
    priority: int = 0


# デフォルト設定: 環境変数でオーバーライド可能
DEFAULT_MODELS: dict[ModelRole, list[ModelConfig]] = {
    ModelRole.CODING: [
        ModelConfig(
            name=os.getenv("MODEL_CODING_PRIMARY", "codegemma:7b"),
            role=ModelRole.CODING,
            base_url=os.getenv("VLLM_URL_CODING", "http://localhost:8001/v1"),
            max_tokens=4096,
            context_window=8192,
            gpu_memory_gb=5.0,
            description="コーディング専用: コード生成・デバッグ・補完",
            priority=0,
        ),
        ModelConfig(
            name=os.getenv("MODEL_CODING_FALLBACK", "starcoder2:15b"),
            role=ModelRole.CODING,
            base_url=os.getenv("VLLM_URL_CODING_FB", "http://localhost:8001/v1"),
            max_tokens=4096,
            context_window=16384,
            gpu_memory_gb=9.0,
            description="コーディング補助: 多言語・コードレビュー",
            priority=1,
        ),
    ],
    ModelRole.SPACE: [
        ModelConfig(
            name=os.getenv("MODEL_SPACE", "gemma2:27b-space-ft"),
            role=ModelRole.SPACE,
            base_url=os.getenv("VLLM_URL_SPACE", "http://localhost:8002/v1"),
            max_tokens=4096,
            context_window=8192,
            gpu_memory_gb=18.0,
            description="宇宙専門知識: ファインチューニング済み Gemma 2 27B",
            priority=0,
        ),
    ],
    ModelRole.REASONING: [
        ModelConfig(
            name=os.getenv("MODEL_REASONING_PRIMARY", "llama3.1:70b"),
            role=ModelRole.REASONING,
            base_url=os.getenv("VLLM_URL_REASONING", "http://localhost:8003/v1"),
            max_tokens=8192,
            context_window=131072,
            gpu_memory_gb=42.0,
            description="複雑推論: Llama 3.1 70B",
            priority=0,
        ),
        ModelConfig(
            name=os.getenv("MODEL_REASONING_FALLBACK", "gemma2:27b"),
            role=ModelRole.REASONING,
            base_url=os.getenv("VLLM_URL_REASONING_FB", "http://localhost:8002/v1"),
            max_tokens=4096,
            context_window=8192,
            gpu_memory_gb=18.0,
            description="推論補助: Gemma 2 27B (汎用)",
            priority=1,
        ),
    ],
    ModelRole.LIGHTWEIGHT: [
        ModelConfig(
            name=os.getenv("MODEL_LIGHT", "gemma2:9b"),
            role=ModelRole.LIGHTWEIGHT,
            base_url=os.getenv("VLLM_URL_LIGHT", "http://localhost:8004/v1"),
            max_tokens=2048,
            context_window=8192,
            gpu_memory_gb=6.0,
            description="軽量: 要約・分類・翻訳",
            priority=0,
        ),
    ],
    ModelRole.ULTRALIGHT: [
        ModelConfig(
            name=os.getenv("MODEL_ULTRA", "phi3:3.8b"),
            role=ModelRole.ULTRALIGHT,
            base_url=os.getenv("VLLM_URL_ULTRA", "http://localhost:8005/v1"),
            max_tokens=1024,
            context_window=4096,
            gpu_memory_gb=3.0,
            description="超軽量: 即答・単純変換",
            priority=0,
        ),
    ],
}


# ---------------------------------------------------------------------------
# タスク分類 (キーワードスコアリング)
# ---------------------------------------------------------------------------

# 各 role に対するキーワードとスコア (重み)
_KEYWORD_RULES: dict[ModelRole, list[tuple[str, float]]] = {
    ModelRole.CODING: [
        # 言語名
        (r"\bpython\b", 2.0), (r"\bjavascript\b", 2.0), (r"\btypescript\b", 2.0),
        (r"\brust\b", 2.0), (r"\bc\+\+", 2.0), (r"\bgolang\b", 2.0),
        # 行為
        (r"コード", 2.0), (r"実装", 2.0), (r"関数", 1.5), (r"クラス", 1.5),
        (r"バグ", 2.0), (r"デバッグ", 2.0), (r"エラー.{0,10}修正", 2.0),
        (r"書いて", 1.5), (r"作って", 1.2), (r"コードを書", 3.0),
        (r"テスト", 1.0), (r"ユニットテスト", 2.0),
        (r"リファクタ", 2.0), (r"最適化", 1.5),
        (r"アルゴリズム", 1.5), (r"データ構造", 1.5),
        # 英語
        (r"\bcode\b", 1.5), (r"\bfunction\b", 1.5), (r"\bbug\b", 2.0),
        (r"\bimplement\b", 1.5), (r"\brefactor\b", 2.0), (r"\bdebug\b", 2.0),
        (r"\bapi\b", 1.0), (r"\bsql\b", 1.5), (r"\bregex\b", 1.5),
    ],
    ModelRole.SPACE: [
        # 宇宙工学
        (r"宇宙", 2.0), (r"衛星", 2.0), (r"ロケット", 2.0), (r"軌道", 2.0),
        (r"スラスタ", 2.5), (r"推進", 2.0), (r"姿勢制御", 2.5),
        (r"熱制御", 2.5), (r"構造", 1.5), (r"ミッション", 1.5),
        (r"打上", 2.0), (r"探査", 2.0), (r"JAXA|NASA|ESA", 2.0),
        (r"太陽電池", 2.0), (r"電力系", 1.5), (r"通信系", 1.5),
        (r"放射線", 2.0), (r"宇宙環境", 2.5), (r"デブリ", 2.0),
        # 英語
        (r"\bsatellite\b", 2.0), (r"\borbit\b", 2.0), (r"\bspacecraft\b", 2.5),
        (r"\bthruster\b", 2.5), (r"\battitude\b", 2.0), (r"\bpropulsion\b", 2.0),
        (r"\bthermal\b", 1.5), (r"\blaunch\b", 1.5), (r"\bpayload\b", 2.0),
        (r"\bjerg\b", 3.0), (r"宇宙機", 2.5),
        (r"電気試験", 2.5), (r"環境試験", 2.5), (r"構造試験", 2.5),
        (r"打上環境", 2.5), (r"放射線試験", 2.5), (r"JERG-", 3.5),
    ],
    ModelRole.REASONING: [
        (r"分析", 1.5), (r"比較", 1.5), (r"設計", 1.5), (r"評価", 1.5),
        (r"検討", 1.5), (r"なぜ", 1.0), (r"理由", 1.0), (r"原因", 1.5),
        (r"計画", 1.5), (r"戦略", 2.0), (r"アーキテクチャ", 2.0),
        (r"複数.{0,5}観点", 2.0), (r"トレードオフ", 2.0), (r"最適", 1.5),
        (r"リスク", 1.5), (r"レビュー", 1.5), (r"どのように", 1.0),
        # 英語
        (r"\banalyze\b", 1.5), (r"\bcompare\b", 1.5), (r"\bdesign\b", 1.5),
        (r"\barchitecture\b", 2.0), (r"\btradeoff\b", 2.0), (r"\bstrategy\b", 2.0),
    ],
    ModelRole.LIGHTWEIGHT: [
        (r"要約", 2.0), (r"まとめ", 1.5), (r"翻訳", 2.0), (r"変換", 1.5),
        (r"分類", 1.5), (r"整理", 1.5), (r"リスト", 1.0), (r"抽出", 1.5),
        (r"短く", 1.5), (r"一言", 2.0), (r"箇条書き", 1.5),
        # 英語
        (r"\bsummarize\b", 2.0), (r"\btranslate\b", 2.0), (r"\bclassify\b", 1.5),
        (r"\bextract\b", 1.5), (r"\blist\b", 0.8),
    ],
    ModelRole.ULTRALIGHT: [
        (r"はい|いいえ|yes|no", 2.0),
        (r"何文字", 2.0), (r"文字数", 2.0), (r"何行", 2.0),
        (r"日付", 1.5), (r"時刻", 1.5), (r"今日", 1.5),
        (r"単純", 2.0), (r"簡単", 1.5), (r"すぐに", 1.5),
    ],
}


@dataclass
class RoutingResult:
    """ルーティング結果"""
    role: ModelRole
    model: ModelConfig
    scores: dict[ModelRole, float]   # 各 role のスコア
    method: str                       # "keyword" / "classifier" / "llm"
    confidence: float                 # 0.0 - 1.0
    reasoning: str = ""


def classify_task(
    prompt: str,
    *,
    method: str = "keyword",       # "keyword" | "llm"
    llm_client: Optional[OpenAI] = None,
    llm_model: Optional[str] = None,
    history_summary: str = "",     # 会話履歴の要約（追加コンテキスト）
) -> RoutingResult:
    """
    タスクを分類して適切なモデルロールを返す。

    Args:
        prompt: ユーザーの入力テキスト
        method: 分類方法 ("keyword" が推奨、高速)
        llm_client: LLM判定を使う場合に必要
        llm_model: LLM判定用モデル（軽量モデルを推奨）
        history_summary: 会話コンテキスト

    Returns:
        RoutingResult
    """
    text = (prompt + " " + history_summary).lower()

    if method == "llm" and llm_client is not None:
        return _classify_by_llm(text, llm_client, llm_model or "gemma2:9b")

    return _classify_by_keywords(text)


def _classify_by_keywords(text: str) -> RoutingResult:
    """キーワードスコアリングで分類"""
    scores: dict[ModelRole, float] = {role: 0.0 for role in ModelRole}

    for role, rules in _KEYWORD_RULES.items():
        for pattern, weight in rules:
            if re.search(pattern, text, re.IGNORECASE):
                scores[role] += weight

    # 最高スコアのロールを選択
    best_role = max(scores, key=lambda r: scores[r])
    best_score = scores[best_role]

    # スコアが0の場合はデフォルトで REASONING
    if best_score == 0.0:
        best_role = ModelRole.REASONING
        confidence = 0.3
    else:
        # 2位とのスコア差でconfidenceを計算
        sorted_scores = sorted(scores.values(), reverse=True)
        gap = sorted_scores[0] - sorted_scores[1] if len(sorted_scores) > 1 else sorted_scores[0]
        confidence = min(1.0, 0.4 + gap * 0.1)

    model = _select_model(best_role)
    return RoutingResult(
        role=best_role,
        model=model,
        scores=scores,
        method="keyword",
        confidence=confidence,
        reasoning=f"top_score={best_score:.1f}",
    )


def _classify_by_llm(text: str, client: OpenAI, model: str) -> RoutingResult:
    """LLMに分類させる（高精度だが遅い）"""
    role_descriptions = "\n".join(
        f"- {role.value}: {[m.description for m in models][0]}"
        for role, models in DEFAULT_MODELS.items()
    )
    prompt = (
        f"以下のタスクを最も適切なカテゴリに分類してください。\n\n"
        f"## カテゴリ\n{role_descriptions}\n\n"
        f"## タスク\n{text[:500]}\n\n"
        f"## 回答形式\nROLE: [カテゴリ名]\nREASON: [理由を1文で]"
    )
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=100,
        )
        content = response.choices[0].message.content or ""
        role_str, reason = "", ""
        for line in content.splitlines():
            if line.upper().startswith("ROLE:"):
                role_str = line[5:].strip().lower()
            elif line.upper().startswith("REASON:"):
                reason = line[7:].strip()

        # role_str を ModelRole にマッピング
        role_map = {r.value: r for r in ModelRole}
        best_role = role_map.get(role_str, ModelRole.REASONING)
        model_cfg = _select_model(best_role)
        # keyword でもスコア計算しておく
        kw_result = _classify_by_keywords(text)
        return RoutingResult(
            role=best_role,
            model=model_cfg,
            scores=kw_result.scores,
            method="llm",
            confidence=0.85,
            reasoning=reason,
        )
    except Exception as e:
        # LLM失敗時はキーワードにフォールバック
        result = _classify_by_keywords(text)
        result.reasoning += f" [llm_fallback: {e}]"
        return result


def _select_model(role: ModelRole) -> ModelConfig:
    """ロールから最優先モデルを選択"""
    candidates = DEFAULT_MODELS.get(role, [])
    if not candidates:
        # フォールバック: REASONING
        candidates = DEFAULT_MODELS.get(ModelRole.REASONING, [])
    # priority 昇順で最初のものを返す
    return sorted(candidates, key=lambda m: m.priority)[0]


def create_routed_client(result: RoutingResult) -> tuple[OpenAI, str]:
    """RoutingResult からOpenAIクライアントを生成"""
    cfg = result.model
    client = OpenAI(base_url=cfg.base_url, api_key=cfg.api_key)
    return client, cfg.name


# ---------------------------------------------------------------------------
# ヘルスチェック
# ---------------------------------------------------------------------------

def check_model_health(model: ModelConfig, timeout: float = 3.0) -> bool:
    """モデルエンドポイントが生きているか確認"""
    try:
        client = OpenAI(base_url=model.base_url, api_key=model.api_key)
        client.models.list()  # 簡単なAPIコール
        return True
    except Exception:
        return False


def get_available_models(roles: Optional[list[ModelRole]] = None) -> dict[ModelRole, ModelConfig]:
    """ヘルスチェックして利用可能なモデルを返す"""
    result = {}
    target_roles = roles or list(ModelRole)
    for role in target_roles:
        for model in sorted(DEFAULT_MODELS.get(role, []), key=lambda m: m.priority):
            if check_model_health(model):
                result[role] = model
                break
    return result


# ---------------------------------------------------------------------------
# Router クラス (統合インターフェース)
# ---------------------------------------------------------------------------

@dataclass
class RouterStats:
    """ルーティング統計"""
    total_requests: int = 0
    routing_counts: dict = field(default_factory=dict)
    latency_sum_ms: float = 0.0
    errors: int = 0


class ModelRouter:
    """
    モデルルーター: タスク分類 → モデル選択 → クライアント生成

    使い方:
        router = ModelRouter()
        client, model_name = router.route("Pythonのバブルソートを実装して")
        # → CodeGemma の client が返る
    """

    def __init__(
        self,
        models: Optional[dict[ModelRole, list[ModelConfig]]] = None,
        method: str = "keyword",
        fallback_role: ModelRole = ModelRole.REASONING,
        verbose: bool = False,
    ):
        self.models = models or DEFAULT_MODELS
        self.method = method
        self.fallback_role = fallback_role
        self.verbose = verbose
        self.stats = RouterStats()
        self._health_cache: dict[str, tuple[bool, float]] = {}  # name -> (ok, ts)
        self._health_ttl = 60.0  # 60秒キャッシュ

    def route(
        self,
        prompt: str,
        *,
        history_summary: str = "",
        llm_client: Optional[OpenAI] = None,
        llm_model: Optional[str] = None,
    ) -> tuple[OpenAI, str, RoutingResult]:
        """
        プロンプトを分類してクライアントとモデル名を返す。

        Returns:
            (OpenAI client, model_name, RoutingResult)
        """
        t0 = time.perf_counter()
        self.stats.total_requests += 1

        try:
            result = classify_task(
                prompt,
                method=self.method,
                llm_client=llm_client,
                llm_model=llm_model,
                history_summary=history_summary,
            )

            # ヘルスチェック (キャッシュ付き)
            model = self._pick_healthy(result.role)
            result.model = model

            self.stats.routing_counts[result.role.value] = (
                self.stats.routing_counts.get(result.role.value, 0) + 1
            )

            if self.verbose:
                score_str = ", ".join(
                    f"{r.value}:{s:.1f}" for r, s in sorted(
                        result.scores.items(), key=lambda x: x[1], reverse=True
                    ) if s > 0
                )
                print(f"[Router] {result.role.value} <- {model.name} "
                      f"(conf={result.confidence:.2f}, {score_str})")

            client = OpenAI(base_url=model.base_url, api_key=model.api_key)
            return client, model.name, result

        except Exception as e:
            self.stats.errors += 1
            # フォールバック
            fallback = _select_model(self.fallback_role)
            client = OpenAI(base_url=fallback.base_url, api_key=fallback.api_key)
            dummy_scores = {r: 0.0 for r in ModelRole}
            result = RoutingResult(
                role=self.fallback_role,
                model=fallback,
                scores=dummy_scores,
                method="fallback",
                confidence=0.0,
                reasoning=f"error: {e}",
            )
            return client, fallback.name, result

        finally:
            elapsed = (time.perf_counter() - t0) * 1000
            self.stats.latency_sum_ms += elapsed

    def _pick_healthy(self, role: ModelRole) -> ModelConfig:
        """指定ロールの候補からヘルスチェック済みモデルを選ぶ"""
        candidates = sorted(self.models.get(role, []), key=lambda m: m.priority)
        for model in candidates:
            if self._is_healthy(model):
                return model
        # 全滅: フォールバックロールから試す
        fallback_candidates = sorted(
            self.models.get(self.fallback_role, []), key=lambda m: m.priority
        )
        if fallback_candidates:
            return fallback_candidates[0]
        raise RuntimeError(f"No healthy model found for role: {role}")

    def _is_healthy(self, model: ModelConfig) -> bool:
        """キャッシュ付きヘルスチェック"""
        now = time.time()
        cached = self._health_cache.get(model.name)
        if cached:
            ok, ts = cached
            if now - ts < self._health_ttl:
                return ok

        ok = check_model_health(model)
        self._health_cache[model.name] = (ok, now)
        return ok

    def get_stats(self) -> dict:
        total = self.stats.total_requests or 1
        return {
            "total_requests": self.stats.total_requests,
            "routing_counts": self.stats.routing_counts,
            "avg_latency_ms": round(self.stats.latency_sum_ms / total, 2),
            "errors": self.stats.errors,
        }

    def print_stats(self):
        s = self.get_stats()
        print("=== Router Stats ===")
        print(f"Total: {s['total_requests']}, Errors: {s['errors']}")
        print(f"Avg routing latency: {s['avg_latency_ms']} ms")
        print("Routing distribution:")
        for role, count in sorted(s["routing_counts"].items(), key=lambda x: x[1], reverse=True):
            pct = count / max(s["total_requests"], 1) * 100
            print(f"  {role}: {count} ({pct:.1f}%)")


# ---------------------------------------------------------------------------
# クイックテスト用
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    tests = [
        ("Pythonでバブルソートを実装してください", ModelRole.CODING),
        ("衛星の熱制御設計について説明して", ModelRole.SPACE),
        ("このアーキテクチャのトレードオフを分析して", ModelRole.REASONING),
        ("この文章を3行で要約して", ModelRole.LIGHTWEIGHT),
        ("今日の日付は？", ModelRole.ULTRALIGHT),
        ("RustでHTTPサーバーを書いて", ModelRole.CODING),
        ("JERGの電気試験要求について", ModelRole.SPACE),
    ]

    router = ModelRouter(verbose=True)
    print("=== Routing Test (keyword method) ===")
    correct = 0
    for prompt, expected in tests:
        result = classify_task(prompt)
        ok = "OK" if result.role == expected else "NG"
        if result.role == expected:
            correct += 1
        print(f"[{ok}] {prompt[:40]!r}")
        print(f"     -> {result.role.value} (expected: {expected.value}, conf={result.confidence:.2f})")
    print(f"\nAccuracy: {correct}/{len(tests)} ({correct/len(tests)*100:.0f}%)")
