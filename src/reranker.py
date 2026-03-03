"""
reranker.py - Cross-encoder リランキング

初回の検索結果（BM25 + ベクトル検索）を
より精密なモデルでリランキングして精度を向上させる。

実装する手法:
  1. LLMベースリランク: クエリと各結果のペアをLLMで評価
     - 最も精度が高いが遅い（本番は結果上位10件のみに適用）
  2. Cross-encoder風スコアリング: ルールベースの再評価
     - BM25とベクトルスコアを組み合わせたRRF
  3. Reciprocal Rank Fusion (RRF): 複数のランキングを統合
     - スコールスケール非依存、シンプルで強力

RRF式: score(d) = Σ 1/(k + rank(d))
  k=60 がデフォルト（論文で提案された定数）
"""

from __future__ import annotations

import json
from typing import Any


# =========================================================
# Reciprocal Rank Fusion (RRF)
# =========================================================

def reciprocal_rank_fusion(
    rankings: list[list[str]],
    k: int = 60,
) -> list[tuple[str, float]]:
    """
    複数のランキングリストをRRFで統合する。

    Args:
        rankings: ランキングリストのリスト
                  例: [["chunk_1", "chunk_3", ...], ["chunk_2", "chunk_1", ...]]
        k: RRFの定数（デフォルト60、論文推奨値）

    Returns:
        [(doc_id, rrf_score), ...] スコア降順

    使用例:
        bm25_ranking = ["doc_A", "doc_B", "doc_C"]
        vector_ranking = ["doc_B", "doc_A", "doc_D"]
        fused = reciprocal_rank_fusion([bm25_ranking, vector_ranking])
        # → [("doc_A", 0.032), ("doc_B", 0.031), ("doc_C", 0.016), ("doc_D", 0.016)]
    """
    scores: dict[str, float] = {}

    for ranking in rankings:
        for rank, doc_id in enumerate(ranking, start=1):
            rrf_score = 1.0 / (k + rank)
            scores[doc_id] = scores.get(doc_id, 0.0) + rrf_score

    sorted_results = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return sorted_results


def rrf_from_search_results(
    result_sets: list[list[dict]],
    k: int = 60,
    id_key: str = "chunk_id",
) -> list[dict]:
    """
    検索結果の辞書リストのリストにRRFを適用する。

    Args:
        result_sets: 各検索手法の結果リスト
                     例: [[{"chunk_id": "c1", "score": 0.9, ...}, ...], [...]]
        k: RRF定数
        id_key: ドキュメントIDのキー名

    Returns:
        RRFスコア付きの結果リスト（重複除去済み）
    """
    # 各結果セットのランキングを抽出
    rankings = [[r[id_key] for r in results] for results in result_sets]

    # 全結果を辞書化（chunk_id → dict）
    all_docs: dict[str, dict] = {}
    for results in result_sets:
        for r in results:
            doc_id = r[id_key]
            if doc_id not in all_docs:
                all_docs[doc_id] = dict(r)

    # RRFスコアを計算
    rrf_scores = dict(reciprocal_rank_fusion(rankings, k=k))

    # スコアを付与して返す
    output = []
    for doc_id, rrf_score in sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True):
        if doc_id in all_docs:
            doc = dict(all_docs[doc_id])
            doc["rrf_score"] = rrf_score
            doc["original_score"] = doc.get("score", 0.0)
            doc["score"] = rrf_score  # メインスコアをRRFに更新
            output.append(doc)

    return output


# =========================================================
# LLMベースリランキング
# =========================================================

RERANK_PROMPT = """\
あなたは技術文書検索の専門家です。
以下のユーザーの質問に対して、各検索結果がどれだけ関連しているか評価してください。

【ユーザーの質問】
{query}

【評価対象の文書（{n}件）】
{documents}

【評価基準】
- 5: 質問に完全に答えている（必要な情報が全て含まれる）
- 4: 質問に主要な情報が含まれている
- 3: 部分的に関連している
- 2: 間接的に関連している
- 1: ほとんど関連していない

各文書に1〜5のスコアを付けて、JSON形式で返してください（説明文なし）:
{"scores": [5, 3, 1, ...]}  ← 入力と同じ順序で
"""


