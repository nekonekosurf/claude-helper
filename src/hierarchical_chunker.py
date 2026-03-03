"""
階層的チャンキング (Hierarchical Chunking)

宇宙分野の硬い文書向け高度チャンキング戦略:
- Parent-Child チャンキング（親=セクション全体, 子=段落）
- 階層メタデータの付与（章・節の関係をJSONで保存）
- セクション番号による自動親子関係検出
- Agentic Chunking（LLMが分割境界を判断）
"""

from __future__ import annotations

import re
import json
import hashlib
from dataclasses import dataclass, field
from typing import Any


@dataclass
class HierarchicalChunk:
    """階層構造を持つチャンク"""
    chunk_id: str
    text: str
    depth: int                          # 0=文書, 1=章, 2=節, 3=項, 4=段落
    hierarchy: list[str]                # ["第3章 システム設計", "3.2 安全設計", "3.2.4 冗長化方針"]
    parent: str | None                  # 親チャンクID
    children: list[str]                 # 子チャンクIDリスト
    summary: str = ""                   # セクションのサマリー
    keywords: list[str] = field(default_factory=list)
    section_number: str = ""            # "3.2.4"
    page_refs: list[int] = field(default_factory=list)
    cross_refs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "text": self.text,
            "depth": self.depth,
            "hierarchy": self.hierarchy,
            "parent": self.parent,
            "children": self.children,
            "summary": self.summary,
            "keywords": self.keywords,
            "section_number": self.section_number,
            "page_refs": self.page_refs,
            "cross_refs": self.cross_refs
        }

    @property
    def hierarchy_path(self) -> str:
        """階層パスを文字列で返す（検索結果表示用）"""
        return " > ".join(self.hierarchy)


