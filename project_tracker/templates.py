"""
project_tracker/templates.py
よくある手続きのテンプレートから Project を自動生成する
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from .models import Phase, Priority, Project, Task, TaskStatus


# ─── テンプレート定義 ─────────────────────────────────────────────────────────

def _days(n: int) -> date:
    return date.today() + timedelta(days=n)


TEMPLATES: dict[str, dict] = {

    "産業廃棄物処理": {
        "description": "産業廃棄物の適正処理・委託手続きテンプレート",
        "tags": ["廃棄物", "法務", "環境"],
        "phases": [
            {
                "name": "事前調査・分類",
                "order": 1,
                "tasks": [
                    {"title": "廃棄物の種類・数量確認",      "assignee": "環境管理担当",  "days": 7,  "priority": Priority.HIGH,     "description": "マニフェスト記載のため廃棄物種類・数量を確認"},
                    {"title": "法定区分（産廃/特別管理産廃）判定", "assignee": "法務担当",  "days": 7,  "priority": Priority.HIGH,     "description": "廃棄物処理法に基づく区分確認"},
                    {"title": "処理業者リストアップ",          "assignee": "購買担当",      "days": 10, "priority": Priority.MEDIUM,   "description": "許可証保有業者を3社以上選定"},
                ],
            },
            {
                "name": "業者選定・契約",
                "order": 2,
                "tasks": [
                    {"title": "業者許可証の確認・コピー取得",  "assignee": "法務担当",      "days": 14, "priority": Priority.HIGH,     "description": "都道府県知事許可証の有効期限確認"},
                    {"title": "処理委託契約書の締結",          "assignee": "法務担当",      "days": 21, "priority": Priority.CRITICAL, "description": "二者間契約書（書面）、収集運搬・処分それぞれ"},
                    {"title": "見積もり取得・稟議申請",        "assignee": "購買担当",      "days": 21, "priority": Priority.HIGH,     "description": "3社相見積もり必須、稟議書作成"},
                    {"title": "財務部への支払申請",            "assignee": "購買担当",      "days": 25, "priority": Priority.MEDIUM,   "description": "稟議承認後に支払い申請書提出"},
                ],
            },
            {
                "name": "廃棄実施",
                "order": 3,
                "tasks": [
                    {"title": "マニフェスト（産業廃棄物管理票）発行", "assignee": "環境管理担当", "days": 28, "priority": Priority.CRITICAL, "description": "紙またはE-manifest、A票〜E票の管理"},
                    {"title": "廃棄物の引き渡し立会い",        "assignee": "環境管理担当",  "days": 30, "priority": Priority.HIGH,     "description": "積み込み時に種類・数量を確認"},
                    {"title": "収集運搬確認（B2票返送確認）",  "assignee": "環境管理担当",  "days": 37, "priority": Priority.HIGH,     "description": "運搬終了後10日以内"},
                ],
            },
            {
                "name": "完了報告",
                "order": 4,
                "tasks": [
                    {"title": "処分終了確認（D票返送確認）",   "assignee": "環境管理担当",  "days": 60, "priority": Priority.HIGH,     "description": "最終処分後90日以内、未着の場合は業者に催促"},
                    {"title": "マニフェスト5年間保管設定",      "assignee": "総務担当",      "days": 65, "priority": Priority.MEDIUM,   "description": "法定保存期間5年、保管場所記録"},
                    {"title": "廃棄物処理実績報告書作成",       "assignee": "環境管理担当",  "days": 70, "priority": Priority.MEDIUM,   "description": "社内報告・必要に応じて行政報告"},
                ],
            },
        ],
    },

    "資産廃棄・除却": {
        "description": "固定資産の廃棄・除却手続きテンプレート",
        "tags": ["資産管理", "経理", "総務"],
        "phases": [
            {
                "name": "資産確認",
                "order": 1,
                "tasks": [
                    {"title": "廃棄対象資産の固定資産台帳確認",  "assignee": "経理担当",  "days": 5,  "priority": Priority.HIGH},
                    {"title": "資産の現物確認・写真撮影",         "assignee": "総務担当",  "days": 7,  "priority": Priority.MEDIUM},
                    {"title": "残存価格・減価償却計算",           "assignee": "経理担当",  "days": 10, "priority": Priority.HIGH},
                ],
            },
            {
                "name": "申請・承認",
                "order": 2,
                "tasks": [
                    {"title": "資産除却申請書作成・提出",  "assignee": "総務担当",  "days": 14, "priority": Priority.HIGH},
                    {"title": "上長承認取得",              "assignee": "総務担当",  "days": 18, "priority": Priority.CRITICAL},
                    {"title": "経理部への除却処理依頼",    "assignee": "経理担当",  "days": 20, "priority": Priority.HIGH},
                ],
            },
            {
                "name": "廃棄実施・記録",
                "order": 3,
                "tasks": [
                    {"title": "廃棄実施（産廃テンプレートと連携）", "assignee": "総務担当",  "days": 30, "priority": Priority.HIGH},
                    {"title": "固定資産台帳から除却処理",           "assignee": "経理担当",  "days": 35, "priority": Priority.HIGH},
                    {"title": "除却証明書のファイリング",           "assignee": "総務担当",  "days": 40, "priority": Priority.MEDIUM},
                ],
            },
        ],
    },

    "購買・発注": {
        "description": "物品購買・発注の標準手続きテンプレート",
        "tags": ["購買", "経理"],
        "phases": [
            {
                "name": "要件定義・見積",
                "order": 1,
                "tasks": [
                    {"title": "購買要求書作成",           "assignee": "依頼部門担当", "days": 3,  "priority": Priority.MEDIUM},
                    {"title": "仕様書・要件定義書作成",   "assignee": "依頼部門担当", "days": 5,  "priority": Priority.HIGH},
                    {"title": "相見積もり取得（3社）",    "assignee": "購買担当",     "days": 10, "priority": Priority.HIGH},
                ],
            },
            {
                "name": "稟議・承認",
                "order": 2,
                "tasks": [
                    {"title": "稟議書作成・回付",         "assignee": "購買担当",  "days": 14, "priority": Priority.HIGH},
                    {"title": "決裁権者の承認取得",       "assignee": "購買担当",  "days": 17, "priority": Priority.CRITICAL},
                ],
            },
            {
                "name": "発注・検収",
                "order": 3,
                "tasks": [
                    {"title": "発注書発行・送付",         "assignee": "購買担当",  "days": 18, "priority": Priority.HIGH},
                    {"title": "納品・検収確認",           "assignee": "依頼部門担当", "days": 30, "priority": Priority.HIGH},
                    {"title": "請求書照合・支払依頼",     "assignee": "経理担当",  "days": 35, "priority": Priority.HIGH},
                ],
            },
        ],
    },

}


# ─── テンプレートからプロジェクト生成 ────────────────────────────────────────

def create_from_template(
    template_name: str,
    project_name: Optional[str] = None,
    assignee_map: Optional[dict[str, str]] = None,
    start_offset_days: int = 0,
) -> Project:
    """
    テンプレートからプロジェクトを生成する。

    Args:
        template_name: templates.py の TEMPLATES キー
        project_name:  プロジェクト名（省略時はテンプレート名）
        assignee_map:  担当者名のマッピング {"環境管理担当": "山田太郎", ...}
        start_offset_days: 期限を何日後ろ倒しにするか
    """
    if template_name not in TEMPLATES:
        raise ValueError(f"テンプレート '{template_name}' が見つかりません。"
                         f"利用可能: {list(TEMPLATES.keys())}")

    tmpl = TEMPLATES[template_name]
    proj = Project(
        name        = project_name or template_name,
        description = tmpl["description"],
        tags        = list(tmpl["tags"]),
    )

    for ph_def in tmpl["phases"]:
        phase = Phase(name=ph_def["name"], order=ph_def["order"])
        for t_def in ph_def["tasks"]:
            assignee = t_def["assignee"]
            if assignee_map and assignee in assignee_map:
                assignee = assignee_map[assignee]

            due = _days(t_def["days"] + start_offset_days)
            task = Task(
                title       = t_def["title"],
                assignee    = assignee,
                due_date    = due,
                priority    = t_def.get("priority", Priority.MEDIUM),
                description = t_def.get("description", ""),
            )
            phase.tasks.append(task)
        proj.phases.append(phase)

    # 依存関係の自動設定（前フェーズのタスクは後フェーズに先行する）
    # 簡易版: フェーズ内での順序依存のみ設定
    for phase in proj.phases:
        for i, task in enumerate(phase.tasks[1:], 1):
            prev_task = phase.tasks[i - 1]
            task.depends_on.append(prev_task.task_id)

    return proj


def list_templates() -> None:
    print("\n利用可能なテンプレート:")
    for name, tmpl in TEMPLATES.items():
        phases = len(tmpl["phases"])
        total_tasks = sum(len(p["tasks"]) for p in tmpl["phases"])
        print(f"  - {name}: {tmpl['description']} ({phases}フェーズ / {total_tasks}タスク)")
    print()
