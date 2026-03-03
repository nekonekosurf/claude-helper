"""
データモデル定義 - メールタスク抽出システム
Pydantic v2 を使用
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ─── 列挙型 ──────────────────────────────────────────────────────────────────

class TaskStatus(str, Enum):
    PENDING   = "pending"    # 依頼中（未着手）
    IN_PROGRESS = "in_progress"  # 対応中
    WAITING   = "waiting"    # 待機中（他タスク待ち）
    DONE      = "done"       # 完了
    CANCELLED = "cancelled"  # キャンセル

class Priority(str, Enum):
    HIGH   = "high"
    MEDIUM = "medium"
    LOW    = "low"

class AttachmentType(str, Enum):
    PDF      = "pdf"
    EXCEL    = "excel"
    WORD     = "word"
    IMAGE    = "image"
    OTHER    = "other"


# ─── メールモデル ────────────────────────────────────────────────────────────

class EmailAttachment(BaseModel):
    filename: str
    content_type: str
    size_bytes: int = 0
    attachment_type: AttachmentType = AttachmentType.OTHER
    # 実際のバイナリは別途保存パスで管理
    saved_path: Optional[str] = None

class ParsedEmail(BaseModel):
    """正規化済みメール1通"""
    message_id: str
    subject: str
    sender: str           # "田中 太郎 <tanaka@example.com>"
    sender_name: str      # "田中 太郎"
    sender_address: str   # "tanaka@example.com"
    recipients: list[str] = Field(default_factory=list)
    cc: list[str] = Field(default_factory=list)
    date: datetime
    body_text: str        # プレーンテキスト本文
    body_html: str = ""
    # スレッド関連
    in_reply_to: Optional[str] = None
    references: list[str] = Field(default_factory=list)
    thread_id: Optional[str] = None   # 後から付与
    attachments: list[EmailAttachment] = Field(default_factory=list)
    # メタ
    raw_source: str = ""  # imap/gmail/exchange/eml/msg

class EmailThread(BaseModel):
    """スレッド（会話）単位でまとめたメール群"""
    thread_id: str
    subject: str
    emails: list[ParsedEmail] = Field(default_factory=list)
    participants: list[str] = Field(default_factory=list)
    started_at: Optional[datetime] = None
    last_activity: Optional[datetime] = None
    is_stale: bool = False   # 一定期間返信なし


# ─── タスクモデル ────────────────────────────────────────────────────────────

class TaskDependency(BaseModel):
    """依存関係: このタスクはどのタスクが完了してから開始すべきか"""
    depends_on_task_id: str
    description: str = ""

class ExtractedTask(BaseModel):
    """LLMが抽出したタスク1件"""
    task_id: str              # 自動採番: TK-0001 等
    title: str                # タスクの簡潔なタイトル
    description: str          # 詳細説明（メール文章から抜粋）
    assignee: str             # 担当者名
    assignee_email: Optional[str] = None
    requester: str            # 依頼者名
    requester_email: Optional[str] = None
    deadline: Optional[datetime] = None
    deadline_text: str = ""   # LLMが抽出した元テキスト（「来週金曜まで」等）
    priority: Priority = Priority.MEDIUM
    status: TaskStatus = TaskStatus.PENDING
    dependencies: list[TaskDependency] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)  # ["廃棄物処理", "許可申請"]
    # 出典
    source_email_id: str      # どのメールから抽出したか
    source_thread_id: Optional[str] = None
    extracted_at: datetime = Field(default_factory=datetime.now)
    # 更新履歴
    status_history: list[dict] = Field(default_factory=list)

class TaskExtractionResult(BaseModel):
    """LLMの1回の抽出結果（1通のメールから複数タスク出る可能性）"""
    tasks: list[ExtractedTask] = Field(default_factory=list)
    completed_task_ids: list[str] = Field(default_factory=list)  # 完了が検出されたタスク
    new_issues: list[str] = Field(default_factory=list)          # 追加で発覚した問題
    summary: str = ""         # メール全体のサマリー

class ProjectProgress(BaseModel):
    """プロジェクト全体の進捗サマリー"""
    total_tasks: int
    done: int
    in_progress: int
    pending: int
    waiting: int
    overdue: int              # 期限超過
    stale_threads: list[str] = Field(default_factory=list)
    bottleneck_tasks: list[str] = Field(default_factory=list)  # 他タスクが待っているタスク
