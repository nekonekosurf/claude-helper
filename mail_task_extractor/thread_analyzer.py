"""
thread_analyzer.py - スレッド全体分析モジュール

機能:
  - スレッド時系列分析（誰が何をいつ言ったか）
  - 未回答メール検出（返信が来ていない依頼を特定）
  - プロジェクト進捗サマリー生成（LLM）
  - 会話パターン分析（ボールを誰が持っているか）
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from openai import AsyncOpenAI

from .models import (
    EmailThread,
    ExtractedTask,
    ParsedEmail,
    ProjectProgress,
    TaskStatus,
)


# ─── スレッド分析 ─────────────────────────────────────────────────────────────

class ThreadAnalyzer:
    """
    スレッド内のメール群から進捗・課題を分析する。
    LLM を使う機能（スレッドサマリー）と、
    ルールベースの機能（未回答検出等）を両方持つ。
    """

    UNANSWERED_HOURS = 48   # 何時間返信がないと「未回答」とみなすか
    STALE_DAYS = 7          # 何日で「放置」とみなすか

    def __init__(
        self,
        base_url: str = "http://localhost:8000/v1",
        model: str = "meta-llama/Llama-3.1-8B-Instruct",
        api_key: str = "dummy",
    ):
        self.client = AsyncOpenAI(base_url=base_url, api_key=api_key)
        self.model = model

    # ── ルールベース分析 ──

    def find_unanswered(self, thread: EmailThread) -> list[ParsedEmail]:
        """
        スレッド内で返信が来ていないメールを返す。
        「返信あり」の判定: In-Reply-To または References に含まれているか。
        """
        replied_ids: set[str] = set()
        for em in thread.emails:
            if em.in_reply_to:
                replied_ids.add(em.in_reply_to)
            for ref in em.references:
                replied_ids.add(ref)

        now = datetime.now(tz=timezone.utc)
        unanswered: list[ParsedEmail] = []

        for em in thread.emails:
            # 最新メールは当然返信なし → 時間経過で判定
            if em.message_id in replied_ids:
                continue
            age = now - em.date
            if age.total_seconds() / 3600 > self.UNANSWERED_HOURS:
                unanswered.append(em)

        return unanswered

    def find_ball_holder(self, thread: EmailThread) -> str | None:
        """
        現在「ボールを持っている人」を返す。
        = スレッドの最後のメール送信者。
        受信者側が対応すべき場合は、依頼者を返す。
        """
        if not thread.emails:
            return None
        last = thread.emails[-1]
        return last.sender_name or last.sender_address

    def analyze_thread(self, thread: EmailThread) -> dict[str, Any]:
        """
        スレッド全体のルールベース分析結果を返す。
        LLM不要で高速に動作する。
        """
        emails = thread.emails
        if not emails:
            return {}

        # 参加者別 送受信数
        sender_count: Counter = Counter()
        for em in emails:
            sender_count[em.sender_name or em.sender_address] += 1

        # 平均返信時間（時間）
        avg_reply_hours = self._calc_avg_reply_time(emails)

        # 最後のアクティビティからの経過日数
        now = datetime.now(tz=timezone.utc)
        last = thread.last_activity or emails[-1].date
        days_since_last = (now - last).days

        # 未回答メール
        unanswered = self.find_unanswered(thread)

        # ボールホルダー
        ball_holder = self.find_ball_holder(thread)

        return {
            "thread_id": thread.thread_id,
            "subject": thread.subject,
            "email_count": len(emails),
            "participant_count": len(thread.participants),
            "sender_distribution": dict(sender_count.most_common()),
            "avg_reply_hours": avg_reply_hours,
            "days_since_last_activity": days_since_last,
            "is_stale": days_since_last > self.STALE_DAYS,
            "unanswered_count": len(unanswered),
            "unanswered_emails": [
                {
                    "from": em.sender_name or em.sender_address,
                    "subject": em.subject,
                    "date": em.date.isoformat(),
                }
                for em in unanswered
            ],
            "ball_holder": ball_holder,
        }

    def _calc_avg_reply_time(self, emails: list[ParsedEmail]) -> float | None:
        """平均返信時間（時間）を計算。返信が1件もない場合は None。"""
        if len(emails) < 2:
            return None

        reply_times: list[float] = []
        sorted_emails = sorted(emails, key=lambda e: e.date)

        for i in range(1, len(sorted_emails)):
            prev = sorted_emails[i - 1]
            curr = sorted_emails[i]
            diff = (curr.date - prev.date).total_seconds() / 3600
            if diff > 0:
                reply_times.append(diff)

        if not reply_times:
            return None

        return sum(reply_times) / len(reply_times)

    # ── LLM を使ったスレッドサマリー ──

    async def summarize_thread(self, thread: EmailThread) -> str:
        """
        スレッド全体を時系列で LLM に渡して進捗サマリーを生成。
        スレッドが長い場合は最初と最後のメールのみ渡す（コンテキスト節約）。
        """
        emails = thread.emails
        if not emails:
            return "メールなし"

        # 長いスレッドは最初2通 + 最後3通に絞る
        if len(emails) > 8:
            sampled = emails[:2] + emails[-3:]
            note = f"（全{len(emails)}通から抜粋）"
        else:
            sampled = emails
            note = ""

        thread_text = self._format_thread_for_llm(sampled, thread.subject)

        prompt = f"""以下のメールスレッドを分析して、プロジェクトの進捗状況を3〜5行で要約してください。{note}

