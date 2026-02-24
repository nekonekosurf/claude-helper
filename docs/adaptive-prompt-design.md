# 適応型システムプロンプト設計

## 概要

ユーザーからの指示を受けて、AIがシステムプロンプトを自動で更新・拡張する仕組み。
宇宙開発（衛星・ロケット設計、JERG文書）に特化したドメイン知識を蓄積し、
質問への回答精度を段階的に向上させる。

---

## 解決する課題

| 課題 | 解決策 |
|---|---|
| 人間がシステムプロンプトを書くのは大変 | ユーザーは自然言語で指示、AIが構造化して反映 |
| 知識が体系化されない | カテゴリ別に整理されたナレッジファイル |
| 質問→文書の対応が分からない | ルーティングルール（質問パターン→参照文書→手順）|
| プロンプトが肥大化する | 階層構造で必要な部分だけ動的にロード |

---

## アーキテクチャ

```
┌──────────────────────────────────────────────────┐
│                   ユーザー                         │
│  「熱設計の質問が来たらJERG-2-200とJERG-2-211を     │
│    見て、まず設計要件を確認してから回答して」         │
└───────────────────┬──────────────────────────────┘
                    │
                    ▼
┌──────────────────────────────────────────────────┐
│              メタエージェント                       │
│  ユーザー指示を解析 → ナレッジファイルを更新          │
│  - どのカテゴリに該当するか判定                      │
│  - 既存ルールとの重複・矛盾チェック                  │
│  - 構造化して追記                                   │
└───────────────────┬──────────────────────────────┘
                    │
                    ▼
┌──────────────────────────────────────────────────┐
│            ナレッジファイル群                        │
│                                                    │
│  knowledge/                                        │
│  ├── _index.yaml         # カテゴリ一覧・概要        │
│  ├── routing_rules.yaml  # 質問→文書→手順マッピング  │
│  ├── thermal.yaml        # 熱設計ドメイン知識         │
│  ├── structure.yaml      # 構造設計ドメイン知識       │
│  ├── software.yaml       # ソフトウェア開発知識       │
│  ├── reliability.yaml    # 信頼性・品質保証知識       │
│  ├── electrical.yaml     # 電気設計知識              │
│  └── general.yaml        # 共通・プロジェクト管理     │
└───────────────────┬──────────────────────────────┘
                    │
                    ▼
┌──────────────────────────────────────────────────┐
│          システムプロンプト組立（実行時）             │
│                                                    │
│  1. ベースプロンプト（常時ロード）                    │
│  2. ユーザー質問を分類                               │
│  3. 該当カテゴリのナレッジをロード                    │
│  4. ルーティングルールをロード                        │
│  5. 組み立てたプロンプト + ツール定義 → LLM           │
└──────────────────────────────────────────────────┘
```

---

## ナレッジファイルの構造

### routing_rules.yaml（質問→文書→手順マッピング）

```yaml
rules:
  - pattern: "熱設計|熱解析|温度制御|ヒータ|放熱"
    category: thermal
    documents:
      - id: JERG-2-200
        title: 宇宙機熱制御設計標準
        priority: 1
      - id: JERG-2-211
        title: 宇宙機熱設計ハンドブック
        priority: 2
    procedure:
      - まず JERG-2-200 の該当セクションを検索して設計要件を確認
      - 次に JERG-2-211 で具体的な設計手法を確認
      - 要件と手法を整理して回答
    notes: "熱設計は温度範囲、軌道環境、熱収支の3つの観点で回答すること"

  - pattern: "ソフトウェア|コーディング|テスト工程|V&V"
    category: software
    documents:
      - id: JERG-0-049
        title: ソフトウェア開発標準
        priority: 1
      - id: JERG-2-610
        title: 宇宙機ソフトウェア開発標準
        priority: 2
      - id: JERG-1-008
        title: ロケット搭載ソフトウェア開発標準
        priority: 3
    procedure:
      - 対象がロケットか宇宙機かを質問から判定
      - 該当する文書を優先して検索
      - 開発工程（要件定義→設計→実装→テスト）のどこに該当するか特定
```

