"""
advanced_query_expander.py - 高度なクエリ拡張

公的文書検索に特化した複数のクエリ拡張手法を実装する。

手法:
  1. Query Expansion - 同義語・専門用語の自動拡張
     例: 「テレメトリ」→ 「テレメトリ OR 遠隔測定 OR telemetry OR TLM」
  2. HyDE (Hypothetical Document Embeddings)
     - クエリから仮想的な回答文書を生成し、それでベクトル検索
  3. Sub-query Decomposition - 複合クエリを分解
     例: 「熱制御の温度マージンと試験方法」→ 2つのサブクエリに分解
  4. Step-back Prompting - 具体的なクエリから抽象的な上位概念を生成
     例: 「3.2.4節の緊急時手順」→ 「緊急時対応」「安全手順」
  5. Multi-perspective Expansion - 複数の観点からクエリを生成
     例: 設計者視点/運用者視点/審査者視点

既存の query_expander.py とは別ファイルとして追加実装。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any


# =========================================================
# データモデル
# =========================================================

@dataclass
class ExpandedQuery:
    """拡張されたクエリセット"""
    original: str
    expanded_terms: list[str]        # 同義語・別表現
    hyde_document: str               # HyDE生成文書
    sub_queries: list[str]           # サブクエリ分解
    step_back_queries: list[str]     # ステップバック（抽象化）クエリ
    perspective_queries: list[str]   # 多視点クエリ
    boolean_query: str               # Boolean検索クエリ形式

    def all_queries(self) -> list[str]:
        """全クエリのフラットリスト"""
        queries = [self.original]
        queries.extend(self.expanded_terms)
        queries.extend(self.sub_queries)
        queries.extend(self.step_back_queries)
        queries.extend(self.perspective_queries)
        # 重複除去・順序保持
        seen = set()
        result = []
        for q in queries:
            if q and q not in seen:
                seen.add(q)
                result.append(q)
        return result


# =========================================================
# 1. Query Expansion（同義語・専門用語拡張）
# =========================================================

# 公的文書でよく出る硬い表現 → 平易な表現のマッピング
FORMAL_TO_PLAIN = {
    "当該": ["この", "その", "対象の"],
    "資する": ["役立てる", "活用する", "貢献する"],
    "了する": ["終える", "完了する", "終了する"],
    "措置": ["対応", "対策", "処置"],
    "要す": ["必要とする", "求める"],
    "供する": ["提供する", "使用する"],
    "係る": ["関する", "関係する", "に関連する"],
    "かかる": ["このような", "そのような"],
    "準拠": ["従う", "基づく", "準ずる"],
    "規定": ["定める", "決める", "ルール"],
    "明示": ["はっきり示す", "明確に記載する"],
    "具備": ["持つ", "備える", "有する"],
    "遵守": ["守る", "従う", "順守する"],
    "勘案": ["考慮する", "踏まえる", "検討する"],
    "鑑み": ["踏まえて", "考慮して", "に基づいて"],
    "以下に掲げる": ["次の", "以下の", "下記の"],
    "前項": ["前のセクション", "上記"],
    "別途": ["別に", "他に", "それぞれ"],
}

# 宇宙・航空技術用語の日英対応
TECH_TERMS_JA_EN = {
    "テレメトリ": ["telemetry", "TLM", "遠隔測定"],
    "熱制御": ["thermal control", "熱管理", "温度管理"],
    "熱設計": ["thermal design", "熱工学設計"],
    "軌道": ["orbit", "オービット", "軌道面"],
    "宇宙機": ["spacecraft", "衛星", "探査機"],
    "機器": ["equipment", "component", "unit", "装置"],
    "バス機器": ["bus equipment", "衛星バス"],
    "ミッション機器": ["mission equipment", "ペイロード", "payload"],
    "マージン": ["margin", "余裕", "余裕値"],
    "要件": ["requirement", "要求", "要求事項"],
    "試験": ["test", "testing", "verification", "検証"],
    "認定試験": ["qualification test", "QT"],
    "受入試験": ["acceptance test", "AT"],
    "熱真空試験": ["thermal vacuum test", "TVT", "TV試験"],
    "熱サイクル試験": ["thermal cycling test", "TCT"],
    "寿命": ["lifetime", "mission life", "設計寿命"],
    "信頼性": ["reliability", "RAMS"],
    "フェールセーフ": ["fail-safe", "フォールトトレランス"],
    "冗長": ["redundancy", "冗長性", "バックアップ"],
    "EMC": ["電磁両立性", "電磁干渉", "electromagnetic compatibility"],
    "TLM": ["テレメトリ", "telemetry", "遠隔測定"],
    "TMM": ["熱数学モデル", "thermal mathematical model"],
    "DTR": ["設計温度範囲", "design temperature range"],
}


def expand_with_domain_dict(query: str) -> list[str]:
    """
    ドメイン辞書（硬い表現・専門用語）でクエリを拡張する。

    Returns:
        追加クエリのリスト（元のクエリは含まない）
    """
    extra_terms = []

    # 硬い表現を平易な表現に変換
    plain_query = query
    for formal, plains in FORMAL_TO_PLAIN.items():
        if formal in query:
            plain_query = plain_query.replace(formal, plains[0])
            extra_terms.extend(plains[1:])

    if plain_query != query:
        extra_terms.insert(0, plain_query)

    # 専門用語の日英展開
    for ja_term, alternatives in TECH_TERMS_JA_EN.items():
        if ja_term in query:
            # 元クエリのja_termを各代替表現で置き換えたクエリを追加
            for alt in alternatives[:2]:  # 最大2種類
                expanded = query.replace(ja_term, alt)
                if expanded != query:
                    extra_terms.append(expanded)
            # 代替表現をそのままキーワードとして追加
            extra_terms.extend(alternatives[:2])

    # 重複除去
    seen = set([query])
    result = []
    for t in extra_terms:
        if t and t not in seen:
            seen.add(t)
            result.append(t)

    return result


def build_boolean_query(query: str, extra_terms: list[str]) -> str:
    """
    BM25/Elasticsearch向けのBoolean検索クエリを生成する。

    例: 「テレメトリ」→ 「テレメトリ OR telemetry OR TLM OR 遠隔測定」
    """
    all_terms = [query] + extra_terms[:5]  # 最大6個
    # 重複除去
    seen = set()
    unique = []
    for t in all_terms:
        if t not in seen:
            seen.add(t)
            unique.append(t)

    return " OR ".join(f'"{t}"' if ' ' in t else t for t in unique)


# =========================================================
# 2. HyDE (Hypothetical Document Embeddings)
# =========================================================

HYDE_PROMPT = """\
あなたは公的技術文書（政府文書、宇宙技術仕様書等）の専門家です。
以下のユーザーの質問に対して、実際の技術文書に書かれているような文体で
回答文書を生成してください。

