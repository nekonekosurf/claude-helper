"""文書・ナレッジ整合性検証 - 定期チェックと監査"""

import json
from pathlib import Path
from src.knowledge import (
    load_routing_rules, list_categories, load_category,
    get_all_knowledge_summary,
)
from src.searcher import get_document_list

INDEX_DIR = Path(__file__).parent.parent / "data" / "index"
JERG_DIR = Path(__file__).parent.parent / "data" / "jerg"


def validate_all() -> dict:
    """全体の整合性チェックを実行し、結果を返す"""
    results = {
        "pdf_check": check_pdf_files(),
        "index_check": check_index_consistency(),
        "knowledge_check": check_knowledge_consistency(),
        "summary": "",
    }

    # 総合サマリ
    total_issues = sum(len(r.get("issues", [])) for r in results.values() if isinstance(r, dict))
    total_ok = sum(r.get("ok_count", 0) for r in results.values() if isinstance(r, dict))
    results["summary"] = f"検証完了: OK={total_ok}, 問題={total_issues}"

    return results


def check_pdf_files() -> dict:
    """PDFファイルの存在チェック"""
    issues = []
    ok_count = 0

    if not JERG_DIR.exists():
        return {"ok_count": 0, "issues": ["JERG PDFディレクトリが見つかりません"]}

    pdfs = list(JERG_DIR.glob("*.pdf"))
    ok_count = len(pdfs)

    # サイズが異常に小さいPDFを検出（破損の可能性）
    for pdf in pdfs:
        size = pdf.stat().st_size
        if size < 1000:
            issues.append(f"異常に小さいPDF: {pdf.name} ({size} bytes) - 破損の可能性")

    return {"ok_count": ok_count, "issues": issues, "total_pdfs": len(pdfs)}


def check_index_consistency() -> dict:
    """インデックスとPDFの整合性チェック"""
    issues = []
    ok_count = 0

    chunks_path = INDEX_DIR / "chunks.json"
    doc_list_path = INDEX_DIR / "documents.json"

    if not chunks_path.exists():
        return {"ok_count": 0, "issues": ["インデックスが未構築です。indexer.py を実行してください。"]}

    # チャンクファイル読み込み
    with open(chunks_path, encoding="utf-8") as f:
        chunks = json.load(f)

    # 文書一覧読み込み
    doc_list = get_document_list()

    # 各文書のPDFが存在するか確認
    for doc_id, info in doc_list.items():
        filename = info.get("filename", "")
        pdf_path = JERG_DIR / filename
        if pdf_path.exists():
            ok_count += 1
        else:
            issues.append(f"インデックスにあるがPDFがない: {filename}")

    # 空チャンクの検出
    empty_chunks = [c for c in chunks if not c.get("text", "").strip()]
    if empty_chunks:
        issues.append(f"空のチャンクが {len(empty_chunks)} 件あります")

    return {
        "ok_count": ok_count,
        "issues": issues,
        "total_chunks": len(chunks),
        "total_docs": len(doc_list),
    }


def check_knowledge_consistency() -> dict:
    """ナレッジファイルの整合性チェック"""
    issues = []
    ok_count = 0

    rules = load_routing_rules()
    doc_list = get_document_list()
    categories = list_categories()

    # ルールの文書参照チェック
    for rule in rules:
        pattern = rule.get("pattern", "")
        cat = rule.get("category", "")

        # カテゴリファイルが存在するか
        if cat and cat not in categories:
            issues.append(f"ルール '{pattern}' のカテゴリ '{cat}' のファイルがありません")
        else:
            ok_count += 1

        # 参照文書がインデックスに存在するか
        for doc in rule.get("documents", []):
            doc_id = doc.get("id", "")
            # 部分一致で確認
            found = any(doc_id in did for did in doc_list.keys())
            if found:
                ok_count += 1
            else:
                issues.append(f"ルール '{pattern}' の参照文書 '{doc_id}' がインデックスにありません")

    return {"ok_count": ok_count, "issues": issues, "total_rules": len(rules)}


def format_report(results: dict) -> str:
    """検証結果を読みやすいレポートに整形"""
    lines = ["=" * 50, "📋 文書・ナレッジ整合性レポート", "=" * 50, ""]

    # PDF チェック
    pdf = results.get("pdf_check", {})
    lines.append(f"## 1. PDFファイル ({pdf.get('total_pdfs', 0)} 件)")
    if pdf.get("issues"):
        for issue in pdf["issues"]:
            lines.append(f"  ⚠️  {issue}")
    else:
        lines.append(f"  ✅ 全 {pdf.get('ok_count', 0)} ファイル正常")

    # インデックス チェック
    idx = results.get("index_check", {})
    lines.append(f"\n## 2. 検索インデックス (文書: {idx.get('total_docs', 0)}, チャンク: {idx.get('total_chunks', 0)})")
    if idx.get("issues"):
        for issue in idx["issues"]:
            lines.append(f"  ⚠️  {issue}")
    else:
        lines.append(f"  ✅ 全 {idx.get('ok_count', 0)} 文書の整合性OK")

    # ナレッジ チェック
    know = results.get("knowledge_check", {})
    lines.append(f"\n## 3. ナレッジ ({know.get('total_rules', 0)} ルール)")
    if know.get("issues"):
        for issue in know["issues"]:
            lines.append(f"  ⚠️  {issue}")
    else:
        lines.append(f"  ✅ 全 {know.get('ok_count', 0)} 項目の整合性OK")

    # サマリ
    lines.append(f"\n{'=' * 50}")
    lines.append(results.get("summary", ""))
    lines.append("=" * 50)

    return "\n".join(lines)


def run_validation() -> str:
    """検証を実行してレポートを返す"""
    results = validate_all()
    return format_report(results)


if __name__ == "__main__":
    print(run_validation())
