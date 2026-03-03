"""
document_processor.py - 公的文書の前処理パイプライン

硬い公的文書（政府文書、技術仕様書、法令文書等）を
AIシステムで検索・活用しやすくするための前処理を行う。

処理ステップ:
  1. テキスト抽出・クリーニング
  2. 文書構造解析（階層セクション検出）
  3. LLMによる平易化（原文保持 + 平易版並存）
  4. パラフレーズ生成（検索クエリ候補）
  5. セクションサマリー生成
  6. ブレッドクラム生成（階層パス表示）
  7. メタデータ付与（対象読者、キーワード等）
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


# =========================================================
# データモデル
# =========================================================

@dataclass
class SectionNode:
    """文書の1セクション（階層ノード）"""
    section_id: str          # 例: "3.2.4"
    title: str               # 例: "緊急時対応手順"
    level: int               # 階層の深さ（1始まり）
    text_original: str       # 原文テキスト
    text_plain: str = ""     # LLMによる平易化テキスト
    summary: str = ""        # セクション要約（100字以内）
    paraphrases: list[str] = field(default_factory=list)   # 検索用別表現
    keywords: list[str] = field(default_factory=list)       # 抽出キーワード
    breadcrumb: str = ""     # 階層パス "第3章 > 3.2 安全管理 > 3.2.4 緊急時対応"
    target_audience: str = ""  # 想定読者
    parent_id: str = ""      # 親セクションのID
    children_ids: list[str] = field(default_factory=list)
    cross_refs: list[str] = field(default_factory=list)   # 他文書への参照

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ProcessedDocument:
    """前処理済み文書全体"""
    doc_id: str
    title: str
    sections: list[SectionNode] = field(default_factory=list)
    glossary: dict[str, str] = field(default_factory=dict)  # 用語 → 平易な説明
    abbreviations: dict[str, str] = field(default_factory=dict)  # 略語 → 展開

    def to_dict(self) -> dict:
        return {
            "doc_id": self.doc_id,
            "title": self.title,
            "sections": [s.to_dict() for s in self.sections],
            "glossary": self.glossary,
            "abbreviations": self.abbreviations,
        }


# =========================================================
# 文書構造解析
# =========================================================

# セクション番号パターン
# 例: "3.2.4", "3.2.4.5", "第3章", "（3）"
_SECTION_PATTERNS = [
    # X.Y.Z.W 形式（4階層まで）
    re.compile(r'^(\d+(?:\.\d+){0,3})\s+(.{2,50})', re.MULTILINE),
    # 第X章 / 第X節 形式
    re.compile(r'^(第\s*\d+\s*[章節条項])\s+(.{1,40})', re.MULTILINE),
    # （X） / (X) 形式
    re.compile(r'^[（(](\d+)[）)]\s+(.{1,40})', re.MULTILINE),
]

_TOC_LINE = re.compile(r'\.{5,}|…{3,}')  # 目次のドット列を検出


def parse_structure(text: str, doc_id: str = "") -> list[SectionNode]:
    """
    文書テキストからセクション構造を解析してSectionNodeリストを返す。

    戦略:
    - セクション番号パターンを検出してスプリットポイントを特定
    - 各セクションのテキストを抽出
    - 階層の深さを番号から計算
    - 親子関係・ブレッドクラムを構築
    """
    sections: list[SectionNode] = []
    splits: list[tuple[str, str, int, int]] = []  # (section_id, title, level, pos)

    for pat in _SECTION_PATTERNS:
        for m in pat.finditer(text):
            line = text[max(0, m.start()-5):m.end()]
            # 目次ドット列を含む行は除外
            if _TOC_LINE.search(line):
                continue
            sec_id = m.group(1).strip()
            sec_title = m.group(2).strip()
            level = _calc_level(sec_id)
            splits.append((sec_id, sec_title, level, m.start()))

    # 重複・目次を除去して位置順にソート
    splits = sorted(set(splits), key=lambda x: x[3])

    if not splits:
        # 構造が検出できない場合は文書全体を1セクションとして扱う
        return [SectionNode(
            section_id="1",
            title="本文",
            level=1,
            text_original=text.strip(),
        )]

    # 各スプリット間のテキストを取得
    for i, (sec_id, sec_title, level, pos) in enumerate(splits):
        end_pos = splits[i + 1][3] if i + 1 < len(splits) else len(text)
        sec_text = text[pos:end_pos].strip()

        node = SectionNode(
            section_id=sec_id,
            title=sec_title,
            level=level,
            text_original=sec_text,
        )
        sections.append(node)

    # 親子関係を構築
    _build_hierarchy(sections)

    return sections


def _calc_level(section_id: str) -> int:
    """セクション番号から階層の深さを計算する。
    例: "3" → 1, "3.2" → 2, "3.2.4" → 3, "第3章" → 1
    """
    if re.match(r'^\d+$', section_id):
        return 1
    if re.match(r'第', section_id):
        return 1
    dots = section_id.count('.')
    return dots + 1


def _build_hierarchy(sections: list[SectionNode]):
    """セクションリストに親子関係とブレッドクラムを付与する（in-place）"""
    # stack: 現在の各レベルのノード
    stack: list[SectionNode] = []

    for node in sections:
        # 自分より深いまたは同レベルをpop
        while stack and stack[-1].level >= node.level:
            stack.pop()

        if stack:
            parent = stack[-1]
            node.parent_id = parent.section_id
            parent.children_ids.append(node.section_id)
            node.breadcrumb = parent.breadcrumb + " > " + f"{node.section_id} {node.title}"
        else:
            node.breadcrumb = f"{node.section_id} {node.title}"

        stack.append(node)


# =========================================================
# 相互参照抽出
# =========================================================

def extract_cross_refs(text: str, ref_pattern: re.Pattern | None = None) -> list[str]:
    """
    テキストから他文書への参照を抽出する。

    ref_pattern: 文書番号の正規表現（デフォルトはJERGパターン）
    """
    if ref_pattern is None:
        ref_pattern = re.compile(r'JERG-\d{1,2}-\d{3}(?:-[A-Z]+\d+)?')
    return sorted(set(ref_pattern.findall(text)))


# =========================================================
# LLMによる平易化・パラフレーズ生成
# =========================================================

PLAIN_LANGUAGE_PROMPT = """\
あなたは行政文書・技術仕様書の専門翻訳者です。
以下の硬い公的文書の文章を、一般の読者が理解できる平易な日本語に書き換えてください。