class HierarchicalChunker:
    """
    階層的チャンキングの実装

    特徴:
    - 宇宙文書（JERG等）の章番号パターンを認識
    - Parent-Child 関係を自動構築
    - 子チャンクで検索し、親チャンクのコンテキストを付与
    """

    # 宇宙文書の典型的なセクションパターン
    SECTION_PATTERNS = [
        # 1. の形式（章）
        (re.compile(r'^(\d+)\.\s+(.+)$', re.MULTILINE), 1),
        # 1.2 の形式（節）
        (re.compile(r'^(\d+\.\d+)\s+(.+)$', re.MULTILINE), 2),
        # 1.2.3 の形式（項）
        (re.compile(r'^(\d+\.\d+\.\d+)\s+(.+)$', re.MULTILINE), 3),
        # 1.2.3.4 の形式（細項）
        (re.compile(r'^(\d+\.\d+\.\d+\.\d+)\s+(.+)$', re.MULTILINE), 4),
        # 第N章 の形式
        (re.compile(r'^第(\d+)章\s+(.+)$', re.MULTILINE), 1),
        # (N) の形式
        (re.compile(r'^\((\d+)\)\s+(.+)$', re.MULTILINE), 3),
    ]

    def __init__(
        self,
        parent_chunk_size: int = 1500,  # 親チャンク（セクション全体）の最大サイズ
        child_chunk_size: int = 400,    # 子チャンク（段落）の最大サイズ
        overlap: int = 50,              # チャンク間オーバーラップ
        llm_client: Any = None          # Agentic Chunking用（Noneの場合はルールベース）
    ) -> None:
        self.parent_chunk_size = parent_chunk_size
        self.child_chunk_size = child_chunk_size
        self.overlap = overlap
        self.llm = llm_client
        self._chunks: dict[str, HierarchicalChunk] = {}

    def chunk_document(
        self,
        text: str,
        doc_id: str = "doc",
        doc_title: str = ""
    ) -> list[HierarchicalChunk]:
        """
        文書を階層的にチャンキング

        Args:
            text: 文書テキスト
            doc_id: 文書ID
            doc_title: 文書タイトル

        Returns:
            HierarchicalChunk のリスト（親子関係付き）
        """
        self._chunks = {}

        # 文書ルートチャンク
        root_id = self._make_id(doc_id, "root")
        root = HierarchicalChunk(
            chunk_id=root_id,
            text=doc_title or doc_id,
            depth=0,
            hierarchy=[doc_title or doc_id],
            parent=None,
            children=[]
        )
        self._chunks[root_id] = root

        # セクション分割
        sections = self._split_by_sections(text)

        if not sections:
            # セクションが検出できない場合は段落分割にフォールバック
            sections = self._split_by_paragraphs(text)

        # 階層構造の構築
        self._build_hierarchy(sections, doc_id, root_id, [doc_title or doc_id])

        # 親チャンクから子チャンクを生成
        self._generate_children()

        return list(self._chunks.values())

    def _split_by_sections(self, text: str) -> list[dict[str, Any]]:
        """セクション番号パターンで文書を分割"""
        # 全セクション境界を検出
        boundaries: list[tuple[int, str, str, int]] = []  # (pos, number, title, depth)

        for pattern, depth in self.SECTION_PATTERNS:
            for m in pattern.finditer(text):
                number = m.group(1)
                title = m.group(2).strip()
                # タイトルが長すぎる場合は無視（誤検出対策）
                if len(title) > 100:
                    continue
                boundaries.append((m.start(), number, title, depth))

        if not boundaries:
            return []

        # 位置でソート
        boundaries.sort(key=lambda x: x[0])

        sections = []
        for i, (pos, number, title, depth) in enumerate(boundaries):
            end = boundaries[i + 1][0] if i + 1 < len(boundaries) else len(text)
            section_text = text[pos:end].strip()
            if len(section_text) < 20:
                continue
            sections.append({
                "number": number,
                "title": title,
                "text": section_text,
                "depth": depth,
                "start": pos
            })

        return sections

    def _split_by_paragraphs(self, text: str) -> list[dict[str, Any]]:
        """段落で分割（セクションが検出できない場合のフォールバック）"""
        paragraphs = re.split(r'\n{2,}', text)
        sections = []
        for i, para in enumerate(paragraphs):
            para = para.strip()
            if len(para) < 30:
                continue
            sections.append({
                "number": str(i + 1),
                "title": para[:50] + ("..." if len(para) > 50 else ""),
                "text": para,
                "depth": 1,
                "start": 0
            })
        return sections

    def _build_hierarchy(
        self,
        sections: list[dict[str, Any]],
        doc_id: str,
        root_id: str,
        root_hierarchy: list[str]
    ) -> None:
        """セクションから階層ツリーを構築"""
        # 深さ順に処理するためのスタック
        # stack: [(chunk_id, depth, hierarchy_path)]
        stack: list[tuple[str, int, list[str]]] = [(root_id, 0, root_hierarchy)]

        for section in sections:
            depth = section["depth"]
            number = section["number"]
            title = section["title"]
            text = section["text"]

            # 適切な親を見つける（自分より浅い深さの最後の要素）
            while len(stack) > 1 and stack[-1][1] >= depth:
                stack.pop()

            parent_id, parent_depth, parent_hierarchy = stack[-1]
            hierarchy = parent_hierarchy + [f"{number} {title}"]

            chunk_id = self._make_id(doc_id, number)
            chunk = HierarchicalChunk(
                chunk_id=chunk_id,
                text=text,
                depth=depth,
                hierarchy=hierarchy,
                parent=parent_id,
                children=[],
                section_number=number
            )

            # 相互参照の検出
            chunk.cross_refs = self._extract_cross_refs(text)

            self._chunks[chunk_id] = chunk

            # 親に子として登録
            if parent_id in self._chunks:
                self._chunks[parent_id].children.append(chunk_id)

            stack.append((chunk_id, depth, hierarchy))

    def _generate_children(self) -> None:
        """
        親チャンクが大きい場合、子チャンク（段落単位）を生成

        検索は子チャンクで行い、コンテキストとして親チャンクも返す
        Parent-Child Retrieval パターンの実装
        """
        parent_chunks = [
            c for c in self._chunks.values()
            if c.depth >= 1 and len(c.text) > self.child_chunk_size
        ]

        for parent in parent_chunks:
            child_texts = self._split_text_with_overlap(
                parent.text,
                self.child_chunk_size,
                self.overlap
            )

            if len(child_texts) <= 1:
                continue

            for i, child_text in enumerate(child_texts):
                child_id = f"{parent.chunk_id}_child_{i}"
                child = HierarchicalChunk(
                    chunk_id=child_id,
                    text=child_text,
                    depth=parent.depth + 1,
                    hierarchy=parent.hierarchy + [f"段落 {i + 1}"],
                    parent=parent.chunk_id,
                    children=[],
                    section_number=f"{parent.section_number}.{i + 1}"
                )
                self._chunks[child_id] = child
                parent.children.append(child_id)

    def _split_text_with_overlap(
        self,
        text: str,
        chunk_size: int,
        overlap: int
    ) -> list[str]:
        """オーバーラップ付きテキスト分割"""
        if len(text) <= chunk_size:
            return [text]

        chunks = []
        start = 0
        while start < len(text):
            end = start + chunk_size
            # 文の境界で区切る
            if end < len(text):
                # 句点・改行を探す
                for boundary in ['。\n', '。', '\n', '、']:
                    pos = text.rfind(boundary, start, end)
                    if pos > start:
                        end = pos + len(boundary)
                        break
            chunks.append(text[start:end].strip())
            start = end - overlap
            if start >= len(text):
                break

        return [c for c in chunks if c.strip()]

    def _extract_cross_refs(self, text: str) -> list[str]:
        """相互参照パターンの抽出"""
        refs = []
        patterns = [
            re.compile(r'(JERG[-\s]?\d+[-\s]?\d+[-\s]?\w*)'),
            re.compile(r'(第\d+[章節項](?:[\s　]*[\d\.]+)*)'),
            re.compile(r'(\d+\.\d+(?:\.\d+)*)項'),
        ]
        for pat in patterns:
            for m in pat.finditer(text):
                refs.append(m.group(1).strip())
        return list(set(refs))

    def _make_id(self, doc_id: str, section: str) -> str:
        raw = f"{doc_id}_{section}"
        return f"sec_{hashlib.md5(raw.encode()).hexdigest()[:8]}"

    def get_parent_context(
        self,
        chunk_id: str,
        include_siblings: bool = False
    ) -> str:
        """
        Parent-Child Retrieval: 子チャンクの親コンテキストを取得

        Args:
            chunk_id: 子チャンクのID
            include_siblings: 兄弟チャンク（同じ親の他の子）も含めるか

        Returns:
            親チャンクのテキスト（または親チャンクが存在しない場合は空文字）
        """
        if chunk_id not in self._chunks:
            return ""

        chunk = self._chunks[chunk_id]
        if not chunk.parent or chunk.parent not in self._chunks:
            return ""

        parent = self._chunks[chunk.parent]

        if include_siblings:
            # 兄弟チャンクも含める
            sibling_texts = []
            for sibling_id in parent.children:
                if sibling_id != chunk_id and sibling_id in self._chunks:
                    sibling_texts.append(self._chunks[sibling_id].text[:200])
            return parent.text
        else:
            return parent.text

    def format_result_with_hierarchy(
        self,
        chunk_id: str
    ) -> dict[str, Any]:
        """
        検索結果に階層パスを付与したフォーマット

        Returns:
            {
                "chunk_id": ...,
                "text": ...,
                "hierarchy_path": "第3章 システム設計 > 3.2 安全設計 > 3.2.4 冗長化方針",
                "parent_text": ...,  # 親チャンクのテキスト
                "depth": 3
            }
        """
        if chunk_id not in self._chunks:
            return {}

        chunk = self._chunks[chunk_id]
        parent_text = self.get_parent_context(chunk_id)

        return {
            "chunk_id": chunk_id,
            "text": chunk.text,
            "hierarchy_path": chunk.hierarchy_path,
            "hierarchy": chunk.hierarchy,
            "depth": chunk.depth,
            "section_number": chunk.section_number,
            "parent_text": parent_text,
            "cross_refs": chunk.cross_refs,
            "metadata": chunk.to_dict()
        }

    def export_hierarchy_json(self) -> list[dict[str, Any]]:
        """全チャンクを階層メタデータ付きでエクスポート"""
        return [chunk.to_dict() for chunk in self._chunks.values()]


