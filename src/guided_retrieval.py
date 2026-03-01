"""
2段階ガイド付き検索（Guided Retrieval）

Stage 1: 質問のドメインを特定し、参照すべき文書を絞り込む
Stage 2: 絞り込んだ文書内でハイブリッド検索を実行

これにより：
- 検索精度が向上（無関係な文書からのノイズ除去）
- コンテキスト消費を削減（少ないチャンクで高精度）
- 専門知識による検索ガイダンス
"""

import yaml
import re
from pathlib import Path
from typing import Optional

KNOWLEDGE_DIR = Path(__file__).parent.parent / "knowledge"


def load_domain_map() -> dict:
    """domain_map.yamlを読み込む"""
    path = KNOWLEDGE_DIR / "domain_map.yaml"
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data.get("domains", {})


def load_glossary() -> dict:
    """glossary.yamlを読み込む"""
    path = KNOWLEDGE_DIR / "glossary.yaml"
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data.get("terms", {})


def load_decision_trees() -> dict:
    """decision_trees.yamlを読み込む"""
    path = KNOWLEDGE_DIR / "decision_trees.yaml"
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data.get("trees", {})


def _is_subsumed_by_any(kw: str, all_keywords: list[str], text: str) -> bool:
    """
    kw がテキスト中で他のより長いキーワードの一部としてのみ出現するか判定する。

    テキスト中に kw を含むより長いキーワードが存在する場合、
    kw の一致はその長いキーワードにより説明される（spurious match）。

    例: kw="制御", text="熱制御", other_kw="熱制御" が any_keywords に含まれる
        → "熱制御" が text に存在し、かつ "制御" ⊂ "熱制御" → subsumed=True
    """
    for other_kw in all_keywords:
        if other_kw != kw and kw in other_kw and other_kw in text:
            return True
    return False


def _keyword_coverage_score(kw: str, query: str) -> float:
    """
    キーワードとクエリの一致品質を返す。

    - キーワードがクエリ全体と完全一致: 1.0 (最高)
    - クエリの大部分をキーワードが占める (len(kw)/len(query) が高い): 高スコア
    - キーワードがクエリのごく一部にしか含まれない: 低スコア

    日本語テキストに単語境界がないため、カバレッジ比率で品質を測る。
    """
    if not query:
        return 0.0
    if kw == query:
        return 1.0
    # Strip Japanese particles and punctuation from query for length comparison
    # Approximate: use character count ratio
    ratio = len(kw) / max(len(query), 1)
    return min(ratio, 1.0)


def detect_domain(query: str) -> list[dict]:
    """
    クエリからドメインを特定する。
    複数ドメインにマッチする可能性があるため、スコア付きリストを返す。

    スコアリングロジック:
    - キーワード完全語マッチ（独立した語として出現）: base_score += 4.0
    - キーワード部分一致（他語に包含されている可能性あり）: base_score += 1.0
      ただし同ドメインの長いキーワードに包含される場合はスキップ（二重加点防止）
    - 用語集（glossary）でドメインが確定したもの: +3
    - 専門性（specificity, 1-5）による補正: score *= (1 + (specificity - 1) * 0.3)
      specificity=1 → x1.0, specificity=5 → x2.2
      広い汎用ドメイン（systems, management 等）はスコアが大幅に下がる

    Returns: [{"domain": "thermal", "score": 3.0, "primary_docs": [...], ...}, ...]
    """
    domain_map = load_domain_map()
    glossary = load_glossary()

    # 全ドメインのキーワードを1つのリストにまとめる（包含チェック用）
    all_keywords: list[str] = []
    for domain_info in domain_map.values():
        all_keywords.extend(domain_info.get("keywords", []))

    # キーワード → そのキーワードを持つドメインの最高 specificity を記録
    # 同じキーワードが複数ドメインにある場合、より専門的なドメインを優先するために使用
    keyword_max_specificity: dict[str, int] = {}
    for domain_info in domain_map.values():
        sp = domain_info.get("specificity", 3)
        for kw in domain_info.get("keywords", []):
            if kw not in keyword_max_specificity or sp > keyword_max_specificity[kw]:
                keyword_max_specificity[kw] = sp

    # Step 1: Normalize query using glossary, collect glossary-matched domains
    normalized_query = query
    matched_domains_from_glossary = set()
    glossary_matched_terms: dict[str, str] = {}  # term -> domain

    for term, info in glossary.items():
        if term in query:
            domain = info.get("domain")
            if domain and domain != "null":
                matched_domains_from_glossary.add(domain)
                glossary_matched_terms[term] = domain
            # Expand normalized_query with formal terms for keyword matching
            if "formal" in info:
                normalized_query += " " + " ".join(info["formal"])

    # Step 2: Score each domain
    results = []
    for domain_key, domain_info in domain_map.items():
        base_score = 0.0
        keywords = domain_info.get("keywords", [])
        specificity = domain_info.get("specificity", 3)  # default 3 if not set

        for kw in keywords:
            # Check match in original query or glossary-expanded normalized query
            matched_in_original = kw in query
            matched_in_normalized = (not matched_in_original) and (kw in normalized_query)

            if not (matched_in_original or matched_in_normalized):
                continue

            # Glossary-expanded matches get reduced base weight because the keyword
            # was not literally in the user's query — it was inferred via synonym expansion.
            # We cap the score addition for normalized-only matches to avoid spurious
            # over-matching (e.g., "試験" glossary expanding to "環境試験", "認定試験", etc.)
            if matched_in_normalized and not matched_in_original:
                # Only give partial credit for glossary-inferred matches
                base_score += 0.5
                continue

            # From here: matched_in_original is True (keyword literally in query)

            # Check if this keyword match is "explained" by a longer keyword from any domain.
            # Example: kw="制御" matches in query="熱制御" because "熱制御" is there.
            # If "熱制御" exists as a keyword of any domain and appears in query,
            # then kw="制御" is a spurious sub-match → give only minimal credit.
            subsumed = _is_subsumed_by_any(kw, all_keywords, query)

            if subsumed:
                # Spurious sub-match: another (longer, more specific) keyword explains it
                base_score += 0.1
            else:
                # Check if a more specialized domain also has this exact keyword.
                # If this domain's specificity is lower than the max specificity for this kw,
                # the match is "claimed" by a more specialized domain → give reduced credit.
                kw_max_sp = keyword_max_specificity.get(kw, specificity)
                is_outspecialized = (kw_max_sp > specificity)

                # Genuine match: compute coverage quality
                coverage = _keyword_coverage_score(kw, query)
                # High coverage (kw takes up most of query) → strong match
                # Low coverage (kw is a tiny fragment of a long query) → weaker match
                if coverage >= 0.5:
                    if is_outspecialized:
                        # Another domain with higher specificity has this kw → halve credit
                        base_score += 2.0
                    else:
                        base_score += 4.0
                elif coverage >= 0.2:
                    base_score += 1.5 if is_outspecialized else 2.5
                else:
                    base_score += 0.8 if is_outspecialized else 1.5

        # Glossary domain match bonus (high confidence signal)
        if domain_key in matched_domains_from_glossary:
            base_score += 3.0

        if base_score <= 0:
            continue

        # Specificity multiplier: narrow/specialized domains get boosted more aggressively,
        # broad domains (systems, management) get relatively much less weight.
        # specificity 1 -> 1.0x, 2 -> 1.3x, 3 -> 1.6x, 4 -> 1.9x, 5 -> 2.2x
        specificity_multiplier = 1.0 + (specificity - 1) * 0.3
        final_score = base_score * specificity_multiplier

        results.append({
            "domain": domain_key,
            "name": domain_info.get("name", domain_key),
            "score": round(final_score, 2),
            "primary_docs": domain_info.get("primary_docs", []),
            "related_docs": domain_info.get("related_docs", []),
            "expert_note": domain_info.get("expert_note", ""),
        })

    # Sort by score descending
    results.sort(key=lambda x: x["score"], reverse=True)
    return results