【ルール】
- 意味・内容を変えない（情報の正確性を最優先）
- 専門用語はそのまま使い、括弧内に平易な説明を追加する
  例: 「テレメトリ（遠隔地からのデータ送信）」
- 「当該」→「この/その」、「資する」→「役立てる」、「了する」→「終える」等
- 受動態を避け、能動態に変える
- 長い一文は分割する
- 箇条書きを活用する
- JSON形式で返すこと（説明文なし）

出力形式:
{
  "plain_text": "平易化したテキスト",
  "terms": [{"term": "専門用語", "explanation": "平易な説明"}],
  "paraphrases": ["検索クエリ候補1", "検索クエリ候補2", "検索クエリ候補3"],
  "summary": "このセクションの要約（50字以内）",
  "keywords": ["キーワード1", "キーワード2"],
  "target_audience": "想定読者（例: 設計担当者、運用担当者）"
}

【原文】
{text}
"""


def process_with_llm(
    client: Any,
    model: str,
    node: SectionNode,
    max_chars: int = 2000,
) -> SectionNode:
    """
    LLMを使ってセクションノードの平易化・パラフレーズ生成を行う。

    client: OpenAI互換クライアント
    model: モデル名
    node: 処理対象のSectionNode
    max_chars: LLMに渡すテキストの最大文字数（超える場合は先頭を使用）
    """
    from src.llm_client import chat

    # 長すぎる場合は先頭部分のみ使用
    text_for_llm = node.text_original[:max_chars]
    if len(node.text_original) > max_chars:
        text_for_llm += "\n...[以降省略]"

    prompt = PLAIN_LANGUAGE_PROMPT.format(text=text_for_llm)
    messages = [{"role": "user", "content": prompt}]

    try:
        response = chat(client, model, messages, tools=None)
        content = response.content or ""

        # JSONブロック抽出
        if "```" in content:
            start = content.index("```") + 3
            if content[start:start+4] == "json":
                start += 4
            end = content.index("```", start)
            content = content[start:end].strip()

        data = json.loads(content)
        node.text_plain = data.get("plain_text", "")
        node.paraphrases = data.get("paraphrases", [])
        node.summary = data.get("summary", "")
        node.keywords = data.get("keywords", [])
        node.target_audience = data.get("target_audience", "")

    except Exception as e:
        # LLM失敗時は空のまま継続
        print(f"  LLM処理失敗 [{node.section_id}]: {e}")

    return node


# =========================================================
# バッチ処理パイプライン
# =========================================================

def process_document(
    text: str,
    doc_id: str,
    title: str,
    client: Any = None,
    model: str = "",
    use_llm: bool = True,
    llm_batch_size: int = 10,
    verbose: bool = True,
) -> ProcessedDocument:
    """
    文書全体を前処理してProcessedDocumentを返す。

    text: 文書の全テキスト
    doc_id: 文書ID
    title: 文書タイトル
    client: LLMクライアント（use_llm=Trueの場合必須）
    model: LLMモデル名
    use_llm: LLMによる平易化を行うか
    llm_batch_size: LLMを呼び出すセクション数の上限（コスト制御）
    """
    doc = ProcessedDocument(doc_id=doc_id, title=title)

    if verbose:
        print(f"[document_processor] {doc_id}: 構造解析中...")

    # Step 1: 構造解析
    sections = parse_structure(text, doc_id)
    if verbose:
        print(f"  {len(sections)} セクションを検出")

    # Step 2: 相互参照抽出
    for node in sections:
        node.cross_refs = extract_cross_refs(node.text_original)

    # Step 3: LLM平易化（bodyセクションのみ、上限あり）
    if use_llm and client and model:
        body_sections = [s for s in sections if len(s.text_original) > 50]
        target_sections = body_sections[:llm_batch_size]

        if verbose:
            print(f"  LLM平易化: {len(target_sections)}/{len(body_sections)} セクション")

        for i, node in enumerate(target_sections):
            if verbose and (i + 1) % 5 == 0:
                print(f"    {i+1}/{len(target_sections)} 完了...")
            process_with_llm(client, model, node)

    doc.sections = sections
    return doc


def save_processed_document(doc: ProcessedDocument, output_path: str | Path):
    """処理済み文書をJSONで保存する"""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(doc.to_dict(), f, ensure_ascii=False, indent=2)

    print(f"Saved: {output_path}")
    print(f"  Sections: {len(doc.sections)}")


# =========================================================
# スタンドアロン実行例
# =========================================================

if __name__ == "__main__":
    import sys

    # サンプル文書（テスト用）
    sample_text = """
