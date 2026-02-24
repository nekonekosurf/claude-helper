"""JERG PDF → テキスト → チャンク → BM25インデックス構築"""

import json
import re
from pathlib import Path
from pypdf import PdfReader
from fugashi import Tagger

from src.config import WORKING_DIR

DATA_DIR = Path(__file__).parent.parent / "data"
JERG_DIR = DATA_DIR / "jerg"
INDEX_DIR = DATA_DIR / "index"

# チャンク設定
CHUNK_SIZE = 800
CHUNK_OVERLAP = 100


def extract_text_from_pdf(pdf_path: Path) -> str:
    """PDFからテキストを抽出"""
    try:
        reader = PdfReader(str(pdf_path))
        pages = []
        for page in reader.pages:
            text = page.extract_text()
            if text:
                pages.append(text)
        return "\n".join(pages)
    except Exception as e:
        print(f"  Warning: {pdf_path.name} の読み取りに失敗: {e}")
        return ""


def parse_doc_id(filename: str) -> str:
    """ファイル名から文書番号を抽出: JAXA-JERG-0-049D.pdf → JERG-0-049"""
    name = filename.replace("JAXA-", "").replace(".pdf", "")
    # 末尾のバージョン文字を除去 (D, A, B, _N1 等)
    name = re.sub(r'[A-F]?(_N\d+)?$', '', name)
    return name


def split_into_chunks(text: str, doc_id: str, filename: str) -> list[dict]:
    """テキストをチャンクに分割してメタデータを付与"""
    if not text.strip():
        return []

    # 段落・文で分割してからチャンクにまとめる
    segments = re.split(r'(?<=[。\n])', text)
    segments = [s for s in segments if s.strip()]

    chunks = []
    current = ""
    chunk_idx = 0

    for seg in segments:
        if len(current) + len(seg) > CHUNK_SIZE and current:
            chunks.append({
                "doc_id": doc_id,
                "filename": filename,
                "chunk_id": f"{doc_id}_{chunk_idx}",
                "text": current.strip(),
            })
            chunk_idx += 1
            # オーバーラップ: 現在チャンクの末尾を次に持ち越し
            if len(current) > CHUNK_OVERLAP:
                current = current[-CHUNK_OVERLAP:] + seg
            else:
                current = seg
        else:
            current += seg

    if current.strip():
        chunks.append({
            "doc_id": doc_id,
            "filename": filename,
            "chunk_id": f"{doc_id}_{chunk_idx}",
            "text": current.strip(),
        })

    return chunks


def tokenize_japanese(text: str) -> list[str]:
    """fugashi (MeCab) で日本語をトークン化"""
    tagger = Tagger()
    tokens = []
    for word in tagger(text):
        surface = word.surface
        if len(surface) > 1 or not surface.isascii():
            tokens.append(surface)
    return tokens


def build_index():
    """全JERG PDFからインデックスを構築"""
    if not JERG_DIR.exists():
        print(f"Error: {JERG_DIR} が見つかりません。PDFをダウンロードしてください。")
        return

    pdf_files = sorted(JERG_DIR.glob("*.pdf"))
    if not pdf_files:
        print(f"Error: {JERG_DIR} にPDFファイルがありません。")
        return

    print(f"📚 {len(pdf_files)} 件のPDFをインデックス化中...")

    all_chunks = []
    all_tokenized = []
    tagger = Tagger()

    for i, pdf_path in enumerate(pdf_files, 1):
        doc_id = parse_doc_id(pdf_path.name)
        print(f"  [{i}/{len(pdf_files)}] {pdf_path.name} → {doc_id}")

        text = extract_text_from_pdf(pdf_path)
        if not text:
            continue

        chunks = split_into_chunks(text, doc_id, pdf_path.name)
        for chunk in chunks:
            # トークン化
            tokens = []
            for word in tagger(chunk["text"]):
                surface = word.surface
                if len(surface) > 1 or not surface.isascii():
                    tokens.append(surface)
            all_tokenized.append(tokens)
            all_chunks.append(chunk)

    print(f"\n📊 合計: {len(all_chunks)} チャンク")

    # BM25 インデックスを構築
    from rank_bm25 import BM25Okapi
    bm25 = BM25Okapi(all_tokenized)

    # 保存
    INDEX_DIR.mkdir(parents=True, exist_ok=True)

    # チャンクデータ保存
    chunks_path = INDEX_DIR / "chunks.json"
    with open(chunks_path, "w", encoding="utf-8") as f:
        json.dump(all_chunks, f, ensure_ascii=False, indent=1)

    # トークン化データ保存（BM25再構築用）
    tokens_path = INDEX_DIR / "tokens.json"
    with open(tokens_path, "w", encoding="utf-8") as f:
        json.dump(all_tokenized, f, ensure_ascii=False)

    # 文書一覧保存
    doc_list = {}
    for chunk in all_chunks:
        did = chunk["doc_id"]
        if did not in doc_list:
            doc_list[did] = {"filename": chunk["filename"], "chunk_count": 0}
        doc_list[did]["chunk_count"] += 1

    doc_list_path = INDEX_DIR / "documents.json"
    with open(doc_list_path, "w", encoding="utf-8") as f:
        json.dump(doc_list, f, ensure_ascii=False, indent=2)

    print(f"✅ インデックス保存完了: {INDEX_DIR}")
    print(f"   チャンク: {chunks_path}")
    print(f"   文書数: {len(doc_list)}")


if __name__ == "__main__":
    build_index()
