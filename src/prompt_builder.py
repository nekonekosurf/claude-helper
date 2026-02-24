"""動的システムプロンプト組立 - 質問に応じてドメイン知識をロード"""

import re
from src.knowledge import load_routing_rules, load_category
from src.memory import load_memory

BASE_PROMPT = """\
あなたは宇宙開発に精通したコーディングアシスタントです。
JAXA JERG技術文書を参照し、正確な回答を提供します。

利用可能なツール:
- read_file: ファイルを読み取る
- write_file: ファイルを作成/上書きする
- edit_file: ファイル内のテキストを置換する
- bash: シェルコマンドを実行する
- glob: ファイルパターンで検索する
- grep: 正規表現でファイル内容を検索する
- search_docs: JERG技術文書をキーワード検索する

ルール:
- ファイルを編集する前に、必ず read_file で内容を確認してください
- 危険なコマンド（rm -rf, etc）は実行前にユーザーに確認を取ってください
- 回答は簡潔に、日本語で行ってください
- ツールを使う必要がある場合は積極的にツールを使ってください
- JERG文書に関する質問には search_docs ツールを使って文書を検索してから回答してください
"""


def build_system_prompt(user_question: str | None = None) -> str:
    """ユーザーの質問に基づいてシステムプロンプトを動的に組み立てる"""
    parts = [BASE_PROMPT]

    # 記憶をロード
    memory = load_memory()
    if memory:
        parts.append(f"\n## 記憶（前回のセッションからの引き継ぎ）\n{memory}")

    if not user_question:
        return "\n".join(parts)

    # ルーティングルールで該当するドメイン知識を判定
    rules = load_routing_rules()
    matched_rules = []
    for rule in rules:
        pattern = rule.get("pattern", "")
        if pattern and re.search(pattern, user_question):
            matched_rules.append(rule)

    if not matched_rules:
        return "\n".join(parts)

    # マッチしたカテゴリのナレッジをロード
    loaded_categories = set()
    for rule in matched_rules:
        cat = rule.get("category")
        if cat and cat not in loaded_categories:
            knowledge = load_category(cat)
            if knowledge:
                display = knowledge.get("display_name", cat)
                parts.append(f"\n## ドメイン知識: {display}")

                # 用語集
                terms = knowledge.get("terminology", {})
                if terms:
                    term_lines = [f"- {k}: {v}" for k, v in terms.items()]
                    parts.append("### 用語\n" + "\n".join(term_lines))

                # 重要概念
                concepts = knowledge.get("key_concepts", [])
                if concepts:
                    concept_lines = []
                    for c in concepts:
                        concept_lines.append(f"- {c['name']}: {c.get('description', '')}")
                    parts.append("### 重要概念\n" + "\n".join(concept_lines))

            loaded_categories.add(cat)

        # 回答手順を追加
        if "procedure" in rule:
            docs = ", ".join(d.get("id", "?") for d in rule.get("documents", []))
            steps = "\n".join(f"  {i+1}. {s}" for i, s in enumerate(rule["procedure"]))
            parts.append(f"\n### 回答手順（参照文書: {docs}）\n{steps}")

        if "notes" in rule:
            parts.append(f"\n注意: {rule['notes']}")

    return "\n".join(parts)
