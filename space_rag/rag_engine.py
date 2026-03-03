"""
宇宙分野RAGエンジン

ローカルLLMエージェント用のRetrieval-Augmented Generation エンジン。
BM25 + ベクトル検索のハイブリッド検索と、宇宙専門用語辞書を組み合わせる。

使い方:
    from space_rag.rag_engine import SpaceRAG

    rag = SpaceRAG()
    result = rag.retrieve("LEO衛星の熱制御設計について")
    context = rag.build_prompt_context(result)
"""

from __future__ import annotations

import json
import time
import logging
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

from space_rag.space_glossary import (
    build_context_header,
    extract_abbreviations_from_text,
    search_terms,
    expand_abbreviation,
)

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
SPACE_KB_DIR = DATA_DIR / "space_kb"


@dataclass
class RetrievedChunk:
    """検索結果の1チャンク"""
    doc_id: str
    chunk_id: str
    text: str
    score: float
    source: str        # データソース（nasa_ntrs, jaxa, arxiv等）
    url: str = ""      # 元文書のURL
    title: str = ""    # 文書タイトル
    methods: list[str] = field(default_factory=list)  # 使用した検索手法


@dataclass
class RAGResult:
    """RAG検索の結果セット"""
    query: str
    chunks: list[RetrievedChunk]
    glossary_context: str   # 専門用語コンテキスト
    domains: list[dict]     # 検出されたドメイン
    methods_used: list[str]
    elapsed_ms: float


