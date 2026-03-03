"""
section_indexer.py - 階層構造インデックス

深い階層構造（3.2.4.5章のような5階層以上）を持つ文書を
効率的に検索・ナビゲートするためのインデックスを構築する。

機能:
  1. 階層構造のフラット化（全セクションをフラットなリストとして検索可能に）
  2. セクションメタデータの付与（概要、キーワード、対象読者）
  3. ブレッドクラム生成
  4. セクション間クロスリファレンス自動生成
  5. 階層をたどるナビゲーション API
  6. BM25インデックスへの統合
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


# =========================================================
# データモデル
# =========================================================

@dataclass
class SectionEntry:
    """フラット化されたセクションエントリ（インデックスの1件）"""
    doc_id: str
    section_id: str           # 例: "3.2.4"
    title: str
    level: int                # 1〜5+
    breadcrumb: str           # 例: "第3章 安全管理 > 3.2 手順 > 3.2.4 緊急時対応"
    text_original: str
    text_plain: str = ""      # 平易化テキスト
    summary: str = ""
    keywords: list[str] = field(default_factory=list)
    target_audience: str = ""
    parent_section_id: str = ""
    children_section_ids: list[str] = field(default_factory=list)
    cross_refs: list[str] = field(default_factory=list)   # 参照先の (doc_id, section_id)
    page_number: int | None = None
    chunk_ids: list[str] = field(default_factory=list)    # 対応するchunk_id群

    @property
    def full_id(self) -> str:
        """一意のID: "JERG-2-100#3.2.4" 形式"""
        return f"{self.doc_id}#{self.section_id}"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["full_id"] = self.full_id
        return d

    def searchable_text(self) -> str:
        """
        BM25やベクトル検索に使うテキスト。
        ブレッドクラム + タイトル + 本文 + キーワード + 要約を連結。
        上位の文脈情報をプレフィックスとして含めることで、
        「3.2.4だけでなく第3章の文脈も含む検索」を実現する。
        """
        parts = [
            f"[{self.breadcrumb}]",
            self.text_plain or self.text_original,
            " ".join(self.keywords),
            self.summary,
        ]
        return "\n".join(p for p in parts if p)


# =========================================================
# インデックス構築
# =========================================================

class SectionIndex:
    """
    階層構造インデックスの主クラス。

    使用例:
        idx = SectionIndex()
        idx.build_from_chunks(chunks)
        results = idx.search("緊急時対応手順", top_k=5)
        nav = idx.get_navigation("JERG-2-100", "3.2.4")
    """

    def __init__(self):
        # 全エントリ: full_id → SectionEntry
        self.entries: dict[str, SectionEntry] = {}
        # doc_id → {section_id → SectionEntry}
        self._doc_sections: dict[str, dict[str, SectionEntry]] = defaultdict(dict)
        # BM25用インデックス（簡易版）
        self._bm25_index: dict[str, list[str]] | None = None

    # ---------- 構築 ----------

    def add_entry(self, entry: SectionEntry):
        self.entries[entry.full_id] = entry
        self._doc_sections[entry.doc_id][entry.section_id] = entry
        self._bm25_index = None  # キャッシュをクリア

    def build_from_chunks(
        self,
        chunks: list[dict],
        verbose: bool = True,
    ):
        """
        chunks.json 形式のデータからセクションインデックスを構築する。

        チャンクをセクション番号でグループ化し、
        同じセクション番号のチャンクを連結して1エントリとする。
        """
        # セクション番号でチャンクをグループ化
        section_chunks: dict[str, list[dict]] = defaultdict(list)

        for chunk in chunks:
            doc_id = chunk["doc_id"]
            sec_num = chunk.get("section_number", "")
            if not sec_num:
                # セクション番号なし → doc_id#0 としてまとめる
                sec_num = "0"
            key = f"{doc_id}#{sec_num}"
            section_chunks[key].append(chunk)

        if verbose:
            print(f"[section_indexer] {len(section_chunks)} セクションを検出")

        # 各セクションのエントリを構築
        for key, group in section_chunks.items():
            doc_id, sec_num = key.split("#", 1)
            first_chunk = group[0]

            # テキストを連結
            text_combined = "\n\n".join(c["text"] for c in group)
            text_plain_combined = "\n\n".join(c.get("text_plain", "") for c in group)

            # キーワードをまとめる
            keywords = []
            for c in group:
                keywords.extend(c.get("keywords", []))
            keywords = list(dict.fromkeys(keywords))  # 重複除去・順序保持

            # クロスリファレンスをまとめる
            cross_refs = []
            for c in group:
                cross_refs.extend(c.get("cross_refs", []))
            cross_refs = list(set(cross_refs))

            entry = SectionEntry(
                doc_id=doc_id,
                section_id=sec_num,
                title=first_chunk.get("section_title", ""),
                level=sec_num.count('.') + 1 if sec_num != "0" else 0,
                breadcrumb="",  # 後で設定
                text_original=text_combined,
                text_plain=text_plain_combined,
                summary=first_chunk.get("summary", ""),
                keywords=keywords,
                target_audience=first_chunk.get("target_audience", ""),
                cross_refs=cross_refs,
                page_number=first_chunk.get("page_number"),
                chunk_ids=[c["chunk_id"] for c in group],
            )
            self.add_entry(entry)

        # 階層関係を構築
        self._build_hierarchy()

        if verbose:
            print(f"  エントリ数: {len(self.entries)}")
            depths = [e.level for e in self.entries.values()]
            if depths:
                print(f"  最大深さ: {max(depths)}, 平均: {sum(depths)/len(depths):.1f}")

    def _build_hierarchy(self):
        """
        セクション番号の包含関係から親子関係とブレッドクラムを設定する。

        "3.2.4" の親は "3.2"、"3.2"の親は "3" という規則を使う。
        """
        for full_id, entry in self.entries.items():
            sec_num = entry.section_id
            if '.' not in sec_num:
                # トップレベル
                entry.parent_section_id = ""
                entry.breadcrumb = f"{sec_num} {entry.title}"
            else:
                parent_num = sec_num.rsplit('.', 1)[0]
                parent = self._doc_sections[entry.doc_id].get(parent_num)
                if parent:
                    entry.parent_section_id = parent_num
                    parent.children_section_ids.append(sec_num)
                    entry.breadcrumb = parent.breadcrumb + f" > {sec_num} {entry.title}"
                else:
                    entry.breadcrumb = f"{sec_num} {entry.title}"

    # ---------- 検索 ----------

    def search(
        self,
        query: str,
        top_k: int = 10,
        doc_id: str | None = None,
        level_max: int | None = None,
    ) -> list[dict]:
        """
        セクションインデックスをBM25で検索する。

        Args:
            query: 検索クエリ
            top_k: 返す件数
            doc_id: 文書IDフィルタ
            level_max: この階層以下のセクションのみ（1=章レベル、3=3階層まで）

        Returns:
            [{"section": SectionEntry, "score": float}]
        """
        try:
            from rank_bm25 import BM25Okapi
        except ImportError:
            return self._simple_keyword_search(query, top_k, doc_id, level_max)

        # フィルタリング
        candidates = list(self.entries.values())
        if doc_id:
            candidates = [e for e in candidates if e.doc_id == doc_id]
        if level_max:
            candidates = [e for e in candidates if e.level <= level_max]

        if not candidates:
            return []

        # コーパス構築（検索用テキスト）
        corpus = [self._tokenize(e.searchable_text()) for e in candidates]
        bm25 = BM25Okapi(corpus)
        query_tokens = self._tokenize(query)
        scores = bm25.get_scores(query_tokens)

        # スコア順にソート
        ranked = sorted(zip(candidates, scores), key=lambda x: x[1], reverse=True)
        return [
            {"section": e, "score": float(s)}
            for e, s in ranked[:top_k]
            if s > 0
        ]

    def _simple_keyword_search(
        self,
        query: str,
        top_k: int,
        doc_id: str | None,
        level_max: int | None,
    ) -> list[dict]:
        """rank_bm25が使えない場合の簡易キーワード検索"""
        results = []
        query_terms = set(query.lower().split())

        for entry in self.entries.values():
            if doc_id and entry.doc_id != doc_id:
                continue
            if level_max and entry.level > level_max:
                continue

            text = entry.searchable_text().lower()
            score = sum(1 for term in query_terms if term in text)
            if score > 0:
                results.append({"section": entry, "score": float(score)})

        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_k]

    def _tokenize(self, text: str) -> list[str]:
        """テキストをトークン分割（日本語は文字n-gram）"""
        # 英数字はスペース分割
        tokens = re.findall(r'[a-zA-Z0-9]+|[\u3040-\u9fff]+', text)
        # 日本語部分はbigramに分割
        jp_bigrams = []
        for token in tokens:
            if re.search(r'[\u3040-\u9fff]', token) and len(token) >= 2:
                jp_bigrams.extend(token[i:i+2] for i in range(len(token)-1))
            else:
                jp_bigrams.append(token.lower())
        return jp_bigrams

    # ---------- ナビゲーション API ----------

    def get_navigation(self, doc_id: str, section_id: str) -> dict:
        """
        指定セクションのナビゲーション情報を返す。

        Returns:
            {
              "current": SectionEntry,
              "parent": SectionEntry | None,
              "children": [SectionEntry, ...],
              "siblings": [SectionEntry, ...],
              "breadcrumb": str,
              "breadcrumb_path": [(section_id, title), ...]
            }
        """
        full_id = f"{doc_id}#{section_id}"
        current = self.entries.get(full_id)
        if not current:
            return {}

        # 親
        parent = None
        if current.parent_section_id:
            parent_full = f"{doc_id}#{current.parent_section_id}"
            parent = self.entries.get(parent_full)

        # 子
        children = []
        for child_sec_id in current.children_section_ids:
            child_full = f"{doc_id}#{child_sec_id}"
            child = self.entries.get(child_full)
            if child:
                children.append(child)

        # 兄弟（同じ親を持つ）
        siblings = []
        if parent:
            for sib_sec_id in parent.children_section_ids:
                if sib_sec_id != section_id:
                    sib_full = f"{doc_id}#{sib_sec_id}"
                    sib = self.entries.get(sib_full)
                    if sib:
                        siblings.append(sib)

        # ブレッドクラムパス（クリック可能なリスト形式）
        breadcrumb_path = self._get_breadcrumb_path(doc_id, section_id)

        return {
            "current": current,
            "parent": parent,
            "children": children,
            "siblings": siblings,
            "breadcrumb": current.breadcrumb,
            "breadcrumb_path": breadcrumb_path,
        }

    def _get_breadcrumb_path(self, doc_id: str, section_id: str) -> list[tuple[str, str]]:
        """ブレッドクラムを (section_id, title) のリストとして返す"""
        path = []
        current_id = section_id

        while current_id:
            full_id = f"{doc_id}#{current_id}"
            entry = self.entries.get(full_id)
            if not entry:
                break
            path.insert(0, (current_id, entry.title))
            current_id = entry.parent_section_id

        return path

    def get_toc(self, doc_id: str, max_level: int = 3) -> list[dict]:
        """
        文書の目次を階層構造で返す。

        Returns:
            [{"section_id": ..., "title": ..., "level": ..., "breadcrumb": ...}]
        """
        entries = [
            e for e in self.entries.values()
            if e.doc_id == doc_id and e.level <= max_level
        ]
        entries.sort(key=lambda e: [
            int(p) if p.isdigit() else 0
            for p in e.section_id.replace('第', '').replace('章', '').replace('節', '').split('.')
        ])

        return [
            {
                "section_id": e.section_id,
                "title": e.title,
                "level": e.level,
                "breadcrumb": e.breadcrumb,
                "summary": e.summary,
                "has_children": bool(e.children_section_ids),
            }
            for e in entries
        ]

    def get_cross_ref_sections(self, doc_id: str, section_id: str) -> list[dict]:
        """
        指定セクションが参照する他文書のセクション情報を返す。
        クロスリファレンスをたどった関連情報の提示に使用。
        """
        full_id = f"{doc_id}#{section_id}"
        entry = self.entries.get(full_id)
        if not entry:
            return []

        related = []
        for ref_doc_id in entry.cross_refs:
            # 参照先文書の全セクションを取得
            ref_sections = list(self._doc_sections.get(ref_doc_id, {}).values())
            if ref_sections:
                related.append({
                    "doc_id": ref_doc_id,
                    "sections": [
                        {"section_id": s.section_id, "title": s.title, "summary": s.summary}
                        for s in ref_sections[:5]  # 先頭5セクション
                    ],
                })

        return related

    # ---------- 保存・読み込み ----------

    def save(self, path: str | Path):
        """インデックスをJSONで保存"""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = [e.to_dict() for e in self.entries.values()]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"Section index saved: {path} ({len(data)} entries)")

    @classmethod
    def load(cls, path: str | Path) -> "SectionIndex":
        """保存済みインデックスを読み込む"""
        path = Path(path)
        with open(path, encoding="utf-8") as f:
            data = json.load(f)

        idx = cls()
        for d in data:
            d.pop("full_id", None)
            entry = SectionEntry(**d)
            idx.entries[entry.full_id] = entry
            idx._doc_sections[entry.doc_id][entry.section_id] = entry

        return idx


# =========================================================
# スタンドアロン実行
# =========================================================

if __name__ == "__main__":
    # テスト用サンプルデータ
    sample_chunks = [
        {
            "doc_id": "JERG-2-100",
            "chunk_id": "chunk_001",
            "section_number": "3",
            "section_title": "要求事項",
            "text": "宇宙機の熱制御システムに関する要求事項を規定する。",
            "keywords": ["熱制御", "宇宙機"],
        },
        {
            "doc_id": "JERG-2-100",
            "chunk_id": "chunk_002",
            "section_number": "3.1",
            "section_title": "熱設計要件",
            "text": "宇宙機の熱制御システムは、軌道上における全運用モードにおいて搭載機器の温度を許容範囲内に維持しなければならない。",
            "keywords": ["熱設計", "温度", "軌道"],
        },
        {
            "doc_id": "JERG-2-100",
            "chunk_id": "chunk_003",
            "section_number": "3.1.1",
            "section_title": "温度許容範囲",
            "text": "各機器の動作温度範囲はDTRとして規定し、マージンを5℃以上確保すること。",
            "keywords": ["DTR", "温度マージン"],
        },
        {
            "doc_id": "JERG-2-100",
            "chunk_id": "chunk_004",
            "section_number": "3.2",
            "section_title": "熱解析要件",
            "text": "熱数学モデル（TMM）を用いた軌道熱環境シミュレーションを実施すること。",
            "cross_refs": ["JERG-0-051"],
            "keywords": ["TMM", "熱解析", "シミュレーション"],
        },
    ]

    idx = SectionIndex()
    idx.build_from_chunks(sample_chunks, verbose=True)

    print("\n=== 目次（3階層まで） ===")
    toc = idx.get_toc("JERG-2-100", max_level=3)
    for item in toc:
        indent = "  " * (item["level"] - 1)
        print(f"{indent}{item['section_id']} {item['title']}")

    print("\n=== 検索: 「温度管理の範囲は？」 ===")
    results = idx.search("温度管理の範囲", top_k=3)
    for r in results:
        s = r["section"]
        print(f"  [{s.section_id}] {s.title} (score={r['score']:.2f})")
        print(f"    ブレッドクラム: {s.breadcrumb}")

    print("\n=== ナビゲーション: 3.1.1 ===")
    nav = idx.get_navigation("JERG-2-100", "3.1.1")
    if nav:
        print(f"  現在: {nav['current'].section_id} {nav['current'].title}")
        if nav["parent"]:
            print(f"  親:   {nav['parent'].section_id} {nav['parent'].title}")
        print(f"  パス: {nav['breadcrumb_path']}")
