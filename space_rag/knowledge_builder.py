"""
宇宙分野ナレッジベース構築スクリプト

収集したドキュメントをチャンキング・前処理して、
RAGエンジンが使えるJSONL形式のインデックスに変換する。

実行方法:
    # 単一ファイルを処理
    uv run python -m space_rag.knowledge_builder process path/to/document.pdf

    # ディレクトリ全体を処理
    uv run python -m space_rag.knowledge_builder build data/space_docs/

    # 宇宙用語辞書からチャンクを生成
    uv run python -m space_rag.knowledge_builder glossary
"""

from __future__ import annotations

import json
import hashlib
import re
import sys
import logging
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import Iterator

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
SPACE_KB_DIR = DATA_DIR / "space_kb"
SPACE_DOCS_DIR = DATA_DIR / "space_docs"


@dataclass
class Chunk:
    """知識ベースの1チャンク"""
    chunk_id: str
    doc_id: str
    source: str          # nasa_ntrs / jaxa / esa / arxiv / glossary
    title: str
    text: str
    url: str = ""
    page: int = 0
    section: str = ""
    language: str = "ja"  # ja / en
    category: str = ""    # thermal / structures / aocs / etc.
    keywords: list[str] = None

    def __post_init__(self):
        if self.keywords is None:
            self.keywords = []


# ============================================================
# チャンキング戦略（技術文書向け）
# ============================================================

class TechDocChunker:
    """
    技術文書向けのチャンキング戦略。

    宇宙分野の文書はセクション構造が明確なため、
    セクション境界を優先してチャンクを分割する。

    方針:
    - 目標サイズ: 512〜1024文字（日本語）/ 400〜800トークン（英語）
    - セクション境界（見出し）で優先的に分割
    - 表・数式を含むチャンクは独立させる
    - 前後のオーバーラップを128文字追加して文脈を保持
    """

    def __init__(
        self,
        target_size: int = 800,
        overlap: int = 128,
        min_size: int = 50,
    ):
        self.target_size = target_size
        self.overlap = overlap
        self.min_size = min_size

        # 見出しパターン（日本語・英語共通）
        self.section_patterns = [
            # 日本語: "1.2.3 タイトル" / "第1章 タイトル"
            re.compile(r'^(\d+\.)+\d*\s+\S'),
            re.compile(r'^第\d+[章節]\s+\S'),
            # 英語: "1.2.3 Title" / "SECTION 1.2"
            re.compile(r'^(SECTION|CHAPTER|APPENDIX)\s+\d', re.IGNORECASE),
            re.compile(r'^\d+\.\d+\s+[A-Z]'),
            # マークダウン見出し
            re.compile(r'^#{1,4}\s+\S'),
        ]

    def chunk(self, text: str, doc_id: str, source: str, **meta) -> list[Chunk]:
        """テキストをチャンクのリストに分割する"""
        if not text or len(text) < self.min_size:
            return []

        # 段落に分割
        paragraphs = self._split_paragraphs(text)

        # セクション境界でグループ化してチャンクを生成
        chunks = []
        current_text = ""
        current_section = ""
        chunk_index = 0

        for para in paragraphs:
            # セクション見出しの検出
            is_heading = any(p.match(para.strip()) for p in self.section_patterns)

            if is_heading:
                current_section = para.strip()[:80]

            # チャンクサイズのチェック
            if len(current_text) + len(para) > self.target_size and current_text:
                # チャンクを確定
                chunk = self._make_chunk(
                    text=current_text,
                    doc_id=doc_id,
                    source=source,
                    section=current_section,
                    index=chunk_index,
                    **meta,
                )
                if chunk:
                    chunks.append(chunk)
                    chunk_index += 1

                # オーバーラップ: 前のチャンクの末尾を次に引き継ぐ
                current_text = current_text[-self.overlap:] + "\n" + para
            else:
                current_text += "\n" + para if current_text else para

        # 残りのテキスト
        if current_text and len(current_text) >= self.min_size:
            chunk = self._make_chunk(
                text=current_text,
                doc_id=doc_id,
                source=source,
                section=current_section,
                index=chunk_index,
                **meta,
            )
            if chunk:
                chunks.append(chunk)

        return chunks

    def _split_paragraphs(self, text: str) -> list[str]:
        """テキストを段落リストに分割する"""
        # 空行区切りで段落に分割
        paragraphs = re.split(r'\n\s*\n', text)
        # 空段落を除去し、長すぎる段落をさらに分割
        result = []
        for para in paragraphs:
            para = para.strip()
            if not para:
                continue
            if len(para) > self.target_size * 2:
                # 文単位でさらに分割
                sentences = re.split(r'(?<=[。．.!?])\s*', para)
                current = ""
                for sent in sentences:
                    if len(current) + len(sent) > self.target_size and current:
                        result.append(current)
                        current = sent
                    else:
                        current += sent
                if current:
                    result.append(current)
            else:
                result.append(para)
        return result

    def _make_chunk(
        self,
        text: str,
        doc_id: str,
        source: str,
        section: str,
        index: int,
        **meta,
    ) -> Chunk | None:
        """チャンクオブジェクトを生成する"""
        text = self._clean_text(text)
        if len(text) < self.min_size:
            return None

        # chunk_id: doc_id + インデックスのハッシュ
        raw_id = f"{doc_id}_{index}"
        chunk_id = raw_id + "_" + hashlib.md5(text[:100].encode()).hexdigest()[:6]

        return Chunk(
            chunk_id=chunk_id,
            doc_id=doc_id,
            source=source,
            title=meta.get("title", ""),
            text=text,
            url=meta.get("url", ""),
            page=meta.get("page", 0),
            section=section,
            language=self._detect_language(text),
            category=meta.get("category", ""),
            keywords=meta.get("keywords", []),
        )

    def _clean_text(self, text: str) -> str:
        """テキストのノイズを除去する"""
        # 複数の空白行を1つに
        text = re.sub(r'\n{3,}', '\n\n', text)
        # 行頭のページ番号パターンを除去
        text = re.sub(r'^\s*-?\s*\d+\s*-?\s*$', '', text, flags=re.MULTILINE)
        # ヘッダー・フッターのパターンを除去（JERG等）
        text = re.sub(r'^(JAXA|NASA|ESA)\s+(技術参照文書|Technical Report)\s*$', '', text, flags=re.MULTILINE)
        return text.strip()

    def _detect_language(self, text: str) -> str:
        """テキストの言語を検出する（簡易版）"""
        # 日本語文字（ひらがな・カタカナ・漢字）の割合で判定
        jp_chars = len(re.findall(r'[\u3040-\u9FFF]', text))
        if jp_chars / max(len(text), 1) > 0.1:
            return "ja"
        return "en"