def rerank_with_llm(
    client: Any,
    model: str,
    query: str,
    results: list[dict],
    top_k: int = 10,
    text_key: str = "text",
    max_text_chars: int = 300,
) -> list[dict]:
    """
    LLMを使って検索結果をリランキングする。

    初回検索の上位結果に対してLLMでペアワイズ評価を行い、
    より関連性の高い順に並び替える。

    Args:
        client: LLMクライアント
        model: モデル名
        query: ユーザーの検索クエリ
        results: 検索結果のリスト（辞書形式）
        top_k: リランキングする上位件数（コスト制御）
        text_key: テキストのキー名
        max_text_chars: LLMに渡す1文書あたりの最大文字数

    Returns:
        リランキングされた結果リスト（LLMスコア付き）
    """
    from src.llm_client import chat

    # 上位top_k件のみリランキング（コスト・速度制御）
    candidates = results[:top_k]
    if not candidates:
        return results

    # 文書テキストを整形
    doc_texts = []
    for i, r in enumerate(candidates):
        text = r.get(text_key, "")[:max_text_chars]
        source = r.get("filename", r.get("doc_id", f"文書{i+1}"))
        doc_texts.append(f"[{i+1}] 出典: {source}\n{text}")

    documents_str = "\n\n".join(doc_texts)
    prompt = RERANK_PROMPT.format(
        query=query,
        n=len(candidates),
        documents=documents_str,
    )
    messages = [{"role": "user", "content": prompt}]

    try:
        response = chat(client, model, messages, tools=None)
        content = response.content or ""

        if "```" in content:
            start = content.index("```") + 3
            if content[start:start+4] == "json":
                start += 4
            end = content.index("```", start)
            content = content[start:end].strip()

        data = json.loads(content)
        llm_scores = data.get("scores", [])

        if len(llm_scores) == len(candidates):
            # LLMスコアを付与
            for i, r in enumerate(candidates):
                r["llm_relevance_score"] = llm_scores[i]
                # 元のスコアとLLMスコアを組み合わせ
                original_score = r.get("score", 0.0)
                r["score"] = original_score * 0.3 + llm_scores[i] / 5.0 * 0.7

            # LLMスコア順にソート
            candidates.sort(key=lambda x: x["score"], reverse=True)

    except Exception as e:
        print(f"  LLMリランキング失敗: {e}")
        # 失敗時は元の順序を維持

    # リランキング対象外の残り結果を後ろに追加
    remaining = results[top_k:]
    return candidates + remaining


# =========================================================
# Colbert風スコアリング（簡易版）
# =========================================================

def colbert_style_score(query: str, document: str, tokenizer=None) -> float:
    """
    ColBERTの考え方（token-level late interaction）を
    TF-IDF的な手法で近似した簡易スコアラー。

    本物のColBERTはBERTエンコーダが必要だが、
    この実装は文字n-gramマッチングで近似する。

    実際の本番環境では:
    - sentence-transformers の CrossEncoder を使う
    - または jina-reranker-v2-base-multilingual (日本語対応) を使う
    """
    # クエリのbigramセット
    def bigrams(text: str) -> set:
        text = text.lower()
        return {text[i:i+2] for i in range(len(text)-1) if text[i:i+2].strip()}

    query_bigrams = bigrams(query)
    doc_bigrams = bigrams(document[:500])  # 先頭500文字のみ

    if not query_bigrams:
        return 0.0

    # マッチング率（クエリbigramのうち文書にも含まれる割合）
    matched = query_bigrams & doc_bigrams
    score = len(matched) / len(query_bigrams)

    return score


def rerank_with_colbert_style(
    query: str,
    results: list[dict],
    top_k: int = 20,
    text_key: str = "text",
    alpha: float = 0.5,
) -> list[dict]:
    """
    Colbert風スコアで検索結果をリランキングする（LLM不要）。

    Args:
        query: クエリ
        results: 検索結果
        top_k: リランキング対象数
        text_key: テキストキー
        alpha: ColBERTスコアの重み（1-alpha が元スコアの重み）

    Returns:
        リランキングされた結果
    """
    candidates = results[:top_k]

    for r in candidates:
        text = r.get(text_key, "")
        colbert_s = colbert_style_score(query, text)
        original_s = r.get("score", 0.0)
        # 正規化済みスコアを想定（0-1範囲）
        r["colbert_score"] = colbert_s
        r["score"] = original_s * (1 - alpha) + colbert_s * alpha

    candidates.sort(key=lambda x: x["score"], reverse=True)
    return candidates + results[top_k:]


