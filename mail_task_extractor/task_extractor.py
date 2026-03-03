"""
task_extractor.py - LLMによるタスク抽出モジュール

vLLM OpenAI互換 API を使用。
モデル: Llama 3.1 / Gemma 2 / Mistral（中国系モデル使用不可）

機能:
  - 1通のメールからタスクを JSON 構造で抽出
  - Few-shot 例付きプロンプト（日本語ビジネスメール向け）
  - vLLM の guided decoding (JSON Schema) で出力を強制
  - 返信メールからステータス更新を検出
  - タスク ID の採番・管理

使い方:
    extractor = TaskExtractor(base_url="http://localhost:8000/v1", model="meta-llama/Llama-3.1-8B-Instruct")
    result = await extractor.extract(email)
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta
from typing import Any

from openai import AsyncOpenAI

from .models import (
    ExtractedTask,
    ParsedEmail,
    Priority,
    TaskDependency,
    TaskExtractionResult,
    TaskStatus,
)


# ─── プロンプト定義 ───────────────────────────────────────────────────────────

SYSTEM_PROMPT = """あなたは日本語のビジネスメールからタスク情報を抽出する専門家です。

## 抽出ルール

1. **タスク**: 誰かが誰かに何かをするよう求めている依頼・指示・確認事項
2. **担当者 (assignee)**: タスクを実行すべき人。明示されていない場合は「受信者」
3. **依頼者 (requester)**: タスクを依頼した人。通常はメール送信者
4. **期限 (deadline)**: 明示的な日付、「来週中」「今月末」等のあいまい表現も抽出
5. **優先度**: 「至急」「緊急」→ high, 「できれば」→ low, それ以外 → medium
6. **依存関係**: 「○○が完了してから」「○○の承認後」等の条件関係
7. **ステータス更新**: 「完了しました」「承認します」「対応不可」等の完了・却下シグナル

## 抽出しないもの
- 挨拶・定型句のみの内容
- 過去の出来事の報告（「～しました」の経緯説明）
- FYI（情報共有のみ、対応不要）

## 出力形式
必ず以下のJSONスキーマに従ってください。"""

# Few-shot 例（日本語ビジネスメール向け）
FEW_SHOT_EXAMPLES = [
    {
        "email": """件名: 産業廃棄物処理申請書類について
送信者: 環境科 山田 太郎
受信者: 施設管理課 鈴木 一郎

鈴木様

お世話になっております。環境科の山田です。

来月の廃棄物処理に向けて、以下の書類準備をお願いします。

1. 廃棄物処理委託契約書（3月15日までに押印して返送）
2. マニフェスト（A票〜E票）の準備（処理当日まで）
3. 排出事業者証明書の更新（有効期限が3月末のため至急）

排出事業者証明書は市役所への申請が必要ですので、できるだけ早くご対応ください。
なお、マニフェストの準備は契約書の締結後でないとできません。

よろしくお願いいたします。""",
        "output": {
            "tasks": [
                {
                    "title": "廃棄物処理委託契約書への押印・返送",
                    "description": "廃棄物処理委託契約書に押印して環境科山田へ返送する",
                    "assignee": "鈴木 一郎",
                    "requester": "山田 太郎",
                    "deadline_text": "3月15日まで",
                    "priority": "medium",
                    "tags": ["廃棄物処理", "書類", "押印"]
                },
                {
                    "title": "マニフェスト（A票〜E票）の準備",
                    "description": "産業廃棄物管理票（マニフェスト）A票からE票を準備する",
                    "assignee": "鈴木 一郎",
                    "requester": "山田 太郎",
                    "deadline_text": "処理当日まで",
                    "priority": "medium",
                    "dependencies": ["廃棄物処理委託契約書への押印・返送"],
                    "tags": ["廃棄物処理", "マニフェスト"]
                },
                {
                    "title": "排出事業者証明書の更新申請",
                    "description": "有効期限が3月末のため市役所に排出事業者証明書の更新を申請する",
                    "assignee": "鈴木 一郎",
                    "requester": "山田 太郎",
                    "deadline_text": "3月末（至急）",
                    "priority": "high",
                    "tags": ["廃棄物処理", "行政手続き", "証明書"]
                }
            ],
            "completed_task_ids": [],
            "summary": "来月の産業廃棄物処理に向けて、施設管理課に書類準備3件を依頼。排出事業者証明書の更新が至急。"
        }
    },
    {
        "email": """件名: Re: 予算申請について
