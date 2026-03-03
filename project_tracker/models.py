"""
project_tracker/models.py
プロジェクト・タスク管理のデータモデル
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Optional


# ─── Enum定義 ────────────────────────────────────────────────────────────────

class TaskStatus(str, Enum):
    TODO       = "未着手"
    IN_PROGRESS = "進行中"
    DONE       = "完了"
    ON_HOLD    = "保留"
    OVERDUE    = "期限超過"

class Priority(str, Enum):
    CRITICAL = "緊急"
    HIGH     = "高"
    MEDIUM   = "中"
    LOW      = "低"


# ─── タスク ───────────────────────────────────────────────────────────────────

@dataclass
class Task:
    title: str
    assignee: str
    due_date: Optional[date]
    status: TaskStatus = TaskStatus.TODO
    priority: Priority = Priority.MEDIUM
    description: str = ""
    depends_on: list[str] = field(default_factory=list)   # task_id のリスト
    related_docs: list[str] = field(default_factory=list)  # ファイルパスや文書名
    related_emails: list[str] = field(default_factory=list)# メール件名など
    notes: str = ""
    task_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    # ─── 自動ステータス更新 ────────────────────────────────────────────
    def refresh_status(self) -> None:
        """期限超過チェック（完了・保留は変更しない）"""
        if self.status in (TaskStatus.DONE, TaskStatus.ON_HOLD):
            return
        if self.due_date and self.due_date < date.today():
            self.status = TaskStatus.OVERDUE

    def is_blocked(self, all_tasks: dict[str, "Task"]) -> bool:
        """依存タスクが未完了なら True"""
        for dep_id in self.depends_on:
            dep = all_tasks.get(dep_id)
            if dep and dep.status != TaskStatus.DONE:
                return True
        return False

    def days_until_due(self) -> Optional[int]:
        if self.due_date is None:
            return None
        return (self.due_date - date.today()).days

    # ─── シリアライズ ────────────────────────────────────────────────────
    def to_dict(self) -> dict:
        return {
            "task_id":       self.task_id,
            "title":         self.title,
            "assignee":      self.assignee,
            "due_date":      self.due_date.isoformat() if self.due_date else None,
            "status":        self.status.value,
            "priority":      self.priority.value,
            "description":   self.description,
            "depends_on":    self.depends_on,
            "related_docs":  self.related_docs,
            "related_emails":self.related_emails,
            "notes":         self.notes,
            "created_at":    self.created_at.isoformat(),
            "updated_at":    self.updated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Task":
        return cls(
            task_id      = d["task_id"],
            title        = d["title"],
            assignee     = d["assignee"],
            due_date     = date.fromisoformat(d["due_date"]) if d.get("due_date") else None,
            status       = TaskStatus(d["status"]),
            priority     = Priority(d["priority"]),
            description  = d.get("description", ""),
            depends_on   = d.get("depends_on", []),
            related_docs = d.get("related_docs", []),
            related_emails=d.get("related_emails", []),
            notes        = d.get("notes", ""),
            created_at   = datetime.fromisoformat(d["created_at"]),
            updated_at   = datetime.fromisoformat(d["updated_at"]),
        )


# ─── フェーズ ─────────────────────────────────────────────────────────────────

@dataclass
class Phase:
    name: str
    order: int = 0
    description: str = ""
    tasks: list[Task] = field(default_factory=list)
    phase_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])

    def completion_rate(self) -> float:
        if not self.tasks:
            return 0.0
        done = sum(1 for t in self.tasks if t.status == TaskStatus.DONE)
        return done / len(self.tasks)

    def to_dict(self) -> dict:
        return {
            "phase_id":   self.phase_id,
            "name":       self.name,
            "order":      self.order,
            "description":self.description,
            "tasks":      [t.to_dict() for t in self.tasks],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Phase":
        ph = cls(
            phase_id   = d["phase_id"],
            name       = d["name"],
            order      = d.get("order", 0),
            description= d.get("description", ""),
        )
        ph.tasks = [Task.from_dict(t) for t in d.get("tasks", [])]
        return ph


# ─── プロジェクト ─────────────────────────────────────────────────────────────

@dataclass
class Project:
    name: str
    description: str = ""
    phases: list[Phase] = field(default_factory=list)
    project_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    created_at: datetime = field(default_factory=datetime.now)
    tags: list[str] = field(default_factory=list)

    # ─── 集計 ────────────────────────────────────────────────────────────
    def all_tasks(self) -> list[Task]:
        tasks = []
        for ph in self.phases:
            tasks.extend(ph.tasks)
        return tasks

    def all_tasks_dict(self) -> dict[str, Task]:
        return {t.task_id: t for t in self.all_tasks()}

    def completion_rate(self) -> float:
        tasks = self.all_tasks()
        if not tasks:
            return 0.0
        done = sum(1 for t in tasks if t.status == TaskStatus.DONE)
        return done / len(tasks)

    def overdue_tasks(self) -> list[Task]:
        return [t for t in self.all_tasks() if t.status == TaskStatus.OVERDUE]

    def stale_tasks(self, days: int = 7) -> list[Task]:
        """N日以上更新されていない進行中タスク"""
        threshold = datetime.now() - timedelta(days=days)
        return [
            t for t in self.all_tasks()
            if t.status == TaskStatus.IN_PROGRESS and t.updated_at < threshold
        ]

    def next_actions(self) -> list[Task]:
        """
        次にやるべきタスクを優先順位付きで返す。
        条件: 未着手 or 期限超過、かつブロックされていない
        ソート: 期限超過 > 緊急 > 高 > 中 > 低、期限が近い順
        """
        all_dict = self.all_tasks_dict()
        candidates = [
            t for t in self.all_tasks()
            if t.status in (TaskStatus.TODO, TaskStatus.OVERDUE)
            and not t.is_blocked(all_dict)
        ]

        PRIORITY_ORDER = {
            Priority.CRITICAL: 0,
            Priority.HIGH:     1,
            Priority.MEDIUM:   2,
            Priority.LOW:      3,
        }
        STATUS_ORDER = {
            TaskStatus.OVERDUE:    0,
            TaskStatus.IN_PROGRESS:1,
            TaskStatus.TODO:       2,
            TaskStatus.ON_HOLD:    3,
            TaskStatus.DONE:       4,
        }

        def sort_key(t: Task):
            days = t.days_until_due()
            due_sort = days if days is not None else 9999
            return (STATUS_ORDER[t.status], PRIORITY_ORDER[t.priority], due_sort)

        return sorted(candidates, key=sort_key)

    def refresh_all_statuses(self) -> None:
        for t in self.all_tasks():
            t.refresh_status()

    # ─── シリアライズ ────────────────────────────────────────────────────
    def to_dict(self) -> dict:
        return {
            "project_id": self.project_id,
            "name":       self.name,
            "description":self.description,
            "tags":       self.tags,
            "created_at": self.created_at.isoformat(),
            "phases":     [p.to_dict() for p in self.phases],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Project":
        proj = cls(
            project_id  = d["project_id"],
            name        = d["name"],
            description = d.get("description", ""),
            tags        = d.get("tags", []),
            created_at  = datetime.fromisoformat(d["created_at"]),
        )
        proj.phases = sorted(
            [Phase.from_dict(p) for p in d.get("phases", [])],
            key=lambda p: p.order,
        )
        return proj

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2))

    @classmethod
    def load(cls, path: Path) -> "Project":
        data = json.loads(path.read_text())
        proj = cls.from_dict(data)
        proj.refresh_all_statuses()
        return proj
