"""ベクトル検索 - 意味ベースの文書検索（fastembed + numpy）"""

import json
import numpy as np
from pathlib import Path

INDEX_DIR = Path(__file__).parent.parent / "data" / "index"

_model = None
_embeddings = None
_chunks = None


def _load_model():
    """埋め込みモデルをロード（初回のみ）"""
    global _model
    if _model is not None:
        return

    from fastembed import TextEmbedding
    # 軽量な多言語モデル（CPU対応）
    _model = TextEmbedding("sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")


def _load_embeddings():
    """事前計算した埋め込みをロード"""
    global _embeddings, _chunks

    if _embeddings is not None:
        return

    emb_path = INDEX_DIR / "embeddings.npy"
    chunks_path = INDEX_DIR / "chunks.json"

    if not emb_path.exists():
        raise FileNotFoundError(
            f"ベクトルインデックスが未構築です。"
            f"'uv run python -m src.vector_search build' を実行してください。"
        )

    _embeddings = np.load(str(emb_path))

    with open(chunks_path, encoding="utf-8") as f:
        _chunks = json.load(f)


def build_embeddings(batch_size: int = 64):
    """全チャンクの埋め込みを計算して保存"""
    _load_model()

    chunks_path = INDEX_DIR / "chunks.json"
    if not chunks_path.exists():
        print("Error: chunks.json が見つかりません。先に indexer.py を実行してください。")
        return

    with open(chunks_path, encoding="utf-8") as f:
        chunks = json.load(f)

    texts = [c["text"] for c in chunks]
    print(f"🔢 {len(texts)} チャンクの埋め込みを計算中...")

    # バッチで埋め込み計算
    all_embeddings = list(_model.embed(texts, batch_size=batch_size))
    embedding_matrix = np.array(all_embeddings, dtype=np.float32)

    # 保存
    emb_path = INDEX_DIR / "embeddings.npy"
    np.save(str(emb_path), embedding_matrix)

    print(f"✅ 埋め込み保存完了: {emb_path}")
    print(f"   形状: {embedding_matrix.shape}")


def search(query: str, top_k: int = 5, doc_filter: str | None = None) -> list[dict]:
    """ベクトル検索 - 意味的に近いチャンクを返す"""
    _load_model()
    _load_embeddings()

    # クエリの埋め込みを計算
    query_emb = list(_model.embed([query]))[0]
    query_emb = np.array(query_emb, dtype=np.float32)

    # コサイン類似度計算
    # 正規化
    query_norm = query_emb / (np.linalg.norm(query_emb) + 1e-10)
    emb_norms = _embeddings / (np.linalg.norm(_embeddings, axis=1, keepdims=True) + 1e-10)
    similarities = emb_norms @ query_norm

    # フィルタ適用
    if doc_filter:
        for i, chunk in enumerate(_chunks):
            if doc_filter not in chunk["doc_id"]:
                similarities[i] = -1

    # 上位N件
    top_indices = np.argsort(similarities)[::-1][:top_k]

    results = []
    for idx in top_indices:
        score = float(similarities[idx])
        if score <= 0:
            break
        chunk = _chunks[idx]
        results.append({
            "doc_id": chunk["doc_id"],
            "chunk_id": chunk["chunk_id"],
            "filename": chunk["filename"],
            "text": chunk["text"],
            "score": round(score, 4),
            "method": "vector",
        })

    return results


def is_available() -> bool:
    """ベクトルインデックスが利用可能か"""
    return (INDEX_DIR / "embeddings.npy").exists()


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "build":
        build_embeddings()
    else:
        print("Usage: python -m src.vector_search build")