class SpaceRAG:
    """
    宇宙分野特化RAGエンジン

    機能:
    1. ハイブリッド検索 (BM25 + Vector)
    2. 宇宙専門用語辞書による略語展開・クエリ強化
    3. ドメイン検出による絞り込み検索
    4. LLM向けコンテキスト生成

    設計方針:
    - 全機能がローカルで完結（外部APIなし）
    - 宇宙分野知識ベースとJERG文書を統合検索
    - スコアリングは既存 hybrid_search.py の仕組みを継承
    """

    def __init__(
        self,
        kb_dir: Path | None = None,
        top_k: int = 5,
        use_vector: bool = True,
        use_domain_filter: bool = True,
        vector_weight: float = 0.9,
        bm25_weight: float = 1.0,
    ):
        self.kb_dir = kb_dir or SPACE_KB_DIR
        self.top_k = top_k
        self.use_vector = use_vector
        self.use_domain_filter = use_domain_filter
        self.vector_weight = vector_weight
        self.bm25_weight = bm25_weight

        # 知識ベースのインデックス（lazy load）
        self._chunks: list[dict] | None = None
        self._bm25_index = None
        self._vector_index = None
        self._vector_model = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def retrieve(
        self,
        query: str,
        doc_filter: str | None = None,
        extra_context: str = "",
    ) -> RAGResult:
        """
        クエリに関連するチャンクを検索して返す。

        Args:
            query: ユーザーの質問文（日本語・英語どちらでも可）
            doc_filter: 文書IDフィルタ（部分一致）
            extra_context: 追加コンテキスト（会話履歴など）

        Returns:
            RAGResult オブジェクト
        """
        start = time.time()

        # Step 1: クエリ強化（略語展開 + 専門用語検索）
        enhanced_query = self._enhance_query(query)
        glossary_context = build_context_header(query)

        # Step 2: ドメイン検出（知識ベースとJERGを統合）
        domains = []
        if self.use_domain_filter:
            domains = self._detect_space_domains(enhanced_query)
            if not doc_filter and domains:
                # 上位ドメインの文書に絞り込む
                top_domain = domains[0]
                doc_filter = top_domain.get("doc_filter")

        # Step 3: ハイブリッド検索
        chunks, methods = self._search(enhanced_query, doc_filter)

        # Step 4: 宇宙知識ベースからも検索（独立インデックス）
        if self.kb_dir.exists():
            kb_chunks, kb_methods = self._search_space_kb(enhanced_query)
            # 両方の結果をマージ（スコア順）
            chunks = self._merge_chunk_results(chunks, kb_chunks)
            methods = list(set(methods + [m + "_kb" for m in kb_methods]))

        elapsed = (time.time() - start) * 1000

        return RAGResult(
            query=query,
            chunks=[
                RetrievedChunk(
                    doc_id=c.get("doc_id", ""),
                    chunk_id=c.get("chunk_id", ""),
                    text=c["text"],
                    score=c.get("score", 0.0),
                    source=c.get("source", "jerg"),
                    url=c.get("url", ""),
                    title=c.get("title", ""),
                    methods=c.get("methods", []),
                )
                for c in chunks[: self.top_k]
            ],
            glossary_context=glossary_context,
            domains=domains[:3],
            methods_used=methods,
            elapsed_ms=elapsed,
        )

    def build_prompt_context(
        self,
        result: RAGResult,
        max_tokens: int = 4000,
        include_glossary: bool = True,
    ) -> str:
        """
        RAG検索結果をLLMのプロンプトに注入するコンテキスト文字列を生成する。

        Args:
            result: retrieve()の戻り値
            max_tokens: コンテキストの最大トークン数（概算: 文字数/1.5）
            include_glossary: 専門用語コンテキストを含めるか

        Returns:
            プロンプトに挿入するコンテキスト文字列
        """
        sections = []
        char_budget = int(max_tokens * 1.5)  # 1トークン≈1.5文字として概算

        # 専門用語コンテキスト（先頭に配置）
        if include_glossary and result.glossary_context:
            sections.append(result.glossary_context)
            char_budget -= len(result.glossary_context)

        # 検索結果チャンク
        if result.chunks:
            sections.append("\n[参照文書]")
            for i, chunk in enumerate(result.chunks, 1):
                header = f"\n--- [{i}] {chunk.doc_id}"
                if chunk.title:
                    header += f": {chunk.title}"
                header += f" (score={chunk.score:.3f}) ---"

                text = chunk.text
                if len(text) > char_budget // max(len(result.chunks), 1):
                    text = text[: char_budget // max(len(result.chunks), 1)] + "..."

                sections.append(header)
                sections.append(text)

                if chunk.url:
                    sections.append(f"出典: {chunk.url}")

                char_budget -= len(header) + len(text)
                if char_budget <= 0:
                    break

        return "\n".join(sections)

    def as_tool(self, llm_client=None, model: str = "") -> dict:
        """
        LLMのツール定義として返す（OpenAI / Anthropic ToolUse形式）。
        LLMがRAGを自律的に呼び出せるようにする「ツールとしてのRAG」パターン。

        Returns:
            tools リストに追加できる辞書
        """
        return {
            "name": "search_space_knowledge",
            "description": (
                "宇宙・航空宇宙分野の専門知識ベースを検索する。"
                "軌道力学、熱制御、構造、推進、通信、電力、信頼性など"
                "宇宙機設計に関する質問に回答するために使用する。"
                "JAXA JERG文書、NASA技術報告書、ESA文書などを参照できる。"
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "検索クエリ（日本語・英語どちらでも可）"
                    },
                    "doc_filter": {
                        "type": "string",
                        "description": "特定の文書に絞り込む場合の文書IDプレフィックス（例: JERG-2-210）"
                    },
                },
                "required": ["query"],
            },
        }

    def search_as_tool(self, query: str, doc_filter: str | None = None) -> str:
        """
        ツール呼び出し時のハンドラ。LLMに返すテキストを生成する。
        """
        result = self.retrieve(query, doc_filter=doc_filter)
        context = self.build_prompt_context(result, max_tokens=2000)
        return context or "該当する知識ベースの情報が見つかりませんでした。"

    # ------------------------------------------------------------------
    # Private: クエリ強化
    # ------------------------------------------------------------------

    def _enhance_query(self, query: str) -> str:
        """
        宇宙専門用語辞書を使ってクエリを強化する。

        - 略語を正式名称に展開して追記
        - 関連英語用語を追記（ベクトル検索の精度向上）
        """
        enhanced = query

        # 略語を展開して追記
        abbrs = extract_abbreviations_from_text(query)
        if abbrs:
            expansions = [f"{a}: {info['full']}" for a, info in abbrs]
            enhanced += " " + "; ".join(expansions)

        # 日本語用語から英語を追記
        terms = search_terms(query)[:2]
        for t in terms:
            if t.en.lower() not in enhanced.lower():
                enhanced += f" {t.en}"

        return enhanced

    # ------------------------------------------------------------------
    # Private: ドメイン検出（宇宙分野特化）
    # ------------------------------------------------------------------

    def _detect_space_domains(self, query: str) -> list[dict]:
        """
        宇宙分野のドメインを検出する。
        JERGのdomain_map.yamlと宇宙用語辞書を組み合わせる。
        """
        # 既存のJERG domain detection を活用
        try:
            from src.guided_retrieval import detect_domain
            jerg_domains = detect_domain(query)
        except (ImportError, Exception):
            jerg_domains = []

        # 宇宙用語辞書からのドメイン検出を追加
        abbr_domains: dict[str, int] = {}
        for abbr, info in extract_abbreviations_from_text(query):
            cat = info.get("category", "")
            if cat:
                abbr_domains[cat] = abbr_domains.get(cat, 0) + 2

        term_domains: dict[str, int] = {}
        for term in search_terms(query)[:5]:
            if term.category:
                term_domains[term.category] = term_domains.get(term.category, 0) + 1

        # スペースKBのdoc_filterマッピング
        category_to_docs: dict[str, str] = {
            "thermal":    "thermal",
            "structures": "structures",
            "aocs":       "aocs",
            "propulsion": "propulsion",
            "power":      "eps",
            "comms":      "comms",
            "reliability":"reliability",
            "orbit":      "orbit",
            "payload":    "payload",
            "launch":     "launch",
        }

        space_domains = []
        all_cats = {**abbr_domains, **term_domains}
        for cat, score in sorted(all_cats.items(), key=lambda x: -x[1]):
            space_domains.append({
                "domain": cat,
                "score": float(score),
                "doc_filter": category_to_docs.get(cat),
            })

        # JERG domains を先頭に、宇宙用語由来のドメインを後に
        merged = jerg_domains + [d for d in space_domains if not any(j["domain"] == d["domain"] for j in jerg_domains)]
        return merged

    # ------------------------------------------------------------------
    # Private: 検索
    # ------------------------------------------------------------------

    def _search(
        self,
        query: str,
        doc_filter: str | None,
    ) -> tuple[list[dict], list[str]]:
        """既存のhybrid_searchを呼び出す（JERGインデックスが対象）"""
        try:
            from src.hybrid_search import hybrid_search
            results, methods = hybrid_search(
                query=query,
                top_k=self.top_k,
                doc_filter=doc_filter,
                use_llm_expansion=False,  # 速度優先
                use_cross_reference=True,
            )
            return results, methods
        except Exception as e:
            logger.debug(f"hybrid_search failed: {e}")
            return [], []

    def _search_space_kb(
        self,
        query: str,
    ) -> tuple[list[dict], list[str]]:
        """
        宇宙専門知識ベース（space_kb/）をBM25で検索する。
        JERG文書とは独立したインデックス。
        """
        if self._chunks is None:
            self._load_space_kb()

        if not self._chunks:
            return [], []

        # BM25 検索
        try:
            from rank_bm25 import BM25Okapi
            if self._bm25_index is None:
                self._build_bm25_index()

            tokenized_query = query.split()
            scores = self._bm25_index.get_scores(tokenized_query)

            # 上位k件を取得
            import numpy as np
            top_indices = np.argsort(scores)[::-1][: self.top_k]
            results = []
            max_score = scores[top_indices[0]] if len(top_indices) > 0 else 1.0

            for idx in top_indices:
                if scores[idx] <= 0:
                    break
                chunk = self._chunks[idx]
                results.append({
                    **chunk,
                    "score": round(float(scores[idx]) / max(max_score, 1e-6), 4),
                    "methods": ["bm25"],
                })

            return results, ["bm25"]
        except Exception as e:
            logger.debug(f"space_kb BM25 search failed: {e}")
            return [], []

    def _load_space_kb(self):
        """宇宙知識ベースのJSONLファイルを読み込む"""
        self._chunks = []
        if not self.kb_dir.exists():
            return

        for jsonl_file in sorted(self.kb_dir.glob("**/*.jsonl")):
            try:
                with open(jsonl_file, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            chunk = json.loads(line)
                            self._chunks.append(chunk)
            except Exception as e:
                logger.warning(f"Failed to load {jsonl_file}: {e}")

        logger.info(f"Loaded {len(self._chunks)} chunks from space_kb")

    def _build_bm25_index(self):
        """BM25インデックスを構築する"""
        from rank_bm25 import BM25Okapi
        corpus = [c.get("text", "").split() for c in self._chunks]
        self._bm25_index = BM25Okapi(corpus)

    def _merge_chunk_results(
        self,
        jerg_chunks: list[dict],
        kb_chunks: list[dict],
    ) -> list[dict]:
        """JERGとspace_kbの検索結果をスコア順にマージして返す"""
        seen: dict[str, dict] = {}

        for chunk in jerg_chunks:
            cid = chunk.get("chunk_id", chunk.get("text", "")[:50])
            seen[cid] = chunk

        for chunk in kb_chunks:
            cid = chunk.get("chunk_id", chunk.get("text", "")[:50])
            if cid in seen:
                existing = seen[cid]
                existing["score"] = existing.get("score", 0) + chunk.get("score", 0) * 0.5
            else:
                seen[cid] = chunk

        return sorted(seen.values(), key=lambda x: x.get("score", 0), reverse=True)
