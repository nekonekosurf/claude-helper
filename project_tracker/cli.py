"""
project_tracker/cli.py
プロジェクトトラッカー CLI エントリポイント

使い方:
  uv run python -m project_tracker.cli --help
  uv run python -m project_tracker.cli new --template 産業廃棄物処理
  uv run python -m project_tracker.cli show data/projects/sample.json
  uv run python -m project_tracker.cli alert data/projects/sample.json
  uv run python -m project_tracker.cli chat data/projects/sample.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

from .models import Phase, Priority, Project, Task, TaskStatus
from .notification import generate_summary_text, log_alerts, print_alerts
from .progress_view import (
    show_by_assignee,
    show_dashboard,
    show_gantt,
    show_next_actions,
    show_summary,
    show_task_table,
)
from .templates import TEMPLATES, create_from_template, list_templates


# ─── コマンドハンドラ ─────────────────────────────────────────────────────────

def cmd_new(args):
    """テンプレートまたはインタラクティブ入力でプロジェクトを新規作成"""
    if args.template:
        if args.template not in TEMPLATES:
            print(f"テンプレート '{args.template}' が見つかりません。")
            list_templates()
            sys.exit(1)

        name = args.name or args.template
        proj = create_from_template(args.template, name)

    else:
        # インタラクティブ作成（最小限）
        name = input("プロジェクト名: ").strip()
        desc = input("説明: ").strip()
        proj = Project(name=name, description=desc)

        while True:
            phase_name = input("\nフェーズ名（空欄で終了）: ").strip()
            if not phase_name:
                break
            phase = Phase(name=phase_name, order=len(proj.phases) + 1)

            while True:
                title = input(f"  [{phase_name}] タスク名（空欄でフェーズ終了）: ").strip()
                if not title:
                    break
                assignee = input(f"    担当者: ").strip()
                due_str  = input(f"    期限 (YYYY-MM-DD, 空欄でスキップ): ").strip()
                due = date.fromisoformat(due_str) if due_str else None
                priority_str = input("    優先度 (緊急/高/中/低, 省略で中): ").strip() or "中"
                priority = {
                    "緊急": Priority.CRITICAL, "高": Priority.HIGH,
                    "中":   Priority.MEDIUM,   "低": Priority.LOW,
                }.get(priority_str, Priority.MEDIUM)

                phase.tasks.append(Task(
                    title=title, assignee=assignee, due_date=due, priority=priority
                ))
            proj.phases.append(phase)

    out_path = Path(args.output) if args.output else \
               Path(f"data/projects/{proj.name.replace(' ', '_')}.json")
    proj.save(out_path)
    print(f"プロジェクトを保存しました: {out_path}")
    show_summary(proj)


def cmd_show(args):
    """プロジェクトの進捗を表示する"""
    proj = _load(args.project)

    view = args.view or "dashboard"
    if view == "dashboard":
        show_dashboard(proj)
    elif view == "summary":
        show_summary(proj)
    elif view == "tasks":
        show_task_table(proj)
    elif view == "gantt":
        show_gantt(proj, weeks=int(args.weeks or 8))
    elif view == "assignee":
        show_by_assignee(proj)
    elif view == "next":
        show_next_actions(proj, top_n=int(args.top or 5))
    else:
        print(f"不明なビュー: {view}")
        sys.exit(1)


def cmd_alert(args):
    """アラート・リマインダーをチェックして表示する"""
    proj = _load(args.project)

    if args.log:
        log_alerts(proj, Path(args.log))
        print(f"ログを記録しました: {args.log}")

    if args.email:
        from .notification import EmailNotifier
        notifier = EmailNotifier.from_env()
        sent = notifier.send_alert(proj, [args.email])
        if sent:
            print(f"メールを送信しました: {args.email}")
        else:
            print("メール送信に失敗しました（アラートなし、またはSMTPエラー）。")

    print_alerts(proj, stale_days=int(args.stale or 7))


def cmd_update(args):
    """タスクのステータスを更新する"""
    proj = _load(args.project)
    all_dict = proj.all_tasks_dict()

    if args.task_id not in all_dict:
        print(f"タスクID '{args.task_id}' が見つかりません。")
        _print_task_ids(proj)
        sys.exit(1)

    task = all_dict[args.task_id]
    print(f"更新対象: {task.title} (現在: {task.status.value})")

    STATUS_MAP = {
        "todo": TaskStatus.TODO,
        "未着手": TaskStatus.TODO,
        "progress": TaskStatus.IN_PROGRESS,
        "進行中": TaskStatus.IN_PROGRESS,
        "done": TaskStatus.DONE,
        "完了": TaskStatus.DONE,
        "hold": TaskStatus.ON_HOLD,
        "保留": TaskStatus.ON_HOLD,
    }

    if args.status:
        new_status = STATUS_MAP.get(args.status.lower())
        if not new_status:
            print(f"ステータス '{args.status}' は無効です。todo/done/progress/hold を指定してください。")
            sys.exit(1)
        task.status = new_status

    if args.assignee:
        task.assignee = args.assignee

    if args.note:
        task.notes = args.note

    task.updated_at = datetime.now()

    proj.save(Path(args.project))
    print(f"更新しました: {task.title} -> {task.status.value}")


def cmd_add_task(args):
    """既存プロジェクトにタスクを追加する"""
    proj = _load(args.project)

    # フェーズを探す
    phase = next((p for p in proj.phases if p.name == args.phase), None)
    if not phase:
        print(f"フェーズ '{args.phase}' が見つかりません。")
        print("利用可能なフェーズ:", [p.name for p in proj.phases])
        sys.exit(1)

    due = date.fromisoformat(args.due) if args.due else None
    priority = {
        "critical": Priority.CRITICAL, "緊急": Priority.CRITICAL,
        "high":     Priority.HIGH,     "高":   Priority.HIGH,
        "medium":   Priority.MEDIUM,   "中":   Priority.MEDIUM,
        "low":      Priority.LOW,      "低":   Priority.LOW,
    }.get((args.priority or "medium").lower(), Priority.MEDIUM)

    task = Task(
        title       = args.title,
        assignee    = args.assignee,
        due_date    = due,
        priority    = priority,
        description = args.description or "",
    )

    if args.depends:
        task.depends_on = args.depends.split(",")

    phase.tasks.append(task)
    proj.save(Path(args.project))
    print(f"タスクを追加しました: {task.task_id} / {task.title} -> [{phase.name}]")


def cmd_report(args):
    """進捗サマリーレポートをテキストで出力する"""
    proj = _load(args.project)
    text = generate_summary_text(proj)
    print(text)

    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
        print(f"レポートを保存しました: {args.output}")


def cmd_chat(args):
    """LLMと自然言語で進捗を確認する"""
    from .smart_assistant import SmartAssistant

    proj = _load(args.project)
    base_url = args.base_url or "http://localhost:8000/v1"
    model    = args.model    or "gpt-oss-120b"

    assistant = SmartAssistant(proj, base_url=base_url, model=model)
    assistant.interactive()


def cmd_templates(args):
    """利用可能なテンプレート一覧を表示する"""
    list_templates()


# ─── ヘルパー ─────────────────────────────────────────────────────────────────

def _load(path_str: str) -> Project:
    path = Path(path_str)
    if not path.exists():
        print(f"ファイルが見つかりません: {path_str}")
        sys.exit(1)
    proj = Project.load(path)
    return proj


def _print_task_ids(proj: Project) -> None:
    print("タスクID一覧:")
    for phase in proj.phases:
        for task in phase.tasks:
            print(f"  {task.task_id}  [{phase.name}] {task.title}")


# ─── CLIパーサー ─────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="project_tracker",
        description="プロジェクト進捗トラッカー CLI",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # new
    p_new = sub.add_parser("new", help="新規プロジェクト作成")
    p_new.add_argument("--template", "-t", help="テンプレート名")
    p_new.add_argument("--name",     "-n", help="プロジェクト名")
    p_new.add_argument("--output",   "-o", help="保存先JSONパス")

    # show
    p_show = sub.add_parser("show", help="進捗を表示する")
    p_show.add_argument("project", help="プロジェクトJSONパス")
    p_show.add_argument("--view", choices=["dashboard","summary","tasks","gantt","assignee","next"],
                        default="dashboard", help="表示形式")
    p_show.add_argument("--weeks", default=8, type=int, help="ガントチャートの週数")
    p_show.add_argument("--top",   default=5, type=int, help="次にやること: 表示件数")

    # alert
    p_alert = sub.add_parser("alert", help="アラートチェック")
    p_alert.add_argument("project", help="プロジェクトJSONパス")
    p_alert.add_argument("--stale", default=7, type=int, help="放置判定日数")
    p_alert.add_argument("--log",   help="ログ保存先（JSONLファイル）")
    p_alert.add_argument("--email", help="通知先メールアドレス")

    # update
    p_upd = sub.add_parser("update", help="タスクを更新する")
    p_upd.add_argument("project", help="プロジェクトJSONパス")
    p_upd.add_argument("task_id", help="タスクID（8文字）")
    p_upd.add_argument("--status",   help="新ステータス (todo/done/progress/hold)")
    p_upd.add_argument("--assignee", help="担当者")
    p_upd.add_argument("--note",     help="メモ")

    # add-task
    p_add = sub.add_parser("add-task", help="タスクを追加する")
    p_add.add_argument("project",   help="プロジェクトJSONパス")
    p_add.add_argument("phase",     help="追加先フェーズ名")
    p_add.add_argument("title",     help="タスク名")
    p_add.add_argument("assignee",  help="担当者")
    p_add.add_argument("--due",         help="期限 (YYYY-MM-DD)")
    p_add.add_argument("--priority",    help="優先度 (critical/high/medium/low)")
    p_add.add_argument("--description", help="説明")
    p_add.add_argument("--depends",     help="依存タスクID (カンマ区切り)")

    # report
    p_report = sub.add_parser("report", help="進捗レポートを出力する")
    p_report.add_argument("project", help="プロジェクトJSONパス")
    p_report.add_argument("--output", "-o", help="レポートファイル保存先")

    # chat
    p_chat = sub.add_parser("chat", help="LLMと自然言語で進捗確認")
    p_chat.add_argument("project", help="プロジェクトJSONパス")
    p_chat.add_argument("--base-url", help="vLLM APIのURL (デフォルト: http://localhost:8000/v1)")
    p_chat.add_argument("--model",    help="モデル名 (デフォルト: gpt-oss-120b)")

    # templates
    sub.add_parser("templates", help="テンプレート一覧を表示する")

    return parser


def main():
    parser = build_parser()
    args   = parser.parse_args()

    HANDLERS = {
        "new":       cmd_new,
        "show":      cmd_show,
        "alert":     cmd_alert,
        "update":    cmd_update,
        "add-task":  cmd_add_task,
        "report":    cmd_report,
        "chat":      cmd_chat,
        "templates": cmd_templates,
    }

    handler = HANDLERS.get(args.command)
    if handler:
        handler(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