送信者: 財務部 佐藤 部長
受信者: 環境科 山田 太郎

山田さん

承認しました。
予算の執行をお進めください。

なお、領収書は月末までに経理に提出をお願いします。

佐藤""",
        "output": {
            "tasks": [
                {
                    "title": "領収書の経理提出",
                    "description": "予算執行に関する領収書を月末までに経理部に提出する",
                    "assignee": "山田 太郎",
                    "requester": "佐藤 部長",
                    "deadline_text": "月末まで",
                    "priority": "medium",
                    "tags": ["経費精算", "領収書"]
                }
            ],
            "completed_task_ids": ["予算申請"],
            "summary": "財務部長が予算を承認。山田は予算執行を進め、領収書を月末までに経理へ提出。"
        }
    }
]

def build_few_shot_prompt(examples: list[dict]) -> str:
    """Few-shot 例をプロンプト文字列に変換"""
    parts = ["\n## 抽出例\n"]
    for i, ex in enumerate(examples, 1):
        parts.append(f"### 例{i}")
        parts.append(f"```\n{ex['email']}\n```")
        parts.append("抽出結果:")
        parts.append(f"```json\n{json.dumps(ex['output'], ensure_ascii=False, indent=2)}\n```\n")
    return "\n".join(parts)


# ─── JSON スキーマ定義 ────────────────────────────────────────────────────────

TASK_EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "tasks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title":        {"type": "string"},
                    "description":  {"type": "string"},
                    "assignee":     {"type": "string"},
                    "assignee_email": {"type": ["string", "null"]},
                    "requester":    {"type": "string"},
                    "requester_email": {"type": ["string", "null"]},
                    "deadline_text": {"type": "string"},
                    "priority":     {"type": "string", "enum": ["high", "medium", "low"]},
                    "dependencies": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "このタスクが依存する他タスクのタイトルリスト"
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"}
                    }
                },
                "required": ["title", "description", "assignee", "requester", "priority"]
            }
        },
        "completed_task_ids": {
            "type": "array",
            "items": {"type": "string"},
            "description": "このメールで完了が確認されたタスクのタイトルまたはID"
        },
        "new_issues": {
            "type": "array",
            "items": {"type": "string"},
            "description": "メールで新たに発覚した問題・リスク"
        },
        "summary": {"type": "string"}
    },
    "required": ["tasks", "completed_task_ids", "summary"]
}


# ─── タスク抽出器 ─────────────────────────────────────────────────────────────

class TaskExtractor:
    """
    vLLM OpenAI互換 API を使ったタスク抽出。

    Args:
        base_url: vLLM サーバーの URL (例: "http://localhost:8000/v1")
        model: モデル名 (例: "meta-llama/Llama-3.1-8B-Instruct")
        use_guided_decoding: True なら vLLM の JSON Schema 強制を使用
        temperature: 抽出は再現性重視で低め (0.1)
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8000/v1",
        model: str = "meta-llama/Llama-3.1-8B-Instruct",
        api_key: str = "dummy",  # vLLM は任意の文字列でOK
        use_guided_decoding: bool = True,
        temperature: float = 0.1,
        max_tokens: int = 2048,
    ):
        self.client = AsyncOpenAI(base_url=base_url, api_key=api_key)
        self.model = model
        self.use_guided_decoding = use_guided_decoding
        self.temperature = temperature
        self.max_tokens = max_tokens

        # システムプロンプト + Few-shot を結合
        self._system = (
            SYSTEM_PROMPT
            + build_few_shot_prompt(FEW_SHOT_EXAMPLES)
        )

        # タスク ID カウンター
        self._task_counter = 0

    def _next_task_id(self) -> str:
        self._task_counter += 1
        return f"TK-{self._task_counter:04d}"

    def _build_user_message(self, em: ParsedEmail) -> str:
        """メール情報をプロンプト用テキストに変換"""
        lines = [
            f"件名: {em.subject}",
            f"送信者: {em.sender_name} <{em.sender_address}>",
            f"受信者: {', '.join(em.recipients)}",
        ]
        if em.cc:
            lines.append(f"CC: {', '.join(em.cc)}")
        lines.append(f"日時: {em.date.strftime('%Y年%m月%d日 %H:%M')}")
        if em.attachments:
            lines.append(
                f"添付: {', '.join(a.filename for a in em.attachments)}"
            )
        lines.append("")
        lines.append(em.body_text)

        return "\n".join(lines)

    async def extract(self, em: ParsedEmail) -> TaskExtractionResult:
        """
        1通のメールからタスクを抽出する。
        """
        user_msg = self._build_user_message(em)

        # vLLM guided decoding: JSON Schema を extra_body で渡す
        extra_body: dict[str, Any] = {}
        if self.use_guided_decoding:
            extra_body["guided_json"] = TASK_EXTRACTION_SCHEMA

        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": self._system},
                {
                    "role": "user",
                    "content": (
                        f"以下のメールからタスクを抽出してください。\n\n"
                        f"---\n{user_msg}\n---\n\n"
                        "JSONで出力してください。"
                    ),
                },
            ],
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            extra_body=extra_body if extra_body else None,
        )

        raw_content = response.choices[0].message.content or "{}"

        # JSON パース（guided decoding なしの場合はフォールバック処理）
        extracted = self._parse_llm_output(raw_content)

        # ParsedEmail の情報で担当者メールアドレスを補完
        all_people = self._build_people_map(em)

        # ExtractedTask オブジェクトに変換
        tasks = []
        for t in extracted.get("tasks", []):
            task = self._dict_to_task(t, em, all_people)
            tasks.append(task)

        return TaskExtractionResult(
            tasks=tasks,
            completed_task_ids=extracted.get("completed_task_ids", []),
            new_issues=extracted.get("new_issues", []),
            summary=extracted.get("summary", ""),
        )

    def _parse_llm_output(self, content: str) -> dict:
        """LLM出力をJSON辞書に変換。コードブロック等を除去してパース。"""
        # ```json ... ``` ブロックを除去
        content = re.sub(r"```json\s*", "", content)
        content = re.sub(r"```\s*", "", content)
        content = content.strip()

        try:
            return json.loads(content)
        except json.JSONDecodeError:
            # JSONが途中で切れている場合は再試行なしでデフォルト返却
            print(f"[TaskExtractor] JSONパース失敗: {content[:200]}")
            return {"tasks": [], "completed_task_ids": [], "summary": ""}

    def _build_people_map(self, em: ParsedEmail) -> dict[str, str]:
        """名前 → メールアドレスのマッピングを構築"""
        mapping: dict[str, str] = {}
        if em.sender_name:
            mapping[em.sender_name] = em.sender_address
        # To/CC は名前情報が取れないことが多いのでアドレスのみ
        return mapping

    def _dict_to_task(
        self,
        d: dict,
        em: ParsedEmail,
        people_map: dict[str, str],
    ) -> ExtractedTask:
        """辞書 → ExtractedTask オブジェクト"""
        # 担当者のメールアドレス補完
        assignee = d.get("assignee", "")
        assignee_email = d.get("assignee_email") or people_map.get(assignee)

        requester = d.get("requester", em.sender_name or em.sender_address)
        requester_email = d.get("requester_email") or people_map.get(requester)

        # 期限テキストから datetime の推定
        deadline_text = d.get("deadline_text", "")
        deadline = self._parse_deadline(deadline_text, em.date)

        # 優先度
        try:
            priority = Priority(d.get("priority", "medium"))
        except ValueError:
            priority = Priority.MEDIUM

        # 依存関係（タイトル文字列リストから構築）
        deps: list[TaskDependency] = []
        for dep_title in d.get("dependencies", []):
            deps.append(
                TaskDependency(
                    depends_on_task_id=dep_title,  # 後でタスクIDに解決
                    description=dep_title,
                )
            )

        return ExtractedTask(
            task_id=self._next_task_id(),
            title=d.get("title", "タスク"),
            description=d.get("description", ""),
            assignee=assignee,
            assignee_email=assignee_email,
            requester=requester,
            requester_email=requester_email,
            deadline=deadline,
            deadline_text=deadline_text,
            priority=priority,
            status=TaskStatus.PENDING,
            dependencies=deps,
            tags=d.get("tags", []),
            source_email_id=em.message_id,
            source_thread_id=em.thread_id,
        )

    def _parse_deadline(
        self, text: str, reference_date: datetime
    ) -> datetime | None:
        """
        期限テキストを datetime に変換（ベストエフォート）。
        例: "3月15日まで" → datetime(year, 3, 15)
            "来週金曜まで" → reference_date から来週金曜日
            "月末まで" → その月の末日
        """
        if not text:
            return None

        # 具体的な日付: "3月15日", "2024年3月15日"
        m = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", text)
        if m:
            try:
                return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                                tzinfo=reference_date.tzinfo)
            except ValueError:
                pass

        m = re.search(r"(\d{1,2})月(\d{1,2})日", text)
        if m:
            try:
                year = reference_date.year
                month, day = int(m.group(1)), int(m.group(2))
                dt = datetime(year, month, day, tzinfo=reference_date.tzinfo)
                # 過去の日付なら来年として解釈
                if dt < reference_date:
                    dt = dt.replace(year=year + 1)
                return dt
            except ValueError:
                pass

        # 相対日付
        if "今日" in text or "本日" in text:
            return reference_date.replace(hour=17, minute=0, second=0)
        if "明日" in text:
            return (reference_date + timedelta(days=1)).replace(hour=17, minute=0, second=0)
        if "今週中" in text or "今週末" in text:
            # 今週の金曜日
            days_until_friday = (4 - reference_date.weekday()) % 7
            return (reference_date + timedelta(days=days_until_friday)).replace(
                hour=17, minute=0, second=0
            )
        if "来週" in text:
            days_until_friday = (4 - reference_date.weekday()) % 7 + 7
            return (reference_date + timedelta(days=days_until_friday)).replace(
                hour=17, minute=0, second=0
            )
        if "月末" in text or "今月末" in text:
            import calendar
            last_day = calendar.monthrange(reference_date.year, reference_date.month)[1]
            return reference_date.replace(day=last_day, hour=17, minute=0, second=0)
        if "来月末" in text:
            import calendar
            next_month = reference_date.month % 12 + 1
            year = reference_date.year if next_month > 1 else reference_date.year + 1
            last_day = calendar.monthrange(year, next_month)[1]
            return datetime(year, next_month, last_day, 17, 0, 0,
                           tzinfo=reference_date.tzinfo)

        # 数日以内: "3日以内", "1週間以内"
        m = re.search(r"(\d+)日以内", text)
        if m:
            return reference_date + timedelta(days=int(m.group(1)))
        m = re.search(r"(\d+)週間以内", text)
        if m:
            return reference_date + timedelta(weeks=int(m.group(1)))

        return None


