"""全Phase統合テスト - エージェントに質問を投げて動作確認"""

import sys
import os
import json
import traceback

sys.path.insert(0, os.path.dirname(__file__))

from src.agent import run_single
from src.meta_agent import process_teach
from src.llm_client import create_client
from src.searcher import search
from src.validator import run_validation
from src.knowledge import load_routing_rules, list_categories


TEST_CASES = [
    # --- Phase 1: 基本ツール ---
    {
        "id": 1,
        "category": "テキスト応答（ツールなし）",
        "question": "Pythonのデコレータについて簡単に教えてください",
        "check": lambda ans: ans and len(ans) > 20,
    },
    {
        "id": 2,
        "category": "read_file",
        "question": "/home/neko/projects/claude-helper/pyproject.toml を読んで内容を教えてください",
        "check": lambda ans: ans and "claude-helper" in ans.lower(),
    },
    {
        "id": 3,
        "category": "bash",
        "question": "ls -la /home/neko/projects/claude-helper/src/ を実行してください",
        "check": lambda ans: ans and "agent.py" in ans,
    },
    {
        "id": 4,
        "category": "write_file + read back",
        "question": "/tmp/test_full_output.txt に 'テスト成功' と書き込んでください",
        "check": lambda ans: os.path.exists("/tmp/test_full_output.txt"),
    },
    # --- Phase 2: JERG文書検索 ---
    {
        "id": 5,
        "category": "search_docs（ソフトウェア）",
        "question": "JERGのソフトウェア開発標準で、テスト工程について教えてください",
        "check": lambda ans: ans and ("テスト" in ans or "ソフトウェア" in ans),
    },
    {
        "id": 6,
        "category": "search_docs（熱設計）",
        "question": "宇宙機の熱制御設計について、JERG文書ではどのように規定されていますか",
        "check": lambda ans: ans and ("熱" in ans or "温度" in ans),
    },
    {
        "id": 7,
        "category": "search_docs（構造）",
        "question": "JERG文書で機械的な構造設計について教えてください",
        "check": lambda ans: ans and len(ans) > 50,
    },
    # --- Phase 4: glob/grep ---
    {
        "id": 8,
        "category": "glob",
        "question": "/home/neko/projects/claude-helper/src/ の中の全てのPythonファイルを探してください",
        "check": lambda ans: ans and "agent.py" in ans,
    },
    {
        "id": 9,
        "category": "grep",
        "question": "/home/neko/projects/claude-helper/src/ の中で 'def search' を含むファイルを探してください",
        "check": lambda ans: ans and "searcher" in ans.lower(),
    },
    # --- 複合タスク ---
    {
        "id": 10,
        "category": "複合（検索→分析）",
        "question": "JERG文書の中で「信頼性」に関する規定を検索して、どの文書に記載されているか教えてください",
        "check": lambda ans: ans and ("JERG" in ans),
    },
]


def run_direct_search_tests():
    """BM25検索エンジンの直接テスト"""
    print("\n" + "=" * 60)
    print("BM25 検索エンジン直接テスト")
    print("=" * 60)

    queries = [
        ("ソフトウェア テスト 要件", "ソフトウェア系の文書がヒットすべき"),
        ("熱制御 温度", "熱制御系の文書がヒットすべき"),
        ("構造 強度 設計", "構造設計系の文書がヒットすべき"),
        ("信頼性 品質保証", "信頼性・品質系の文書がヒットすべき"),
        ("電気 電源", "電気設計系の文書がヒットすべき"),
    ]

    for query, expected in queries:
        results = search(query, top_k=3)
        if results:
            docs = ", ".join(r["doc_id"] for r in results)
            print(f"  ✅ '{query}' → {docs}")
        else:
            print(f"  ❌ '{query}' → 結果なし（期待: {expected}）")


def run_teach_test():
    """/teach コマンドのテスト"""
    print("\n" + "=" * 60)
    print("/teach コマンドテスト")
    print("=" * 60)

    client, model = create_client()

    result = process_teach(
        client, model,
        "熱設計の質問が来たらJERG-2-200とJERG-2-211を参照して、"
        "まず設計要件を確認してから回答してください"
    )
    print(f"  teach結果:\n  {result}")

    # ルールが追加されたか確認
    rules = load_routing_rules()
    cats = list_categories()
    print(f"  ルール数: {len(rules)}")
    print(f"  カテゴリ: {cats}")

    if rules:
        print("  ✅ /teach テスト PASS")
    else:
        print("  ❌ /teach テスト FAIL - ルールが追加されていない")


def run_validation_test():
    """/validate コマンドのテスト"""
    print("\n" + "=" * 60)
    print("/validate コマンドテスト")
    print("=" * 60)
    report = run_validation()
    print(report)


def run_agent_tests():
    """エージェント統合テスト"""
    print("=" * 60)
    print("全Phase統合テスト")
    print("=" * 60)

    passed = 0
    failed = 0
    results = []

    for tc in TEST_CASES:
        print(f"\n--- Test {tc['id']}: {tc['category']} ---")
        print(f"Q: {tc['question'][:80]}...")

        try:
            answer = run_single(tc["question"])
            ok = tc["check"](answer)

            if ok:
                status = "PASS"
                passed += 1
            else:
                status = "FAIL"
                failed += 1

            print(f"A: {(answer or '(empty)')[:150]}")
            print(f"結果: {status}")

            results.append({
                "id": tc["id"],
                "category": tc["category"],
                "status": status,
                "answer_preview": (answer or "")[:200],
            })

        except Exception as e:
            failed += 1
            print(f"ERROR: {e}")
            traceback.print_exc()
            results.append({
                "id": tc["id"],
                "category": tc["category"],
                "status": "ERROR",
                "error": str(e),
            })

    print(f"\n{'=' * 60}")
    print(f"エージェントテスト結果: {passed} PASS / {failed} FAIL / {len(TEST_CASES)} TOTAL")
    print("=" * 60)

    with open("/tmp/test_full_results.json", "w") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    return failed == 0


if __name__ == "__main__":
    # 1. BM25検索の直接テスト
    run_direct_search_tests()

    # 2. /teach テスト
    run_teach_test()

    # 3. /validate テスト
    run_validation_test()

    # 4. エージェント統合テスト
    success = run_agent_tests()

    sys.exit(0 if success else 1)
