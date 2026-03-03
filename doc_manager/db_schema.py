"""
db_schema.py - 文書管理システム データベーススキーマ定義

テーブル構成:
  documents        - 文書マスタ（論理的な1文書を表す）
  document_versions - バージョン管理（物理ファイルごと）
  document_links   - タスク/メール/プロジェクトとの紐付け
  tags             - タグマスタ
  document_tags    - 文書×タグの中間テーブル
  document_content - 全文検索用（FTS5）
"""

import sqlite3
from pathlib import Path


# デフォルトDBパス
DEFAULT_DB_PATH = Path(__file__).parent.parent / "data" / "documents.db"


def get_connection(db_path: str | Path | None = None) -> sqlite3.Connection:
    """SQLite接続を取得する。Row アクセスを dict 風に有効化。"""
    path = Path(db_path) if db_path else DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")   # 並列読み取り性能向上
    conn.execute("PRAGMA foreign_keys=ON")    # 外部キー制約を有効化
    return conn


def create_tables(conn: sqlite3.Connection) -> None:
    """全テーブルを作成する（冪等）。"""

    conn.executescript("""
    -- ─────────────────────────────────────────────────
    -- 1. documents - 文書マスタ
    --    「マニフェスト」という概念的な文書を 1 行で表す。
    --    物理ファイルは document_versions で管理する。
    -- ─────────────────────────────────────────────────
    CREATE TABLE IF NOT EXISTS documents (
        doc_id          TEXT PRIMARY KEY,           -- UUID
        title           TEXT NOT NULL,              -- 文書名 例: "産業廃棄物マニフェスト"
        doc_type        TEXT NOT NULL,              -- manifest / application / permit /
                                                   -- pledge / receipt / approval / other
        project_id      TEXT,                       -- プロジェクトID（外部システム参照）
        description     TEXT,                       -- 概要・備考
        current_version TEXT,                       -- 最新バージョンID (FK: document_versions)
        status          TEXT DEFAULT 'active',      -- active / archived / deleted
        created_at      TEXT NOT NULL,              -- ISO8601
        updated_at      TEXT NOT NULL               -- ISO8601
    );

    -- ─────────────────────────────────────────────────
    -- 2. document_versions - バージョン管理
    --    同一文書の改定履歴を追跡する。
    -- ─────────────────────────────────────────────────
    CREATE TABLE IF NOT EXISTS document_versions (
        version_id      TEXT PRIMARY KEY,           -- UUID
        doc_id          TEXT NOT NULL
                            REFERENCES documents(doc_id) ON DELETE CASCADE,
        version_number  TEXT NOT NULL,              -- "1.0", "2.3", "v3" など
        version_seq     INTEGER NOT NULL DEFAULT 1, -- 自動採番（比較用整数）
        file_path       TEXT,                       -- ローカルパス
        file_url        TEXT,                       -- SharePoint/S3 URL
        file_name       TEXT,                       -- 元のファイル名
        file_ext        TEXT,                       -- pdf / pptx / docx / xlsx / jpg
        file_size_bytes INTEGER,
        file_hash       TEXT,                       -- SHA256（同一ファイル検出）
        is_latest       INTEGER NOT NULL DEFAULT 0, -- 1=最新版
        author          TEXT,                       -- 作成者
        summary         TEXT,                       -- LLMによる要約
        extracted_text  TEXT,                       -- 抽出テキスト（全文検索用）
        validity_status TEXT DEFAULT 'unchecked',   -- unchecked / valid / invalid / warning
        validity_notes  TEXT,                       -- 正当性チェック結果メモ
        effective_date  TEXT,                       -- 文書の効力発生日
        expiry_date     TEXT,                       -- 有効期限
        created_at      TEXT NOT NULL,
        notes           TEXT                        -- 変更メモ
    );

    CREATE INDEX IF NOT EXISTS idx_versions_doc_id
        ON document_versions(doc_id);
    CREATE INDEX IF NOT EXISTS idx_versions_is_latest
        ON document_versions(doc_id, is_latest);
    CREATE UNIQUE INDEX IF NOT EXISTS idx_versions_hash
        ON document_versions(file_hash)
        WHERE file_hash IS NOT NULL;

    -- ─────────────────────────────────────────────────
    -- 3. document_links - タスク/メール/プロジェクトとの紐付け
    -- ─────────────────────────────────────────────────
    CREATE TABLE IF NOT EXISTS document_links (
        link_id         TEXT PRIMARY KEY,           -- UUID
        doc_id          TEXT NOT NULL
                            REFERENCES documents(doc_id) ON DELETE CASCADE,
        version_id      TEXT                        -- NULL=常に最新版を参照
                            REFERENCES document_versions(version_id),
        link_type       TEXT NOT NULL,              -- task / email / project / meeting / other
        target_id       TEXT NOT NULL,              -- タスクID / メールID / プロジェクトID
        target_title    TEXT,                       -- 表示用タイトル
        relationship    TEXT DEFAULT 'reference',   -- reference / required / submitted / approved
        created_at      TEXT NOT NULL,
        created_by      TEXT,
        notes           TEXT
    );

    CREATE INDEX IF NOT EXISTS idx_links_target
        ON document_links(link_type, target_id);
    CREATE INDEX IF NOT EXISTS idx_links_doc_id
        ON document_links(doc_id);

    -- ─────────────────────────────────────────────────
    -- 4. tags - タグマスタ
    -- ─────────────────────────────────────────────────
    CREATE TABLE IF NOT EXISTS tags (
        tag_id   TEXT PRIMARY KEY,
        name     TEXT NOT NULL UNIQUE,
        color    TEXT DEFAULT '#888888'
    );

    -- ─────────────────────────────────────────────────
    -- 5. document_tags - 文書×タグ中間テーブル
    -- ─────────────────────────────────────────────────
    CREATE TABLE IF NOT EXISTS document_tags (
        doc_id TEXT NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
        tag_id TEXT NOT NULL REFERENCES tags(tag_id) ON DELETE CASCADE,
        PRIMARY KEY (doc_id, tag_id)
    );

    -- ─────────────────────────────────────────────────
    -- 6. document_content - 全文検索（FTS5）
    --    version_id → extracted_text をインデックス化
    -- ─────────────────────────────────────────────────
    CREATE VIRTUAL TABLE IF NOT EXISTS document_content
        USING fts5(
            version_id UNINDEXED,
            doc_id UNINDEXED,
            title,
            content=document_versions,
            content_rowid=rowid
        );
    """)

    # FTS5 用トリガー（document_versions への INSERT/UPDATE/DELETE 連動）
    conn.executescript("""
    CREATE TRIGGER IF NOT EXISTS fts_versions_insert
        AFTER INSERT ON document_versions BEGIN
            INSERT INTO document_content(rowid, version_id, doc_id, title)
            SELECT new.rowid, new.version_id, new.doc_id,
                   (SELECT title FROM documents WHERE doc_id = new.doc_id);
        END;

    CREATE TRIGGER IF NOT EXISTS fts_versions_delete
        AFTER DELETE ON document_versions BEGIN
            INSERT INTO document_content(document_content, rowid, version_id, doc_id, title)
            VALUES('delete', old.rowid, old.version_id, old.doc_id, '');
        END;

    CREATE TRIGGER IF NOT EXISTS fts_versions_update
        AFTER UPDATE ON document_versions BEGIN
            INSERT INTO document_content(document_content, rowid, version_id, doc_id, title)
            VALUES('delete', old.rowid, old.version_id, old.doc_id, '');
            INSERT INTO document_content(rowid, version_id, doc_id, title)
            SELECT new.rowid, new.version_id, new.doc_id,
                   (SELECT title FROM documents WHERE doc_id = new.doc_id);
        END;
    """)

    conn.commit()


