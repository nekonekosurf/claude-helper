"""ナレッジファイル管理 - YAMLベースのドメイン知識・ルーティングルール"""

import yaml
from pathlib import Path

KNOWLEDGE_DIR = Path(__file__).parent.parent / "knowledge"


def _ensure_dir():
    KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)


def load_index() -> dict:
    """カテゴリ一覧を読み込む"""
    path = KNOWLEDGE_DIR / "_index.yaml"
    if path.exists():
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {"categories": []}


def save_index(data: dict):
    """カテゴリ一覧を保存"""
    _ensure_dir()
    path = KNOWLEDGE_DIR / "_index.yaml"
    path.write_text(yaml.dump(data, allow_unicode=True, default_flow_style=False), encoding="utf-8")


def load_routing_rules() -> list:
    """ルーティングルールを読み込む"""
    path = KNOWLEDGE_DIR / "routing_rules.yaml"
    if path.exists():
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if data:
            return data.get("rules", [])
    return []


def save_routing_rules(rules: list):
    """ルーティングルールを保存"""
    _ensure_dir()
    path = KNOWLEDGE_DIR / "routing_rules.yaml"
    path.write_text(
        yaml.dump({"rules": rules}, allow_unicode=True, default_flow_style=False),
        encoding="utf-8",
    )


def load_category(name: str) -> dict:
    """カテゴリ別ナレッジを読み込む"""
    path = KNOWLEDGE_DIR / f"{name}.yaml"
    if path.exists():
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {}


def save_category(name: str, data: dict):
    """カテゴリ別ナレッジを保存"""
    _ensure_dir()
    path = KNOWLEDGE_DIR / f"{name}.yaml"
    path.write_text(yaml.dump(data, allow_unicode=True, default_flow_style=False), encoding="utf-8")


def list_categories() -> list[str]:
    """知識カテゴリの一覧を返す"""
    _ensure_dir()
    return [
        p.stem for p in sorted(KNOWLEDGE_DIR.glob("*.yaml"))
        if p.stem != "_index" and p.stem != "routing_rules"
    ]


def get_all_knowledge_summary() -> str:
    """全ナレッジの概要をテキストで返す（監査・検証用）"""
    parts = []

    # ルーティングルール
    rules = load_routing_rules()
    parts.append(f"## ルーティングルール: {len(rules)} 件")
    for r in rules:
        docs = ", ".join(d.get("id", "?") for d in r.get("documents", []))
        parts.append(f"  - パターン: {r.get('pattern', '?')} → カテゴリ: {r.get('category', '?')} → 文書: {docs}")

    # カテゴリ別知識
    cats = list_categories()
    for cat in cats:
        data = load_category(cat)
        display = data.get("display_name", cat)
        concepts = data.get("key_concepts", [])
        terms = data.get("terminology", {})
        parts.append(f"\n## {display}")
        parts.append(f"  概念: {len(concepts)} 件, 用語: {len(terms)} 件")
        for c in concepts[:5]:
            parts.append(f"  - {c.get('name', '?')}: {c.get('description', '')[:80]}")
        for k, v in list(terms.items())[:5]:
            parts.append(f"  - {k}: {v}")

    return "\n".join(parts) if parts else "(ナレッジなし)"
