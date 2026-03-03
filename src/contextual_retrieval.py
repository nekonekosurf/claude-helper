"""
Contextual Retrieval (Anthropic 提案手法)

各チャンクに「文書全体の文脈を説明するプレフィックス」を自動付与することで
検索精度を49%向上させる手法（公式発表）

参照: https://www.anthropic.com/news/contextual-retrieval

実装のポイント:
- Prompt Caching: 文書全体をキャッシュして処理コストを削減
- Hybrid Search: Contextual Embeddings + Contextual BM25
- Reranking: 上位150件を20件に絞り込む
"""

from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ContextualChunk:
    """文脈プレフィックス付きチャンク"""
    chunk_id: str
    original_text: str              # 元のチャンクテキスト
    context_prefix: str             # LLMが生成した文脈説明
    contextualized_text: str        # prefix + original_text（インデックス化対象）
    document_title: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "original_text": self.original_text,
            "context_prefix": self.context_prefix,
            "contextualized_text": self.contextualized_text,
            "document_title": self.document_title,
            "metadata": self.metadata
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ContextualChunk":
        return cls(
            chunk_id=data["chunk_id"],
            original_text=data["original_text"],
            context_prefix=data["context_prefix"],
            contextualized_text=data["contextualized_text"],
            document_title=data.get("document_title", ""),
            metadata=data.get("metadata", {})
        )


