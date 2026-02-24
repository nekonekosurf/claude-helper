"""Phase 1 統合テスト - エージェントに質問を投げて動作確認"""

import sys
import os
import json
import traceback

# プロジェクトルートをパスに追加
sys.path.insert(0, os.path.dirname(__file__))

from src.agent import run_single


TEST_CASES = [
    {
        "id": 1,
        "category": "テキスト応答（ツールなし）",
        "question": "Pythonのリスト内包表記について簡単に教えてください",
        "expect": "リスト内包表記の説明が返る（ツール不使用）",
        "check": lambda ans: ans and len(ans) > 20,
    },
    {
        "id": 2,
        "category": "read_file ツール",
        "question": "このファイルを読んでください: /home/neko/projects/claude-helper/pyproject.toml",
        "expect": "pyproject.tomlの内容を読んで説明する",
        "check": lambda ans: ans and "claude-helper" in ans.lower(),
    },
    {
        "id": 3,
        "category": "bash ツール",
        "question": "現在のディレクトリにあるファイル一覧を表示してください。ls -la を実行してください。",
        "expect": "ls -la の結果を表示する",
        "check": lambda ans: ans and ("pyproject" in ans or "src" in ans or "docs" in ans),
    },
    {
        "id": 4,
        "category": "write_file ツール",
        "question": "以下の内容で /tmp/agent_test_output.txt を作成してください:\nHello from Agent\nThis is a test file.",
        "expect": "ファイルが作成される",
        "check": lambda ans: (
            os.path.exists("/tmp/agent_test_output.txt")
            and "Hello from Agent" in open("/tmp/agent_test_output.txt").read()
        ),
    },
    {
        "id": 5,
        "category": "edit_file ツール",
        "question": "/tmp/agent_test_output.txt を編集して、'Hello from Agent' を 'Hello from Claude Helper' に変更してください",
        "expect": "ファイルが編集される",
        "check": lambda ans: (
            os.path.exists("/tmp/agent_test_output.txt")
            and "Hello from Claude Helper" in open("/tmp/agent_test_output.txt").read()
        ),
    },
    {
        "id": 6,
        "category": "複合タスク（read + 分析）",
        "question": "/home/neko/projects/claude-helper/src/config.py を読んで、どのLLMプロバイダに対応しているか教えてください",
        "expect": "cerebras と vllm の2つのプロバイダを認識する",
        "check": lambda ans: ans and "cerebras" in ans.lower() and "vllm" in ans.lower(),
    },
]


def run_tests():
    results = []
    passed = 0
    failed = 0

    print("=" * 60)
    print("Phase 1 統合テスト")
    print("=" * 60)

    for tc in TEST_CASES:
        print(f"\n--- Test {tc['id']}: {tc['category']} ---")
        print(f"Q: {tc['question'][:80]}...")
        print(f"期待: {tc['expect']}")

        try:
            answer = run_single(tc["question"])
            ok = tc["check"](answer)

            if ok:
                status = "PASS"
                passed += 1
            else:
                status = "FAIL"
                failed += 1

            print(f"A: {(answer or '(empty)')[:200]}")
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
    print(f"結果: {passed} PASS / {failed} FAIL / {len(TEST_CASES)} TOTAL")
    print("=" * 60)

    # 結果をJSONで保存
    with open("/tmp/agent_test_results.json", "w") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n詳細: /tmp/agent_test_results.json")

    return failed == 0


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