def find_matching_procedure(query: str) -> Optional[dict]:
    """
    decision_trees.yamlから質問にマッチする手順を見つける
    """
    trees = load_decision_trees()

    for tree_key, tree_info in trees.items():
        patterns = tree_info.get("trigger_patterns", [])
        for pattern in patterns:
            try:
                if re.search(pattern, query):
                    return {
                        "tree": tree_key,
                        "description": tree_info.get("description", ""),
                        "steps": tree_info.get("steps", []),
                    }
            except re.error:
                continue

    return None


def load_procedure(procedure_name: str) -> Optional[dict]:
    """
    procedures/ディレクトリから手順書を読み込む
    """
    proc_dir = KNOWLEDGE_DIR / "procedures"
    if not proc_dir.exists():
        return None

    # Try exact filename match
    for ext in [".yaml", ".yml"]:
        path = proc_dir / f"{procedure_name}{ext}"
        if path.exists():
            with open(path, encoding="utf-8") as f:
                return yaml.safe_load(f)

    return None


def guided_search(query: str, top_k: int = 5, client=None, model=None) -> dict:
    """
    ガイド付き2段階検索のメインエントリポイント

    Returns:
        {
            "results": [...],          # 検索結果
            "domains": [...],          # 検出されたドメイン
            "procedure": {...} or None, # マッチした手順
            "expert_notes": [...],     # 専門家ノート
            "methods_used": [...],     # 使用された検索手法
            "doc_filter": "..." or None # 適用された文書フィルタ
        }
    """
    from src.hybrid_search import hybrid_search

    # Stage 1: Domain detection
    domains = detect_domain(query)
    procedure = find_matching_procedure(query)

    expert_notes = []
    doc_filter = None

    if domains:
        top_domain = domains[0]
        expert_notes.append(top_domain.get("expert_note", ""))

        # Collect document IDs for filtering
        target_docs = []
        # Use primary docs from top 2 domains (if scores are close)
        for d in domains[:2]:
            target_docs.extend(d.get("primary_docs", []))
            if d["score"] >= domains[0]["score"] * 0.7:
                target_docs.extend(d.get("related_docs", []))

        if target_docs:
            # Create doc_filter pattern (e.g., "JERG-2-210|JERG-2-211")
            doc_filter = "|".join(set(target_docs))

    # Stage 2: Focused hybrid search
    results, methods = hybrid_search(
        query=query,
        top_k=top_k,
        doc_filter=doc_filter,
        client=client,
        model=model,
    )

    # If no results with filter, fall back to unfiltered search
    if not results and doc_filter:
        results, methods = hybrid_search(
            query=query,
            top_k=top_k,
            doc_filter=None,
            client=client,
            model=model,
        )
        doc_filter = None  # Mark that filter was removed

    return {
        "results": results,
        "domains": domains[:3],  # Top 3 domains
        "procedure": procedure,
        "expert_notes": [n for n in expert_notes if n],
        "methods_used": methods,
        "doc_filter": doc_filter,
    }
