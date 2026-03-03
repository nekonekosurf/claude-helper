"""
project_tracker/smart_assistant.py
vLLM（OpenAI互換API）を使ったインテリジェント進捗確認
"""

from __future__ import annotations

import json
import os
from datetime import date

from openai import OpenAI

from .models import Project, TaskStatus


class SmartAssistant:
    """
    自然言語でプロジェクトの進捗を確認できるアシスタント。
    vLLM / OpenAI互換APIを想定。
    """

    def __init__(
        self,
        project: Project,
        base_url: str = "http://localhost:8000/v1",
        model: str = "gpt-oss-120b",
        api_key: str = "dummy",
    ):
        self.project = project
        self.model   = model
        self.client  = OpenAI(base_url=base_url, api_key=api_key)
        self._context_cache: str | None = None

    # ─── プロジェクト状況のコンテキスト生成 ─────────────────────────────────

    def _build_context(self) -> str:
        if self._context_cache:
            return self._context_cache

        self.project.refresh_all_statuses()
        tasks = self.project.all_tasks()
        all_dict = self.project.all_tasks_dict()

        lines = [
            f"# プロジェクト: {self.project.name}",
            f"説明: {self.project.description}",
            f"完了率: {self.project.completion_rate()*100:.1f}%",
            f"今日の日付: {date.today().isoformat()}",
            "",
            "## フェーズ・タスク一覧",
        ]

        for phase in self.project.phases:
            rate = phase.completion_rate()
            lines.append(f"\n### {phase.name} (完了率 {rate*100:.0f}%)")
            for t in phase.tasks:
                blocked = "【ブロック中】" if t.is_blocked(all_dict) else ""
                days    = t.days_until_due()
                due_str = f"期限:{t.due_date} (あと{days}日)" if days is not None else "期限:未設定"
                if days is not None and days < 0:
                    due_str = f"期限:{t.due_date} (【{abs(days)}日超過】)"
                lines.append(
                    f"- [{t.status.value}] {t.title} / {t.assignee} / "
                    f"{t.priority.value}優先度 / {due_str} {blocked}"
                )
                if t.related_emails:
                    lines.append(f"  関連メール: {', '.join(t.related_emails)}")
                if t.related_docs:
                    lines.append(f"  関連文書: {', '.join(t.related_docs)}")
                if t.notes:
                    lines.append(f"  メモ: {t.notes}")

        lines.append("\n## リスク情報")
        overdue = self.project.overdue_tasks()
        stale   = self.project.stale_tasks(7)
        lines.append(f"- 期限超過タスク: {len(overdue)}件")
        lines.append(f"- 7日以上放置タスク: {len(stale)}件")

        next_actions = self.project.next_actions()[:5]
        lines.append("\n## 次にやるべきタスク（優先順）")
        for i, t in enumerate(next_actions, 1):
            lines.append(f"{i}. {t.title} ({t.assignee}) - {t.priority.value}")

        ctx = "\n".join(lines)
        self._context_cache = ctx
        return ctx

    def invalidate_cache(self) -> None:
        self._context_cache = None

    # ─── 自然言語クエリ処理 ──────────────────────────────────────────────────

    def ask(self, question: str) -> str:
        """
        自然言語でプロジェクトに関する質問を行う。

        例:
          "今何をやるべき？"
          "財務部への申請はどうなった？"
          "産廃の手続き、あとどれくらい？"
        """
        context = self._build_context()

        system_prompt = (
            "あなたはプロジェクト管理の専門家アシスタントです。"
            "以下のプロジェクト状況データに基づいて、ユーザーの質問に日本語で簡潔に答えてください。\n\n"
            "回答のガイドライン:\n"
            "- 数値や具体的な情報を含めてください\n"
            "- リスクがある場合は明確に指摘してください\n"
            "- 推奨アクションがあれば提示してください\n"
            "- 不明な情報については正直に「データなし」と伝えてください\n\n"
            f"## プロジェクトデータ\n{context}"
        )

        try:
            response = self.client.chat.completions.create(
                model    = self.model,
                messages = [
                    {"role": "system",  "content": system_prompt},
                    {"role": "user",    "content": question},
                ],
                max_tokens  = 1024,
                temperature = 0.3,
            )
            return response.choices[0].message.content or "(応答なし)"
        except Exception as e:
            return f"LLM接続エラー: {e}\n\nオフライン回答:\n{self._offline_answer(question)}"

    # ─── リスク分析 ──────────────────────────────────────────────────────────

    def analyze_risks(self) -> str:
        """
        プロジェクト全体のリスクをLLMで分析する。
        """
        context = self._build_context()

        prompt = (
            "上記プロジェクトのリスクを以下の観点で分析してください:\n"
            "1. 期限超過リスク（どのタスクが危険か）\n"
            "2. ボトルネック（ブロックされているタスク）\n"
            "3. 担当者の負荷集中\n"
            "4. 手続き的リスク（法的・コンプライアンス面）\n"
            "5. 推奨する即時アクション（上位3件）\n"
            "簡潔にMarkdown形式で答えてください。"
        )

        return self.ask(prompt)

    def generate_progress_report(self) -> str:
        """定期進捗レポートを生成する"""
        context = self._build_context()

        prompt = (
            "以下の形式で週次進捗レポートを生成してください:\n\n"
            "## 進捗レポート\n"
            "### 全体状況（1-2文）\n"
            "### 今週の成果\n"
            "### 課題・リスク\n"
            "### 来週の予定\n"
            "### 要対応事項\n\n"
            "日本語で、関係者向けに分かりやすく書いてください。"
        )

        return self.ask(prompt)

    # ─── オフラインフォールバック ─────────────────────────────────────────────

    def _offline_answer(self, question: str) -> str:
        """LLMが利用できない場合のルールベース回答"""
        self.project.refresh_all_statuses()
        q = question.lower()

        if "次" in question or "やるべき" in question or "何をす" in question:
            actions = self.project.next_actions()[:3]
            if not actions:
                return "実行可能なタスクはありません。全て完了またはブロック中です。"
            lines = ["次にやるべきタスク:"]
            for i, t in enumerate(actions, 1):
                lines.append(f"{i}. {t.title} ({t.assignee}) - {t.priority.value}優先")
            return "\n".join(lines)

        if "完了" in question or "進捗" in question or "どれくらい" in question:
            rate = self.project.completion_rate()
            tasks = self.project.all_tasks()
            done = sum(1 for t in tasks if t.status == TaskStatus.DONE)
            return (
                f"完了率: {rate*100:.1f}% ({done}/{len(tasks)}件)\n"
                f"期限超過: {len(self.project.overdue_tasks())}件"
            )

        if "期限" in question or "超過" in question:
            overdue = self.project.overdue_tasks()
            if not overdue:
                return "期限超過タスクはありません。"
            lines = [f"期限超過タスク ({len(overdue)}件):"]
            for t in overdue:
                lines.append(f"- {t.title} ({t.assignee}) / 期限: {t.due_date}")
            return "\n".join(lines)

        return (
            f"プロジェクト '{self.project.name}' の状況:\n"
            f"完了率: {self.project.completion_rate()*100:.1f}%\n"
            f"期限超過: {len(self.project.overdue_tasks())}件\n"
            f"LLMに接続できないため詳細な分析はできません。"
        )

    # ─── 対話ループ ───────────────────────────────────────────────────────────

    def interactive(self) -> None:
        """CLIでの対話ループ"""
        print(f"\nプロジェクト: {self.project.name}")
        print("質問を入力してください（'exit' で終了、'risk' でリスク分析、'report' でレポート）\n")

        while True:
            try:
                user_input = input("あなた> ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n終了します。")
                break

            if not user_input:
                continue
            if user_input.lower() in ("exit", "quit", "終了"):
                print("終了します。")
                break
            if user_input.lower() == "risk":
                print("\n--- リスク分析 ---")
                print(self.analyze_risks())
                continue
            if user_input.lower() == "report":
                print("\n--- 進捗レポート ---")
                print(self.generate_progress_report())
                continue

            answer = self.ask(user_input)
            print(f"\nAI> {answer}\n")
