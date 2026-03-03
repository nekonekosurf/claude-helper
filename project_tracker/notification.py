"""
project_tracker/notification.py
期限・放置タスクの通知・リマインダー
"""

from __future__ import annotations

import json
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional

from .models import Project, Task, TaskStatus


# ─── CLIアラート ─────────────────────────────────────────────────────────────

def check_alerts(project: Project, stale_days: int = 7) -> list[dict]:
    """
    期限・放置タスクをチェックしてアラートリストを返す。

    Returns:
        [{"level": "critical"|"warning"|"info", "message": str, "task": Task}, ...]
    """
    project.refresh_all_statuses()
    alerts = []
    all_dict = project.all_tasks_dict()

    for task in project.all_tasks():
        if task.status == TaskStatus.DONE:
            continue

        days = task.days_until_due()

        # 期限超過
        if task.status == TaskStatus.OVERDUE:
            diff = abs(days) if days is not None else "?"
            alerts.append({
                "level":   "critical",
                "message": f"[期限超過 {diff}日] {task.title} / {task.assignee}",
                "task":    task,
            })

        # 期限まで3日以内（未完了）
        elif days is not None and days <= 3 and task.status != TaskStatus.DONE:
            alerts.append({
                "level":   "warning",
                "message": f"[期限まで{days}日] {task.title} / {task.assignee}",
                "task":    task,
            })

        # 放置タスク（進行中で N日以上更新なし）
        stale_threshold = datetime.now() - __import__("datetime").timedelta(days=stale_days)
        if task.status == TaskStatus.IN_PROGRESS and task.updated_at < stale_threshold:
            stale_days_actual = (datetime.now() - task.updated_at).days
            alerts.append({
                "level":   "warning",
                "message": f"[放置 {stale_days_actual}日] {task.title} / {task.assignee}",
                "task":    task,
            })

        # ブロックされているタスク
        if task.is_blocked(all_dict) and task.status not in (TaskStatus.DONE, TaskStatus.ON_HOLD):
            alerts.append({
                "level":   "info",
                "message": f"[依存ブロック] {task.title} / {task.assignee}",
                "task":    task,
            })

    # critical → warning → info の順にソート
    level_order = {"critical": 0, "warning": 1, "info": 2}
    alerts.sort(key=lambda a: level_order[a["level"]])

    return alerts


def print_alerts(project: Project, stale_days: int = 7) -> None:
    """アラートをCLIに出力する"""
    alerts = check_alerts(project, stale_days)

    if not alerts:
        print(f"[OK] '{project.name}': アラートはありません。")
        return

    try:
        from rich.console import Console
        from rich.table import Table
        import rich.box as box
        console = Console()

        table = Table(
            title=f"{project.name} アラート ({len(alerts)}件)",
            box=box.ROUNDED,
            header_style="bold red",
        )
        table.add_column("レベル",   width=10)
        table.add_column("メッセージ", width=55)
        table.add_column("優先度",   width=6)

        LEVEL_COLOR = {"critical": "bold red", "warning": "yellow", "info": "cyan"}

        for a in alerts:
            level_text = {"critical": "緊急", "warning": "警告", "info": "情報"}.get(a["level"], a["level"])
            console.print() if False else None
            table.add_row(
                f"[{LEVEL_COLOR[a['level']]}]{level_text}[/{LEVEL_COLOR[a['level']]}]",
                a["message"],
                a["task"].priority.value,
            )

        console.print(table)

    except ImportError:
        print(f"\n=== {project.name} アラート ({len(alerts)}件) ===")
        for a in alerts:
            level_label = {"critical": "[緊急]", "warning": "[警告]", "info": "[情報]"}.get(a["level"], "")
            print(f"  {level_label} {a['message']}")


# ─── 進捗サマリー生成（テキスト） ────────────────────────────────────────────

