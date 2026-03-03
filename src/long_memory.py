"""
長期記憶管理 (Long-term Memory)

セッションをまたいで知識を保持する仕組み:

1. WorkingMemory  : 現在セッションの短期記憶 (最近N件)
2. EpisodicMemory : 過去の会話エピソードを圧縮して保存
3. SemanticMemory : 宇宙ドメインの事実・定義・ルールを構造化保存
4. ProceduralMemory: よく使う手順・パターンを記録

## ファイル構成
agent_memory/
  MEMORY.md       ← 既存の汎用メモ
  episodes/       ← 過去の会話エピソード (JSON)
  semantic/       ← 事実・定義データベース
  procedural/     ← 手順パターン
  working.json    ← 現セッションの短期記憶
"""

from __future__ import annotations

import json
import time
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional
from openai import OpenAI


# ---------------------------------------------------------------------------
# パス設定
# ---------------------------------------------------------------------------

MEMORY_ROOT    = Path(__file__).parent.parent / "agent_memory"
EPISODE_DIR    = MEMORY_ROOT / "episodes"
SEMANTIC_DIR   = MEMORY_ROOT / "semantic"
PROCEDURAL_DIR = MEMORY_ROOT / "procedural"
WORKING_FILE   = MEMORY_ROOT / "working.json"


def _ensure_dirs():
    for d in (MEMORY_ROOT, EPISODE_DIR, SEMANTIC_DIR, PROCEDURAL_DIR):
        d.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# データ型
# ---------------------------------------------------------------------------

@dataclass
class Episode:
    """会話エピソード（圧縮済み）"""
    episode_id: str
    summary: str           # 会話の要約 (LLM生成)
    key_facts: list[str]   # 重要な事実
    tags: list[str]
    created_at: float = field(default_factory=time.time)
    session_id: str = ""
    importance: float = 1.0  # 重要度スコア (1.0-5.0)


@dataclass
class SemanticFact:
    """宇宙ドメインの事実・定義"""
    fact_id: str
    category: str          # "definition" / "constraint" / "standard" / "formula"
    subject: str           # 主語（例: "太陽電池効率"）
    content: str           # 内容
    source: str = ""       # 出典
    confidence: float = 1.0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    tags: list[str] = field(default_factory=list)


@dataclass
class ProceduralPattern:
    """手順・パターン"""
    pattern_id: str
    name: str
    description: str
    trigger_keywords: list[str]  # この手順を使うキーワード
    steps: list[str]             # 実行ステップ
    example: str = ""
    created_at: float = field(default_factory=time.time)
    use_count: int = 0


# ---------------------------------------------------------------------------
# WorkingMemory (短期記憶)
# ---------------------------------------------------------------------------

