"""
version_tracker.py - バージョン管理・差分検出

機能:
  - ファイル名パターンからバージョンを自動抽出
  - ファイルの更新日時・ハッシュ比較で最新版を判定
  - LLM を使ったテキスト差分の要約（変更点の自動検出）
  - 同一文書の複数バージョン追跡
"""

from __future__ import annotations

import difflib
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────
# バージョン番号の抽出
# ──────────────────────────────────────────────────

# ファイル名からバージョンを推測するパターン（優先度順）
VERSION_PATTERNS: list[tuple[str, str]] = [
    # rev3 / Rev3 / REV3（v より先に評価して "rev2" を取りこぼさない）
    (r'[Rr][Ee][Vv]\.?(\d+)', r'rev\1'),
    # v1.2.3 / V1.2 / v3
    (r'[vV](\d+(?:\.\d+)*)', r'v\1'),
    # 第3版 / 第3改定（数字パターンより先に評価）
    (r'第(\d+)[版改]', r'第\1版'),
    # _最終 / _final / _確定
    (r'[_\-](最終|final|確定|FINAL)', r'最終'),
    # 日付形式 2024-01-15 / 2024_01_15（ハイフン/アンダースコア区切りのみ）
    (r'(\d{4}[-_]\d{2}[-_]\d{2})', r'\1'),
    # _2 / -2 / (2) のような末尾の数字
    (r'[_\-\(](\d{1,3})[_\-\).]', r'\1'),
]


def extract_version_from_filename(filename: str) -> str:
    """
    ファイル名からバージョン番号を推測する。

    例:
      "マニフェストv3.pdf"        → "v3"
      "申請書_rev2_20240115.docx" → "rev2"
      "許可証_2024-03-01.pdf"     → "2024-03-01"
      "誓約書_最終.pdf"           → "最終"
      "稟議書.pdf"                → "1.0"  (判定不能 → デフォルト)
    """
    stem = Path(filename).stem  # 拡張子を除いたファイル名

    for pattern, fmt in VERSION_PATTERNS:
        m = re.search(pattern, stem)
        if m:
            # fmt に \1 が含まれる場合は後方参照として展開
            if r'\1' in fmt:
                version = re.sub(pattern, fmt, m.group(0))
            else:
                version = fmt
            logger.debug("バージョン検出: %s → %s (pattern: %s)", filename, version, pattern)
            return version

    return "1.0"  # デフォルト


def parse_version_to_comparable(version: str) -> tuple[int, int, int, str]:
    """
    バージョン文字列を比較可能なタプルに変換する。
    数値が大きいほど新しいと見なす。

    Returns: (major, minor, patch, original)
    """
    # 数値パターンを全部抽出
    nums = re.findall(r'\d+', version)

    if not nums:
        return (0, 0, 0, version)

    major = int(nums[0]) if len(nums) > 0 else 0
    minor = int(nums[1]) if len(nums) > 1 else 0
    patch = int(nums[2]) if len(nums) > 2 else 0

    return (major, minor, patch, version)


def is_newer(version_a: str, version_b: str) -> bool:
    """version_a が version_b より新しければ True。"""
    return parse_version_to_comparable(version_a) > parse_version_to_comparable(version_b)


# ──────────────────────────────────────────────────
# 最新版の自動判定
# ──────────────────────────────────────────────────

@dataclass
class VersionCandidate:
    """バージョン候補ファイルの情報。"""
    file_path: Path
    file_name: str
    version_str: str
    mtime: datetime      # ファイル最終更新日時
    file_hash: str
    version_comparable: tuple[int, int, int, str]


def find_latest_version(file_paths: list[str | Path]) -> VersionCandidate | None:
    """
    複数のファイルから最新版を判定して返す。

    判定優先度:
      1. バージョン番号（v3 > v2 > v1）
      2. ファイル更新日時（新しいほど優先）

    Args:
        file_paths: 同一文書の複数バージョンファイルパス

    Returns:
        最新版の VersionCandidate、空リストの場合は None
    """
    import hashlib

    candidates: list[VersionCandidate] = []

    for p in file_paths:
        path = Path(p)
        if not path.exists():
            logger.warning("ファイルが存在しません: %s", p)
            continue

        version_str = extract_version_from_filename(path.name)
        mtime = datetime.fromtimestamp(path.stat().st_mtime)

        # ハッシュ計算
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        file_hash = h.hexdigest()

        candidates.append(VersionCandidate(
            file_path=path,
            file_name=path.name,
            version_str=version_str,
            mtime=mtime,
            file_hash=file_hash,
            version_comparable=parse_version_to_comparable(version_str),
        ))

    if not candidates:
        return None

    # バージョン番号で降順ソート → 同じなら更新日時で降順ソート
    candidates.sort(
        key=lambda c: (c.version_comparable[:3], c.mtime),
        reverse=True
    )

    return candidates[0]


# ──────────────────────────────────────────────────
# テキスト差分検出
# ──────────────────────────────────────────────────

@dataclass
class DiffResult:
    """2バージョン間の差分結果。"""
    added_lines: list[str]
    removed_lines: list[str]
    changed_sections: list[str]
    similarity_ratio: float      # 0.0 (完全に異なる) ～ 1.0 (同一)
    summary: str                 # 変更点の人間向けサマリー


