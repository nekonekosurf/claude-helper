"""
Multi-Representation Indexing (マルチ表現インデックス)

同一文書に対して複数のベクトル表現を持ち、
どの表現でヒットしても同じ文書にたどり着けるようにする

表現の種類:
- original: 原文ベクトル
- summary: 要約ベクトル（短い説明文）
- keywords: キーワードベクトル（用語リスト）
- paraphrase: 平易版ベクトル

参照: LangChain Multi-Vector Retriever パターン
"""

from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
import numpy as np


class RepresentationType(str, Enum):
    ORIGINAL = "original"
    SUMMARY = "summary"
    KEYWORDS = "keywords"
    PARAPHRASE = "paraphrase"


@dataclass
class MultiRepDoc:
    """複数の表現を持つ文書"""
    doc_id: str                          # 原文書のID（検索結果の紐付けに使用）
    original_text: str                   # 原文
    representations: dict[str, str] = field(default_factory=dict)  # type -> text
    vectors: dict[str, Any] = field(default_factory=dict)          # type -> np.array
    metadata: dict[str, Any] = field(default_factory=dict)

    def get_text(self, rep_type: str) -> str:
        return self.representations.get(rep_type, self.original_text)


class MultiRepresentationIndex:
    """
    マルチ表現インデックス

    設計思想:
    - 同一文書を異なる角度から表現した複数のベクトルを保持
    - 検索はすべての表現で行い、最高スコアを採用
    - 最終的な回答には常に original_text を使用

    使い方:
        idx = MultiRepresentationIndex(embed_fn=my_embedder)
        idx.add_documents(docs_with_representations)
        results = idx.search("熱制御システムの冗長化", top_k=5)
    """

    def __init__(
        self,
        embed_fn: Any = None,
        weights: dict[str, float] | None = None
    ) -> None:
        """
        Args:
            embed_fn: テキスト → np.array の埋め込み関数
            weights: 各表現タイプのスコア重み
                     例: {"original": 1.0, "summary": 0.8, "keywords": 0.7, "paraphrase": 0.9}
        """
        self.embed_fn = embed_fn
        self.weights = weights or {
            RepresentationType.ORIGINAL: 1.0,
            RepresentationType.SUMMARY: 0.8,
            RepresentationType.KEYWORDS: 0.7,
            RepresentationType.PARAPHRASE: 0.9
        }
        self._docs: dict[str, MultiRepDoc] = {}
        # 各表現タイプ別のベクトルDB
        # type -> [(doc_id, vector)]
        self._vector_stores: dict[str, list[tuple[str, np.ndarray]]] = {
            t: [] for t in RepresentationType
        }

    def add_document(self, doc: MultiRepDoc) -> None:
        """文書を追加してすべての表現をベクトル化"""
        self._docs[doc.doc_id] = doc

        for rep_type, text in doc.representations.items():
            if not text.strip():
                continue
            vector = self._embed(text)
            if vector is not None:
                doc.vectors[rep_type] = vector
                self._vector_stores[rep_type].append((doc.doc_id, vector))

    def add_documents(self, docs: list[MultiRepDoc]) -> None:
        """複数文書を一括追加"""
        for i, doc in enumerate(docs):
            self.add_document(doc)
            if (i + 1) % 100 == 0:
                print(f"[MultiRepIndex] {i + 1}/{len(docs)} 追加完了")

    def search(
        self,
        query: str,
        top_k: int = 5,
        rep_types: list[str] | None = None
    ) -> list[dict[str, Any]]:
        """
        マルチ表現検索

        すべての表現タイプで検索し、スコアを統合

        Args:
            query: 検索クエリ
            top_k: 返す結果数
            rep_types: 使用する表現タイプ（Noneの場合は全て）

        Returns:
            [{"doc_id": ..., "score": ..., "original_text": ...,
              "matched_via": ..., "metadata": ...}]
        """
        query_vector = self._embed(query)
        if query_vector is None:
            return []

        target_types = rep_types or list(RepresentationType)
        doc_scores: dict[str, dict[str, float]] = {}  # doc_id -> {rep_type: score}

        for rep_type in target_types:
            store = self._vector_stores.get(rep_type, [])
            if not store:
                continue

            weight = self.weights.get(rep_type, 1.0)

            for doc_id, doc_vector in store:
                score = self._cosine_similarity(query_vector, doc_vector)
                weighted_score = score * weight

                if doc_id not in doc_scores:
                    doc_scores[doc_id] = {}

                # 同じ文書の同じ表現は最高スコアを採用
                if rep_type not in doc_scores[doc_id] or \
                        doc_scores[doc_id][rep_type] < weighted_score:
                    doc_scores[doc_id][rep_type] = weighted_score

        # 各文書の最終スコア（各表現の最高スコアを統合）
        final_scores: list[tuple[float, str, str]] = []  # (score, doc_id, best_rep_type)
        for doc_id, rep_scores in doc_scores.items():
            max_score = max(rep_scores.values())
            best_rep = max(rep_scores, key=rep_scores.get)
            # 複数の表現でヒットした場合はボーナス
            multi_hit_bonus = 0.05 * (len(rep_scores) - 1)
            final_scores.append((max_score + multi_hit_bonus, doc_id, best_rep))

        final_scores.sort(key=lambda x: x[0], reverse=True)

        results = []
        for score, doc_id, best_rep in final_scores[:top_k]:
            if doc_id not in self._docs:
                continue
            doc = self._docs[doc_id]
            results.append({
                "doc_id": doc_id,
                "score": round(score, 4),
                "original_text": doc.original_text,
                "matched_via": best_rep,        # どの表現でヒットしたか
                "hit_representations": list(doc_scores[doc_id].keys()),
                "representation_scores": {
                    k: round(v, 4) for k, v in doc_scores[doc_id].items()
                },
                "metadata": doc.metadata
            })

        return results

    def _embed(self, text: str) -> np.ndarray | None:
        """テキストをベクトルに変換"""
        if self.embed_fn is None:
            # フォールバック: ランダムベクトル（テスト用）
            np.random.seed(hash(text) % (2**32))
            return np.random.rand(384).astype(np.float32)
        try:
            result = self.embed_fn(text)
            if isinstance(result, list):
                return np.array(result, dtype=np.float32)
            return result
        except Exception as e:
            print(f"[MultiRepIndex] 埋め込みエラー: {e}")
            return None

    @staticmethod
    def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
        """コサイン類似度"""
        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return float(np.dot(a, b) / (norm_a * norm_b))

    def save(self, path: str) -> None:
        """インデックスをJSONに保存（ベクトルは.npy形式で別途保存）"""
        docs_data = {}
        for doc_id, doc in self._docs.items():
            docs_data[doc_id] = {
                "original_text": doc.original_text,
                "representations": doc.representations,
                "metadata": doc.metadata
            }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(docs_data, f, ensure_ascii=False, indent=2)
        print(f"[MultiRepIndex] 保存完了: {path} ({len(docs_data)} 文書)")

    def load(self, path: str) -> None:
        """保存したインデックスを読み込み"""
        with open(path, encoding="utf-8") as f:
            docs_data = json.load(f)
        for doc_id, data in docs_data.items():
            doc = MultiRepDoc(
                doc_id=doc_id,
                original_text=data["original_text"],
                representations=data["representations"],
                metadata=data.get("metadata", {})
            )
            self.add_document(doc)
        print(f"[MultiRepIndex] 読み込み完了: {len(docs_data)} 文書")