def init_default_tags(conn: sqlite3.Connection) -> None:
    """産業廃棄物処理でよく使うタグを初期投入する。"""
    default_tags = [
        ("tag_manifest",    "マニフェスト",     "#e74c3c"),
        ("tag_application", "申請書",           "#3498db"),
        ("tag_permit",      "許可証",           "#2ecc71"),
        ("tag_pledge",      "誓約書",           "#9b59b6"),
        ("tag_receipt",     "受領通知書",       "#f39c12"),
        ("tag_approval",    "内部稟議書",       "#1abc9c"),
        ("tag_waste",       "産業廃棄物",       "#e67e22"),
        ("tag_urgent",      "要対応",           "#c0392b"),
        ("tag_expired",     "期限切れ",         "#7f8c8d"),
    ]
    conn.executemany(
        "INSERT OR IGNORE INTO tags(tag_id, name, color) VALUES (?, ?, ?)",
        default_tags
    )
    conn.commit()


def migrate(conn: sqlite3.Connection) -> None:
    """将来のスキーマ変更用マイグレーション枠。"""
    # バージョン管理テーブルを使ったマイグレーションをここに追加する
    pass


# ─────────────────────────────────────────
# 直接実行: DB 初期化
# ─────────────────────────────────────────
if __name__ == "__main__":
    import sys
    db_path = sys.argv[1] if len(sys.argv) > 1 else None
    conn = get_connection(db_path)
    create_tables(conn)
    init_default_tags(conn)
    print(f"DB 初期化完了: {db_path or DEFAULT_DB_PATH}")