### thermal.yaml（ドメイン知識の例）

```yaml
category: thermal
display_name: 熱設計
description: 宇宙機・ロケットの熱制御設計に関するドメイン知識

key_concepts:
  - name: 熱収支解析
    description: 外部熱入力（太陽、地球反射、地球放射）と内部発熱のバランスを計算
    related_docs: [JERG-2-200, JERG-2-211]

  - name: 温度要件
    description: 各機器の動作温度範囲・保管温度範囲の定義
    related_docs: [JERG-2-200]

terminology:
  MLI: 多層断熱材（Multi-Layer Insulation）
  OSR: 光学太陽反射鏡（Optical Solar Reflector）
  ヒータ制御: サーモスタット制御またはソフトウェア制御で温度を維持

common_questions:
  - question: "衛星の熱設計で最初に考えるべきことは？"
    approach: "JERG-2-200 の第3章（熱制御系設計）を参照。軌道・姿勢→外部熱環境→温度要件→熱制御方式の順に検討"
```

---

## 実装（Phase 1 に追加する形）

### 追加ファイル

```
src/
├── knowledge.py       # ナレッジファイル読み書き
├── prompt_builder.py  # 動的システムプロンプト組立
└── meta_agent.py      # ユーザー指示→ナレッジ更新
knowledge/
├── _index.yaml
├── routing_rules.yaml
└── (カテゴリ別 .yaml)
```

### 1. knowledge.py - ナレッジ管理

```python
"""ナレッジファイルの読み書き"""
import yaml
from pathlib import Path

KNOWLEDGE_DIR = Path(__file__).parent.parent / "knowledge"

def load_index() -> dict:
    """カテゴリ一覧を読み込む"""
    path = KNOWLEDGE_DIR / "_index.yaml"
    if path.exists():
        return yaml.safe_load(path.read_text())
    return {"categories": []}

def load_routing_rules() -> list:
    """ルーティングルールを読み込む"""
    path = KNOWLEDGE_DIR / "routing_rules.yaml"
    if path.exists():
        data = yaml.safe_load(path.read_text())
        return data.get("rules", [])
    return []

def load_category(name: str) -> dict:
    """カテゴリ別ナレッジを読み込む"""
    path = KNOWLEDGE_DIR / f"{name}.yaml"
    if path.exists():
        return yaml.safe_load(path.read_text())
    return {}

def save_category(name: str, data: dict):
    """カテゴリ別ナレッジを保存"""
    KNOWLEDGE_DIR.mkdir(exist_ok=True)
    path = KNOWLEDGE_DIR / f"{name}.yaml"
    path.write_text(yaml.dump(data, allow_unicode=True, default_flow_style=False))

def save_routing_rules(rules: list):
    """ルーティングルールを保存"""
    KNOWLEDGE_DIR.mkdir(exist_ok=True)
    path = KNOWLEDGE_DIR / "routing_rules.yaml"
    path.write_text(yaml.dump({"rules": rules}, allow_unicode=True, default_flow_style=False))
```

### 2. prompt_builder.py - 動的プロンプト組立

```python
"""質問に応じてシステムプロンプトを動的に組み立てる"""
import re
from src.knowledge import load_routing_rules, load_category

BASE_PROMPT = """あなたは宇宙開発の技術文書に精通したアシスタントです。
JAXA JERG文書を参照して、正確な回答を提供します。"""

def build_prompt(user_question: str) -> str:
    """ユーザーの質問に基づいてシステムプロンプトを組み立てる"""
    parts = [BASE_PROMPT]

    # ルーティングルールでカテゴリ判定
    rules = load_routing_rules()
    matched_rules = []
    for rule in rules:
        if re.search(rule["pattern"], user_question):
            matched_rules.append(rule)

    # マッチしたカテゴリのナレッジを追加
    loaded_categories = set()
    for rule in matched_rules:
        cat = rule.get("category")
        if cat and cat not in loaded_categories:
            knowledge = load_category(cat)
            if knowledge:
                parts.append(f"\n## {knowledge.get('display_name', cat)} ドメイン知識")
                # 用語集
                if "terminology" in knowledge:
                    terms = "\n".join(f"- {k}: {v}" for k, v in knowledge["terminology"].items())
                    parts.append(f"### 用語\n{terms}")

            loaded_categories.add(cat)

        # ルーティング手順を追加
        if "procedure" in rule:
            steps = "\n".join(f"  {i+1}. {s}" for i, s in enumerate(rule["procedure"]))
            docs = ", ".join(d["id"] for d in rule.get("documents", []))
            parts.append(f"\n### 回答手順（参照文書: {docs}）\n{steps}")

        if "notes" in rule:
            parts.append(f"注意: {rule['notes']}")

    return "\n".join(parts)
```

