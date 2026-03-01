"""ハイブリッド検索 - 4手法を統合して最適な検索結果を返す

1. BM25キーワード検索（基本）
2. 同義語展開 + BM25
3. ベクトル検索（意味検索、利用可能な場合）
4. 要約インデックス検索（利用可能な場合）
5. LLMクエリ拡張 + BM25
6. 相互参照グラフによる関連文書補強（doc_filter指定時）

スコアの統合方針:
- 各手法のスコアは正規化（最大値で除算）してから weight を掛ける
- これにより BM25（スコア ~10-30）とベクトル（スコア 0-1）のスケール差を吸収
- 複数手法でヒットした場合は 10% ボーナス

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
    use_cross_reference: bool = True,
    cross_ref_depth: int = 1,
) -> tuple[list[dict], list[str]]:
    """4手法を全て使ってハイブリッド検索を実行する

    Args:
        query: ユーザーの検索クエリ
        top_k: 最終的に返す件数
        doc_filter: 文書番号フィルタ（部分一致）
        client: LLMクライアント（クエリ拡張用）
        model: LLMモデル名
        use_llm_expansion: LLMクエリ拡張を使うか
        use_cross_reference: 相互参照グラフによる関連文書検索を使うか
        cross_ref_depth: 相互参照を何ホップまで辿るか

    Returns:
        (スコア統合・重複除去済みの検索結果リスト, 使用した検索手法リスト)
    """
    all_results: dict[str, dict] = {}  # chunk_id → result dict (最高スコアを保持)
    methods_used = []

    # --- 1. BM25キーワード検索（基本）---
    bm25_results = bm25_search(query, top_k=top_k, doc_filter=doc_filter)
    _merge_results(all_results, bm25_results, weight=1.0, method="bm25", normalize=True)
    methods_used.append("bm25")

    # --- 2. 同義語展開 + BM25 ---
    syn_queries = expand_with_synonyms(query)
    for sq in syn_queries[1:]:  # 元のクエリは既に検索済み
        syn_results = bm25_search(sq, top_k=top_k, doc_filter=doc_filter)
        _merge_results(all_results, syn_results, weight=0.8, method="synonym", normalize=True)
    if len(syn_queries) > 1:
        methods_used.append("synonym")

    # --- 3. ベクトル検索（利用可能な場合）---
    # コサイン類似度（0-1）は既にスケール済みなので normalize=False
    try:
        from src.vector_search import search as vec_search, is_available as vec_available
        if vec_available():
            vec_results = vec_search(query, top_k=top_k, doc_filter=doc_filter)
            _merge_results(all_results, vec_results, weight=0.9, method="vector", normalize=False)
            methods_used.append("vector")
    except (ImportError, FileNotFoundError):
        pass

    # --- 4. 要約インデックス検索（利用可能な場合）---
    try:
        from src.chunk_summarizer import search as summary_search, is_available as sum_available
        if sum_available():
            sum_results = summary_search(query, top_k=top_k, doc_filter=doc_filter)
            _merge_results(all_results, sum_results, weight=0.7, method="summary", normalize=True)
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
                _merge_results(all_results, exp_results, weight=0.6, method="llm_expand", normalize=True)
            if len(expanded) > 1:
                methods_used.append("llm_expand")
        except Exception:
            pass

    # --- 6. 相互参照グラフによる関連文書補強 ---
    if use_cross_reference:
        try:
            related_docs = _get_cross_ref_docs(
                query=query,
                doc_filter=doc_filter,
                current_results=all_results,
                depth=cross_ref_depth,
            )
            if related_docs:
                # 関連文書に絞ってBM25検索を追加実行
                for rel_doc in related_docs[:5]:  # 最大5文書まで
                    xref_results = bm25_search(query, top_k=3, doc_filter=rel_doc)
                    _merge_results(all_results, xref_results, weight=0.5, method="cross_ref", normalize=True)
                methods_used.append("cross_ref")
        except Exception:
            pass

    # --- スコア順にソートして返す ---
    sorted_results = sorted(
        all_results.values(),
        key=lambda x: x["combined_score"],
        reverse=True,
    )

    # 上位N件
    final = sorted_results[:top_k]

    # メタ情報を追加
    for r in final:
        r["score"] = r.pop("combined_score")
        r["methods"] = r.pop("matched_methods")

    return final, methods_used


def _get_cross_ref_docs(
    query: str,
    doc_filter: str | None,
    current_results: dict,
    depth: int = 1,
) -> list[str]:
    """
    現在の検索結果から参照されている関連文書IDリストを返す。

    現在の検索結果に含まれる文書から、相互参照グラフを辿って
    まだ結果に含まれていない関連文書を見つける。
    """
    from src.cross_reference import load_graph, get_related_docs

    graph = load_graph()
    nodes = graph.get("nodes", {})

    # 現在の結果に含まれる文書を収集
    found_docs = set(r["doc_id"] for r in current_results.values())

    # doc_filterが指定されている場合はその文書から参照を辿る
    if doc_filter:
        # doc_filterに部分一致する文書ID
        seed_docs = [d for d in nodes if doc_filter in d]
    else:
        seed_docs = list(found_docs)

    if not seed_docs:
        return []

    # 関連文書を収集
    related = set()
    for doc_id in seed_docs:
        refs = get_related_docs(doc_id, direction="both", depth=depth)
        related.update(refs)

    # 既に結果にある文書は除外
    new_docs = related - found_docs

    return sorted(new_docs)


def _merge_results(
    all_results: dict,
    new_results: list[dict],
    weight: float,
    method: str,
    normalize: bool = True,
):
    """検索結果をマージ。同じチャンクは最高スコアを保持。

    Args:
        all_results: マージ先の辞書
        new_results: 追加する検索結果
        weight: このメソッドのスコアに掛ける重み（0-1）
        method: 手法名（重複判定に使用）
        normalize: True の場合、バッチ内の最大スコアで正規化して 0-1 に揃える
                   False の場合はスコアをそのまま使う（コサイン類似度など 0-1 が保証されているもの）
    """
    if not new_results:
        return

    # スコア正規化: BM25 のような生スコアを 0-1 に変換
    if normalize:
        max_score = max(r["score"] for r in new_results)
        if max_score <= 0:
            return
        scores = [r["score"] / max_score for r in new_results]
    else:
        scores = [r["score"] for r in new_results]

    for r, norm_score in zip(new_results, scores):
        chunk_id = r["chunk_id"]
        weighted_score = norm_score * weight

        if chunk_id in all_results:
            existing = all_results[chunk_id]
            if method not in existing["matched_methods"]:
                existing["matched_methods"].append(method)
                # 複数手法でヒットした場合は加算してボーナス
                existing["combined_score"] = existing["combined_score"] + weighted_score * 0.5
            else:
                # 同じ手法で再度ヒット（クエリ拡張等）は最大値を採用
                existing["combined_score"] = max(existing["combined_score"], weighted_score)
        else:
            all_results[chunk_id] = {
                "doc_id": r["doc_id"],
                "chunk_id": chunk_id,
                "filename": r["filename"],
                "text": r["text"],
                "combined_score": weighted_score,
                "matched_methods": [method],
            }