def generate_summary_text(project: Project) -> str:
    """メール本文などに使えるテキストサマリーを生成する"""
    project.refresh_all_statuses()
    tasks = project.all_tasks()
    done  = sum(1 for t in tasks if t.status == TaskStatus.DONE)
    rate  = project.completion_rate()
    alerts = check_alerts(project)
    critical = [a for a in alerts if a["level"] == "critical"]
    warnings = [a for a in alerts if a["level"] == "warning"]
    next_actions = project.next_actions()[:3]

    lines = [
        f"■ {project.name} 進捗サマリー ({datetime.now().strftime('%Y/%m/%d %H:%M')})",
        f"",
        f"  完了率: {rate*100:.1f}% ({done}/{len(tasks)}件)",
        f"",
    ]

    if critical:
        lines.append(f"  【緊急対応が必要 ({len(critical)}件)】")
        for a in critical:
            lines.append(f"  - {a['message']}")
        lines.append("")

    if warnings:
        lines.append(f"  【要注意 ({len(warnings)}件)】")
        for a in warnings:
            lines.append(f"  - {a['message']}")
        lines.append("")

    if next_actions:
        lines.append("  【次にやるべきこと】")
        for i, t in enumerate(next_actions, 1):
            due = f" (期限: {t.due_date})" if t.due_date else ""
            lines.append(f"  {i}. {t.title} - {t.assignee}{due}")
        lines.append("")

    return "\n".join(lines)


# ─── メール通知 ───────────────────────────────────────────────────────────────

class EmailNotifier:
    """SMTPでメール通知を送る。.envや設定ファイルから設定を読む。"""

    def __init__(
        self,
        smtp_host: str = "localhost",
        smtp_port: int = 25,
        from_addr: str = "tracker@example.com",
        username: Optional[str] = None,
        password: Optional[str] = None,
        use_tls: bool = False,
    ):
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.from_addr = from_addr
        self.username  = username
        self.password  = password
        self.use_tls   = use_tls

    @classmethod
    def from_env(cls) -> "EmailNotifier":
        """環境変数から設定を読む（.envファイル対応）"""
        import os
        return cls(
            smtp_host = os.getenv("SMTP_HOST", "localhost"),
            smtp_port = int(os.getenv("SMTP_PORT", "25")),
            from_addr = os.getenv("SMTP_FROM", "tracker@example.com"),
            username  = os.getenv("SMTP_USER"),
            password  = os.getenv("SMTP_PASS"),
            use_tls   = os.getenv("SMTP_TLS", "false").lower() == "true",
        )

    def send(self, to_addrs: list[str], subject: str, body: str) -> bool:
        """メールを送信する。成功したら True を返す。"""
        msg = MIMEMultipart("alternative")
        msg["From"]    = self.from_addr
        msg["To"]      = ", ".join(to_addrs)
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain", "utf-8"))

        try:
            if self.use_tls:
                with smtplib.SMTP_SSL(self.smtp_host, self.smtp_port) as server:
                    if self.username:
                        server.login(self.username, self.password)
                    server.sendmail(self.from_addr, to_addrs, msg.as_string())
            else:
                with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
                    if self.username:
                        server.login(self.username, self.password)
                    server.sendmail(self.from_addr, to_addrs, msg.as_string())
            return True
        except Exception as e:
            print(f"メール送信エラー: {e}")
            return False

    def send_alert(self, project: Project, to_addrs: list[str]) -> bool:
        """アラートをメールで送信する"""
        alerts = check_alerts(project)
        if not alerts:
            return False  # アラートがなければ送らない

        subject = f"[要対応] {project.name} - {len(alerts)}件のアラート"
        body    = generate_summary_text(project)
        return self.send(to_addrs, subject, body)

    def send_weekly_report(self, project: Project, to_addrs: list[str]) -> bool:
        """週次進捗レポートをメールで送信する"""
        subject = f"[週次報告] {project.name} 進捗レポート"
        body    = generate_summary_text(project)
        return self.send(to_addrs, subject, body)


# ─── ログファイルへの記録 ─────────────────────────────────────────────────────

def log_alerts(project: Project, log_path: Path) -> None:
    """アラートをJSONLファイルに記録する"""
    alerts = check_alerts(project)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    with log_path.open("a", encoding="utf-8") as f:
        for a in alerts:
            record = {
                "timestamp":  datetime.now().isoformat(),
                "project":    project.name,
                "level":      a["level"],
                "message":    a["message"],
                "task_id":    a["task"].task_id,
                "task_title": a["task"].title,
                "assignee":   a["task"].assignee,
            }
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