class WorkingMemory:
    """
    現セッションの短期記憶。
    重要な情報をセッション中に随時記録し、次のLLM呼び出しに渡す。
    """

    def __init__(self, max_items: int = 20):
        self.max_items = max_items
        self._items: list[dict] = []
        self._load()

    def add(self, content: str, category: str = "general", importance: float = 1.0):
        """記憶を追加"""
        self._items.append({
            "content": content,
            "category": category,
            "importance": importance,
            "added_at": time.time(),
        })
        # 重要度でソートして上限を維持
        self._items.sort(key=lambda x: x["importance"], reverse=True)
        self._items = self._items[:self.max_items]
        self._save()

    def get_context(self, max_chars: int = 2000) -> str:
        """LLMに渡すコンテキスト文字列を生成"""
        if not self._items:
            return ""
        lines = ["[作業記憶]"]
        total = 0
        for item in self._items:
            line = f"- [{item['category']}] {item['content']}"
            if total + len(line) > max_chars:
                break
            lines.append(line)
            total += len(line)
        return "\n".join(lines)

    def search(self, query: str) -> list[dict]:
        """キーワード検索"""
        q = query.lower()
        return [item for item in self._items if q in item["content"].lower()]

    def clear(self):
        self._items = []
        self._save()

    def _save(self):
        _ensure_dirs()
        WORKING_FILE.write_text(
            json.dumps(self._items, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _load(self):
        if WORKING_FILE.exists():
            try:
                self._items = json.loads(WORKING_FILE.read_text(encoding="utf-8"))
            except Exception:
                self._items = []


# ---------------------------------------------------------------------------
# EpisodicMemory (エピソード記憶)
# ---------------------------------------------------------------------------

class EpisodicMemory:
    """過去の会話エピソードを保存・検索"""

    def store(
        self,
        messages: list[dict],
        session_id: str = "",
        client: Optional[OpenAI] = None,
        model: str = "",
    ) -> Episode:
        """
        会話をエピソードとして保存。
        LLMが利用可能なら要約を生成。

        Args:
            messages: 会話メッセージリスト
            session_id: セッションID
            client: LLMクライアント (要約生成用、省略可)
            model: モデル名

        Returns:
            Episode
        """
        _ensure_dirs()
        episode_id = f"ep_{int(time.time())}"

        # 会話テキストを作成
        conversation = "\n".join(
            f"{m.get('role','?')}: {str(m.get('content',''))[:300]}"
            for m in messages
            if m.get("role") in ("user", "assistant")
        )

        summary, key_facts, tags, importance = self._extract_episode_info(
            conversation, client, model
        )

        episode = Episode(
            episode_id=episode_id,
            summary=summary,
            key_facts=key_facts,
            tags=tags,
            session_id=session_id,
            importance=importance,
        )

        path = EPISODE_DIR / f"{episode_id}.json"
        path.write_text(
            json.dumps(asdict(episode), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return episode

    def _extract_episode_info(
        self,
        conversation: str,
        client: Optional[OpenAI],
        model: str,
    ) -> tuple[str, list[str], list[str], float]:
        """会話から要約・重要事実・タグを抽出"""
        if client is None:
            # LLMなし: 単純抽出
            summary = conversation[:200]
            key_facts = []
            tags = _auto_tags_from_text(conversation)
            importance = 1.0
            return summary, key_facts, tags, importance

        prompt = f"""以下の会話を分析して情報を抽出してください。

## 会話
{conversation[:2000]}

## 出力形式
SUMMARY: [会話の要約、100文字以内]
KEY_FACTS:
- [重要な事実1]
- [重要な事実2]
TAGS: [タグ1, タグ2, タグ3]
IMPORTANCE: [1-5の重要度、5が最重要]
"""
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=400,
            )
            content = response.choices[0].message.content or ""
            return self._parse_extraction(content, conversation)
        except Exception:
            return conversation[:200], [], _auto_tags_from_text(conversation), 1.0

    def _parse_extraction(
        self, content: str, fallback: str
    ) -> tuple[str, list[str], list[str], float]:
        summary = fallback[:200]
        key_facts = []
        tags = []
        importance = 1.0

        section = None
        for line in content.splitlines():
            line = line.strip()
            if line.upper().startswith("SUMMARY:"):
                summary = line[8:].strip()
            elif line.upper().startswith("KEY_FACTS:"):
                section = "facts"
            elif line.upper().startswith("TAGS:"):
                tags_str = line[5:].strip()
                tags = [t.strip() for t in re.split(r"[,、]", tags_str) if t.strip()]
                section = None
            elif line.upper().startswith("IMPORTANCE:"):
                try:
                    importance = float(re.search(r"\d+", line).group())
                    importance = max(1.0, min(5.0, importance))
                except Exception:
                    pass
                section = None
            elif section == "facts" and line.startswith("-"):
                key_facts.append(line[1:].strip())

        return summary, key_facts, tags, importance

    def search(
        self,
        query: str,
        *,
        limit: int = 5,
        min_importance: float = 0.0,
        tags: Optional[list[str]] = None,
    ) -> list[Episode]:
        """
        エピソードをキーワード検索。

        Args:
            query: 検索クエリ
            limit: 最大件数
            min_importance: 最低重要度
            tags: タグフィルタ (OR)

        Returns:
            Episode のリスト（重要度順）
        """
        _ensure_dirs()
        q = query.lower()
        results = []

        for path in EPISODE_DIR.glob("*.json"):
            try:
                episode = Episode(**json.loads(path.read_text(encoding="utf-8")))
            except Exception:
                continue

            if episode.importance < min_importance:
                continue
            if tags and not any(t in episode.tags for t in tags):
                continue

            # スコアリング: クエリとの一致度
            score = 0.0
            text = (episode.summary + " " + " ".join(episode.key_facts)).lower()
            for word in q.split():
                if word in text:
                    score += 1.0
            score += episode.importance * 0.5

            if score > 0 or not q:
                results.append((score, episode))

        results.sort(key=lambda x: x[0], reverse=True)
        return [ep for _, ep in results[:limit]]

    def get_context(self, query: str, max_chars: int = 1500) -> str:
        """検索結果をLLMコンテキスト用の文字列に変換"""
        episodes = self.search(query, limit=3)
        if not episodes:
            return ""
        lines = ["[関連エピソード]"]
        total = 0
        for ep in episodes:
            block = f"- {ep.summary}"
            if ep.key_facts:
                block += "\n  事実: " + "; ".join(ep.key_facts[:2])
            if total + len(block) > max_chars:
                break
            lines.append(block)
            total += len(block)
        return "\n".join(lines)

    def list_recent(self, limit: int = 10) -> list[Episode]:
        """最近のエピソードを返す"""
        _ensure_dirs()
        episodes = []
        for path in sorted(EPISODE_DIR.glob("*.json"),
                            key=lambda p: p.stat().st_mtime, reverse=True)[:limit]:
            try:
                episodes.append(Episode(**json.loads(path.read_text(encoding="utf-8"))))
            except Exception:
                pass
        return episodes


# ---------------------------------------------------------------------------
# SemanticMemory (意味記憶)
# ---------------------------------------------------------------------------

class SemanticMemory:
    """
    宇宙ドメインの事実・定義・制約を構造化保存。

    カテゴリ:
    - definition : 用語定義
    - constraint : 設計制約・要求
    - standard   : 標準・規格
    - formula    : 計算式・数値
    """

    _INDEX_FILE = SEMANTIC_DIR / "index.json"

    def add(
        self,
        subject: str,
        content: str,
        category: str = "definition",
        source: str = "",
        tags: Optional[list[str]] = None,
        confidence: float = 1.0,
    ) -> SemanticFact:
        """事実を追加"""
        _ensure_dirs()
        fact_id = f"fact_{subject[:20].replace(' ', '_')}_{int(time.time())}"
        fact = SemanticFact(
            fact_id=fact_id,
            category=category,
            subject=subject,
            content=content,
            source=source,
            confidence=confidence,
            tags=tags or [],
        )
        path = SEMANTIC_DIR / f"{fact_id}.json"
        path.write_text(json.dumps(asdict(fact), ensure_ascii=False, indent=2))
        return fact

    def search(
        self,
        query: str,
        *,
        category: Optional[str] = None,
        limit: int = 5,
    ) -> list[SemanticFact]:
        """意味記憶を検索"""
        _ensure_dirs()
        q = query.lower()
        results = []

        for path in SEMANTIC_DIR.glob("*.json"):
            if path.name == "index.json":
                continue
            try:
                fact = SemanticFact(**json.loads(path.read_text(encoding="utf-8")))
            except Exception:
                continue

            if category and fact.category != category:
                continue

            score = 0.0
            text = (fact.subject + " " + fact.content).lower()
            for word in q.split():
                if word in text:
                    score += 1.0
            score += fact.confidence * 0.2

            if score > 0 or not q:
                results.append((score, fact))

        results.sort(key=lambda x: x[0], reverse=True)
        return [f for _, f in results[:limit]]

    def get_context(self, query: str, max_chars: int = 1500) -> str:
        """検索結果をLLMコンテキスト用の文字列に変換"""
        facts = self.search(query, limit=5)
        if not facts:
            return ""
        lines = ["[ドメイン知識]"]
        total = 0
        for f in facts:
            line = f"- [{f.category}] {f.subject}: {f.content}"
            if f.source:
                line += f" (出典: {f.source})"
            if total + len(line) > max_chars:
                break
            lines.append(line)
            total += len(line)
        return "\n".join(lines)

    def bulk_add(self, facts: list[dict]) -> list[SemanticFact]:
        """複数の事実を一括追加"""
        return [self.add(**f) for f in facts]

    def update(self, fact_id: str, content: str) -> bool:
        """既存の事実を更新"""
        path = SEMANTIC_DIR / f"{fact_id}.json"
        if not path.exists():
            return False
        try:
            data = json.loads(path.read_text())
            data["content"] = content
            data["updated_at"] = time.time()
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
            return True
        except Exception:
            return False


# ---------------------------------------------------------------------------
# ProceduralMemory (手続き記憶)
# ---------------------------------------------------------------------------

class ProceduralMemory:
    """よく使う手順・パターンを記録"""

    def add(
        self,
        name: str,
        description: str,
        steps: list[str],
        trigger_keywords: list[str],
        example: str = "",
    ) -> ProceduralPattern:
        """手順を追加"""
        _ensure_dirs()
        pattern_id = f"proc_{name[:20].replace(' ', '_')}_{int(time.time())}"
        pattern = ProceduralPattern(
            pattern_id=pattern_id,
            name=name,
            description=description,
            trigger_keywords=trigger_keywords,
            steps=steps,
            example=example,
        )
        path = PROCEDURAL_DIR / f"{pattern_id}.json"
        path.write_text(json.dumps(asdict(pattern), ensure_ascii=False, indent=2))
        return pattern

    def match(self, query: str) -> list[ProceduralPattern]:
        """クエリに一致する手順を返す"""
        _ensure_dirs()
        q = query.lower()
        results = []
        for path in PROCEDURAL_DIR.glob("*.json"):
            try:
                p = ProceduralPattern(**json.loads(path.read_text()))
            except Exception:
                continue
            score = sum(1 for kw in p.trigger_keywords if kw.lower() in q)
            if score > 0:
                results.append((score, p))
        results.sort(key=lambda x: x[0], reverse=True)
        return [p for _, p in results]

    def get_context(self, query: str, max_chars: int = 1000) -> str:
        """マッチした手順をコンテキスト文字列に変換"""
        patterns = self.match(query)[:2]
        if not patterns:
            return ""
        lines = ["[手順パターン]"]
        total = 0
        for p in patterns:
            block = f"- {p.name}: {p.description}\n  手順: " + " → ".join(p.steps[:3])
            if total + len(block) > max_chars:
                break
            lines.append(block)
            total += len(block)
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 統合インターフェース
# ---------------------------------------------------------------------------

class MemorySystem:
    """
    全メモリタイプを統合したシステム。

    使い方:
        mem = MemorySystem()
        # コンテキスト取得
        context = mem.get_context("衛星熱制御の設計")
        # 作業記憶に追加
        mem.working.add("熱放射板の面積は 0.5 m² と決定", category="decision")
        # エピソードを保存
        mem.episodic.store(messages, session_id="session_001", client=client, model="gemma2:27b")
        # 知識を追加
        mem.semantic.add("太陽定数", "1361 W/m²", category="formula", source="JAXA")
    """

    def __init__(self):
        self.working    = WorkingMemory()
        self.episodic   = EpisodicMemory()
        self.semantic   = SemanticMemory()
        self.procedural = ProceduralMemory()

    def get_context(self, query: str, max_total_chars: int = 4000) -> str:
        """
        全メモリから関連情報を取得してLLMコンテキストを構築。

        Args:
            query: 現在のユーザークエリ
            max_total_chars: 全体の最大文字数

        Returns:
            コンテキスト文字列
        """
        budget_per_type = max_total_chars // 4
        parts = []

        # 1. 作業記憶 (最優先)
        wm = self.working.get_context(max_chars=budget_per_type)
        if wm:
            parts.append(wm)

        # 2. 意味記憶 (事実・定義)
        sm = self.semantic.get_context(query, max_chars=budget_per_type)
        if sm:
            parts.append(sm)

        # 3. 手順記憶
        pm = self.procedural.get_context(query, max_chars=budget_per_type)
        if pm:
            parts.append(pm)

        # 4. エピソード記憶 (最後に追加)
        em = self.episodic.get_context(query, max_chars=budget_per_type)
        if em:
            parts.append(em)

        return "\n\n".join(parts)

    def auto_extract_and_store(
        self,
        messages: list[dict],
        client: Optional[OpenAI] = None,
        model: str = "",
        session_id: str = "",
    ):
        """
        会話からメモリを自動抽出して保存。
        セッション終了時に呼ぶことを想定。

        Args:
            messages: 会話メッセージリスト
            client: LLMクライアント
            model: モデル名
            session_id: セッションID
        """
        # エピソードとして保存
        self.episodic.store(messages, session_id=session_id, client=client, model=model)

        # 重要な決定事項を作業記憶に追加
        for msg in messages:
            if msg.get("role") == "assistant":
                content = str(msg.get("content", ""))
                # 「決定」「合意」「確認」を含む文章を記憶
                for line in content.splitlines():
                    if any(kw in line for kw in ["決定", "合意", "確認", "採用", "設定"]):
                        if len(line) < 150:
                            self.working.add(line.strip(), category="decision", importance=2.0)

    def print_stats(self):
        """統計情報を表示"""
        working_count = len(self.working._items)
        episode_count = len(list(EPISODE_DIR.glob("*.json")))
        semantic_count = len(list(SEMANTIC_DIR.glob("*.json")))
        procedural_count = len(list(PROCEDURAL_DIR.glob("*.json")))

        print("=== Memory Stats ===")
        print(f"Working:    {working_count} items")
        print(f"Episodic:   {episode_count} episodes")
        print(f"Semantic:   {semantic_count} facts")
        print(f"Procedural: {procedural_count} patterns")


# ---------------------------------------------------------------------------
# ユーティリティ
# ---------------------------------------------------------------------------

def _auto_tags_from_text(text: str) -> list[str]:
    tag_rules = {
        "space":    ["衛星", "宇宙", "orbit", "spacecraft"],
        "coding":   ["コード", "python", "実装"],
        "thermal":  ["熱", "温度", "thermal"],
        "power":    ["電力", "太陽電池", "power"],
        "attitude": ["姿勢", "attitude", "制御"],
    }
    t = text.lower()
    return [tag for tag, kws in tag_rules.items() if any(kw in t for kw in kws)]


# ---------------------------------------------------------------------------
# 宇宙ドメイン初期知識のシード
# ---------------------------------------------------------------------------

SPACE_KNOWLEDGE_SEED = [
    {
        "subject": "太陽定数",
        "content": "太陽から1AUでの太陽放射強度: 約1361 W/m²",
        "category": "formula",
        "source": "NASA",
        "tags": ["thermal", "power"],
    },
    {
        "subject": "LEO軌道高度",
        "content": "低軌道 (LEO): 高度200-2000 km、周期約90-127分",
        "category": "definition",
        "source": "JAXA",
        "tags": ["orbit"],
    },
    {
        "subject": "宇宙温度環境",
        "content": "LEOでの温度サイクル: 日照時+120℃、日陰時-100℃程度、1軌道で1サイクル",
        "category": "constraint",
        "source": "JERG",
        "tags": ["thermal"],
    },
    {
        "subject": "宇宙放射線",
        "content": "LEOでの全吸収線量: 10-100 krad/year (軌道・シールド依存)",
        "category": "constraint",
        "source": "JAXA",
        "tags": ["radiation"],
    },
    {
        "subject": "太陽電池変換効率",
        "content": "標準的な宇宙用 GaAs: 28-30%、Si: 15-18%、マルチジャンクション: 30-40%",
        "category": "formula",
        "source": "一般的な値",
        "tags": ["power"],
    },
    {
        "subject": "スラスタ比推力",
        "content": "化学推進: 200-450 s、電気推進(イオン): 1000-10000 s",
        "category": "formula",
        "source": "一般的な値",
        "tags": ["propulsion"],
    },
    {
        "subject": "JERG-0-022",
        "content": "JAXA 電子機器一般規格 (JERG-0-022): 宇宙用電子機器の設計・試験要求",
        "category": "standard",
        "source": "JAXA",
        "tags": ["standard", "electronics"],
    },
]


def seed_space_knowledge(memory: SemanticMemory):
    """宇宙ドメインの初期知識をシード"""
    existing = {f.subject for f in memory.search("", limit=1000)}
    added = 0
    for fact_dict in SPACE_KNOWLEDGE_SEED:
        if fact_dict["subject"] not in existing:
            memory.add(**fact_dict)
            added += 1
    return added


# ---------------------------------------------------------------------------
# クイックテスト
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mem = MemorySystem()

    # シード
    added = seed_space_knowledge(mem.semantic)
    print(f"シード追加: {added} 件")

    # 作業記憶テスト
    mem.working.add("熱放射板の面積を 0.5 m² と仮決定", category="decision", importance=3.0)
    mem.working.add("太陽電池は Si タイプを使用", category="decision", importance=2.5)

    # 手順パターン追加
    mem.procedural.add(
        name="熱設計手順",
        description="衛星熱設計の基本フロー",
        steps=["熱入力の推定", "熱収支計算", "温度推定", "制御方法選定", "解析検証"],
        trigger_keywords=["熱設計", "熱制御", "温度設計"],
        example="太陽電池パネルの熱設計: 太陽入力1361*A_panel*α を放射 σε*A*T^4 で放散",
    )

    # コンテキスト取得テスト
    query = "衛星の熱制御設計を教えて"
    context = mem.get_context(query)
    print(f"\n[コンテキスト for: {query!r}]")
    print(context)

    mem.print_stats()