def compute_text_diff(text_old: str, text_new: str) -> DiffResult:
    """
    2つのテキスト間の差分を計算する。

    Returns:
        DiffResult
    """
    old_lines = text_old.splitlines()
    new_lines = text_new.splitlines()

    matcher = difflib.SequenceMatcher(None, old_lines, new_lines)
    ratio = matcher.ratio()

    added: list[str] = []
    removed: list[str] = []
    changed: list[str] = []

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "insert":
            added.extend(new_lines[j1:j2])
        elif tag == "delete":
            removed.extend(old_lines[i1:i2])
        elif tag == "replace":
            removed.extend(old_lines[i1:i2])
            added.extend(new_lines[j1:j2])
            changed.append(
                f"[変更] 旧: {' / '.join(old_lines[i1:i2][:2])} "
                f"→ 新: {' / '.join(new_lines[j1:j2][:2])}"
            )

    # 人間向けサマリー生成
    parts: list[str] = []
    if added:
        parts.append(f"追加 {len(added)} 行")
    if removed:
        parts.append(f"削除 {len(removed)} 行")
    if not added and not removed:
        parts.append("変更なし")
    parts.append(f"類似度 {ratio:.1%}")

    summary = " / ".join(parts)
    if changed:
        summary += "\n主な変更点:\n" + "\n".join(changed[:5])
        if len(changed) > 5:
            summary += f"\n... 他 {len(changed) - 5} 件"

    return DiffResult(
        added_lines=added,
        removed_lines=removed,
        changed_sections=changed,
        similarity_ratio=ratio,
        summary=summary,
    )


def unified_diff_text(text_old: str, text_new: str, n: int = 3) -> str:
    """unified diff 形式のテキストを返す（ターミナル表示・ログ用）。"""
    old_lines = text_old.splitlines(keepends=True)
    new_lines = text_new.splitlines(keepends=True)
    diff = difflib.unified_diff(
        old_lines, new_lines,
        fromfile="旧バージョン",
        tofile="新バージョン",
        n=n,
    )
    return "".join(diff)


# ──────────────────────────────────────────────────
# LLM による差分サマリー生成
# ──────────────────────────────────────────────────

def summarize_diff_with_llm(
    diff_result: DiffResult,
    doc_type: str,
    llm_client=None,
    model: str = "gpt-oss-120b",
) -> str:
    """
    LLM を使って差分を人間が読みやすい日本語で説明する。

    Args:
        diff_result: compute_text_diff の結果
        doc_type: 文書種類（例: "マニフェスト", "申請書"）
        llm_client: OpenAI 互換クライアント（None の場合は rule-based サマリー返却）
        model: 使用モデル名

    Returns:
        日本語の変更点説明
    """
    if llm_client is None:
        return diff_result.summary

    # 差分の代表サンプルを作成（トークン節約）
    sample_added = diff_result.added_lines[:10]
    sample_removed = diff_result.removed_lines[:10]

    prompt = f"""以下は「{doc_type}」の文書改定における差分情報です。

【追加された内容（抜粋）】
{chr(10).join(sample_added) if sample_added else "なし"}

【削除された内容（抜粋）】
{chr(10).join(sample_removed) if sample_removed else "なし"}

【類似度】{diff_result.similarity_ratio:.1%}

変更点を産業廃棄物処理業務の担当者が理解できるよう、簡潔な日本語（3〜5文）で説明してください。
特に重要な記載事項の変更があれば強調してください。"""

    try:
        response = llm_client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=500,
            temperature=0.3,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.warning("LLM 差分サマリー生成エラー: %s", e)
        return diff_result.summary


# ──────────────────────────────────────────────────
# バージョン履歴の可視化
# ──────────────────────────────────────────────────

def format_version_history(versions: list[dict]) -> str:
    """
    バージョン履歴を見やすい文字列で返す。

    Args:
        versions: document_versions テーブルの行リスト（dict形式）

    Returns:
        ターミナル表示用文字列
    """
    if not versions:
        return "バージョン履歴がありません"

    lines = ["バージョン履歴:"]
    for v in sorted(versions, key=lambda x: x.get("version_seq", 0), reverse=True):
        is_latest = "[最新]" if v.get("is_latest") else "      "
        ver = v.get("version_number", "?")
        date = v.get("created_at", "")[:10]
        author = v.get("author", "不明")
        notes = v.get("notes", "")
        validity = v.get("validity_status", "未確認")

        # バージョン文字列が既に "v" で始まる場合はそのまま使う
        ver_display = ver if ver.startswith(("v", "V", "rev", "第")) else f"v{ver}"
        line = f"  {is_latest} {ver_display:10s} ({date}) 作成者:{author:10s} 有効性:{validity:5s}"
        if notes:
            line += f"  備考:{notes}"
        lines.append(line)

    return "\n".join(lines)


# ──────────────────────────────────────────────────
# 動作確認
# ──────────────────────────────────────────────────
if __name__ == "__main__":
    # バージョン抽出テスト
    test_files = [
        "マニフェストv3.pdf",
        "申請書_rev2_20240115.docx",
        "許可証_2024-03-01.pdf",
        "誓約書_最終.pdf",
        "内部稟議書(2).xlsx",
        "受領通知書_第3版.docx",
        "稟議書.pdf",
    ]
    print("=== バージョン抽出テスト ===")
    for fn in test_files:
        ver = extract_version_from_filename(fn)
        print(f"  {fn:40s} → {ver}")

    # 差分テスト
    print("\n=== 差分テスト ===")
    old = "マニフェストNo: A-001\n廃棄物種類: 廃プラスチック\n数量: 100kg\n"
    new = "マニフェストNo: A-001\n廃棄物種類: 廃プラスチック\n数量: 150kg\n処理方法: 焼却\n"
    diff = compute_text_diff(old, new)
    print(diff.summary)
    print(f"類似度: {diff.similarity_ratio:.1%}")
