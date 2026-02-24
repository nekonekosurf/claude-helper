"""ハイブリッド検索 - 4手法を統合して最適な検索結果を返す

1. BM25キーワード検索（基本）
2. 同義語展開 + BM25
3. ベクトル検索（意味検索、利用可能な場合）
4. 要約インデックス検索（利用可能な場合）
5. LLMクエリ拡張 + BM25

全ての結果をスコア統合して、重複除去して返す。
"""

from src.searcher import search as bm25_search
from src.synonym import expand_with_synonyms


def hybrid_search(
    query: str,
    top_k: int = 10,
    doc_filter: str | None = None,
    client=None,
    model: str | None = None,
    use_llm_expansion: bool = True,
) -> list[dict]:
    """4手法を全て使ってハイブリッド検索を実行する

    Args:
        query: ユーザーの検索クエリ
        top_k: 最終的に返す件数
        doc_filter: 文書番号フィルタ（部分一致）
        client: LLMクライアント（クエリ拡張用）
        model: LLMモデル名
        use_llm_expansion: LLMクエリ拡張を使うか

    Returns:
        スコア統合・重複除去済みの検索結果リスト
    """
    all_results = {}  # chunk_id → result dict (最高スコアを保持)
    methods_used = []

    # --- 1. BM25キーワード検索（基本）---
    bm25_results = bm25_search(query, top_k=top_k, doc_filter=doc_filter)
    _merge_results(all_results, bm25_results, weight=1.0, method="bm25")
    methods_used.append("bm25")

    # --- 2. 同義語展開 + BM25 ---
    syn_queries = expand_with_synonyms(query)
    for sq in syn_queries[1:]:  # 元のクエリは既に検索済み
        syn_results = bm25_search(sq, top_k=top_k, doc_filter=doc_filter)
        _merge_results(all_results, syn_results, weight=0.8, method="synonym")
    if len(syn_queries) > 1:
        methods_used.append("synonym")

    # --- 3. ベクトル検索（利用可能な場合）---
    try:
        from src.vector_search import search as vec_search, is_available as vec_available
        if vec_available():
            vec_results = vec_search(query, top_k=top_k, doc_filter=doc_filter)
            _merge_results(all_results, vec_results, weight=0.9, method="vector")
            methods_used.append("vector")
    except (ImportError, FileNotFoundError):
        pass

    # --- 4. 要約インデックス検索（利用可能な場合）---
    try:
        from src.chunk_summarizer import search as summary_search, is_available as sum_available
        if sum_available():
            sum_results = summary_search(query, top_k=top_k, doc_filter=doc_filter)
            _merge_results(all_results, sum_results, weight=0.7, method="summary")
            methods_used.append("summary")
    except (ImportError, FileNotFoundError):
        pass

    # --- 5. LLMクエリ拡張 + BM25 ---
    if use_llm_expansion and client and model:
        try:
            from src.query_expander import expand_query
            expanded = expand_query(client, model, query)
            for eq in expanded[1:]:  # 元のクエリはスキップ
                exp_results = bm25_search(eq, top_k=top_k, doc_filter=doc_filter)
                _merge_results(all_results, exp_results, weight=0.6, method="llm_expand")
            if len(expanded) > 1:
                methods_used.append("llm_expand")
        except Exception:
            pass

    # --- スコア順にソートして返す ---
    sorted_results = sorted(all_results.values(), key=lambda x: x["combined_score"], reverse=True)

    # 上位N件
    final = sorted_results[:top_k]

    # メタ情報を追加
    for r in final:
        r["score"] = r.pop("combined_score")
        r["methods"] = r.pop("matched_methods")

    return final, methods_used


def _merge_results(
    all_results: dict,
    new_results: list[dict],
    weight: float,
    method: str,
):
    """検索結果をマージ。同じチャンクは最高スコアを保持"""
    for r in new_results:
        chunk_id = r["chunk_id"]
        weighted_score = r["score"] * weight

        if chunk_id in all_results:
            existing = all_results[chunk_id]
            existing["combined_score"] = max(existing["combined_score"], weighted_score)
            if method not in existing["matched_methods"]:
                existing["matched_methods"].append(method)
                # 複数手法でヒットした場合ボーナス
                existing["combined_score"] *= 1.1
        else:
            all_results[chunk_id] = {
                "doc_id": r["doc_id"],
                "chunk_id": chunk_id,
                "filename": r["filename"],
                "text": r["text"],
                "combined_score": weighted_score,
                "matched_methods": [method],
            }