# =========================================================
# 統合リランキングパイプライン
# =========================================================

def full_rerank_pipeline(
    query: str,
    result_sets: list[list[dict]],
    client: Any = None,
    model: str = "",
    use_rrf: bool = True,
    use_llm_rerank: bool = True,
    use_colbert: bool = True,
    rrf_k: int = 60,
    llm_top_k: int = 10,
    final_top_k: int = 5,
    id_key: str = "chunk_id",
    text_key: str = "text",
) -> list[dict]:
    """
    複数の検索結果をRRF → Colbert → LLMリランキングで統合する。

    パイプライン:
    1. 複数の検索結果セットをRRFで統合
    2. Colbert風スコアで再評価
    3. LLMで上位10件を最終評価（use_llm_rerank=Trueの場合）

    Args:
        query: ユーザーのクエリ
        result_sets: 各検索手法の結果リスト群
        client: LLMクライアント
        model: モデル名
        use_rrf: RRFを使うか
        use_llm_rerank: LLMリランキングを使うか
        use_colbert: Colbert風スコアを使うか
        rrf_k: RRF定数
        llm_top_k: LLMリランキングする件数
        final_top_k: 最終的に返す件数
        id_key: ドキュメントIDキー
        text_key: テキストキー

    Returns:
        最終ランキング結果
    """
    # Step 1: RRF統合
    if use_rrf and len(result_sets) > 1:
        results = rrf_from_search_results(result_sets, k=rrf_k, id_key=id_key)
    elif result_sets:
        results = result_sets[0]
    else:
        return []

    # Step 2: Colbert風スコア
    if use_colbert:
        results = rerank_with_colbert_style(query, results, text_key=text_key)

    # Step 3: LLMリランキング（上位のみ）
    if use_llm_rerank and client and model:
        results = rerank_with_llm(
            client=client,
            model=model,
            query=query,
            results=results,
            top_k=llm_top_k,
            text_key=text_key,
        )

    return results[:final_top_k]


# =========================================================
# スタンドアロン実行テスト
# =========================================================

if __name__ == "__main__":
    print("=== RRFテスト ===")

    # 2つの検索手法の結果（ランキング）
    bm25_results = [
        {"chunk_id": "c1", "score": 0.9, "text": "熱制御システムの温度要件"},
        {"chunk_id": "c2", "score": 0.7, "text": "DTRの定義と適用"},
        {"chunk_id": "c3", "score": 0.5, "text": "試験手順の概要"},
    ]
    vector_results = [
        {"chunk_id": "c2", "score": 0.85, "text": "DTRの定義と適用"},
        {"chunk_id": "c4", "score": 0.75, "text": "温度マージン計算方法"},
        {"chunk_id": "c1", "score": 0.6, "text": "熱制御システムの温度要件"},
    ]

    fused = rrf_from_search_results([bm25_results, vector_results], k=60)
    print("RRF統合結果:")
    for r in fused:
        print(f"  {r['chunk_id']}: rrf={r['rrf_score']:.4f}")

    print("\n=== Colbert風スコアテスト ===")
    query = "熱制御 温度 DTR"
    results = bm25_results.copy()
    reranked = rerank_with_colbert_style(query, results)
    print("Colbert風リランク後:")
    for r in reranked:
        print(f"  {r['chunk_id']}: score={r['score']:.3f}, colbert={r['colbert_score']:.3f}")

    print("\n=== RRF単体テスト ===")
    rankings = [
        ["doc_A", "doc_B", "doc_C"],
        ["doc_B", "doc_A", "doc_D"],
        ["doc_A", "doc_D", "doc_E"],
    ]
    fused_simple = reciprocal_rank_fusion(rankings, k=60)
    print("RRF結果:")
    for doc_id, score in fused_simple:
        print(f"  {doc_id}: {score:.4f}")