【重要】
- 実際の技術文書の文体で書く（「〜しなければならない」「〜であること」等）
- 200〜400字程度
- 専門用語を積極的に使用する
- この文書は後でベクトル検索のクエリとして使うため、関連キーワードを多く含める

ユーザーの質問:
{query}

【仮想的な回答文書】:
"""


def generate_hyde_document(client: Any, model: str, query: str) -> str:
    """
    HyDE: ユーザーのクエリから仮想的な回答文書を生成する。

    生成した文書をベクトル検索のクエリとして使うことで、
    クエリと文書の表現の乖離を埋める。

    Args:
        client: LLMクライアント
        model: モデル名
        query: ユーザーの検索クエリ

    Returns:
        仮想的な回答文書テキスト
    """
    from src.llm_client import chat

    prompt = HYDE_PROMPT.format(query=query)
    messages = [{"role": "user", "content": prompt}]

    try:
        response = chat(client, model, messages, tools=None)
        return response.content or ""
    except Exception as e:
        print(f"  HyDE生成失敗: {e}")
        return ""


# =========================================================
# 3. Sub-query Decomposition（複合クエリの分解）
# =========================================================

DECOMPOSE_PROMPT = """\
以下の検索クエリを、より具体的なサブクエリに分解してください。

【ルール】
- 複合的な質問を単純な質問に分解する
- 各サブクエリは独立して検索可能であること
- 2〜4個のサブクエリに分解
- JSON形式で返す（説明文なし）

例:
入力: 「熱制御の温度マージンと熱真空試験の手順」
出力: {"sub_queries": ["熱制御 温度マージン DTR 要件", "熱真空試験 手順 試験条件"]}

入力: {query}
出力:
"""


def decompose_query(client: Any, model: str, query: str) -> list[str]:
    """
    複合クエリをサブクエリに分解する。

    Returns:
        サブクエリのリスト（元クエリは含まない）
    """
    # クエリが短い場合（15文字以下）は分解不要
    if len(query) <= 15:
        return []

    from src.llm_client import chat

    prompt = DECOMPOSE_PROMPT.format(query=query)
    messages = [{"role": "user", "content": prompt}]

    try:
        response = chat(client, model, messages, tools=None)
        content = response.content or ""

        if "```" in content:
            start = content.index("```") + 3
            if content[start:start+4] == "json":
                start += 4
            end = content.index("```", start)
            content = content[start:end].strip()

        data = json.loads(content)
        sub_queries = data.get("sub_queries", [])
        return [q for q in sub_queries if q != query]

    except Exception:
        return []


# =========================================================
# 4. Step-back Prompting（抽象化）
# =========================================================

STEPBACK_PROMPT = """\
以下の具体的な検索クエリから、より抽象的・上位の概念に関するクエリを生成してください。

具体的なクエリを抽象化することで、関連する原則・基準・定義を見つけやすくなります。

例:
入力: 「3.2.4節の緊急時シャットダウン手順」
出力: {"abstract_queries": ["緊急時対応手順", "フェールセーフ設計要件", "安全管理基準"]}

入力: 「テレメトリのビットエラーレート許容値」
出力: {"abstract_queries": ["テレメトリ品質要件", "データ通信信頼性", "誤り率 要件"]}

