"""
document_manager.py - 文書管理システムのメイン制御クラス

DocumentManager が全機能の統合窓口となる:
  - 文書の登録・検索・取得
  - バージョン管理（最新版の自動判定）
  - LLM による要約・正当性チェック
  - 全文検索
  - 有効期限アラート
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, date
from pathlib import Path
from typing import Any

from .db_schema import get_connection, create_tables, init_default_tags
from .document_reader import read_document, ExtractedDocument
from .version_tracker import (
    extract_version_from_filename,
    compute_text_diff,
    find_latest_version,
    format_version_history,
)

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────
# 文書種別の定義
# ──────────────────────────────────────────────────

DOC_TYPES = {
    "manifest":    "マニフェスト（産業廃棄物管理票）",
    "application": "申請書",
    "permit":      "許可証",
    "pledge":      "誓約書",
    "receipt":     "受領通知書",
    "approval":    "内部稟議書",
    "other":       "その他",
}

# 正当性チェック用のチェックリスト（文書種別ごと）
VALIDITY_CHECKLISTS: dict[str, list[str]] = {
    "manifest": [
        "マニフェスト番号（交付番号）が記載されているか",
        "排出事業者名・住所が記載されているか",
        "廃棄物の種類が記載されているか",
        "廃棄物の数量（重量）が記載されているか",
        "収集運搬業者名・許可番号が記載されているか",
        "処分業者名・許可番号が記載されているか",
        "処分方法が記載されているか",
        "運搬先の住所が記載されているか",
        "交付日が記載されているか",
    ],
    "application": [
        "申請者名・住所が記載されているか",
        "申請日が記載されているか",
        "申請内容（目的）が明確か",
        "担当者署名または捺印があるか",
    ],
    "permit": [
        "許可番号が記載されているか",
        "許可の種別（収集運搬/処分）が明確か",
        "許可を受けた事業者名が記載されているか",
        "有効期限が記載されているか",
        "許可証の発行機関（都道府県等）が明確か",
        "廃棄物の種類（許可対象）が記載されているか",
    ],
    "pledge": [
        "誓約内容が具体的に記載されているか",
        "誓約者名・署名または捺印があるか",
        "日付が記載されているか",
    ],
    "receipt": [
        "受領した文書・物品の名称が記載されているか",
        "受領日が記載されているか",
        "受領者名・署名または捺印があるか",
        "発行者（送付元）が明確か",
    ],
    "approval": [
        "稟議のタイトル・件名が明確か",
        "稟議内容（目的・背景・概要）が記載されているか",
        "決裁者の署名または捺印欄があるか",
        "起案日が記載されているか",
        "起案者名が記載されているか",
        "予算・コスト情報が記載されているか（該当する場合）",
    ],
}


# ──────────────────────────────────────────────────
# DocumentManager
# ──────────────────────────────────────────────────

class DocumentManager:
    """
    文書管理システムのメインクラス。

    使い方:
        dm = DocumentManager()
        doc_id = dm.register_document("マニフェスト", "manifest", "/path/to/manifest_v1.pdf")
        dm.add_version(doc_id, "/path/to/manifest_v2.pdf")
        results = dm.search("廃プラスチック")
    """

    def __init__(self, db_path=None, llm_client=None, llm_model: str = "gpt-oss-120b"):
        """
        Args:
            db_path:   SQLite DBのパス。None でデフォルトパスを使用。
            llm_client: OpenAI 互換クライアント。None の場合は LLM 機能を無効化。
            llm_model:  使用するモデル名。
        """
        self.db_path = db_path
        self.llm_client = llm_client
        self.llm_model = llm_model

        # DB 初期化
        with self._conn() as conn:
            create_tables(conn)
            init_default_tags(conn)

    def _conn(self):
        return get_connection(self.db_path)

    # ──────────────────────────────────────────────
    # 文書の登録
    # ──────────────────────────────────────────────

    def register_document(
        self,
        title: str,
        doc_type: str,
        file_path: str | Path | None = None,
        file_url: str | None = None,
        project_id: str | None = None,
        description: str = "",
        author: str = "",
        version_number: str | None = None,
        effective_date: str | None = None,
        expiry_date: str | None = None,
        notes: str = "",
        force_ocr: bool = False,
        auto_summarize: bool = True,
        auto_validate: bool = True,
        tags: list[str] | None = None,
    ) -> str:
        """
        新しい文書を登録する。

        Args:
            title:          文書名
            doc_type:       文書種別（"manifest" / "permit" など）
            file_path:      ローカルファイルパス
            file_url:       SharePoint/S3 URL（ローカルパスがない場合）
            project_id:     プロジェクトID
            description:    概要・備考
            author:         作成者
            version_number: バージョン番号（None の場合はファイル名から推測）
            effective_date: 効力発生日 (ISO8601)
            expiry_date:    有効期限 (ISO8601)
            notes:          変更メモ
            force_ocr:      スキャンPDFの場合 True
            auto_summarize: LLM による自動要約を行うか
            auto_validate:  LLM による自動正当性チェックを行うか
            tags:           タグ名リスト

        Returns:
            doc_id（UUID）
        """
        if doc_type not in DOC_TYPES:
            raise ValueError(f"未知の doc_type: {doc_type}. 有効値: {list(DOC_TYPES)}")

        doc_id = str(uuid.uuid4())
        version_id = str(uuid.uuid4())
        now = datetime.now().isoformat()

        # ファイル読み込み
        extracted: ExtractedDocument | None = None
        if file_path:
            file_path = Path(file_path)
            extracted = read_document(file_path, force_ocr=force_ocr)
            if extracted.error:
                logger.warning("ファイル読み込み警告: %s", extracted.error)

            if version_number is None:
                version_number = extract_version_from_filename(file_path.name)

        version_number = version_number or "1.0"
        # "v1" "v2" 等に既に "v" が含まれていればそのまま使う
        # 含まれていない場合のみ "v" を付けない（extract_version_from_filename が v 付きで返す）

        # テキスト抽出結果
        extracted_text = extracted.text if extracted else ""
        file_hash = extracted.file_hash if extracted else None
        file_name = extracted.file_name if extracted else None
        file_ext = extracted.file_ext if extracted else None
        file_size = extracted.file_size_bytes if extracted else None

        # LLM 要約・正当性チェック
        summary = ""
        validity_status = "unchecked"
        validity_notes = ""

        if auto_summarize and self.llm_client and extracted_text:
            summary = self._summarize(extracted_text, doc_type, title)

        if auto_validate and self.llm_client and extracted_text:
            validity_status, validity_notes = self._validate(
                extracted_text, doc_type, title
            )

        # DB 登録
        with self._conn() as conn:
            # documents テーブル
            conn.execute("""
                INSERT INTO documents
                    (doc_id, title, doc_type, project_id, description,
                     current_version, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)
            """, (doc_id, title, doc_type, project_id, description,
                  version_id, now, now))

            # document_versions テーブル
            conn.execute("""
                INSERT INTO document_versions
                    (version_id, doc_id, version_number, version_seq,
                     file_path, file_url, file_name, file_ext,
                     file_size_bytes, file_hash, is_latest,
                     author, summary, extracted_text,
                     validity_status, validity_notes,
                     effective_date, expiry_date,
                     created_at, notes)
                VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (version_id, doc_id, version_number,
                  str(file_path) if file_path else None,
                  file_url, file_name, file_ext,
                  file_size, file_hash, author,
                  summary, extracted_text,
                  validity_status, validity_notes,
                  effective_date, expiry_date,
                  now, notes))

            # タグ付け
            if tags:
                self._attach_tags(conn, doc_id, tags)

        logger.info("文書登録完了: doc_id=%s title=%s ver=%s", doc_id, title, version_number)
        return doc_id

    # ──────────────────────────────────────────────
    # バージョンの追加
    # ──────────────────────────────────────────────

    def add_version(
        self,
        doc_id: str,
        file_path: str | Path | None = None,
        file_url: str | None = None,
        version_number: str | None = None,
        author: str = "",
        effective_date: str | None = None,
        expiry_date: str | None = None,
        notes: str = "",
        force_ocr: bool = False,
        auto_summarize: bool = True,
        auto_validate: bool = True,
        show_diff: bool = True,
    ) -> str:
        """
        既存文書に新バージョンを追加する。

        Args:
            doc_id:      既存の文書ID
            file_path:   新バージョンのファイルパス
            ...（register_document と同様）
            show_diff:   前バージョンとの差分ログを出力するか

        Returns:
            新しい version_id
        """
        now = datetime.now().isoformat()

        with self._conn() as conn:
            # 既存文書を確認
            doc = conn.execute(
                "SELECT * FROM documents WHERE doc_id = ?", (doc_id,)
            ).fetchone()
            if not doc:
                raise ValueError(f"文書が見つかりません: doc_id={doc_id}")

            # 現在の最新バージョンのシーケンス番号とテキストを取得
            current = conn.execute("""
                SELECT version_seq, extracted_text, version_id
                FROM document_versions
                WHERE doc_id = ? AND is_latest = 1
            """, (doc_id,)).fetchone()

            next_seq = (current["version_seq"] + 1) if current else 1
            old_text = current["extracted_text"] if current else ""

        # ファイル読み込み
        extracted: ExtractedDocument | None = None
        if file_path:
            file_path = Path(file_path)
            extracted = read_document(file_path, force_ocr=force_ocr)
            if extracted.error:
                logger.warning("ファイル読み込み警告: %s", extracted.error)

            if version_number is None:
                version_number = extract_version_from_filename(file_path.name)

        version_number = version_number or str(next_seq)

        extracted_text = extracted.text if extracted else ""
        file_hash = extracted.file_hash if extracted else None
        file_name = extracted.file_name if extracted else None
        file_ext = extracted.file_ext if extracted else None
        file_size = extracted.file_size_bytes if extracted else None

        # 差分計算
        diff_summary = ""
        if show_diff and old_text and extracted_text:
            diff = compute_text_diff(old_text, extracted_text)
            diff_summary = diff.summary
            logger.info("差分: %s", diff_summary)

        # LLM 要約・正当性チェック
        doc_type = doc["doc_type"]
        title = doc["title"]

        summary = ""
        validity_status = "unchecked"
        validity_notes = ""

        if auto_summarize and self.llm_client and extracted_text:
            summary = self._summarize(extracted_text, doc_type, title)

        if auto_validate and self.llm_client and extracted_text:
            validity_status, validity_notes = self._validate(
                extracted_text, doc_type, title
            )

        version_id = str(uuid.uuid4())

        with self._conn() as conn:
            # 既存の is_latest を 0 に
            conn.execute(
                "UPDATE document_versions SET is_latest = 0 WHERE doc_id = ?",
                (doc_id,)
            )

            # 新バージョン挿入
            conn.execute("""
                INSERT INTO document_versions
                    (version_id, doc_id, version_number, version_seq,
                     file_path, file_url, file_name, file_ext,
                     file_size_bytes, file_hash, is_latest,
                     author, summary, extracted_text,
                     validity_status, validity_notes,
                     effective_date, expiry_date,
                     created_at, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (version_id, doc_id, version_number, next_seq,
                  str(file_path) if file_path else None,
                  file_url, file_name, file_ext,
                  file_size, file_hash, author,
                  summary, extracted_text,
                  validity_status, validity_notes,
                  effective_date, expiry_date,
                  now,
                  notes + (f"\n差分: {diff_summary}" if diff_summary else "")))

            # documents の current_version を更新
            conn.execute("""
                UPDATE documents
                SET current_version = ?, updated_at = ?
                WHERE doc_id = ?
            """, (version_id, now, doc_id))

        logger.info(
            "バージョン追加: doc_id=%s title=%s ver=%s → v%s",
            doc_id, title, version_number, next_seq
        )
        return version_id

    # ──────────────────────────────────────────────
    # 文書の検索
    # ──────────────────────────────────────────────

    def search(
        self,
        query: str,
        doc_type: str | None = None,
        project_id: str | None = None,
        latest_only: bool = True,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """
        全文検索で文書を検索する。

        Args:
            query:       検索キーワード
            doc_type:    文書種別でフィルタ（None = 全種別）
            project_id:  プロジェクトでフィルタ（None = 全プロジェクト）
            latest_only: True = 最新版のみ表示
            limit:       最大件数

        Returns:
            マッチした文書リスト
        """
        with self._conn() as conn:
            # FTS5 で全文検索
            # タイトル + 抽出テキスト を対象
            base_query = """
                SELECT
                    d.doc_id,
                    d.title,
                    d.doc_type,
                    d.project_id,
                    d.status,
                    dv.version_id,
                    dv.version_number,
                    dv.file_path,
                    dv.file_url,
                    dv.file_name,
                    dv.validity_status,
                    dv.effective_date,
                    dv.expiry_date,
                    dv.summary,
                    dv.is_latest,
                    dv.created_at
                FROM documents d
                JOIN document_versions dv ON dv.doc_id = d.doc_id
                WHERE d.status = 'active'
                  AND (
                      d.title LIKE ?
                      OR dv.extracted_text LIKE ?
                      OR dv.summary LIKE ?
                  )
            """
            like_q = f"%{query}%"
            params: list[Any] = [like_q, like_q, like_q]

            if doc_type:
                base_query += " AND d.doc_type = ?"
                params.append(doc_type)

            if project_id:
                base_query += " AND d.project_id = ?"
                params.append(project_id)

            if latest_only:
                base_query += " AND dv.is_latest = 1"

            base_query += " ORDER BY dv.created_at DESC LIMIT ?"
            params.append(limit)

            rows = conn.execute(base_query, params).fetchall()

        return [dict(r) for r in rows]

    def get_document(self, doc_id: str, version_id: str | None = None) -> dict[str, Any] | None:
        """
        文書の詳細情報を取得する。

        Args:
            doc_id:      文書ID
            version_id:  特定バージョン。None = 最新版

        Returns:
            文書情報 dict（バージョン情報含む）、見つからない場合は None
        """
        with self._conn() as conn:
            if version_id:
                version_cond = "dv.version_id = ?"
                v_param: Any = version_id
            else:
                version_cond = "dv.is_latest = 1"
                v_param = None

            params = [doc_id]
            if v_param:
                params.append(v_param)

            row = conn.execute(f"""
                SELECT
                    d.*,
                    dv.version_id,
                    dv.version_number,
                    dv.version_seq,
                    dv.file_path,
                    dv.file_url,
                    dv.file_name,
                    dv.file_ext,
                    dv.file_size_bytes,
                    dv.is_latest,
                    dv.author,
                    dv.summary,
                    dv.validity_status,
                    dv.validity_notes,
                    dv.effective_date,
                    dv.expiry_date,
                    dv.created_at AS version_created_at,
                    dv.notes AS version_notes
                FROM documents d
                JOIN document_versions dv ON dv.doc_id = d.doc_id
                WHERE d.doc_id = ? AND {version_cond}
            """, params).fetchone()

            if not row:
                return None

            result = dict(row)

            # タグも取得
            tags = conn.execute("""
                SELECT t.name FROM tags t
                JOIN document_tags dt ON dt.tag_id = t.tag_id
                WHERE dt.doc_id = ?
            """, (doc_id,)).fetchall()
            result["tags"] = [t["name"] for t in tags]

            return result

    def get_version_history(self, doc_id: str) -> list[dict[str, Any]]:
        """文書のバージョン履歴を全件取得する。"""
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT version_id, version_number, version_seq,
                       file_name, is_latest, author,
                       validity_status, effective_date, expiry_date,
                       created_at, notes
                FROM document_versions
                WHERE doc_id = ?
                ORDER BY version_seq
            """, (doc_id,)).fetchall()

        return [dict(r) for r in rows]

    def list_documents(
        self,
        doc_type: str | None = None,
        project_id: str | None = None,
        validity_status: str | None = None,
    ) -> list[dict[str, Any]]:
        """文書一覧（最新版のみ）を取得する。"""
        with self._conn() as conn:
            sql = """
                SELECT
                    d.doc_id, d.title, d.doc_type, d.project_id, d.status,
                    dv.version_number, dv.file_name, dv.validity_status,
                    dv.effective_date, dv.expiry_date, dv.created_at
                FROM documents d
                JOIN document_versions dv ON dv.version_id = d.current_version
                WHERE d.status = 'active'
            """
            params: list[Any] = []

            if doc_type:
                sql += " AND d.doc_type = ?"
                params.append(doc_type)
            if project_id:
                sql += " AND d.project_id = ?"
                params.append(project_id)
            if validity_status:
                sql += " AND dv.validity_status = ?"
                params.append(validity_status)

            sql += " ORDER BY d.updated_at DESC"
            rows = conn.execute(sql, params).fetchall()

        return [dict(r) for r in rows]

    # ──────────────────────────────────────────────
    # 有効期限アラート
    # ──────────────────────────────────────────────

    def get_expiring_documents(self, days_ahead: int = 30) -> list[dict[str, Any]]:
        """
        有効期限が days_ahead 日以内に切れる文書を取得する。

        Args:
            days_ahead: 何日先までをアラート対象にするか

        Returns:
            期限切れ間近の文書リスト（期限の近い順）
        """
        from datetime import timedelta
        today = date.today()
        cutoff = (today + timedelta(days=days_ahead)).isoformat()
        today_str = today.isoformat()

        with self._conn() as conn:
            rows = conn.execute("""
                SELECT
                    d.doc_id, d.title, d.doc_type, d.project_id,
                    dv.version_number, dv.file_name, dv.expiry_date,
                    CAST(
                        (julianday(dv.expiry_date) - julianday('now'))
                        AS INTEGER
                    ) AS days_until_expiry
                FROM documents d
                JOIN document_versions dv ON dv.version_id = d.current_version
                WHERE d.status = 'active'
                  AND dv.expiry_date IS NOT NULL
                  AND dv.expiry_date <= ?
                ORDER BY dv.expiry_date ASC
            """, (cutoff,)).fetchall()

        results = [dict(r) for r in rows]

        # 期限切れ / 期限切れ間近にラベル付け
        for r in results:
            days = r.get("days_until_expiry", 0)
            if days < 0:
                r["alert_level"] = "expired"
                r["alert_label"] = f"期限切れ（{-days}日前）"
            elif days == 0:
                r["alert_level"] = "today"
                r["alert_label"] = "本日期限"
            elif days <= 7:
                r["alert_level"] = "critical"
                r["alert_label"] = f"残{days}日（要対応）"
            elif days <= 30:
                r["alert_level"] = "warning"
                r["alert_label"] = f"残{days}日"
            else:
                r["alert_level"] = "info"
                r["alert_label"] = f"残{days}日"

        return results

    # ──────────────────────────────────────────────
    # LLM 連携
    # ──────────────────────────────────────────────

    def _summarize(self, text: str, doc_type: str, title: str) -> str:
        """LLM で文書を要約する。"""
        if not self.llm_client:
            return ""

        doc_type_label = DOC_TYPES.get(doc_type, doc_type)

        # トークン節約のため最初の 3000 文字を使用
        excerpt = text[:3000] + ("..." if len(text) > 3000 else "")

        prompt = f"""以下は「{title}」（{doc_type_label}）の内容です。

{excerpt}

この文書の要点を3〜5文の日本語で要約してください。
特に日付、金額、当事者名、廃棄物の種類・数量など重要な情報を含めてください。"""

        try:
            response = self.llm_client.chat.completions.create(
                model=self.llm_model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=400,
                temperature=0.2,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.warning("LLM 要約エラー: %s", e)
            return ""

    def _validate(
        self, text: str, doc_type: str, title: str
    ) -> tuple[str, str]:
        """
        LLM で文書の正当性をチェックする。

        Returns:
            (validity_status, validity_notes)
            validity_status: "valid" / "invalid" / "warning" / "unchecked"
        """
        if not self.llm_client:
            return "unchecked", ""

        checklist = VALIDITY_CHECKLISTS.get(doc_type, [])
        if not checklist:
            return "unchecked", f"チェックリストなし（doc_type={doc_type}）"

        doc_type_label = DOC_TYPES.get(doc_type, doc_type)
        excerpt = text[:4000] + ("..." if len(text) > 4000 else "")
        checklist_text = "\n".join(f"- {item}" for item in checklist)

        prompt = f"""以下は「{title}」（{doc_type_label}）の内容です。

【文書内容】
{excerpt}

【チェックリスト】
{checklist_text}

各チェック項目について「OK」「NG」「不明」で判定し、
最後に総合判定（valid/invalid/warning）と理由を日本語で回答してください。

回答形式:
チェック結果:
- [チェック項目]: OK/NG/不明

総合判定: valid/invalid/warning
理由: ..."""

        try:
            response = self.llm_client.chat.completions.create(
                model=self.llm_model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=600,
                temperature=0.1,
            )
            result_text = response.choices[0].message.content.strip()

            # 総合判定を抽出
            if "総合判定: valid" in result_text:
                status = "valid"
            elif "総合判定: invalid" in result_text:
                status = "invalid"
            elif "総合判定: warning" in result_text:
                status = "warning"
            else:
                status = "warning"

            return status, result_text

        except Exception as e:
            logger.warning("LLM 正当性チェックエラー: %s", e)
            return "unchecked", f"チェックエラー: {e}"

    def validate_document(
        self, doc_id: str, version_id: str | None = None
    ) -> dict[str, Any]:
        """
        文書の正当性チェックを実行し、DBに保存して結果を返す。
        （登録後に個別で実行する場合）
        """
        doc = self.get_document(doc_id, version_id)
        if not doc:
            return {"error": "文書が見つかりません"}

        extracted_text = ""
        with self._conn() as conn:
            vid = version_id or doc.get("version_id")
            if vid:
                row = conn.execute(
                    "SELECT extracted_text FROM document_versions WHERE version_id = ?",
                    (vid,)
                ).fetchone()
                if row:
                    extracted_text = row["extracted_text"] or ""

        if not extracted_text:
            return {"error": "テキストが抽出されていません"}

        status, notes = self._validate(extracted_text, doc["doc_type"], doc["title"])

        # DB 更新
        vid = version_id or doc.get("version_id")
        with self._conn() as conn:
            conn.execute("""
                UPDATE document_versions
                SET validity_status = ?, validity_notes = ?
                WHERE version_id = ?
            """, (status, notes, vid))

        return {
            "doc_id":          doc_id,
            "title":           doc["title"],
            "validity_status": status,
            "validity_notes":  notes,
        }

    # ──────────────────────────────────────────────
    # タグ管理
    # ──────────────────────────────────────────────

    def _attach_tags(self, conn, doc_id: str, tag_names: list[str]) -> None:
        """文書にタグを付ける（内部メソッド）。"""
        for name in tag_names:
            # タグが存在しない場合は自動作成
            tag = conn.execute(
                "SELECT tag_id FROM tags WHERE name = ?", (name,)
            ).fetchone()
            if not tag:
                tag_id = str(uuid.uuid4())
                conn.execute(
                    "INSERT INTO tags(tag_id, name) VALUES (?, ?)",
                    (tag_id, name)
                )
            else:
                tag_id = tag["tag_id"]

            conn.execute(
                "INSERT OR IGNORE INTO document_tags(doc_id, tag_id) VALUES (?, ?)",
                (doc_id, tag_id)
            )

    def add_tag(self, doc_id: str, tag_name: str) -> None:
        """文書にタグを追加する。"""
        with self._conn() as conn:
            self._attach_tags(conn, doc_id, [tag_name])

    # ──────────────────────────────────────────────
    # 表示用ヘルパー
    # ──────────────────────────────────────────────

    def format_document_summary(self, doc_id: str) -> str:
        """文書の概要を人間向けに整形した文字列で返す。"""
        doc = self.get_document(doc_id)
        if not doc:
            return f"文書が見つかりません: {doc_id}"

        history = self.get_version_history(doc_id)

        lines = [
            f"文書名:   {doc['title']}",
            f"種別:     {DOC_TYPES.get(doc['doc_type'], doc['doc_type'])}",
            f"プロジェクト: {doc.get('project_id') or '未設定'}",
            f"最新バージョン: {doc['version_number']}",
            f"有効性:   {doc.get('validity_status', '未確認')}",
            f"有効期限: {doc.get('expiry_date') or '未設定'}",
            f"効力発生: {doc.get('effective_date') or '未設定'}",
            f"タグ:     {', '.join(doc.get('tags', [])) or 'なし'}",
        ]

        if doc.get("summary"):
            lines.append(f"\n要約:\n{doc['summary']}")

        if doc.get("validity_notes"):
            lines.append(f"\n正当性チェック:\n{doc['validity_notes'][:500]}")

        lines.append("")
        lines.append(format_version_history(history))

        return "\n".join(lines)

    def print_expiry_alerts(self, days_ahead: int = 30) -> None:
        """有効期限アラートを表示する。"""
        docs = self.get_expiring_documents(days_ahead)
        if not docs:
            print(f"有効期限が {days_ahead} 日以内に切れる文書はありません")
            return

        print(f"=== 有効期限アラート（{days_ahead}日以内）===")
        for doc in docs:
            level = doc.get("alert_level", "info")
            marker = {"expired": "!!!", "critical": "!! ", "warning": "!  ", "today": "!!!"}.get(
                level, "   "
            )
            print(
                f"{marker} [{doc['alert_label']:15s}] "
                f"{doc['title']:30s} "
                f"(v{doc.get('version_number', '?')}) "
                f"期限: {doc.get('expiry_date', '?')}"
            )


# ──────────────────────────────────────────────────
# 動作確認（LLM なし）
# ──────────────────────────────────────────────────
if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        dm = DocumentManager(db_path=f"{tmp}/test.db")

        # テスト用テキストを一時ファイルに書いてみる（実際は PDF/Word 等を渡す）
        test_txt = Path(tmp) / "manifest_v1.txt"
        test_txt.write_text(
            "マニフェスト番号: A-2024-001\n"
            "排出事業者: 株式会社テスト工業\n"
            "廃棄物種類: 廃プラスチック\n"
            "数量: 100kg\n"
            "収集運搬業者: テスト運送株式会社\n"
            "交付日: 2024-01-15\n",
            encoding="utf-8"
        )

        print("=== 文書登録テスト ===")
        doc_id = dm.register_document(
            title="産業廃棄物マニフェスト",
            doc_type="manifest",
            file_path=test_txt,
            author="山田太郎",
            effective_date="2024-01-15",
            expiry_date="2025-01-15",
            notes="初版",
            auto_summarize=False,
            auto_validate=False,
            tags=["マニフェスト", "廃プラスチック"],
        )
        print(f"登録完了: doc_id={doc_id}")

        print("\n=== 文書詳細 ===")
        print(dm.format_document_summary(doc_id))

        print("\n=== 検索テスト ===")
        results = dm.search("廃プラスチック")
        print(f"検索結果: {len(results)} 件")
        for r in results:
            print(f"  - {r['title']} (v{r['version_number']})")

        print("\n=== 有効期限アラート ===")
        dm.print_expiry_alerts(days_ahead=3650)
