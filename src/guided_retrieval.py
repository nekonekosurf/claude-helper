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


def detect_domain(query: str) -> list[dict]:
    """
    クエリからドメインを特定する。
    複数ドメインにマッチする可能性があるため、スコア付きリストを返す。

    Returns: [{"domain": "thermal", "score": 3, "primary_docs": [...], "related_docs": [...], "expert_note": "..."}, ...]
    """
    domain_map = load_domain_map()
    glossary = load_glossary()

    # Step 1: Normalize query using glossary
    normalized_query = query
    matched_domains_from_glossary = set()
    for term, info in glossary.items():
        if term in query:
            if "domain" in info:
                matched_domains_from_glossary.add(info["domain"])
            # Add formal terms for matching
            if "formal" in info:
                normalized_query += " " + " ".join(info["formal"])

    # Step 2: Score each domain by keyword matching
    results = []
    for domain_key, domain_info in domain_map.items():
        score = 0
        keywords = domain_info.get("keywords", [])

        for kw in keywords:
            if kw in normalized_query:
                score += 2  # Direct keyword match
            elif kw in query:
                score += 2

        # Bonus for glossary domain match
        if domain_key in matched_domains_from_glossary:
            score += 3

        if score > 0:
            results.append({
                "domain": domain_key,
                "name": domain_info.get("name", domain_key),
                "score": score,
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