class AgenticChunker:
    """
    Agentic Chunking: LLMが最適な分割境界を判断

    通常のルールベース分割では対応できない複雑な文書構造に対応
    コストが高いため、重要文書のみに使用推奨
    """

    def __init__(self, llm_client: Any) -> None:
        self.llm = llm_client

    def chunk(
        self,
        text: str,
        doc_id: str = "doc",
        target_chunk_size: int = 500
    ) -> list[dict[str, Any]]:
        """
        LLMを使って最適なチャンク境界を決定

        Args:
            text: 分割対象テキスト
            doc_id: 文書ID
            target_chunk_size: 目標チャンクサイズ（文字数）

        Returns:
            チャンクのリスト
        """
        # テキストを粗く分割してからLLMに最適化させる
        paragraphs = re.split(r'\n{2,}', text)
        paragraphs = [p.strip() for p in paragraphs if p.strip()]

        prompt = f"""以下の段落リストを、意味的に一貫したチャンクにグループ化してください。
各チャンクは約{target_chunk_size}文字を目標とし、トピックの境界で区切ってください。

段落リスト（番号付き）:
{chr(10).join(f'{i}. {p[:200]}...' if len(p) > 200 else f'{i}. {p}' for i, p in enumerate(paragraphs))}

JSON形式で出力してください:
{{
  "chunks": [
    {{
      "paragraph_indices": [0, 1, 2],
      "title": "このチャンクのタイトル",
      "summary": "このチャンクの内容サマリー（1文）"
    }}
  ]
}}"""

        try:
            response = self.llm.complete(prompt)
            data = json.loads(response)
            chunks = []
            for i, chunk_def in enumerate(data.get("chunks", [])):
                indices = chunk_def.get("paragraph_indices", [])
                selected_paras = [paragraphs[j] for j in indices if j < len(paragraphs)]
                combined_text = "\n\n".join(selected_paras)
                chunks.append({
                    "chunk_id": f"{doc_id}_agentic_{i}",
                    "text": combined_text,
                    "title": chunk_def.get("title", ""),
                    "summary": chunk_def.get("summary", ""),
                    "paragraph_indices": indices,
                    "metadata": {
                        "chunking_method": "agentic",
                        "doc_id": doc_id
                    }
                })
            return chunks
        except Exception as e:
            # フォールバック: 段落ごとにチャンク化
            print(f"[AgenticChunker] LLM分割失敗、フォールバック使用: {e}")
            return [
                {
                    "chunk_id": f"{doc_id}_para_{i}",
                    "text": p,
                    "metadata": {"chunking_method": "paragraph", "doc_id": doc_id}
                }
                for i, p in enumerate(paragraphs)
            ]
