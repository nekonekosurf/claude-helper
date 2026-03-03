"""
project_tracker/progress_view.py
richライブラリを使ったCLI表示
"""

from __future__ import annotations

from datetime import date
from typing import Optional

try:
    from rich import box
    from rich.console import Console
    from rich.panel import Panel
    from rich.progress import BarColumn, Progress, TextColumn
    from rich.table import Table
    from rich.text import Text
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False

from .models import Priority, Project, Task, TaskStatus

console = Console() if RICH_AVAILABLE else None


# ─── カラーマッピング ─────────────────────────────────────────────────────────

STATUS_COLOR = {
    TaskStatus.TODO:        "white",
    TaskStatus.IN_PROGRESS: "cyan",
    TaskStatus.DONE:        "green",
    TaskStatus.ON_HOLD:     "yellow",
    TaskStatus.OVERDUE:     "bold red",
}

PRIORITY_COLOR = {
    Priority.CRITICAL: "bold red",
    Priority.HIGH:     "red",
    Priority.MEDIUM:   "yellow",
    Priority.LOW:      "dim",
}


def _due_str(task: Task) -> Text:
    if task.due_date is None:
        return Text("—", style="dim")
    days = task.days_until_due()
    s = task.due_date.strftime("%m/%d")
    if task.status == TaskStatus.DONE:
        return Text(s, style="dim green")
    if days is not None and days < 0:
        return Text(f"{s} ({abs(days)}日超過)", style="bold red")
    if days is not None and days <= 3:
        return Text(f"{s} (あと{days}日)", style="yellow")
    return Text(s)


def _blocked_mark(task: Task, all_dict: dict) -> str:
    return "[BLOCKED]" if task.is_blocked(all_dict) else ""


# ─── タスク一覧テーブル ────────────────────────────────────────────────────────

def show_task_table(project: Project, phase_name: Optional[str] = None) -> None:
    if not RICH_AVAILABLE:
        _fallback_task_list(project)
        return

    project.refresh_all_statuses()
    all_dict = project.all_tasks_dict()

    table = Table(
        title=f"[bold]{project.name}[/bold] タスク一覧",
        box=box.ROUNDED,
        show_lines=True,
        header_style="bold blue",
    )
    table.add_column("ID",    style="dim",    width=9)
    table.add_column("フェーズ",               width=14)
    table.add_column("タスク",                 width=28)
    table.add_column("担当者",                 width=10)
    table.add_column("期限",                   width=16)
    table.add_column("優先度",                 width=6)
    table.add_column("ステータス",             width=10)
    table.add_column("備考",   style="dim",    width=16)

    for phase in project.phases:
        if phase_name and phase.name != phase_name:
            continue
        for task in phase.tasks:
            task.refresh_status()
            blocked = _blocked_mark(task, all_dict)
            notes = blocked or (task.notes[:20] if task.notes else "")
            table.add_row(
                task.task_id,
                phase.name,
                task.title,
                task.assignee,
                _due_str(task),
                Text(task.priority.value, style=PRIORITY_COLOR[task.priority]),
                Text(task.status.value,   style=STATUS_COLOR[task.status]),
                Text(notes, style="bold red" if blocked else "dim"),
            )

    console.print(table)


# ─── ステータスサマリー ────────────────────────────────────────────────────────

def show_summary(project: Project) -> None:
    project.refresh_all_statuses()
    tasks = project.all_tasks()

    counts = {s: 0 for s in TaskStatus}
    for t in tasks:
        counts[t.status] += 1

    rate = project.completion_rate()
    overdue_n = counts[TaskStatus.OVERDUE]
    stale = project.stale_tasks(days=7)

    if not RICH_AVAILABLE:
        print(f"\n=== {project.name} サマリー ===")
        print(f"完了率: {rate*100:.1f}%  全{len(tasks)}件")
        for s, n in counts.items():
            print(f"  {s.value}: {n}")
        print(f"期限超過: {overdue_n}件 / 放置(7日): {len(stale)}件")
        return

    panel_lines = [
        f"[bold]プロジェクト:[/bold] {project.name}",
        f"[bold]完了率:[/bold]  {rate*100:.1f}%  (全{len(tasks)}件)",
        "",
    ]
    for s, n in counts.items():
        bar = "█" * n + "░" * max(0, 10 - n)
        color = STATUS_COLOR[s]
        panel_lines.append(f"  [{color}]{s.value}[/{color}]: {n:>3}件  {bar}")

    panel_lines += [
        "",
        f"[bold red]期限超過:[/bold red] {overdue_n}件",
        f"[bold yellow]放置タスク(7日以上):[/bold yellow] {len(stale)}件",
    ]

    console.print(Panel("\n".join(panel_lines), title="進捗サマリー", border_style="blue"))

    # 完了率バー
    with Progress(
        TextColumn("[bold blue]{task.description}"),
        BarColumn(bar_width=50),
        TextColumn("[progress.percentage]{task.percentage:.1f}%"),
        console=console,
        transient=False,
    ) as progress:
        job = progress.add_task("全体進捗", total=100)
        progress.update(job, completed=rate * 100)


# ─── ガントチャート（テキスト） ───────────────────────────────────────────────

