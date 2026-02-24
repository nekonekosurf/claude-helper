"""BM25 文書検索エンジン - JERG文書をキーワード検索"""

import json
from pathlib import Path
from rank_bm25 import BM25Okapi
from fugashi import Tagger

INDEX_DIR = Path(__file__).parent.parent / "data" / "index"

# シングルトンキャッシュ
_bm25 = None
_chunks = None
_tagger = None


def _load_index():
    """インデックスをメモリにロード（初回のみ）"""
    global _bm25, _chunks, _tagger

    if _bm25 is not None:
        return

    chunks_path = INDEX_DIR / "chunks.json"
    tokens_path = INDEX_DIR / "tokens.json"

    if not chunks_path.exists() or not tokens_path.exists():
        raise FileNotFoundError(
            f"インデックスが見つかりません。先に indexer.py を実行してください: {INDEX_DIR}"
        )

    with open(chunks_path, encoding="utf-8") as f:
        _chunks = json.load(f)

    with open(tokens_path, encoding="utf-8") as f:
        tokenized = json.load(f)

    _bm25 = BM25Okapi(tokenized)
    _tagger = Tagger()


def search(query: str, top_k: int = 5, doc_filter: str | None = None) -> list[dict]:
    """クエリで文書を検索し、上位N件を返す

    Args:
        query: 検索クエリ（日本語）
        top_k: 返す件数
        doc_filter: 文書番号フィルタ（部分一致、例: "JERG-2-200"）

    Returns:
        [{"doc_id", "chunk_id", "text", "score", "filename"}, ...]
    """
    _load_index()

    # クエリをトークン化
    tokens = []
    for word in _tagger(query):
        surface = word.surface
        if len(surface) > 1 or not surface.isascii():
            tokens.append(surface)

    if not tokens:
        return []

    # BM25 スコア計算
    scores = _bm25.get_scores(tokens)

    # スコア付きインデックスを作成
    scored = list(enumerate(scores))

    # フィルタ適用
    if doc_filter:
        scored = [(i, s) for i, s in scored if doc_filter in _chunks[i]["doc_id"]]

    # スコア降順ソート
    scored.sort(key=lambda x: x[1], reverse=True)

    # 上位N件を返す
    results = []
    for idx, score in scored[:top_k]:
        if score <= 0:
            break
        chunk = _chunks[idx]
        results.append({
            "doc_id": chunk["doc_id"],
            "chunk_id": chunk["chunk_id"],
            "filename": chunk["filename"],
            "text": chunk["text"],
            "score": round(float(score), 4),
        })

    return results


def get_document_list() -> dict:
    """インデックスに含まれる文書一覧を返す"""
    doc_list_path = INDEX_DIR / "documents.json"
    if not doc_list_path.exists():
        return {}
    with open(doc_list_path, encoding="utf-8") as f:
        return json.load(f)


def reload_index():
    """インデックスを再読み込み（更新後に使用）"""
    global _bm25, _chunks, _tagger
    _bm25 = None
    _chunks = None
    _tagger = None
    _load_index()
