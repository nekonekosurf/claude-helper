"""クエリ拡張 - LLMでユーザー質問を専門用語に言い換え、複数の検索クエリを生成"""

import json
from src.llm_client import chat

EXPAND_PROMPT = """\
あなたは日本語の社内文書・技術文書の検索を支援するエキスパートです。

ユーザーの検索クエリを受け取り、同じ意味だが異なる表現の検索クエリを生成してください。
公式文書では日常語と異なる専門用語・法律用語が使われます。その言い換えを生成してください。

例:
- 「出張と外勤の境目」→ ["出張 外勤 距離 基準", "旅費 規程 起点 km", "出張 外勤 区分 規定"]
- 「給料はいくら」→ ["俸給 給与 報酬", "給与規程 手当 支給", "基本給 号俸"]
- 「衛星の温度管理」→ ["熱制御 温度範囲", "熱設計 宇宙機 温度要件", "熱収支 放熱 断熱"]

必ず以下のJSON形式で、3〜5個のクエリを返してください（JSONのみ、説明文なし）:
{"queries": ["クエリ1", "クエリ2", "クエリ3"]}
"""


def expand_query(client, model: str, user_query: str) -> list[str]:
    """ユーザーの質問をLLMで複数の検索クエリに拡張する

    Returns:
        元のクエリ + 拡張クエリのリスト
    """
    messages = [
        {"role": "system", "content": EXPAND_PROMPT},
        {"role": "user", "content": user_query},
    ]

    try:
        response = chat(client, model, messages, tools=None)
        content = response.content or ""

        # ```json ... ``` ブロック対応
        if "```" in content:
            start = content.index("```") + 3
            if content[start:start + 4] == "json":
                start += 4
            end = content.index("```", start)
            content = content[start:end].strip()

        data = json.loads(content)
        expanded = data.get("queries", [])

        # 元のクエリを先頭に追加（重複除外）
        result = [user_query]
        for q in expanded:
            if q not in result:
                result.append(q)
        return result

    except Exception:
        # LLM呼び出し失敗時は元のクエリのみ返す
        return [user_query]