class MultiRepBuilder:
    """
    MultiRepDoc を生成するためのビルダー

    各種表現をLLMまたはルールベースで自動生成
    """

    SUMMARY_PROMPT = """以下のテキストを検索クエリとして使いやすい形式の
1〜2文（100文字以内）で要約してください。

テキスト:
{text}

要約:"""

    KEYWORDS_PROMPT = """以下のテキストから検索に有効なキーワードを抽出し、
スペース区切りで列挙してください（10〜20語）。

テキスト:
{text}

キーワード:"""

    PARAPHRASE_PROMPT = """以下の技術文書テキストを、
初学者でも検索しやすい平易な言葉に書き換えてください。
（100〜200文字程度）

テキスト:
{text}

平易版:"""

    def __init__(self, llm_client: Any = None) -> None:
        self.llm = llm_client

    def build_from_chunk(
        self,
        chunk: dict[str, Any],
        rep_types: list[str] | None = None
    ) -> MultiRepDoc:
        """
        単一チャンクからMultiRepDocを生成

        Args:
            chunk: {"chunk_id": ..., "text": ..., "metadata": {...}}
            rep_types: 生成する表現タイプ
        """
        chunk_id = chunk.get("chunk_id", "")
        text = chunk.get("text", "")
        metadata = chunk.get("metadata", {})

        target_types = rep_types or [
            RepresentationType.ORIGINAL,
            RepresentationType.SUMMARY,
            RepresentationType.KEYWORDS,
            RepresentationType.PARAPHRASE
        ]

        representations = {RepresentationType.ORIGINAL: text}

        if RepresentationType.SUMMARY in target_types:
            representations[RepresentationType.SUMMARY] = self._generate_summary(text)

        if RepresentationType.KEYWORDS in target_types:
            representations[RepresentationType.KEYWORDS] = self._generate_keywords(text)

        if RepresentationType.PARAPHRASE in target_types:
            representations[RepresentationType.PARAPHRASE] = self._generate_paraphrase(text)

        return MultiRepDoc(
            doc_id=chunk_id,
            original_text=text,
            representations=representations,
            metadata=metadata
        )

    def _generate_summary(self, text: str) -> str:
        if self.llm:
            try:
                return self.llm.complete(
                    self.SUMMARY_PROMPT.format(text=text[:1000])
                ).strip()
            except Exception:
                pass
        return text[:100] + ("..." if len(text) > 100 else "")

    def _generate_keywords(self, text: str) -> str:
        if self.llm:
            try:
                return self.llm.complete(
                    self.KEYWORDS_PROMPT.format(text=text[:800])
                ).strip()
            except Exception:
                pass
        # フォールバック: 名詞的な単語を抽出
        import re
        words = re.findall(r'[\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff]{2,}', text)
        return " ".join(list(dict.fromkeys(words))[:20])  # 重複除去

    def _generate_paraphrase(self, text: str) -> str:
        if self.llm:
            try:
                return self.llm.complete(
                    self.PARAPHRASE_PROMPT.format(text=text[:800])
                ).strip()
            except Exception:
                pass
        # フォールバック: ルールベース簡易化
        result = text
        for old, new in [("当該", "この"), ("ものとする", ""), ("における", "での")]:
            result = result.replace(old, new)
        return result[:200]
