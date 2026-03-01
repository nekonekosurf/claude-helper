"""ベクトル検索 - 意味ベースの文書検索（fastembed + numpy）"""

import json
import numpy as np
from pathlib import Path

INDEX_DIR = Path(__file__).parent.parent / "data" / "index"
EMBEDDINGS_DIR = Path(__file__).parent.parent / "data" / "embeddings"

_model = None
_embeddings = None
_chunks = None
# chunk_id → index のマッピング（高速ルックアップ用）
_chunk_id_to_idx: dict | None = None


def _load_model():
    """埋め込みモデルをロード（初回のみ）"""
    global _model
    if _model is not None:
        return

    from fastembed import TextEmbedding
    # 軽量な多言語モデル（CPU対応）
    _model = TextEmbedding("sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")


def _load_embeddings():
    """
    事前計算した埋め込みをロード。

    優先順位:
    1. data/embeddings/vectors.npy + data/embeddings/chunk_ids.json  (事前計算済み)
    2. data/index/embeddings.npy  (旧形式)

    chunk_ids.json が "chunk_0" 形式（生成時の連番）の場合は、
    chunks.json のインデックス順と対応付ける。
    事前計算時に vectors.npy と chunks.json が同順である前提で動作する。
    """
    global _embeddings, _chunks, _chunk_id_to_idx

    if _embeddings is not None:
        return

    # chunks.json の候補パス（index優先、なければルートの data/）
    chunks_candidates = [
        INDEX_DIR / "chunks.json",
        Path(__file__).parent.parent / "data" / "chunks.json",
    ]
    chunks_path = next((p for p in chunks_candidates if p.exists()), None)
    if chunks_path is None:
        raise FileNotFoundError("chunks.json が見つかりません")

    # --- 優先: data/embeddings/ ディレクトリの事前計算済みembeddings ---
    vectors_path = EMBEDDINGS_DIR / "vectors.npy"
    chunk_ids_path = EMBEDDINGS_DIR / "chunk_ids.json"

    if vectors_path.exists() and chunk_ids_path.exists():
        _embeddings = np.load(str(vectors_path))

        with open(chunk_ids_path, encoding="utf-8") as f:
            stored_ids = json.load(f)

        with open(chunks_path, encoding="utf-8") as f:
            chunks_list = json.load(f)

        chunk_by_id = {c["chunk_id"]: c for c in chunks_list}

        if stored_ids and stored_ids[0] not in chunk_by_id:
            # "chunk_0", "chunk_1", ... 形式: vectors.npy[i] = chunks_list[i]
            # 長さが一致しない場合は安全にトリミング
            n = min(len(_embeddings), len(chunks_list))
            if n < len(_embeddings):
                _embeddings = _embeddings[:n]
            _chunks = chunks_list[:n]
            _chunk_id_to_idx = {c["chunk_id"]: i for i, c in enumerate(_chunks)}
        else:
            # stored_ids が実際の chunk_id と一致する場合
            aligned = [(i, chunk_by_id[cid]) for i, cid in enumerate(stored_ids) if cid in chunk_by_id]
            if len(aligned) < len(stored_ids):
                # 一部の chunk_id が chunks.json に存在しない場合は埋め込みも絞り込む
                indices = [i for i, _ in aligned]
                _embeddings = _embeddings[indices]
                _chunks = [c for _, c in aligned]
            else:
                _chunks = [c for _, c in aligned]
            _chunk_id_to_idx = {c["chunk_id"]: i for i, c in enumerate(_chunks)}

        return

    # --- フォールバック: data/index/embeddings.npy ---
    emb_path = INDEX_DIR / "embeddings.npy"
    if not emb_path.exists():
        raise FileNotFoundError(
            "ベクトルインデックスが未構築です。"
            "'uv run python -m src.vector_search build' を実行してください。"
        )

    _embeddings = np.load(str(emb_path))

    with open(chunks_path, encoding="utf-8") as f:
        _chunks = json.load(f)

    _chunk_id_to_idx = {c["chunk_id"]: i for i, c in enumerate(_chunks)}


def build_embeddings(batch_size: int = 64):
    """全チャンクの埋め込みを計算して data/embeddings/ に保存"""
    _load_model()

    chunks_path = INDEX_DIR / "chunks.json"
    if not chunks_path.exists():
        print("Error: chunks.json が見つかりません。先に indexer.py を実行してください。")
        return

    with open(chunks_path, encoding="utf-8") as f:
        chunks = json.load(f)

    texts = [c["text"] for c in chunks]
    chunk_ids = [c["chunk_id"] for c in chunks]
    print(f"{len(texts)} チャンクの埋め込みを計算中...")

    # バッチで埋め込み計算
    all_embeddings = list(_model.embed(texts, batch_size=batch_size))
    embedding_matrix = np.array(all_embeddings, dtype=np.float32)

    # data/embeddings/ に保存
    EMBEDDINGS_DIR.mkdir(parents=True, exist_ok=True)
    vectors_path = EMBEDDINGS_DIR / "vectors.npy"
    chunk_ids_path = EMBEDDINGS_DIR / "chunk_ids.json"

    np.save(str(vectors_path), embedding_matrix)
    with open(chunk_ids_path, "w", encoding="utf-8") as f:
        json.dump(chunk_ids, f, ensure_ascii=False)

    # 後方互換: data/index/embeddings.npy にも保存
    emb_path = INDEX_DIR / "embeddings.npy"
    np.save(str(emb_path), embedding_matrix)

    print(f"Embeddings saved: {vectors_path}")
    print(f"  Shape: {embedding_matrix.shape}")


def search(
    query: str,
    top_k: int = 5,
    doc_filter: str | None = None,
    score_threshold: float = 0.3,
) -> list[dict]:
    """ベクトル検索 - 意味的に近いチャンクを返す

    コサイン類似度（0〜1）をそのままスコアとして返す。
    hybrid_search 側でスケール調整すること。

    Args:
        query: 検索クエリ
        top_k: 返す件数
        doc_filter: 文書IDフィルタ（部分一致）
        score_threshold: この値未満のコサイン類似度は結果に含めない（デフォルト0.3）
    """
    _load_model()
    _load_embeddings()

    # クエリの埋め込みを計算
    query_emb = np.array(list(_model.embed([query]))[0], dtype=np.float32)

    # コサイン類似度計算（L2正規化後の内積）
    query_norm = query_emb / (np.linalg.norm(query_emb) + 1e-10)
    emb_norms = _embeddings / (np.linalg.norm(_embeddings, axis=1, keepdims=True) + 1e-10)
    similarities = (emb_norms @ query_norm).astype(np.float32)

    # フィルタ適用（マスクを一括処理）
    if doc_filter:
        mask = np.array([doc_filter not in chunk["doc_id"] for chunk in _chunks])
        similarities[mask] = -1.0

    # 閾値以下を除外した上で上位N件取得
    # np.argpartition で上位候補を高速取得してからソート
    n_candidates = min(top_k * 3, len(similarities))
    candidate_indices = np.argpartition(similarities, -n_candidates)[-n_candidates:]
    candidate_indices = candidate_indices[np.argsort(similarities[candidate_indices])[::-1]]

    results = []
    for idx in candidate_indices[:top_k]:
        score = float(similarities[idx])
        if score < score_threshold:
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
    # 事前計算済みembeddingsを優先チェック
    if (EMBEDDINGS_DIR / "vectors.npy").exists() and (EMBEDDINGS_DIR / "chunk_ids.json").exists():
        return True
    return (INDEX_DIR / "embeddings.npy").exists()


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "build":
        build_embeddings()
    else:
        print("Usage: python -m src.vector_search build")
