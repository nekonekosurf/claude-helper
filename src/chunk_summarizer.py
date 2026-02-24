"""チャンク要約インデックス - LLMで各チャンクを要約し、要約もBM25検索対象にする"""

import json
import time
from pathlib import Path
from rank_bm25 import BM25Okapi
from fugashi import Tagger

from src.llm_client import create_client, chat

INDEX_DIR = Path(__file__).parent.parent / "data" / "index"

SUMMARY_PROMPT = """\
以下の技術文書の一部を、検索しやすいように50文字以内で要約してください。
日常的な言葉を使って、内容のキーワードを含めてください。
要約のみ出力してください。

テキスト:
{text}
"""

_bm25_summary = None
_summaries = None
_chunks = None
_tagger = None


def build_summaries(batch_size: int = 10, max_chunks: int = None):
    """全チャンクの要約を生成して保存"""
    chunks_path = INDEX_DIR / "chunks.json"
    if not chunks_path.exists():
        print("Error: chunks.json が見つかりません。")
        return

    with open(chunks_path, encoding="utf-8") as f:
        chunks = json.load(f)

    if max_chunks:
        chunks = chunks[:max_chunks]

    # 既存の要約があれば読み込み（途中再開対応）
    summaries_path = INDEX_DIR / "summaries.json"
    existing = {}
    if summaries_path.exists():
        with open(summaries_path, encoding="utf-8") as f:
            existing = json.load(f)

    client, model = create_client()
    total = len(chunks)
    new_count = 0

    print(f"📝 {total} チャンクの要約を生成中（既存: {len(existing)} 件）...")

    for i, chunk in enumerate(chunks):
        chunk_id = chunk["chunk_id"]

        # 既に要約済みならスキップ
        if chunk_id in existing:
            continue

        text = chunk["text"][:500]  # 要約対象は先頭500文字
        prompt = SUMMARY_PROMPT.format(text=text)

        try:
            response = chat(
                client, model,
                [{"role": "user", "content": prompt}],
                tools=None,
            )
            summary = (response.content or "").strip()
            existing[chunk_id] = summary
            new_count += 1

            if new_count % 50 == 0:
                print(f"  [{i+1}/{total}] {new_count} 件生成済み...")
                # 定期保存
                with open(summaries_path, "w", encoding="utf-8") as f:
                    json.dump(existing, f, ensure_ascii=False, indent=1)

            # レート制限対策
            time.sleep(0.1)

        except Exception as e:
            print(f"  Warning: {chunk_id} の要約生成失敗: {e}")
            time.sleep(1)
            continue

    # 最終保存
    with open(summaries_path, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=1)

    # BM25インデックスを構築
    _build_summary_bm25(chunks, existing)

    print(f"✅ 要約生成完了: {len(existing)} 件（新規: {new_count}）")


def _build_summary_bm25(chunks: list, summaries: dict):
    """要約テキストのBM25インデックスを構築"""
    tagger = Tagger()

    tokenized = []
    for chunk in chunks:
        summary = summaries.get(chunk["chunk_id"], "")
        tokens = [w.surface for w in tagger(summary) if len(w.surface) > 1 or not w.surface.isascii()]
        tokenized.append(tokens)

    tokens_path = INDEX_DIR / "summary_tokens.json"
    with open(tokens_path, "w", encoding="utf-8") as f:
        json.dump(tokenized, f, ensure_ascii=False)


def _load_summary_index():
    """要約BM25インデックスをロード"""
    global _bm25_summary, _summaries, _chunks, _tagger

    if _bm25_summary is not None:
        return

    summaries_path = INDEX_DIR / "summaries.json"
    tokens_path = INDEX_DIR / "summary_tokens.json"
    chunks_path = INDEX_DIR / "chunks.json"

    if not summaries_path.exists() or not tokens_path.exists():
        raise FileNotFoundError("要約インデックスが未構築です。")

    with open(summaries_path, encoding="utf-8") as f:
        _summaries = json.load(f)

    with open(tokens_path, encoding="utf-8") as f:
        tokenized = json.load(f)

    with open(chunks_path, encoding="utf-8") as f:
        _chunks = json.load(f)

    _bm25_summary = BM25Okapi(tokenized)
    _tagger = Tagger()


def search(query: str, top_k: int = 5, doc_filter: str | None = None) -> list[dict]:
    """要約インデックスでBM25検索"""
    _load_summary_index()

    tokens = [w.surface for w in _tagger(query) if len(w.surface) > 1 or not w.surface.isascii()]
    if not tokens:
        return []

    scores = _bm25_summary.get_scores(tokens)
    scored = list(enumerate(scores))

    if doc_filter:
        scored = [(i, s) for i, s in scored if doc_filter in _chunks[i]["doc_id"]]

    scored.sort(key=lambda x: x[1], reverse=True)

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
            "summary": _summaries.get(chunk["chunk_id"], ""),
            "score": round(float(score), 4),
            "method": "summary_bm25",
        })

    return results


def is_available() -> bool:
    """要約インデックスが利用可能か"""
    return (INDEX_DIR / "summaries.json").exists() and (INDEX_DIR / "summary_tokens.json").exists()


if __name__ == "__main__":
    import sys
    max_c = int(sys.argv[1]) if len(sys.argv) > 1 else None
    build_summaries(max_chunks=max_c)
