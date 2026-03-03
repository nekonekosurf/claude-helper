"""
status_tracker.py - タスクステータス自動更新モジュール

機能:
  - 返信メールから完了・承認・却下シグナルを検出
  - タスクのステータス自動更新（PENDING → DONE 等）
  - 新規タスク追加の検出（「追加で○○も必要」）
  - JSONファイルへのタスク永続化
  - 変更の差分ログ記録
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from openai import AsyncOpenAI

from .models import ExtractedTask, ParsedEmail, TaskStatus


# ─── ルールベース ステータス検出 ──────────────────────────────────────────────

# 完了シグナル（正規表現）
COMPLETION_SIGNALS = [
    r"完了(しました|いたしました|です)",
    r"対応(しました|いたしました|完了です)",
    r"送付(しました|いたしました)",
    r"提出(しました|いたしました)",
    r"手配(しました|いたしました)",
    r"確認(しました|いたしました|できました)",
    r"承認(します|いたします|しました)",
    r"了解(しました|いたしました|です)",
    r"承知(しました|いたしました)",
    r"手続き(を完了|しました)",
    r"申請(しました|いたしました)",
    r"取り計らいました",
    r"処理(しました|いたしました|完了)",
    r"解決(しました|しております)",
]

# 却下・対応不可シグナル
REJECTION_SIGNALS = [
    r"対応(できません|不可|難しい状況)",
    r"(お断り|辞退)(させていただきます|いたします)",
    r"難しい(状況|です)",
    r"キャンセル(します|いたします)",
    r"中止(します|になりました)",
    r"取り止め",
]

# 保留シグナル
HOLD_SIGNALS = [
    r"保留(にします|とさせてください|中)",
    r"一旦(待って|ペンディング)",
    r"確認(中|してから)",
    r"検討(中|します|させてください)",
    r"持ち越し",
    r"ペンディング",
]

# 追加タスクシグナル
NEW_TASK_SIGNALS = [
    r"追加で",
    r"加えて",
    r"また(、|。|\s)",
    r"なお(、|。|\s)",
    r"それと",
    r"もう一点",
    r"別途",
]

_COMPLETION_RE = [re.compile(p) for p in COMPLETION_SIGNALS]
_REJECTION_RE  = [re.compile(p) for p in REJECTION_SIGNALS]
_HOLD_RE       = [re.compile(p) for p in HOLD_SIGNALS]
_NEW_TASK_RE   = [re.compile(p) for p in NEW_TASK_SIGNALS]


def detect_status_signals(text: str) -> dict[str, bool]:
    """
    テキストからステータスシグナルを検出する。
    Returns: {
        "completed": bool,  # 完了シグナルあり
        "rejected": bool,   # 却下シグナルあり
        "on_hold": bool,    # 保留シグナルあり
        "has_new_tasks": bool,  # 追加タスクシグナルあり
    }
    """
    return {
        "completed": any(p.search(text) for p in _COMPLETION_RE),
        "rejected": any(p.search(text) for p in _REJECTION_RE),
        "on_hold": any(p.search(text) for p in _HOLD_RE),
        "has_new_tasks": any(p.search(text) for p in _NEW_TASK_RE),
    }


# ─── ステータストラッカー ─────────────────────────────────────────────────────

class StatusTracker:
    """
    タスクの状態管理と自動更新。

    設計方針:
    1. ルールベースで高速判定（ほとんどのケースをカバー）
    2. ルールベースで判定できない場合のみLLMで判定（コスト節約）
    3. 全変更はログに記録
    """

    def __init__(
        self,
        storage_path: str = "tasks.json",
        base_url: str = "http://localhost:8000/v1",
        model: str = "meta-llama/Llama-3.1-8B-Instruct",
        api_key: str = "dummy",
        use_llm_fallback: bool = True,
    ):
        self.storage_path = Path(storage_path)
        self.client = AsyncOpenAI(base_url=base_url, api_key=api_key)
        self.model = model
        self.use_llm_fallback = use_llm_fallback

        # メモリ上のタスク辞書: task_id → ExtractedTask
        self._tasks: dict[str, ExtractedTask] = {}
        self._change_log: list[dict] = []

        # ストレージから読み込み
        if self.storage_path.exists():
            self._load()

    # ── タスク管理 ──

    def add_tasks(self, tasks: list[ExtractedTask]) -> None:
        """新規タスクを追加（既存IDは無視）"""
        for task in tasks:
            if task.task_id not in self._tasks:
                self._tasks[task.task_id] = task
                self._log_change(task.task_id, "created", None, task.status)

    def get_task(self, task_id: str) -> Optional[ExtractedTask]:
        return self._tasks.get(task_id)

    def get_all_tasks(self) -> list[ExtractedTask]:
        return list(self._tasks.values())

    def update_status(
        self, task_id: str, new_status: TaskStatus, reason: str = ""
    ) -> bool:
        """タスクのステータスを更新"""
        task = self._tasks.get(task_id)
        if not task:
            return False

        old_status = task.status
        if old_status == new_status:
            return False

        task.status = new_status
        task.status_history.append({
            "from": old_status.value,
            "to": new_status.value,
            "at": datetime.now().isoformat(),
            "reason": reason,
        })

        self._log_change(task_id, "status_update", old_status, new_status, reason)
        self._save()
        return True

    # ── 返信メールからのステータス更新 ──

    async def process_reply(
        self, reply: ParsedEmail, tasks: list[ExtractedTask]
    ) -> list[tuple[str, TaskStatus]]:
        """
        返信メールを解析し、関連タスクのステータスを更新する。
        Returns: [(task_id, new_status), ...] の更新リスト
        """
        # メール本文のルールベース解析
        signals = detect_status_signals(reply.body_text)
        updates: list[tuple[str, TaskStatus]] = []

        if not any(signals.values()):
            # シグナルなし → このメールは状態変化なし
            return []

        # 返信先のスレッドに関連するタスクを絞り込む
        candidate_tasks = self._find_related_tasks(reply, tasks)

        for task in candidate_tasks:
            new_status = self._determine_new_status(signals, task)
            if new_status and new_status != task.status:
                # ルールベースで判定できた
                reason = f"自動検出: {reply.subject} ({reply.date.strftime('%Y/%m/%d')})"
                self.update_status(task.task_id, new_status, reason)
                updates.append((task.task_id, new_status))

        # ルールベースで更新できたタスクが少ない場合は LLM で補完
        if not updates and self.use_llm_fallback and candidate_tasks:
            llm_updates = await self._llm_status_detection(reply, candidate_tasks)
            for task_id, new_status in llm_updates:
                reason = f"LLM検出: {reply.subject}"
                self.update_status(task_id, new_status, reason)
                updates.append((task_id, new_status))

        return updates

    def _find_related_tasks(
        self, reply: ParsedEmail, tasks: list[ExtractedTask]
    ) -> list[ExtractedTask]:
        """
        返信メールに関連するタスクを特定する。
        1. 同じスレッド ID のタスク
        2. 件名・本文から関連タスクをキーワードマッチ
        """
        related: list[ExtractedTask] = []

        # スレッドIDが一致するタスク
        if reply.thread_id:
            for task in tasks:
                if (
                    task.source_thread_id == reply.thread_id
                    and task.status not in (TaskStatus.DONE, TaskStatus.CANCELLED)
                ):
                    related.append(task)

        # スレッド情報がない場合は件名ベースでマッチ
        if not related:
            subject_keywords = set(
                re.sub(r"Re:|FW:|Fwd:", "", reply.subject, flags=re.IGNORECASE)
                .split()
            )
            for task in tasks:
                if task.status in (TaskStatus.DONE, TaskStatus.CANCELLED):
                    continue
                task_keywords = set(task.title.split())
                if subject_keywords & task_keywords:
                    related.append(task)

        return related

    def _determine_new_status(
        self, signals: dict[str, bool], task: ExtractedTask
    ) -> Optional[TaskStatus]:
        """シグナルからタスクの新ステータスを決定"""
        if signals["completed"]:
            return TaskStatus.DONE
        if signals["rejected"]:
            return TaskStatus.CANCELLED
        if signals["on_hold"]:
            return TaskStatus.WAITING
        return None

    async def _llm_status_detection(
        self,
        reply: ParsedEmail,
        candidate_tasks: list[ExtractedTask],
    ) -> list[tuple[str, TaskStatus]]:
        """
        LLM を使って返信メールから状態変化を検出。
        ルールベースで判定できなかった場合のフォールバック。
        """
        task_list = "\n".join(
            f"  - [{t.task_id}] {t.title} (担当: {t.assignee}, 状態: {t.status.value})"
            for t in candidate_tasks
        )

        prompt = f"""以下の返信メールと、関連する可能性のあるタスクリストを分析してください。

