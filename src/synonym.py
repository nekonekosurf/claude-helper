"""同義語辞書 - 検索クエリの同義語展開"""

import yaml
from pathlib import Path

SYNONYMS_PATH = Path(__file__).parent.parent / "knowledge" / "synonyms.yaml"

_synonyms = None


def _load():
    global _synonyms
    if _synonyms is not None:
        return
    if SYNONYMS_PATH.exists():
        data = yaml.safe_load(SYNONYMS_PATH.read_text(encoding="utf-8"))
        _synonyms = data.get("synonyms", {})
    else:
        _synonyms = {}


def expand_with_synonyms(query: str) -> list[str]:
    """クエリ内の単語を同義語で展開し、追加クエリを生成する

    Returns:
        元のクエリ + 同義語展開されたクエリのリスト
    """
    _load()
    expanded_terms = set()

    for term, syns in _synonyms.items():
        if term in query:
            expanded_terms.update(syns)

    if not expanded_terms:
        return [query]

    # 元のクエリ + 同義語を追加したクエリ
    syn_query = query + " " + " ".join(expanded_terms)
    return [query, syn_query]


def add_synonym(term: str, synonyms: list[str]):
    """同義語を追加して保存"""
    _load()
    existing = _synonyms.get(term, [])
    for s in synonyms:
        if s not in existing:
            existing.append(s)
    _synonyms[term] = existing
    _save()


def _save():
    SYNONYMS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SYNONYMS_PATH.write_text(
        yaml.dump({"synonyms": _synonyms}, allow_unicode=True, default_flow_style=False),
        encoding="utf-8",
    )


def reload():
    """辞書を再読み込み"""
    global _synonyms
    _synonyms = None
    _load()
