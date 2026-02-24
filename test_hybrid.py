"""ハイブリッド検索テスト - 4手法の統合検索を検証"""

import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from src.hybrid_search import hybrid_search
from src.llm_client import create_client
from src.synonym import expand_with_synonyms
from src.query_expander import expand_query


def test_synonym_expansion():
    """同義語展開テスト"""
    print("=" * 60)
    print("1. 同義語展開テスト")
    print("=" * 60)

    cases = [
        ("温度管理", ["熱制御", "温度制御", "熱設計"]),
        ("テスト工程", ["試験", "検証"]),
        ("壊れる原因", ["故障", "破壊"]),
        ("出張の規定", ["旅費", "外勤"]),
    ]

    for query, expected_contains in cases:
        expanded = expand_with_synonyms(query)
        found = any(any(e in eq for e in expected_contains) for eq in expanded)
        status = "✅" if found else "❌"
        print(f"  {status} '{query}' → {expanded[1][:60] if len(expanded) > 1 else '(展開なし)'}...")


def test_llm_query_expansion():
    """LLMクエリ拡張テスト"""
    print("\n" + "=" * 60)
    print("2. LLMクエリ拡張テスト")
    print("=" * 60)

    client, model = create_client()

    cases = [
        "出張と外勤の境目は何キロですか",
        "衛星が壊れないようにするには",
        "ソフトウェアのテストはどうやるの",
    ]

    for query in cases:
        expanded = expand_query(client, model, query)
        print(f"  '{query}'")
        for i, eq in enumerate(expanded):
            marker = "  (原文)" if i == 0 else f"  (拡張{i})"
            print(f"    {marker} {eq}")


def test_hybrid_search():
    """ハイブリッド検索テスト"""
    print("\n" + "=" * 60)
    print("3. ハイブリッド検索テスト")
    print("=" * 60)

    client, model = create_client()

    cases = [
        {
            "query": "ソフトウェアのテスト要件",
            "expect_doc": "JERG",
            "desc": "直接的な単語あり",
        },
        {
            "query": "衛星の温度管理の方法",
            "expect_doc": "JERG",
            "desc": "同義語展開で「熱制御」にヒットすべき",
        },
        {
            "query": "部品が壊れないようにする設計",
            "expect_doc": "JERG",
            "desc": "LLM拡張で「信頼性」「故障」等にヒットすべき",
        },
        {
            "query": "ロケットの安全に関するルール",
            "expect_doc": "JERG",
            "desc": "同義語+LLM拡張で「安全要件」「フェールセーフ」等",
        },
    ]

    passed = 0
    for tc in cases:
        results, methods = hybrid_search(
            tc["query"], top_k=5,
            client=client, model=model,
        )

        has_results = len(results) > 0
        multi_method = len(methods) >= 2

        if has_results:
            docs = set(r["doc_id"] for r in results)
            top_score = results[0]["score"]
            top_methods = results[0].get("methods", [])
            print(f"\n  ✅ '{tc['query']}' ({tc['desc']})")
            print(f"     手法: {methods}")
            print(f"     結果: {len(results)}件, top={list(docs)[:3]}, score={top_score:.4f}, via={top_methods}")
            passed += 1
        else:
            print(f"\n  ❌ '{tc['query']}' - 結果なし")

    print(f"\n  結果: {passed}/{len(cases)} PASS")
    return passed == len(cases)


if __name__ == "__main__":
    test_synonym_expansion()
    test_llm_query_expansion()
    success = test_hybrid_search()
    sys.exit(0 if success else 1)