class ContextualRetrieval:
    """
    Anthropicが提案したContextual Retrieval の実装

    処理フロー:
    1. 各チャンクに文書全体の文脈を説明するプレフィックスを付与
    2. prefix + chunk をベクトル化（Contextual Embeddings）
    3. prefix + chunk でBM25インデックスも構築（Contextual BM25）
    4. 検索時は両方で上位150件を取得
    5. Reciprocal Rank Fusion (RRF) でスコア統合
    6. Rerankで上位20件に絞り込み

    使い方:
        cr = ContextualRetrieval(llm_client=my_llm, embed_fn=my_embedder)
        contextualized = cr.add_context(chunks, full_document)
        cr.build_index(contextualized)
        results = cr.search("熱制御システムの冗長化方針")
    """

    # Anthropic公式プロンプトテンプレート（日本語版）
    CONTEXT_PROMPT = """<document>
{document}
</document>

上記の文書全体の中で、以下のチャンクを位置づけてください。

<chunk>
{chunk}
</chunk>

このチャンクを文書全体の中で位置づける、簡潔なコンテキスト説明を
50〜100語で生成してください。検索精度向上のために使用します。
コンテキスト説明のみを出力し、前置き等は不要です。"""

    def __init__(
        self,
        llm_client: Any = None,
        embed_fn: Any = None,
        use_prompt_caching: bool = True  # Anthropic Prompt Caching 有効化
    ) -> None:
        self.llm = llm_client
        self.embed_fn = embed_fn
        self.use_prompt_caching = use_prompt_caching
        self._contextualized_chunks: dict[str, ContextualChunk] = {}
        self._vectors: list[tuple[str, Any]] = []          # [(chunk_id, vector)]
        self._bm25_index: Any = None                       # BM25インデックス

    def add_context(
        self,
        chunks: list[dict[str, Any]],
        full_document: str,
        document_title: str = ""
    ) -> list[ContextualChunk]:
        """
        各チャンクに文書全体の文脈プレフィックスを付与

        Args:
            chunks: [{"chunk_id": ..., "text": ..., "metadata": {...}}, ...]
            full_document: 文書全体のテキスト（文脈生成用）
            document_title: 文書タイトル

        Returns:
            ContextualChunk のリスト

        Note:
            Anthropic APIのPrompt Cachingを使用する場合、
            full_document を cache_control: ephemeral で送信して
            チャンクごとのキャッシュヒットを活用することでコストを約90%削減できる
        """
        results = []
        total = len(chunks)
        print(f"[ContextualRetrieval] {total} チャンクに文脈を付与中...")

        # 文書をトランケート（LLMのコンテキスト制限対応）
        doc_preview = full_document[:8000] + ("\n...[以下省略]" if len(full_document) > 8000 else "")

        for i, chunk in enumerate(chunks):
            chunk_id = chunk.get("chunk_id", f"chunk_{i}")
            text = chunk.get("text", "")
            metadata = chunk.get("metadata", {})

            if not text.strip():
                continue

            # 文脈プレフィックス生成
            context_prefix = self._generate_context(doc_preview, text)

            # prefix + text を結合
            contextualized_text = f"{context_prefix}\n\n{text}"

            cc = ContextualChunk(
                chunk_id=chunk_id,
                original_text=text,
                context_prefix=context_prefix,
                contextualized_text=contextualized_text,
                document_title=document_title,
                metadata=metadata
            )
            results.append(cc)
            self._contextualized_chunks[chunk_id] = cc

            if (i + 1) % 50 == 0:
                print(f"[ContextualRetrieval] {i + 1}/{total} 完了")

        print(f"[ContextualRetrieval] 文脈付与完了: {len(results)} チャンク")
        return results

    def _generate_context(self, document: str, chunk: str) -> str:
        """
        LLMを使って文脈プレフィックスを生成

        Prompt Cachingを活用する場合は、APIクライアント側で
        document部分を cache_control: ephemeral として送信する
        """
        if self.llm is None:
            # LLMなし: ルールベースのフォールバック
            return self._rule_based_context(chunk)

        prompt = self.CONTEXT_PROMPT.format(document=document, chunk=chunk)
        try:
            return self.llm.complete(prompt).strip()
        except Exception as e:
            print(f"[ContextualRetrieval] 文脈生成失敗: {e}")
            return self._rule_based_context(chunk)

    def _rule_based_context(self, chunk: str) -> str:
        """ルールベースのフォールバック文脈生成"""
        import re
        # セクション番号を検出
        section_match = re.search(r'^(\d+(?:\.\d+)*)\s+(.+?)$', chunk[:200], re.MULTILINE)
        if section_match:
            return f"このチャンクはセクション {section_match.group(1)} 「{section_match.group(2)[:50]}」に関する内容です。"
        # 最初の文を文脈として使用
        first_sentence = chunk[:100].split("。")[0]
        return f"このチャンクは「{first_sentence}」に関する内容を含みます。"

    def build_index(self, contextualized_chunks: list[ContextualChunk]) -> None:
        """
        文脈付きチャンクからハイブリッドインデックスを構築

        - ベクトルインデックス (dense)
        - BM25インデックス (sparse)
        """
        print("[ContextualRetrieval] インデックス構築中...")

        texts_for_bm25 = []
        for cc in contextualized_chunks:
            # ベクトル化（contextual_text全体を使用）
            if self.embed_fn:
                try:
                    vector = self.embed_fn(cc.contextualized_text)
                    self._vectors.append((cc.chunk_id, vector))
                except Exception:
                    pass

            texts_for_bm25.append(cc.contextualized_text)

        # BM25インデックス構築
        self._build_bm25(texts_for_bm25, [cc.chunk_id for cc in contextualized_chunks])

        print(f"[ContextualRetrieval] インデックス完了: "
              f"{len(self._vectors)} ベクトル, BM25 {len(texts_for_bm25)} 文書")

    def _build_bm25(self, texts: list[str], chunk_ids: list[str]) -> None:
        """BM25インデックス構築"""
        try:
            import bm25s
            corpus_tokens = bm25s.tokenize(texts, stopwords="ja")
            retriever = bm25s.BM25()
            retriever.index(corpus_tokens)
            self._bm25_index = {
                "retriever": retriever,
                "chunk_ids": chunk_ids,
                "corpus_tokens": corpus_tokens
            }
        except ImportError:
            print("[ContextualRetrieval] bm25s未インストール。pip install bm25s で追加推奨")
            # フォールバック: 簡易TF-IDF的なBM25
            self._bm25_index = self._build_simple_bm25(texts, chunk_ids)

    def _build_simple_bm25(
        self,
        texts: list[str],
        chunk_ids: list[str]
    ) -> dict[str, Any]:
        """bm25sがない場合の簡易BM25実装"""
        import math
        import re
        from collections import Counter

        def tokenize(text: str) -> list[str]:
            # 日本語: 2文字以上のひらがな・カタカナ・漢字
            return re.findall(r'[\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff]{2,}', text)

        # 文書頻度の計算
        df: dict[str, int] = Counter()
        tokenized_docs = []
        for text in texts:
            tokens = tokenize(text)
            tokenized_docs.append(tokens)
            for token in set(tokens):
                df[token] += 1

        n_docs = len(texts)
        avg_len = sum(len(t) for t in tokenized_docs) / max(n_docs, 1)

        return {
            "type": "simple",
            "tokenized_docs": tokenized_docs,
            "chunk_ids": chunk_ids,
            "df": dict(df),
            "n_docs": n_docs,
            "avg_len": avg_len
        }

    def search(
        self,
        query: str,
        top_k: int = 20,
        dense_top_k: int = 150,
        sparse_top_k: int = 150,
        rerank: bool = False
    ) -> list[dict[str, Any]]:
        """
        ハイブリッド検索 (Contextual Embeddings + Contextual BM25 + RRF)

        Args:
            query: 検索クエリ
            top_k: 最終的に返す件数
            dense_top_k: ベクトル検索の候補数
            sparse_top_k: BM25検索の候補数
            rerank: 再ランキングを行うか（LLMが必要）

        Returns:
            スコア付き検索結果リスト
        """
        # Dense検索（ベクトル）
        dense_results = self._dense_search(query, dense_top_k)

        # Sparse検索（BM25）
        sparse_results = self._sparse_search(query, sparse_top_k)

        # Reciprocal Rank Fusion (RRF) でスコア統合
        fused = self._reciprocal_rank_fusion(dense_results, sparse_results)

        # 上位候補を絞り込み
        candidates = fused[:min(top_k * 3, 150)]

        # Reranking（オプション）
        if rerank and self.llm:
            candidates = self._rerank(query, candidates, top_k)
        else:
            candidates = candidates[:top_k]

        # 最終結果の成形
        results = []
        for rank, (chunk_id, score) in enumerate(candidates):
            if chunk_id not in self._contextualized_chunks:
                continue
            cc = self._contextualized_chunks[chunk_id]
            results.append({
                "rank": rank + 1,
                "chunk_id": chunk_id,
                "score": round(score, 4),
                "original_text": cc.original_text,    # 引用用原文
                "context_prefix": cc.context_prefix,  # 文脈説明
                "document_title": cc.document_title,
                "metadata": cc.metadata
            })

        return results

    def _dense_search(
        self,
        query: str,
        top_k: int
    ) -> list[tuple[str, float]]:
        """ベクトル類似度検索"""
        if not self.embed_fn or not self._vectors:
            return []

        try:
            import numpy as np
            query_vec = self.embed_fn(query)
            if isinstance(query_vec, list):
                query_vec = np.array(query_vec, dtype=np.float32)

            scores = []
            for chunk_id, vec in self._vectors:
                if isinstance(vec, list):
                    vec = np.array(vec, dtype=np.float32)
                score = float(np.dot(query_vec, vec) / (
                    np.linalg.norm(query_vec) * np.linalg.norm(vec) + 1e-8
                ))
                scores.append((chunk_id, score))

            scores.sort(key=lambda x: x[1], reverse=True)
            return scores[:top_k]
        except Exception as e:
            print(f"[ContextualRetrieval] Dense検索エラー: {e}")
            return []

    def _sparse_search(
        self,
        query: str,
        top_k: int
    ) -> list[tuple[str, float]]:
        """BM25キーワード検索"""
        if not self._bm25_index:
            return []

        try:
            if self._bm25_index.get("type") == "simple":
                return self._simple_bm25_search(query, top_k)
            else:
                import bm25s
                import numpy as np
                query_tokens = bm25s.tokenize([query], stopwords="ja")
                results, scores = self._bm25_index["retriever"].retrieve(
                    query_tokens, k=top_k
                )
                chunk_ids = self._bm25_index["chunk_ids"]
                output = []
                for i, score in zip(results[0], scores[0]):
                    if i < len(chunk_ids):
                        output.append((chunk_ids[i], float(score)))
                return output
        except Exception as e:
            print(f"[ContextualRetrieval] Sparse検索エラー: {e}")
            return []

    def _simple_bm25_search(
        self,
        query: str,
        top_k: int
    ) -> list[tuple[str, float]]:
        """簡易BM25検索"""
        import re
        import math

        bm25 = self._bm25_index
        k1, b = 1.5, 0.75

        def tokenize(text: str) -> list[str]:
            return re.findall(r'[\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff]{2,}', text)

        query_tokens = tokenize(query)
        scores = []

        for i, (tokens, chunk_id) in enumerate(
            zip(bm25["tokenized_docs"], bm25["chunk_ids"])
        ):
            if not tokens:
                continue
            score = 0.0
            doc_len = len(tokens)
            tf_counter = {}
            for t in tokens:
                tf_counter[t] = tf_counter.get(t, 0) + 1

            for qt in query_tokens:
                tf = tf_counter.get(qt, 0)
                df = bm25["df"].get(qt, 0)
                if df == 0:
                    continue
                idf = math.log((bm25["n_docs"] - df + 0.5) / (df + 0.5) + 1)
                tf_norm = tf * (k1 + 1) / (tf + k1 * (1 - b + b * doc_len / bm25["avg_len"]))
                score += idf * tf_norm

            scores.append((chunk_id, score))

        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:top_k]

    def _reciprocal_rank_fusion(
        self,
        dense_results: list[tuple[str, float]],
        sparse_results: list[tuple[str, float]],
        k: int = 60
    ) -> list[tuple[str, float]]:
        """
        Reciprocal Rank Fusion (RRF)

        RRF スコア = sum(1 / (k + rank))
        """
        rrf_scores: dict[str, float] = {}

        for rank, (chunk_id, _) in enumerate(dense_results):
            rrf_scores[chunk_id] = rrf_scores.get(chunk_id, 0) + 1 / (k + rank + 1)

        for rank, (chunk_id, _) in enumerate(sparse_results):
            rrf_scores[chunk_id] = rrf_scores.get(chunk_id, 0) + 1 / (k + rank + 1)

        sorted_results = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)
        return sorted_results

    def _rerank(
        self,
        query: str,
        candidates: list[tuple[str, float]],
        top_k: int
    ) -> list[tuple[str, float]]:
        """
        LLMによる再ランキング（上位候補をLLMが精度高くスコアリング）
        """
        candidate_texts = []
        for chunk_id, _ in candidates:
            if chunk_id in self._contextualized_chunks:
                cc = self._contextualized_chunks[chunk_id]
                candidate_texts.append(f"ID: {chunk_id}\n{cc.original_text[:300]}")

        prompt = f"""以下の検索結果を、クエリとの関連性が高い順に並び替えてください。

クエリ: {query}

候補（現在の順序）:
{chr(10).join(f'{i+1}. {t}' for i, t in enumerate(candidate_texts[:20]))}

最も関連性が高い順に番号を列挙してください（カンマ区切り）:"""

        try:
            response = self.llm.complete(prompt).strip()
            # 番号を解析
            import re
            indices = [int(n) - 1 for n in re.findall(r'\d+', response)]
            reranked = []
            seen = set()
            for i in indices:
                if 0 <= i < len(candidates):
                    chunk_id, score = candidates[i]
                    if chunk_id not in seen:
                        reranked.append((chunk_id, score))
                        seen.add(chunk_id)
            # 元の順序でカバーされていないものを末尾に追加
            for chunk_id, score in candidates:
                if chunk_id not in seen:
                    reranked.append((chunk_id, score))
            return reranked[:top_k]
        except Exception:
            return candidates[:top_k]

    def save(self, path: str) -> None:
        """文脈付きチャンクをJSONに保存"""
        data = [cc.to_dict() for cc in self._contextualized_chunks.values()]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"[ContextualRetrieval] 保存完了: {path} ({len(data)} チャンク)")

    def load(self, path: str) -> None:
        """保存した文脈付きチャンクを読み込み"""
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        chunks = [ContextualChunk.from_dict(d) for d in data]
        self._contextualized_chunks = {cc.chunk_id: cc for cc in chunks}
        print(f"[ContextualRetrieval] 読み込み完了: {len(chunks)} チャンク")
