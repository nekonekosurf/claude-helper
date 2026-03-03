"""
文書の「翻訳」パイプライン

硬い宇宙文書 → 平易な文章 への自動変換

戦略:
1. LLMによるパラフレーズ生成
2. 原文と平易版の両方をインデックス化
3. 検索は平易版で行い、回答は原文を引用
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class TranslatedChunk:
    """原文と平易版を対応付けたチャンク"""
    chunk_id: str
    original_text: str          # 原文（宇宙文書の硬い表現）
    simplified_text: str        # 平易版（検索用）
    summary: str                # 1文サマリー
    keywords: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "original_text": self.original_text,
            "simplified_text": self.simplified_text,
            "summary": self.summary,
            "keywords": self.keywords,
            "metadata": self.metadata
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TranslatedChunk":
        return cls(
            chunk_id=data["chunk_id"],
            original_text=data["original_text"],
            simplified_text=data["simplified_text"],
            summary=data.get("summary", ""),
            keywords=data.get("keywords", []),
            metadata=data.get("metadata", {})
        )


class DocumentTranslator:
    """
    文書の平易化パイプライン

    使い方:
        translator = DocumentTranslator(llm_client=my_llm)
        translated = translator.translate_chunks(chunks)
        translator.save("data/translated_chunks.json")
    """

    # 宇宙文書特有の硬い表現の置換ルール（ルールベース事前処理）
    SIMPLIFICATION_RULES = [
        ("当該", "この"),
        ("に係る", "に関する"),
        ("に関し", "について"),
        ("における", "での"),
        ("のための", "のための"),
        ("実施する", "行う"),
        ("確保する", "保証する"),
        ("及び", "および"),
        ("並びに", "また"),
        ("従って", "そのため"),
        ("ものとする", "こととする"),
        ("得る", "できる"),
        ("行うこと", "行う"),
        ("有する", "持つ"),
        ("を要する", "が必要"),
        ("当然のことながら", ""),
        ("なお、", "また、"),
    ]

    SPACE_TRANSLATE_PROMPT = """あなたは宇宙・航空分野の文書を平易な日本語に書き直す専門家です。

以下の宇宙文書のテキストを、技術的な正確さを保ちながら、
エンジニアリングの初学者でも理解できる平易な日本語に書き直してください。

【ルール】
1. 技術用語はそのまま使用（冗長化、フォールトトレランス等）
2. 受動態を能動態に変換
3. 「〜のこと」「〜するものとする」などの形式的表現を簡潔に
4. 箇条書き・数値・単位はそのまま保持
5. 内容を削らず、表現のみ変更

【原文】
{text}

【平易版】（原文の内容を分かりやすく書き直したもの）:"""

    SUMMARY_PROMPT = """以下のテキストを1文（50文字以内）で要約してください。
検索クエリのキーワードになりそうな重要語を含めてください。

テキスト:
{text}

要約:"""

    KEYWORDS_PROMPT = """以下のテキストから重要なキーワードを5〜10個抽出してください。
技術用語、システム名、規格名を優先してください。

テキスト:
{text}