【返信メール】
件名: {reply.subject}
送信者: {reply.sender_name} <{reply.sender_address}>
---
{reply.body_text[:600]}
---

【関連タスク候補】
{task_list}

このメールによって状態が変わるタスクを特定し、以下の形式でJSONを出力してください。
状態変化がない場合は空のリストを返してください。

```json
{{
  "updates": [
    {{
      "task_id": "TK-0001",
      "new_status": "done",
      "reason": "「完了しました」の記述から判断"
    }}
  ]
}}
```

statusの選択肢: "pending", "in_progress", "waiting", "done", "cancelled"
"""

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=512,
                extra_body={"guided_json": {
                    "type": "object",
                    "properties": {
                        "updates": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "task_id": {"type": "string"},
                                    "new_status": {
                                        "type": "string",
                                        "enum": ["pending", "in_progress", "waiting", "done", "cancelled"]
                                    },
                                    "reason": {"type": "string"},
                                },
                                "required": ["task_id", "new_status"],
                            }
                        }
                    },
                    "required": ["updates"]
                }}
            )
            content = response.choices[0].message.content or "{}"
            content = re.sub(r"```json\s*|```", "", content).strip()
            data = json.loads(content)

            results: list[tuple[str, TaskStatus]] = []
            for update in data.get("updates", []):
                try:
                    status = TaskStatus(update["new_status"])
                    results.append((update["task_id"], status))
                except (ValueError, KeyError):
                    continue
            return results

        except Exception as e:
            print(f"[StatusTracker] LLM検出エラー: {e}")
            return []

    # ── 永続化 ──

    def _save(self) -> None:
        """タスクを JSON ファイルに保存"""
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "tasks": [t.model_dump(mode="json") for t in self._tasks.values()],
            "change_log": self._change_log,
            "saved_at": datetime.now().isoformat(),
        }
        with open(self.storage_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)

    def _load(self) -> None:
        """JSON ファイルからタスクを読み込み"""
        try:
            with open(self.storage_path, encoding="utf-8") as f:
                data = json.load(f)
            for td in data.get("tasks", []):
                task = ExtractedTask(**td)
                self._tasks[task.task_id] = task
            self._change_log = data.get("change_log", [])
            print(f"[StatusTracker] {len(self._tasks)}件のタスクを読み込みました")
        except Exception as e:
            print(f"[StatusTracker] 読み込み失敗: {e}")

    def _log_change(
        self,
        task_id: str,
        action: str,
        old_val: Any,
        new_val: Any,
        reason: str = "",
    ) -> None:
        self._change_log.append({
            "task_id": task_id,
            "action": action,
            "old": str(old_val) if old_val is not None else None,
            "new": str(new_val),
            "reason": reason,
            "at": datetime.now().isoformat(),
        })

    def export_csv(self, output_path: str) -> None:
        """タスクを CSV にエクスポート（Excel で開ける）"""
        import csv
        with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow([
                "タスクID", "タイトル", "担当者", "依頼者",
                "期限", "優先度", "ステータス", "タグ", "説明",
            ])
            for task in self._tasks.values():
                writer.writerow([
                    task.task_id,
                    task.title,
                    task.assignee,
                    task.requester,
                    task.deadline_text,
                    task.priority.value,
                    task.status.value,
                    ", ".join(task.tags),
                    task.description,
                ])
        print(f"CSVエクスポート完了: {output_path}")

    def get_change_log(self) -> list[dict]:
        """変更ログを返す"""
        return list(self._change_log)