# ─── バッチ処理ヘルパー ──────────────────────────────────────────────────────

class BatchTaskExtractor:
    """
    複数メールのタスクを並列抽出し、タスク ID の依存関係を解決する。
    """

    def __init__(self, extractor: TaskExtractor):
        self.extractor = extractor

    async def extract_all(
        self, emails: list[ParsedEmail], max_concurrent: int = 5
    ) -> list[TaskExtractionResult]:
        """
        最大 max_concurrent 件を並列処理。
        """
        import asyncio

        semaphore = asyncio.Semaphore(max_concurrent)

        async def extract_one(em: ParsedEmail) -> TaskExtractionResult:
            async with semaphore:
                return await self.extractor.extract(em)

        results = await asyncio.gather(
            *[extract_one(em) for em in emails],
            return_exceptions=True,
        )

        # エラーをログに出力して除外
        valid: list[TaskExtractionResult] = []
        for i, r in enumerate(results):
            if isinstance(r, Exception):
                print(f"[BatchExtractor] エラー (email {i}): {r}")
            else:
                valid.append(r)

        return valid

    def resolve_dependencies(
        self, results: list[TaskExtractionResult]
    ) -> list[ExtractedTask]:
        """
        全タスクを収集し、依存関係のタイトル文字列をタスクIDに解決する。
        """
        all_tasks: list[ExtractedTask] = []
        for r in results:
            all_tasks.extend(r.tasks)

        # タイトル → タスク ID のマッピング
        title_to_id: dict[str, str] = {t.title: t.task_id for t in all_tasks}

        for task in all_tasks:
            resolved: list[TaskDependency] = []
            for dep in task.dependencies:
                task_id = title_to_id.get(dep.description, dep.depends_on_task_id)
                resolved.append(
                    TaskDependency(
                        depends_on_task_id=task_id,
                        description=dep.description,
                    )
                )
            task.dependencies = resolved

        return all_tasks