【要約に含める内容】
- 現在の状況（何が完了し、何が未完了か）
- 次に誰が何をすべきか
- 問題・リスクがあれば指摘

---
{thread_text}
---

要約:"""

        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=300,
        )
        return response.choices[0].message.content or ""

    async def detect_bottleneck_tasks(
        self,
        tasks: list[ExtractedTask],
        thread: EmailThread,
    ) -> list[str]:
        """
        スレッドとタスクリストからボトルネック（他タスクが待っているタスク）を検出。
        """
        # 他タスクから依存されているタスクIDを収集
        dependency_targets: Counter = Counter()
        for task in tasks:
            for dep in task.dependencies:
                dependency_targets[dep.depends_on_task_id] += 1

        # 依存元が多い or ステータスが PENDING のタスクをボトルネック候補とする
        bottlenecks: list[str] = []
        for task in tasks:
            count = dependency_targets.get(task.task_id, 0)
            if count > 0 and task.status in (TaskStatus.PENDING, TaskStatus.WAITING):
                bottlenecks.append(
                    f"{task.task_id}: {task.title} ({count}件が待機中)"
                )

        return bottlenecks

    def _format_thread_for_llm(
        self, emails: list[ParsedEmail], subject: str
    ) -> str:
        """スレッドをLLMに渡しやすいテキスト形式に変換"""
        lines = [f"【スレッド件名】{subject}\n"]
        for em in emails:
            lines.append(
                f"[{em.date.strftime('%Y/%m/%d %H:%M')}] "
                f"{em.sender_name or em.sender_address}"
            )
            lines.append(em.body_text[:500])  # 各メールは最大500文字
            lines.append("---")
        return "\n".join(lines)


# ─── プロジェクト全体進捗サマリー ────────────────────────────────────────────

class ProjectProgressTracker:
    """
    全タスク・全スレッドを横断して進捗を集計する。
    """

    def __init__(self, analyzer: ThreadAnalyzer):
        self.analyzer = analyzer

    def calculate_progress(self, tasks: list[ExtractedTask]) -> ProjectProgress:
        """タスクリストから進捗を集計"""
        now = datetime.now(tz=timezone.utc)

        status_counts: dict[str, int] = defaultdict(int)
        overdue_count = 0

        for task in tasks:
            status_counts[task.status] += 1
            if (
                task.deadline
                and task.status not in (TaskStatus.DONE, TaskStatus.CANCELLED)
            ):
                deadline_aware = task.deadline
                if deadline_aware.tzinfo is None:
                    deadline_aware = deadline_aware.replace(tzinfo=timezone.utc)
                if deadline_aware < now:
                    overdue_count += 1

        return ProjectProgress(
            total_tasks=len(tasks),
            done=status_counts.get(TaskStatus.DONE, 0),
            in_progress=status_counts.get(TaskStatus.IN_PROGRESS, 0),
            pending=status_counts.get(TaskStatus.PENDING, 0),
            waiting=status_counts.get(TaskStatus.WAITING, 0),
            overdue=overdue_count,
        )

    def find_stale_threads(self, threads: list[EmailThread]) -> list[str]:
        """放置されているスレッドのIDリストを返す"""
        return [t.thread_id for t in threads if t.is_stale]

    async def generate_dashboard_report(
        self,
        tasks: list[ExtractedTask],
        threads: list[EmailThread],
    ) -> str:
        """
        全体のダッシュボードレポートをテキストで生成。
        LLM は使わずルールベースで生成（高速）。
        """
        progress = self.calculate_progress(tasks)
        stale = self.find_stale_threads(threads)

        now_str = datetime.now().strftime("%Y年%m月%d日 %H:%M")

        lines = [
            f"# プロジェクト進捗レポート ({now_str})",
            "",
            f"## タスク概要",
            f"- 総タスク数: {progress.total_tasks}件",
            f"- 完了: {progress.done}件",
            f"- 対応中: {progress.in_progress}件",
            f"- 未着手: {progress.pending}件",
            f"- 待機中: {progress.waiting}件",
            f"- 期限超過: {progress.overdue}件",
            "",
        ]

        if progress.overdue > 0:
            lines.append("## 期限超過タスク（要対応）")
            now = datetime.now(tz=timezone.utc)
            overdue_tasks = [
                t for t in tasks
                if t.deadline and t.status not in (TaskStatus.DONE, TaskStatus.CANCELLED)
                and (
                    t.deadline.replace(tzinfo=timezone.utc) if t.deadline.tzinfo is None
                    else t.deadline
                ) < now
            ]
            for task in overdue_tasks:
                lines.append(
                    f"- [{task.task_id}] {task.title} "
                    f"(担当: {task.assignee}, 期限: {task.deadline_text})"
                )
            lines.append("")

        if stale:
            lines.append(f"## 放置スレッド ({len(stale)}件)")
            stale_threads = [t for t in threads if t.thread_id in stale]
            for t in stale_threads:
                days = (datetime.now(tz=t.last_activity.tzinfo) - t.last_activity).days
                lines.append(f"- {t.subject} ({days}日間返信なし)")
            lines.append("")

        # 担当者別タスク集計
        assignee_tasks: dict[str, list[ExtractedTask]] = defaultdict(list)
        for task in tasks:
            if task.status not in (TaskStatus.DONE, TaskStatus.CANCELLED):
                assignee_tasks[task.assignee].append(task)

        if assignee_tasks:
            lines.append("## 担当者別 未完了タスク")
            for assignee, atasks in sorted(assignee_tasks.items()):
                lines.append(f"\n### {assignee} ({len(atasks)}件)")
                for task in sorted(
                    atasks,
                    key=lambda t: (
                        t.priority.value,
                        t.deadline or datetime.max,
                    ),
                ):
                    deadline_str = (
                        f" [期限: {task.deadline_text}]" if task.deadline_text else ""
                    )
                    priority_marker = {"high": "!", "medium": " ", "low": "↓"}.get(
                        task.priority.value, " "
                    )
                    lines.append(
                        f"  {priority_marker} [{task.status.value}] "
                        f"{task.title}{deadline_str}"
                    )

        return "\n".join(lines)