入力: {query}
出力:
"""


def step_back_query(client: Any, model: str, query: str) -> list[str]:
    """
    Step-back: 具体的なクエリから抽象的なクエリを生成する。

    Returns:
        抽象化されたクエリのリスト
    """
    from src.llm_client import chat

    prompt = STEPBACK_PROMPT.format(query=query)
    messages = [{"role": "user", "content": prompt}]

    try:
        response = chat(client, model, messages, tools=None)
        content = response.content or ""

        if "```" in content:
            start = content.index("```") + 3
            if content[start:start+4] == "json":
                start += 4
            end = content.index("```", start)
            content = content[start:end].strip()

        data = json.loads(content)
        return data.get("abstract_queries", [])

    except Exception:
        return []


# =========================================================
# 5. Multi-perspective Expansion（多視点クエリ）
# =========================================================

PERSPECTIVE_PROMPT = """\
以下の技術文書検索クエリを、異なる立場・視点から言い換えてください。

公的文書を利用するのは、設計者、試験担当者、審査官（品質管理）、
運用担当者など様々な立場があります。それぞれの視点でクエリを生成してください。

出力形式（JSON）:
{
  "perspectives": [
    {"role": "設計者", "query": "設計者視点のクエリ"},
    {"role": "試験担当者", "query": "試験担当者視点のクエリ"},
    {"role": "審査官", "query": "審査官視点のクエリ"}
  ]
}

入力クエリ: {query}
出力:
"""


def multi_perspective_expand(client: Any, model: str, query: str) -> list[str]:
    """
    多視点でクエリを拡張する。

    Returns:
        各視点のクエリのリスト
    """
    from src.llm_client import chat

    prompt = PERSPECTIVE_PROMPT.format(query=query)
    messages = [{"role": "user", "content": prompt}]

    try:
        response = chat(client, model, messages, tools=None)
        content = response.content or ""

        if "```" in content:
            start = content.index("```") + 3
            if content[start:start+4] == "json":
                start += 4
            end = content.index("```", start)
            content = content[start:end].strip()

        data = json.loads(content)
        return [p["query"] for p in data.get("perspectives", [])]

    except Exception:
        return []


# =========================================================
# 統合クエリ拡張（全手法を組み合わせ）
# =========================================================

def expand_query_advanced(
    query: str,
    client: Any = None,
    model: str = "",
    use_hyde: bool = True,
    use_decompose: bool = True,
    use_stepback: bool = True,
    use_perspective: bool = False,  # コストが高いのでデフォルトOFF
    use_domain_dict: bool = True,
) -> ExpandedQuery:
    """
    全手法を組み合わせた高度なクエリ拡張。

    Args:
        query: 元のクエリ
        client: LLMクライアント（Noneでもドメイン辞書のみ動作）
        model: LLMモデル名
        use_hyde: HyDE文書生成を使うか
        use_decompose: サブクエリ分解を使うか
        use_stepback: ステップバッククエリを使うか
        use_perspective: 多視点展開を使うか
        use_domain_dict: ドメイン辞書展開を使うか

    Returns:
        ExpandedQuery
    """
    # 1. ドメイン辞書展開（LLM不要）
    expanded_terms = []
    if use_domain_dict:
        expanded_terms = expand_with_domain_dict(query)

    # 2. Boolean検索クエリ
    boolean_query = build_boolean_query(query, expanded_terms)

    # LLMを使う手法（clientが必要）
    hyde_document = ""
    sub_queries = []
    step_back_queries = []
    perspective_queries = []

    if client and model:
        # 2. HyDE文書生成
        if use_hyde:
            hyde_document = generate_hyde_document(client, model, query)

        # 3. サブクエリ分解
        if use_decompose:
            sub_queries = decompose_query(client, model, query)

        # 4. ステップバック
        if use_stepback:
            step_back_queries = step_back_query(client, model, query)

        # 5. 多視点展開
        if use_perspective:
            perspective_queries = multi_perspective_expand(client, model, query)

    return ExpandedQuery(
        original=query,
        expanded_terms=expanded_terms,
        hyde_document=hyde_document,
        sub_queries=sub_queries,
        step_back_queries=step_back_queries,
        perspective_queries=perspective_queries,
        boolean_query=boolean_query,
    )


# =========================================================
# スタンドアロン実行（LLMなしのドメイン辞書テスト）
# =========================================================

if __name__ == "__main__":
    test_queries = [
        "テレメトリのビットエラーレート",
        "当該施設の熱制御に資する要件",
        "熱真空試験のマージンと温度許容範囲の関係",
        "宇宙機の寿命と冗長設計",
    ]

    print("=== ドメイン辞書展開テスト（LLM不要） ===\n")
    for query in test_queries:
        result = expand_query_advanced(
            query=query,
            client=None,  # LLMなし
            use_hyde=False,
            use_decompose=False,
            use_stepback=False,
            use_perspective=False,
            use_domain_dict=True,
        )
        print(f"元クエリ: {result.original}")
        print(f"展開語:  {result.expanded_terms[:3]}")
        print(f"Boolean: {result.boolean_query}")
        print(f"全クエリ: {result.all_queries()[:4]}")
        print()