# ============================================================
# PDF処理
# ============================================================

def extract_text_from_pdf(pdf_path: Path) -> list[dict]:
    """
    PDFからテキストを抽出する。

    Returns:
        [{"page": 1, "text": "..."}, ...] のリスト
    """
    try:
        from pypdf import PdfReader
    except ImportError:
        logger.error("pypdf がインストールされていません: uv add pypdf")
        return []

    pages = []
    reader = PdfReader(str(pdf_path))

    for page_num, page in enumerate(reader.pages, 1):
        try:
            text = page.extract_text() or ""
            if text.strip():
                pages.append({"page": page_num, "text": text})
        except Exception as e:
            logger.warning(f"Page {page_num} extraction failed: {e}")

    return pages


# ============================================================
# ビルダー関数
# ============================================================

def build_from_pdf(
    pdf_path: Path,
    doc_id: str,
    source: str,
    title: str = "",
    url: str = "",
    category: str = "",
    output_dir: Path | None = None,
) -> list[Chunk]:
    """
    PDFファイルから知識ベースチャンクを生成する。

    Args:
        pdf_path: PDFファイルのパス
        doc_id: 文書ID（例: JAXA-2024-001）
        source: データソース（nasa_ntrs / jaxa / esa / arxiv）
        title: 文書タイトル
        url: 元文書のURL
        category: 宇宙分野カテゴリ
        output_dir: 出力先ディレクトリ（Noneの場合は保存しない）

    Returns:
        生成されたChunkのリスト
    """
    logger.info(f"Processing PDF: {pdf_path}")
    pages = extract_text_from_pdf(pdf_path)

    if not pages:
        logger.warning(f"No text extracted from {pdf_path}")
        return []

    chunker = TechDocChunker(target_size=800, overlap=128)
    all_chunks = []

    for page_data in pages:
        chunks = chunker.chunk(
            text=page_data["text"],
            doc_id=doc_id,
            source=source,
            title=title,
            url=url,
            page=page_data["page"],
            category=category,
        )
        all_chunks.extend(chunks)

    logger.info(f"Generated {len(all_chunks)} chunks from {pdf_path.name}")

    if output_dir:
        _save_chunks(all_chunks, output_dir, doc_id)

    return all_chunks


def build_from_text(
    text: str,
    doc_id: str,
    source: str,
    title: str = "",
    url: str = "",
    category: str = "",
    output_dir: Path | None = None,
) -> list[Chunk]:
    """テキストから知識ベースチャンクを生成する"""
    chunker = TechDocChunker(target_size=800, overlap=128)
    chunks = chunker.chunk(
        text=text,
        doc_id=doc_id,
        source=source,
        title=title,
        url=url,
        category=category,
    )

    if output_dir:
        _save_chunks(chunks, output_dir, doc_id)

    return chunks


