"""メタエージェント - ユーザーの /teach 指示からナレッジを自動更新"""

import json
from src.llm_client import chat
from src.knowledge import (
    load_routing_rules, save_routing_rules,
    load_category, save_category,
    load_index, save_index,
    list_categories,
)

META_PROMPT = """\
あなたはナレッジ管理エージェントです。
ユーザーの指示を解析して、ナレッジデータを生成してください。

ユーザーが「こういう質問にはこう対応して」と指示した場合:
→ action = "add_rule" でルーティングルールを追加

ユーザーが「この用語はこういう意味」と教えた場合:
→ action = "add_term" で該当カテゴリの用語を追加

ユーザーが「この分野ではこういう知識が重要」と教えた場合:
→ action = "add_concept" でカテゴリに重要概念を追加

必ず以下のJSON形式で回答してください（JSONのみ、説明文なし）:

ルール追加の場合:
{
  "action": "add_rule",
  "category": "カテゴリ名（英語、例: thermal, software, structure）",
  "display_name": "カテゴリ表示名（日本語、例: 熱設計）",
  "pattern": "正規表現パターン（例: 熱設計|熱解析|温度制御）",
  "documents": [{"id": "JERG-2-200", "title": "文書タイトル"}],
  "procedure": ["手順1", "手順2"],
  "notes": "補足事項（あれば）"
}

用語追加の場合:
{
  "action": "add_term",
  "category": "カテゴリ名",
  "term": "用語",
  "definition": "定義"
}

概念追加の場合:
{
  "action": "add_concept",
  "category": "カテゴリ名",
  "name": "概念名",
  "description": "説明",
  "related_docs": ["JERG-X-XXX"]
}
"""


def process_teach(client, model: str, instruction: str) -> str:
    """ユーザーの /teach 指示を処理してナレッジを更新する"""
    # LLM に指示を解析させる
    messages = [
        {"role": "system", "content": META_PROMPT},
        {"role": "user", "content": instruction},
    ]

    response = chat(client, model, messages, tools=None)
    content = response.content or ""

    # JSON を抽出
    try:
        # ```json ... ``` ブロックから抽出を試みる
        if "```" in content:
            start = content.index("```") + 3
            if content[start:start+4] == "json":
                start += 4
            end = content.index("```", start)
            content = content[start:end].strip()

        data = json.loads(content)
    except (json.JSONDecodeError, ValueError) as e:
        return f"Error: LLMの応答をJSONとして解析できませんでした: {e}\n応答: {content[:200]}"

    action = data.get("action")

    if action == "add_rule":
        return _add_rule(data)
    elif action == "add_term":
        return _add_term(data)
    elif action == "add_concept":
        return _add_concept(data)
    else:
        return f"Error: 不明なアクション: {action}"


def _add_rule(data: dict) -> str:
    """ルーティングルールを追加"""
    category = data.get("category", "general")
    display_name = data.get("display_name", category)
    pattern = data.get("pattern", "")
    documents = data.get("documents", [])
    procedure = data.get("procedure", [])
    notes = data.get("notes", "")

    if not pattern:
        return "Error: パターンが空です"

    # ルール追加
    rules = load_routing_rules()
    new_rule = {
        "pattern": pattern,
        "category": category,
        "documents": documents,
    }
    if procedure:
        new_rule["procedure"] = procedure
    if notes:
        new_rule["notes"] = notes
    rules.append(new_rule)
    save_routing_rules(rules)

    # カテゴリの存在を確認、なければ作成
    cat_data = load_category(category)
    if not cat_data:
        cat_data = {
            "category": category,
            "display_name": display_name,
            "key_concepts": [],
            "terminology": {},
        }
        save_category(category, cat_data)

    # インデックス更新
    index = load_index()
    cats = index.get("categories", [])
    if category not in [c.get("name") for c in cats]:
        cats.append({"name": category, "display_name": display_name})
        index["categories"] = cats
        save_index(index)

    doc_names = ", ".join(d.get("id", "?") for d in documents)
    return (
        f"📝 ルーティングルール追加:\n"
        f"  パターン: {pattern}\n"
        f"  カテゴリ: {display_name} ({category})\n"
        f"  参照文書: {doc_names}\n"
        f"  手順: {len(procedure)} ステップ"
    )


def _add_term(data: dict) -> str:
    """用語を追加"""
    category = data.get("category", "general")
    term = data.get("term", "")
    definition = data.get("definition", "")

    if not term:
        return "Error: 用語が空です"

    cat_data = load_category(category)
    if not cat_data:
        cat_data = {
            "category": category,
            "display_name": category,
            "key_concepts": [],
            "terminology": {},
        }

    cat_data.setdefault("terminology", {})[term] = definition
    save_category(category, cat_data)

    return f"📝 用語追加 [{category}]: {term} = {definition}"


def _add_concept(data: dict) -> str:
    """重要概念を追加"""
    category = data.get("category", "general")
    name = data.get("name", "")
    description = data.get("description", "")
    related_docs = data.get("related_docs", [])

    if not name:
        return "Error: 概念名が空です"

    cat_data = load_category(category)
    if not cat_data:
        cat_data = {
            "category": category,
            "display_name": category,
            "key_concepts": [],
            "terminology": {},
        }

    cat_data.setdefault("key_concepts", []).append({
        "name": name,
        "description": description,
        "related_docs": related_docs,
    })
    save_category(category, cat_data)

    return f"📝 概念追加 [{category}]: {name}"