def show_gantt(project: Project, weeks: int = 8) -> None:
    """テキストベースのガントチャート（週単位）"""
    project.refresh_all_statuses()
    today = date.today()
    start = today

    # 全タスクの最早・最遅期限を探す
    dated = [t for t in project.all_tasks() if t.due_date]
    if dated:
        min_due = min(t.due_date for t in dated)
        start = min(today, min_due)

    # 表示幅は weeks 週
    week_labels = []
    for i in range(weeks):
        d = start + __import__("datetime").timedelta(weeks=i)
        week_labels.append(d.strftime("%m/%d"))

    header = "タスク名              担当者    " + "".join(f"{w:>7}" for w in week_labels)

    if RICH_AVAILABLE:
        console.print(f"\n[bold blue]ガントチャート[/bold blue] (基準: {start})\n")
        console.print(header, style="bold")
        console.print("─" * len(header))
    else:
        print(f"\nガントチャート (基準: {start})")
        print(header)
        print("─" * len(header))

    for task in project.all_tasks():
        name_col = (task.title[:20]).ljust(20)
        asn_col  = (task.assignee[:9]).ljust(9)
        row = f"{name_col} {asn_col} "

        for i in range(weeks):
            week_start = start + __import__("datetime").timedelta(weeks=i)
            week_end   = week_start + __import__("datetime").timedelta(days=6)

            if task.due_date and task.due_date >= week_start and task.due_date <= week_end:
                char = "▶"  # 期限週
            elif task.status == TaskStatus.DONE and task.due_date and task.due_date >= week_start:
                char = "✓"
            elif task.due_date and task.due_date < week_start:
                char = " " if task.status == TaskStatus.DONE else "!"
            else:
                char = "─" if task.status == TaskStatus.IN_PROGRESS else " "

            row += f"   {char}   "

        if RICH_AVAILABLE:
            style = STATUS_COLOR.get(task.status, "")
            console.print(row, style=style)
        else:
            print(row)


# ─── 担当者別一覧 ─────────────────────────────────────────────────────────────

def show_by_assignee(project: Project) -> None:
    project.refresh_all_statuses()

    assignees: dict[str, list[Task]] = {}
    for t in project.all_tasks():
        assignees.setdefault(t.assignee, []).append(t)

    if not RICH_AVAILABLE:
        for person, tasks in sorted(assignees.items()):
            print(f"\n--- {person} ---")
            for t in tasks:
                print(f"  [{t.status.value}] {t.title}  期限:{t.due_date or '—'}")
        return

    table = Table(title="担当者別タスク", box=box.SIMPLE_HEAD, header_style="bold magenta")
    table.add_column("担当者",   width=12)
    table.add_column("タスク数", width=8)
    table.add_column("完了",     width=6)
    table.add_column("期限超過", width=8)
    table.add_column("タスク一覧（最大3件）", width=50)

    for person, tasks in sorted(assignees.items()):
        done    = sum(1 for t in tasks if t.status == TaskStatus.DONE)
        overdue = sum(1 for t in tasks if t.status == TaskStatus.OVERDUE)
        preview = ", ".join(t.title[:18] for t in tasks[:3])
        if len(tasks) > 3:
            preview += f"... (+{len(tasks)-3})"
        table.add_row(
            person,
            str(len(tasks)),
            str(done),
            Text(str(overdue), style="bold red") if overdue else Text("0", style="dim"),
            preview,
        )

    console.print(table)


# ─── 次にやるべきこと ─────────────────────────────────────────────────────────

def show_next_actions(project: Project, top_n: int = 5) -> None:
    project.refresh_all_statuses()
    actions = project.next_actions()[:top_n]

    if not RICH_AVAILABLE:
        print("\n=== 次にやるべきこと ===")
        for i, t in enumerate(actions, 1):
            due = f" (期限:{t.due_date})" if t.due_date else ""
            print(f"{i}. [{t.priority.value}] {t.title} - {t.assignee}{due}")
        return

    table = Table(
        title="次にやるべきこと TOP5",
        box=box.DOUBLE_EDGE,
        header_style="bold green",
    )
    table.add_column("順位", width=4)
    table.add_column("タスク", width=30)
    table.add_column("担当者", width=10)
    table.add_column("優先度", width=6)
    table.add_column("期限", width=14)
    table.add_column("ステータス", width=10)

    for i, t in enumerate(actions, 1):
        table.add_row(
            f"#{i}",
            t.title,
            t.assignee,
            Text(t.priority.value, style=PRIORITY_COLOR[t.priority]),
            _due_str(t),
            Text(t.status.value, style=STATUS_COLOR[t.status]),
        )

    console.print(table)

    # アラート
    stale = project.stale_tasks(7)
    if stale:
        console.print(
            f"\n[bold yellow]警告:[/bold yellow] {len(stale)}件のタスクが7日以上放置されています。",
            style="yellow",
        )
        for t in stale:
            console.print(f"  - {t.title} ({t.assignee})", style="dim yellow")


# ─── フォールバック（richなし） ────────────────────────────────────────────────

def _fallback_task_list(project: Project) -> None:
    print(f"\n=== {project.name} タスク一覧 ===")
    for phase in project.phases:
        print(f"\n[{phase.name}]")
        for t in phase.tasks:
            t.refresh_status()
            due = t.due_date.isoformat() if t.due_date else "—"
            print(f"  {t.task_id} | {t.status.value:6} | {t.priority.value:2} | "
                  f"{t.assignee:10} | {due} | {t.title}")


# ─── 全画面表示 ────────────────────────────────────────────────────────────────

def show_dashboard(project: Project) -> None:
    """全セクションを一括表示するダッシュボード"""
    if RICH_AVAILABLE:
        console.rule(f"[bold blue]{project.name} ダッシュボード[/bold blue]")

    show_summary(project)
    show_next_actions(project)
    show_task_table(project)
    show_by_assignee(project)
    show_gantt(project)
