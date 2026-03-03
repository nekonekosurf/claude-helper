"""
document_linker.py - 文書とタスク/メール/プロジェクトの紐付け管理

機能:
  - タスク・メール・プロジェクトへの紐付け登録/削除
  - 承認状態の追跡
  - メール本文から文書参照を自動検出
  - タスク必要文書の充足チェック
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime
from typing import Any

from .db_schema import get_connection

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────
# 定数
# ──────────────────────────────────────────────────

# link_type の選択肢
LINK_TYPES = {
    "task":    "タスク",
    "email":   "メール",
    "project": "プロジェクト",
    "meeting": "会議",
    "other":   "その他",
}

# relationship の選択肢
RELATIONSHIPS = {
    "reference": "参照",          # 参考資料として参照
    "required":  "必須",          # このタスクに必要な文書
    "submitted": "提出済み",      # 提出完了
    "approved":  "承認済み",      # 承認完了
    "rejected":  "差し戻し",      # 差し戻し
    "pending":   "承認待ち",      # 承認待ち
}


# ──────────────────────────────────────────────────
# 産業廃棄物処理 タスクテンプレート
# ──────────────────────────────────────────────────

# タスク種別 → 必要な文書タイプの定義
WASTE_PROCEDURE_REQUIREMENTS: dict[str, list[dict[str, Any]]] = {
    "廃棄物収集運搬": [
        {"doc_type": "manifest",     "label": "マニフェスト",   "required": True},
        {"doc_type": "permit",       "label": "収集運搬許可証", "required": True},
        {"doc_type": "application",  "label": "収集運搬申請書", "required": False},
    ],
    "廃棄物処分": [
        {"doc_type": "manifest",     "label": "マニフェスト",   "required": True},
        {"doc_type": "permit",       "label": "処分許可証",     "required": True},
        {"doc_type": "receipt",      "label": "受領通知書",     "required": True},
        {"doc_type": "pledge",       "label": "誓約書",         "required": False},
    ],
    "社内稟議": [
        {"doc_type": "approval",     "label": "内部稟議書",     "required": True},
        {"doc_type": "application",  "label": "申請書",         "required": True},
    ],
    "許可証更新": [
        {"doc_type": "application",  "label": "更新申請書",     "required": True},
        {"doc_type": "permit",       "label": "現行許可証",     "required": True},
        {"doc_type": "pledge",       "label": "誓約書",         "required": True},
    ],
}


# ──────────────────────────────────────────────────
# 紐付け CRUD
# ──────────────────────────────────────────────────

class DocumentLinker:
    """文書とタスク/メール/プロジェクトの紐付けを管理するクラス。"""

    def __init__(self, db_path=None):
        self.db_path = db_path
        # DB テーブルが未初期化でも最低限動作するよう try
        try:
            from .db_schema import create_tables, init_default_tags
            with get_connection(self.db_path) as conn:
                create_tables(conn)
                init_default_tags(conn)
        except Exception:
            pass

    def _conn(self):
        return get_connection(self.db_path)

    def link(
        self,
        doc_id: str,
        link_type: str,
        target_id: str,
        target_title: str = "",
        relationship: str = "reference",
        version_id: str | None = None,
        created_by: str = "",
        notes: str = "",
    ) -> str:
        """
        文書とターゲット（タスク/メール/プロジェクト）を紐付ける。

        Args:
            doc_id:       文書ID
            link_type:    "task" / "email" / "project" / "meeting"
            target_id:    タスクID・メールID・プロジェクトID
            target_title: 表示用タイトル
            relationship: "reference" / "required" / "submitted" / "approved"
            version_id:   特定バージョンに紐付ける場合。None=常に最新版
            created_by:   登録者
            notes:        備考

        Returns:
            生成された link_id
        """
        if link_type not in LINK_TYPES:
            raise ValueError(f"未知の link_type: {link_type}. 有効値: {list(LINK_TYPES)}")
        if relationship not in RELATIONSHIPS:
            raise ValueError(f"未知の relationship: {relationship}. 有効値: {list(RELATIONSHIPS)}")

        link_id = str(uuid.uuid4())
        now = datetime.now().isoformat()

        with self._conn() as conn:
            conn.execute("""
                INSERT INTO document_links
                    (link_id, doc_id, version_id, link_type, target_id,
                     target_title, relationship, created_at, created_by, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (link_id, doc_id, version_id, link_type, target_id,
                  target_title, relationship, now, created_by, notes))

        logger.info(
            "紐付け登録: doc=%s → %s:%s [%s]",
            doc_id, link_type, target_id, relationship
        )
        return link_id

    def unlink(self, link_id: str) -> bool:
        """紐付けを削除する。"""
        with self._conn() as conn:
            cursor = conn.execute(
                "DELETE FROM document_links WHERE link_id = ?", (link_id,)
            )
            return cursor.rowcount > 0

    def update_relationship(self, link_id: str, relationship: str, notes: str = "") -> bool:
        """
        紐付けの関係性（承認状態など）を更新する。

        例: "submitted" → "approved" に変更して承認完了を記録。
        """
        if relationship not in RELATIONSHIPS:
            raise ValueError(f"未知の relationship: {relationship}")

        with self._conn() as conn:
            cursor = conn.execute(
                "UPDATE document_links SET relationship = ?, notes = ? WHERE link_id = ?",
                (relationship, notes, link_id)
            )
            return cursor.rowcount > 0

    def get_links_for_target(
        self,
        link_type: str,
        target_id: str,
    ) -> list[dict[str, Any]]:
        """
        タスク/メール/プロジェクトに紐付けられた文書一覧を返す。

        Returns:
            紐付け情報リスト（文書マスタ情報込み）
        """
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT
                    dl.link_id,
                    dl.doc_id,
                    dl.version_id,
                    dl.relationship,
                    dl.created_at,
                    dl.created_by,
                    dl.notes,
                    d.title,
                    d.doc_type,
                    d.current_version,
                    dv.version_number,
                    dv.file_path,
                    dv.file_url,
                    dv.file_name,
                    dv.validity_status,
                    dv.effective_date,
                    dv.expiry_date
                FROM document_links dl
                JOIN documents d ON d.doc_id = dl.doc_id
                LEFT JOIN document_versions dv ON
                    dv.version_id = COALESCE(dl.version_id, d.current_version)
                WHERE dl.link_type = ? AND dl.target_id = ?
                ORDER BY dl.created_at DESC
            """, (link_type, target_id)).fetchall()

        return [dict(r) for r in rows]

    def get_links_for_document(self, doc_id: str) -> list[dict[str, Any]]:
        """文書に紐付けられたタスク/メール/プロジェクト一覧を返す。"""
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT link_id, link_type, target_id, target_title,
                       relationship, created_at, created_by, notes
                FROM document_links
                WHERE doc_id = ?
                ORDER BY created_at DESC
            """, (doc_id,)).fetchall()

        return [dict(r) for r in rows]

    # ──────────────────────────────────────────────
    # 必要文書の充足チェック
    # ──────────────────────────────────────────────

    def check_required_documents(
        self,
        task_id: str,
        procedure_type: str,
    ) -> dict[str, Any]:
        """
        タスクに必要な文書が揃っているか確認する。

        Args:
            task_id:         タスクID
            procedure_type:  "廃棄物収集運搬" / "廃棄物処分" / "社内稟議" / "許可証更新"

        Returns:
            {
              "complete": bool,
              "missing":  [必須だが未登録の文書リスト],
              "linked":   [登録済みの文書リスト],
              "summary":  str,
            }
        """
        requirements = WASTE_PROCEDURE_REQUIREMENTS.get(procedure_type, [])
        linked_docs = self.get_links_for_target("task", task_id)

        # 登録済み文書の doc_type セット
        linked_types = {d["doc_type"] for d in linked_docs}

        missing: list[dict] = []
        satisfied: list[dict] = []

        for req in requirements:
            doc_type = req["doc_type"]
            if doc_type in linked_types:
                satisfied.append(req)
            elif req.get("required", True):
                missing.append(req)

        complete = len(missing) == 0

        if complete:
            summary = f"必要文書は全て揃っています ({len(satisfied)}/{len(requirements)})"
        else:
            missing_labels = [r["label"] for r in missing]
            summary = (
                f"未登録の必須文書あり: {', '.join(missing_labels)} "
                f"({len(satisfied)}/{len(requirements)} 充足)"
            )

        return {
            "complete":   complete,
            "missing":    missing,
            "linked":     linked_docs,
            "satisfied":  satisfied,
            "summary":    summary,
        }

    # ──────────────────────────────────────────────
    # メールからの自動文書検出
    # ──────────────────────────────────────────────

    def detect_document_references_in_email(
        self,
        email_body: str,
        email_id: str,
        auto_link: bool = False,
        db_path=None,
    ) -> list[dict[str, Any]]:
        """
        メール本文から文書への参照（ファイル名、文書名）を自動検出する。

        Args:
            email_body: メール本文テキスト
            email_id:   メールID（紐付けに使用）
            auto_link:  True の場合、検出した文書を自動で紐付け登録する

        Returns:
            検出された文書情報のリスト
        """
        detected: list[dict[str, Any]] = []

        # 1. ファイル名パターンで検出（.pdf / .docx / .xlsx 等）
        file_pattern = re.compile(
            r'[\w\-\s（）()【】]+\.(pdf|docx|doc|xlsx|xls|pptx|ppt)',
            re.IGNORECASE
        )
        file_matches = file_pattern.findall(email_body)

        # 2. 産廃関連キーワードで文書種別を検出
        keyword_doc_types = {
            "マニフェスト": "manifest",
            "管理票":       "manifest",
            "申請書":       "application",
            "許可証":       "permit",
            "誓約書":       "pledge",
            "受領通知":     "receipt",
            "稟議":         "approval",
            "承認":         "approval",
        }

        mentioned_types: set[str] = set()
        for keyword, doc_type in keyword_doc_types.items():
            if keyword in email_body:
                mentioned_types.add(doc_type)
                detected.append({
                    "detection_method": "keyword",
                    "keyword":          keyword,
                    "doc_type":         doc_type,
                    "doc_id":           None,
                })

        # 3. DB から文書タイトルとのマッチング
        with get_connection(db_path or self.db_path) as conn:
            docs = conn.execute(
                "SELECT doc_id, title, doc_type FROM documents WHERE status = 'active'"
            ).fetchall()

        for doc in docs:
            if doc["title"] in email_body:
                result = {
                    "detection_method": "title_match",
                    "doc_id":           doc["doc_id"],
                    "title":            doc["title"],
                    "doc_type":         doc["doc_type"],
                }
                detected.append(result)

                if auto_link:
                    try:
                        self.link(
                            doc_id=doc["doc_id"],
                            link_type="email",
                            target_id=email_id,
                            relationship="reference",
                            notes="メール本文から自動検出",
                        )
                        result["auto_linked"] = True
                    except Exception as e:
                        logger.warning("自動紐付けエラー: %s", e)

        logger.info(
            "メール文書検出: email=%s → %d件検出",
            email_id, len(detected)
        )
        return detected

    # ──────────────────────────────────────────────
    # 承認ワークフロー
    # ──────────────────────────────────────────────

    def submit_for_approval(
        self,
        doc_id: str,
        task_id: str,
        created_by: str = "",
    ) -> str:
        """
        文書を承認申請状態にする。

        Returns: link_id
        """
        return self.link(
            doc_id=doc_id,
            link_type="task",
            target_id=task_id,
            relationship="pending",
            created_by=created_by,
            notes="承認申請",
        )

    def approve(self, link_id: str, approver: str = "", notes: str = "") -> bool:
        """承認済みに更新する。"""
        full_notes = f"承認者: {approver}" + (f" / {notes}" if notes else "")
        return self.update_relationship(link_id, "approved", full_notes)

    def reject(self, link_id: str, reason: str = "") -> bool:
        """差し戻しに更新する。"""
        return self.update_relationship(link_id, "rejected", f"差し戻し理由: {reason}")

    # ──────────────────────────────────────────────
    # 表示用ヘルパー
    # ──────────────────────────────────────────────

    def format_task_document_summary(
        self,
        task_id: str,
        task_title: str = "",
    ) -> str:
        """タスクに紐付けられた文書一覧を表示用文字列で返す。"""
        links = self.get_links_for_target("task", task_id)

        header = f"タスク: {task_title or task_id}"
        if not links:
            return f"{header}\n  紐付け文書なし"

        lines = [header, f"  紐付け文書: {len(links)} 件"]
        for link in links:
            rel_label = RELATIONSHIPS.get(link["relationship"], link["relationship"])
            validity = link.get("validity_status", "未確認")
            ver = link.get("version_number", "?")
            fname = link.get("file_name", "")
            expiry = link.get("expiry_date", "")

            expiry_str = f" [期限: {expiry}]" if expiry else ""
            line = (
                f"  [{rel_label:5s}] {link['title']:30s} "
                f"({ver}) {validity}{expiry_str}"
            )
            if fname:
                line += f"\n         ファイル: {fname}"
            lines.append(line)

        return "\n".join(lines)


# ──────────────────────────────────────────────────
# 動作確認
# ──────────────────────────────────────────────────
if __name__ == "__main__":
    # 必要文書チェックのデモ（DBなし）
    print("=== 手続き別 必要文書テンプレート ===")
    for proc, reqs in WASTE_PROCEDURE_REQUIREMENTS.items():
        print(f"\n{proc}:")
        for r in reqs:
            mark = "必須" if r.get("required") else "任意"
            print(f"  [{mark}] {r['label']} ({r['doc_type']})")

    # メール文書検出デモ
    print("\n=== メール文書検出デモ ===")
    linker = DocumentLinker()
    email_body = """
    お世話になります。
    先日のマニフェストの件ですが、許可証のコピーを添付いたしました。
    稟議書については来週提出予定です。
    """
    detected = linker.detect_document_references_in_email(email_body, "email-001")
    for d in detected:
        print(f"  検出: {d}")