### 3. meta_agent.py - ユーザー指示→ナレッジ更新

```python
"""メタエージェント: ユーザーの指示からナレッジを更新する"""

META_PROMPT = """あなたはナレッジ管理エージェントです。
ユーザーの指示を解析して、以下の形式のYAMLデータを生成してください。

ユーザーが「こういう質問にはこう対応して」と指示した場合:
→ routing_rules に新しいルールを追加

ユーザーが「この用語はこういう意味」と教えた場合:
→ 該当カテゴリの terminology に追加

ユーザーが「この分野ではこういう知識が重要」と教えた場合:
→ 該当カテゴリの key_concepts に追加

必ず以下のJSON形式で回答してください:
{
  "action": "add_rule" | "add_knowledge" | "update_knowledge",
  "category": "カテゴリ名",
  "data": { ... 追加するデータ ... }
}
"""

def process_instruction(client, model, user_instruction: str) -> str:
    """ユーザーの指示を処理してナレッジを更新する"""
    # LLMに指示を解析させる
    # 結果をナレッジファイルに反映
    # 更新内容をユーザーに報告
    pass
```

---

## 使い方のフロー

### ナレッジ追加（teach モード）

```
$ uv run python3 agent.py

🤖 Agent ready (gpt-oss-120b)
> /teach 熱設計の質問が来たらJERG-2-200とJERG-2-211を見て、
  まず設計要件を確認してから回答して

📝 ナレッジを更新しました:
  - ルーティングルール追加: 熱設計 → JERG-2-200, JERG-2-211
  - 回答手順: 設計要件確認 → 回答
  - カテゴリ: thermal

> /teach MLIとは多層断熱材のことで、宇宙機の外側に巻く断熱材です

📝 ナレッジを更新しました:
  - thermal カテゴリに用語追加: MLI = 多層断熱材
```

### 通常の質問（ナレッジ自動適用）

```
> 衛星の熱設計で最初に考えるべきことは？

📌 検索中... [thermal ルール適用]
  → JERG-2-200 を優先検索
  → 回答手順: 設計要件確認 → 回答

JERG-2-200「宇宙機熱制御設計標準」によると...
```

---

## 段階的な実装計画

| ステップ | 内容 | 依存 |
|---|---|---|
| Step 1 | knowledge/ ディレクトリと基本YAML構造 | Phase 1 完了 |
| Step 2 | prompt_builder.py（ルールマッチ→プロンプト組立）| Step 1 |
| Step 3 | /teach コマンド（LLMが指示を解析→YAML書き込み）| Step 2 |
| Step 4 | agent.py 統合（質問時に自動でプロンプト組立）| Step 2 + Phase 2 |
| Step 5 | 検証・改善（実際の質問でルーティング精度を確認）| Step 4 |

---

## 設計上のポイント

### なぜ YAML か？
- 人間が読みやすく、AIが生成しやすい
- Git で差分管理できる
- Python の PyYAML で簡単に読み書き

### プロンプト肥大化の防止
- 全ナレッジを毎回ロードしない
- ルーティングルールで質問を分類 → 該当カテゴリのみロード
- ベースプロンプト + マッチしたルール + カテゴリ知識 の3層構造

### 学習のサイクル
```
ユーザー指示 → AI解析 → YAML更新 → 次回質問で適用 → 回答品質向上
     ↑                                                    │
     └────── 不足があればユーザーが追加指示 ←──────────────┘
```

これにより、使い込むほどシステムが賢くなる仕組みになります。