1 適用範囲

本規格は、宇宙機に搭載する熱制御機器の設計、製造及び試験に適用する。
当該施設の運用に資する全ての熱設計パラメータは、本文書に定める要件に準拠しなければならない。

1.1 目的

本文書の目的は、宇宙機の熱的環境における機器の信頼性を確保するための
技術的要件を規定することにある。

1.2 適用範囲の除外

以下に掲げる事項は本規格の適用範囲外とする。
（1）地上試験設備に係る熱管理
（2）打上げ段階における熱制御

2 引用文書

2.1 適用文書
本規格の適用にあたり、以下の文書を適用する。
JERG-2-100 宇宙機一般要求仕様
JERG-0-051 熱設計標準

3 要求事項

3.1 熱設計要件

宇宙機の熱制御システムは、軌道上における全運用モードにおいて、
搭載機器の温度を許容範囲内に維持しなければならない。

3.1.1 温度許容範囲

各機器の動作温度範囲は、設計温度範囲（DTR）として規定し、
その上限値及び下限値に対してそれぞれ5℃以上のマージンを確保すること。

3.2 熱解析要件

3.2.1 熱数学モデル

熱数学モデル（TMM）は、軌道熱環境シミュレーションツールを用いて構築し、
試験結果との相関検証を実施しなければならない。
    """

    doc = process_document(
        text=sample_text,
        doc_id="SAMPLE-001",
        title="宇宙機熱制御要件サンプル",
        use_llm=False,  # LLMなしでテスト
        verbose=True,
    )

    print("\n=== 解析結果 ===")
    for section in doc.sections:
        indent = "  " * (section.level - 1)
        print(f"{indent}[{section.section_id}] {section.title}")
        print(f"{indent}  ブレッドクラム: {section.breadcrumb}")
        if section.cross_refs:
            print(f"{indent}  参照: {section.cross_refs}")
    print()

    save_processed_document(doc, "/tmp/sample_processed.json")