キーワード（カンマ区切り）:"""

    def __init__(
        self,
        llm_client: Any = None,
        batch_size: int = 10,
        delay_seconds: float = 0.5   # APIレート制限対策
    ) -> None:
        self.llm = llm_client
        self.batch_size = batch_size
        self.delay_seconds = delay_seconds
        self._cache: dict[str, TranslatedChunk] = {}

    def translate_chunks(
        self,
        chunks: list[dict[str, Any]],
        skip_short: int = 100
    ) -> list[TranslatedChunk]:
        """
        複数チャンクを一括翻訳

        Args:
            chunks: [{"chunk_id": ..., "text": ..., "metadata": {...}}, ...]
            skip_short: この文字数以下のチャンクはスキップ

        Returns:
            TranslatedChunk のリスト
        """
        results = []
        total = len(chunks)

        for i, chunk in enumerate(chunks):
            chunk_id = chunk.get("chunk_id", f"chunk_{i}")
            text = chunk.get("text", "")
            metadata = chunk.get("metadata", {})

            if len(text) < skip_short:
                # 短いチャンクはそのまま
                results.append(TranslatedChunk(
                    chunk_id=chunk_id,
                    original_text=text,
                    simplified_text=text,
                    summary=text[:50],
                    metadata=metadata
                ))
                continue

            # キャッシュチェック
            if chunk_id in self._cache:
                results.append(self._cache[chunk_id])
                continue

            translated = self.translate_single(chunk_id, text, metadata)
            results.append(translated)
            self._cache[chunk_id] = translated

            # 進捗表示
            if (i + 1) % 10 == 0:
                print(f"[DocumentTranslator] {i + 1}/{total} 処理完了")

            # レート制限対策
            if self.llm and i > 0 and i % self.batch_size == 0:
                time.sleep(self.delay_seconds)

        return results

    def translate_single(
        self,
        chunk_id: str,
        text: str,
        metadata: dict[str, Any] | None = None
    ) -> TranslatedChunk:
        """
        単一チャンクの翻訳

        1. ルールベース事前処理（LLMなしでも効果的）
        2. LLMによる平易化
        3. サマリー生成
        4. キーワード抽出
        """
        # Step 1: ルールベース事前処理
        preprocessed = self._rule_based_simplify(text)

        # Step 2: LLMによる平易化
        if self.llm:
            simplified = self._llm_simplify(preprocessed)
            summary = self._llm_summarize(simplified)
            keywords = self._llm_extract_keywords(simplified)
        else:
            # LLMなし: ルールベースのみ
            simplified = preprocessed
            summary = text[:80] + ("..." if len(text) > 80 else "")
            keywords = self._rule_based_keywords(text)

        return TranslatedChunk(
            chunk_id=chunk_id,
            original_text=text,
            simplified_text=simplified,
            summary=summary,
            keywords=keywords,
            metadata=metadata or {}
        )

    def _rule_based_simplify(self, text: str) -> str:
        """ルールベースの事前簡易化"""
        result = text
        for old, new in self.SIMPLIFICATION_RULES:
            result = result.replace(old, new)
        return result

    def _llm_simplify(self, text: str) -> str:
        """LLMによる平易化"""
        prompt = self.SPACE_TRANSLATE_PROMPT.format(text=text)
        try:
            return self.llm.complete(prompt).strip()
        except Exception as e:
            print(f"[DocumentTranslator] 平易化失敗: {e}")
            return text

    def _llm_summarize(self, text: str) -> str:
        """LLMによる1文サマリー生成"""
        prompt = self.SUMMARY_PROMPT.format(text=text[:1000])
        try:
            return self.llm.complete(prompt).strip()
        except Exception:
            return text[:50] + "..."

    def _llm_extract_keywords(self, text: str) -> list[str]:
        """LLMによるキーワード抽出"""
        prompt = self.KEYWORDS_PROMPT.format(text=text[:800])
        try:
            response = self.llm.complete(prompt).strip()
            keywords = [k.strip() for k in response.split(",") if k.strip()]
            return keywords[:10]
        except Exception:
            return self._rule_based_keywords(text)

    def _rule_based_keywords(self, text: str) -> list[str]:
        """ルールベースのキーワード抽出（フォールバック）"""
        import re
        keywords = []
        # 規格・文書番号
        std_pattern = re.compile(r'(JERG|JAXA|JIS|ISO|MIL|IEEE)[- ]?[\w\-\.]+')
        keywords.extend(m.group(0) for m in std_pattern.finditer(text))
        # 数字+単位
        unit_pattern = re.compile(r'\d+(?:\.\d+)?\s*(?:km|m|kg|W|V|A|MHz|GHz|dB|°C|Pa|N)')
        keywords.extend(m.group(0) for m in unit_pattern.finditer(text))
        return list(set(keywords))[:10]

    def create_dual_index_records(
        self,
        translated_chunks: list[TranslatedChunk]
    ) -> list[dict[str, Any]]:
        """
        原文と平易版の両方を含むデュアルインデックスレコードを生成

        検索エンジンが平易版でヒットしても原文を返せるよう両方を保持
        """
        records = []
        for tc in translated_chunks:
            # 平易版レコード（検索用）
            records.append({
                "chunk_id": f"{tc.chunk_id}_simplified",
                "text": tc.simplified_text,
                "search_text": tc.simplified_text,  # ベクトル化対象
                "original_chunk_id": tc.chunk_id,
                "index_type": "simplified",
                "metadata": {
                    **tc.metadata,
                    "summary": tc.summary,
                    "keywords": tc.keywords
                }
            })
            # 原文レコード（引用用）
            records.append({
                "chunk_id": f"{tc.chunk_id}_original",
                "text": tc.original_text,
                "search_text": tc.original_text,
                "original_chunk_id": tc.chunk_id,
                "index_type": "original",
                "metadata": {
                    **tc.metadata,
                    "summary": tc.summary,
                    "keywords": tc.keywords
                }
            })
        return records

    def save(self, path: str) -> None:
        """翻訳済みチャンクをJSONに保存"""
        data = [tc.to_dict() for tc in self._cache.values()]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"[DocumentTranslator] 保存完了: {path} ({len(data)} チャンク)")

    def load(self, path: str) -> list[TranslatedChunk]:
        """保存した翻訳済みチャンクを読み込む"""
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        chunks = [TranslatedChunk.from_dict(d) for d in data]
        for tc in chunks:
            self._cache[tc.chunk_id] = tc
        print(f"[DocumentTranslator] 読み込み完了: {len(chunks)} チャンク")
        return chunks
