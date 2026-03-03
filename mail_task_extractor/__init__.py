"""
mail_task_extractor - メールからタスクを自動抽出するシステム

使い方:
    from mail_task_extractor import EmailParser, TaskExtractor, ThreadGrouper
"""

from .email_fetcher import (
    EMLFileFetcher,
    ExchangeFetcher,
    GmailAPIFetcher,
    IMAPFetcher,
    MSGFileFetcher,
    create_fetcher,
)
from .email_parser import EmailParser, ThreadGrouper
from .models import (
    EmailThread,
    ExtractedTask,
    ParsedEmail,
    Priority,
    TaskStatus,
)
from .status_tracker import StatusTracker, detect_status_signals
from .task_extractor import BatchTaskExtractor, TaskExtractor
from .thread_analyzer import ProjectProgressTracker, ThreadAnalyzer

__all__ = [
    # フェッチャー
    "IMAPFetcher",
    "ExchangeFetcher",
    "GmailAPIFetcher",
    "EMLFileFetcher",
    "MSGFileFetcher",
    "create_fetcher",
    # パーサー
    "EmailParser",
    "ThreadGrouper",
    # タスク抽出
    "TaskExtractor",
    "BatchTaskExtractor",
    # スレッド分析
    "ThreadAnalyzer",
    "ProjectProgressTracker",
    # ステータス追跡
    "StatusTracker",
    "detect_status_signals",
    # モデル
    "ParsedEmail",
    "EmailThread",
    "ExtractedTask",
    "TaskStatus",
    "Priority",
]