def build_from_glossary(output_dir: Path | None = None) -> list[Chunk]:
    """
    宇宙用語辞書からチャンクを生成する。
    用語辞書の内容を知識ベースに追加することで、
    RAGが用語定義を返せるようにする。
    """
    from space_rag.space_glossary import SPACE_TERMS, ABBREVIATIONS

    chunks = []

    # 略語辞書チャンク（カテゴリ別にまとめる）
    abbr_by_cat: dict[str, list[str]] = {}
    for abbr, info in ABBREVIATIONS.items():
        cat = info.get("category", "general")
        if cat not in abbr_by_cat:
            abbr_by_cat[cat] = []
        abbr_by_cat[cat].append(
            f"{abbr}: {info['full']} ({info['ja']})"
        )

    for cat, entries in abbr_by_cat.items():
        text = f"【宇宙略語辞書: {cat}】\n" + "\n".join(entries)
        chunk = Chunk(
            chunk_id=f"glossary_abbr_{cat}",
            doc_id="space_glossary",
            source="glossary",
            title=f"宇宙略語辞書 ({cat})",
            text=text,
            category=cat,
            language="ja",
        )
        chunks.append(chunk)

    # 用語詳細チャンク（用語ごと）
    for term in SPACE_TERMS:
        text_parts = [
            f"【{term.ja} / {term.en}】",
        ]
        if term.abbr:
            text_parts.append(f"略語: {term.abbr}")
        text_parts.append(f"説明: {term.description}")
        if term.synonyms_ja:
            text_parts.append(f"別称: {', '.join(term.synonyms_ja)}")
        if term.related:
            # 関連略語を展開
            related_full = []
            for abbr in term.related:
                info = ABBREVIATIONS.get(abbr, {})
                if info:
                    related_full.append(f"{abbr} ({info.get('full', '')})")
                else:
                    related_full.append(abbr)
            text_parts.append(f"関連: {', '.join(related_full)}")

        text = "\n".join(text_parts)
        cid = "glossary_term_" + hashlib.md5(term.en.encode()).hexdigest()[:8]

        chunk = Chunk(
            chunk_id=cid,
            doc_id="space_glossary",
            source="glossary",
            title=f"{term.ja} ({term.en})",
            text=text,
            category=term.category,
            language="ja",
            keywords=[term.ja, term.en] + (([term.abbr]) if term.abbr else []),
        )
        chunks.append(chunk)

    logger.info(f"Generated {len(chunks)} chunks from glossary")

    if output_dir:
        _save_chunks(chunks, output_dir, "glossary")

    return chunks


def build_directory(
    docs_dir: Path,
    output_dir: Path | None = None,
) -> list[Chunk]:
    """
    ディレクトリ内の全PDFをバッチ処理する。

    docs_dir/
      nasa/   → source="nasa_ntrs"
      jaxa/   → source="jaxa"
      esa/    → source="esa"
      arxiv/  → source="arxiv"
    """
    if not docs_dir.exists():
        logger.error(f"Directory not found: {docs_dir}")
        return []

    all_chunks = []
    out = output_dir or SPACE_KB_DIR

    # ディレクトリ名からソースを判定
    source_map = {
        "nasa": "nasa_ntrs",
        "jaxa": "jaxa",
        "esa": "esa",
        "arxiv": "arxiv",
    }

    for subdir in sorted(docs_dir.iterdir()):
        if not subdir.is_dir():
            continue

        source = source_map.get(subdir.name.lower(), subdir.name.lower())

        for pdf_file in sorted(subdir.glob("*.pdf")):
            # ファイル名をdoc_idとして使用
            doc_id = pdf_file.stem.replace(" ", "_")
            chunks = build_from_pdf(
                pdf_path=pdf_file,
                doc_id=f"{source.upper()}_{doc_id}",
                source=source,
                output_dir=out,
            )
            all_chunks.extend(chunks)

    logger.info(f"Total chunks built: {len(all_chunks)}")
    return all_chunks


def _save_chunks(chunks: list[Chunk], output_dir: Path, doc_id: str):
    """チャンクをJSONL形式で保存する"""
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{doc_id}.jsonl"

    with open(output_path, "w", encoding="utf-8") as f:
        for chunk in chunks:
            f.write(json.dumps(asdict(chunk), ensure_ascii=False) + "\n")

    logger.info(f"Saved {len(chunks)} chunks to {output_path}")


# ============================================================
# CLI
# ============================================================

def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    command = sys.argv[1]

    if command == "glossary":
        # 用語辞書からチャンク生成
        chunks = build_from_glossary(output_dir=SPACE_KB_DIR)
        print(f"Generated {len(chunks)} glossary chunks -> {SPACE_KB_DIR}/glossary.jsonl")

    elif command == "process" and len(sys.argv) >= 3:
        # 単一ファイルを処理
        pdf_path = Path(sys.argv[2])
        doc_id = pdf_path.stem
        source = sys.argv[3] if len(sys.argv) > 3 else "unknown"
        chunks = build_from_pdf(
            pdf_path=pdf_path,
            doc_id=doc_id,
            source=source,
            output_dir=SPACE_KB_DIR,
        )
        print(f"Generated {len(chunks)} chunks from {pdf_path.name}")

    elif command == "build":
        # ディレクトリ全体を処理
        docs_dir = Path(sys.argv[2]) if len(sys.argv) >= 3 else SPACE_DOCS_DIR
        chunks = build_directory(docs_dir, output_dir=SPACE_KB_DIR)
        print(f"Total: {len(chunks)} chunks -> {SPACE_KB_DIR}")

    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
