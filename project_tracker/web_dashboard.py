"""
project_tracker/web_dashboard.py
Streamlit による Web ダッシュボード
起動: streamlit run project_tracker/web_dashboard.py -- --project data/projects/sample.json
"""

from __future__ import annotations

import sys
from pathlib import Path

# Streamlit の import を試みる
try:
    import streamlit as st
    STREAMLIT_AVAILABLE = True
except ImportError:
    STREAMLIT_AVAILABLE = False
    print("Streamlit が未インストールです。`uv pip install streamlit` を実行してください。")
    sys.exit(1)

import json
from datetime import date, datetime

from .models import Priority, Project, Task, TaskStatus
from .notification import check_alerts, generate_summary_text
from .templates import TEMPLATES, create_from_template


# ─── ページ設定 ───────────────────────────────────────────────────────────────

st.set_page_config(
    page_title  = "プロジェクト進捗トラッカー",
    page_icon   = "📊",
    layout      = "wide",
    initial_sidebar_state = "expanded",
)


# ─── CSS ─────────────────────────────────────────────────────────────────────

st.markdown("""
<style>
.status-todo        { background:#444; color:#fff; padding:2px 8px; border-radius:4px; }
.status-in-progress { background:#0077cc; color:#fff; padding:2px 8px; border-radius:4px; }
.status-done        { background:#00aa55; color:#fff; padding:2px 8px; border-radius:4px; }
.status-on-hold     { background:#cc8800; color:#fff; padding:2px 8px; border-radius:4px; }
.status-overdue     { background:#cc2200; color:#fff; padding:2px 8px; border-radius:4px; }
.metric-card        { background:#1e1e2e; padding:16px; border-radius:8px; text-align:center; }
</style>
""", unsafe_allow_html=True)


# ─── セッションステート初期化 ─────────────────────────────────────────────────

def _init_state():
    if "project" not in st.session_state:
        st.session_state.project = None
    if "project_path" not in st.session_state:
        st.session_state.project_path = None


_init_state()


# ─── ヘルパー ─────────────────────────────────────────────────────────────────

STATUS_BADGE = {
    TaskStatus.TODO:        '<span class="status-todo">未着手</span>',
    TaskStatus.IN_PROGRESS: '<span class="status-in-progress">進行中</span>',
    TaskStatus.DONE:        '<span class="status-done">完了</span>',
    TaskStatus.ON_HOLD:     '<span class="status-on-hold">保留</span>',
    TaskStatus.OVERDUE:     '<span class="status-overdue">期限超過</span>',
}

STATUS_EMOJI = {
    TaskStatus.TODO:        "⬜",
    TaskStatus.IN_PROGRESS: "🔵",
    TaskStatus.DONE:        "✅",
    TaskStatus.ON_HOLD:     "🟡",
    TaskStatus.OVERDUE:     "🔴",
}


def _status_badge(status: TaskStatus) -> str:
    return STATUS_BADGE.get(status, status.value)


# ─── サイドバー ───────────────────────────────────────────────────────────────

def render_sidebar() -> Project | None:
    with st.sidebar:
        st.title("📊 プロジェクトトラッカー")
        st.divider()

        mode = st.radio("モード", ["ファイルを開く", "テンプレートから作成", "サンプル表示"])

        if mode == "ファイルを開く":
            path_str = st.text_input("JSONファイルパス", placeholder="/path/to/project.json")
            if st.button("読み込む") and path_str:
                try:
                    proj = Project.load(Path(path_str))
                    st.session_state.project = proj
                    st.session_state.project_path = path_str
                    st.success(f"読み込み完了: {proj.name}")
                except Exception as e:
                    st.error(f"エラー: {e}")

        elif mode == "テンプレートから作成":
            tmpl_name = st.selectbox("テンプレート", list(TEMPLATES.keys()))
            proj_name = st.text_input("プロジェクト名", value=tmpl_name)
            save_path = st.text_input("保存先", value=f"data/projects/{proj_name}.json")
            if st.button("作成"):
                try:
                    proj = create_from_template(tmpl_name, proj_name)
                    p = Path(save_path)
                    proj.save(p)
                    st.session_state.project = proj
                    st.session_state.project_path = save_path
                    st.success(f"作成・保存完了: {p}")
                except Exception as e:
                    st.error(f"エラー: {e}")

        elif mode == "サンプル表示":
            if st.button("産廃サンプルを表示"):
                proj = _create_sample_project()
                st.session_state.project = proj
                st.session_state.project_path = None

        st.divider()
        if st.session_state.project:
            proj = st.session_state.project
            st.caption(f"プロジェクト: **{proj.name}**")
            st.caption(f"完了率: **{proj.completion_rate()*100:.1f}%**")
            st.caption(f"タスク数: {len(proj.all_tasks())}件")

            if st.session_state.project_path and st.button("保存"):
                proj.save(Path(st.session_state.project_path))
                st.success("保存しました")

    return st.session_state.project


# ─── サマリーカード ────────────────────────────────────────────────────────────

def render_summary(proj: Project):
    proj.refresh_all_statuses()
    tasks = proj.all_tasks()
    counts = {s: 0 for s in TaskStatus}
    for t in tasks:
        counts[t.status] += 1

    rate     = proj.completion_rate()
    overdue  = len(proj.overdue_tasks())
    stale    = len(proj.stale_tasks(7))
    blocking = sum(1 for t in tasks if t.is_blocked(proj.all_tasks_dict()))

    st.subheader("サマリー")
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("完了率",    f"{rate*100:.1f}%")
    c2.metric("全タスク",  len(tasks))
    c3.metric("完了",      counts[TaskStatus.DONE])
    c4.metric("期限超過",  overdue, delta=f"-{overdue}" if overdue else None, delta_color="inverse")
    c5.metric("放置(7日)", stale,   delta=f"-{stale}"  if stale   else None, delta_color="inverse")

    # 進捗バー
    st.progress(rate, text=f"全体進捗 {rate*100:.1f}%")

    # フェーズ別進捗
    cols = st.columns(len(proj.phases))
    for col, phase in zip(cols, proj.phases):
        ph_rate = phase.completion_rate()
        col.metric(phase.name, f"{ph_rate*100:.0f}%")
        col.progress(ph_rate)


# ─── アラートバナー ────────────────────────────────────────────────────────────

def render_alerts(proj: Project):
    alerts = check_alerts(proj)
    criticals = [a for a in alerts if a["level"] == "critical"]
    warnings  = [a for a in alerts if a["level"] == "warning"]

    if criticals:
        with st.expander(f"🔴 緊急アラート ({len(criticals)}件)", expanded=True):
            for a in criticals:
                st.error(a["message"])

    if warnings:
        with st.expander(f"🟡 警告 ({len(warnings)}件)", expanded=False):
            for a in warnings:
                st.warning(a["message"])

    if not alerts:
        st.success("アラートはありません。")


# ─── カンバンボード ────────────────────────────────────────────────────────────

def render_kanban(proj: Project):
    st.subheader("タスクボード（カンバン）")
    proj.refresh_all_statuses()

    columns = [
        ("未着手",   [TaskStatus.TODO]),
        ("進行中",   [TaskStatus.IN_PROGRESS]),
        ("保留",     [TaskStatus.ON_HOLD]),
        ("完了",     [TaskStatus.DONE]),
        ("期限超過", [TaskStatus.OVERDUE]),
    ]

    col_elems = st.columns(len(columns))
    all_dict  = proj.all_tasks_dict()

    for col_elem, (label, statuses) in zip(col_elems, columns):
        tasks = [t for t in proj.all_tasks() if t.status in statuses]
        col_elem.markdown(f"**{STATUS_EMOJI[statuses[0]]} {label} ({len(tasks)})**")
        col_elem.divider()

        for task in tasks:
            blocked = task.is_blocked(all_dict)
            due_str = ""
            if task.due_date:
                days = task.days_until_due()
                if days is not None and days < 0:
                    due_str = f"🔴 {task.due_date} ({abs(days)}日超過)"
                elif days is not None and days <= 3:
                    due_str = f"🟡 {task.due_date} (あと{days}日)"
                else:
                    due_str = f"📅 {task.due_date}"

            with col_elem.container(border=True):
                col_elem.markdown(f"**{task.title}**")
                col_elem.caption(f"👤 {task.assignee}")
                if due_str:
                    col_elem.caption(due_str)
                if blocked:
                    col_elem.caption("⛔ ブロック中")
                if task.related_emails:
                    col_elem.caption(f"📧 {len(task.related_emails)}通")


# ─── タスクテーブル ────────────────────────────────────────────────────────────

def render_task_table(proj: Project):
    st.subheader("タスク一覧")
    proj.refresh_all_statuses()
    all_dict = proj.all_tasks_dict()

    # フィルタ
    c1, c2, c3 = st.columns(3)
    filter_status   = c1.multiselect("ステータス", [s.value for s in TaskStatus])
    filter_assignee = c2.multiselect("担当者",     sorted({t.assignee for t in proj.all_tasks()}))
    filter_phase    = c3.multiselect("フェーズ",   [p.name for p in proj.phases])

    rows = []
    for phase in proj.phases:
        if filter_phase and phase.name not in filter_phase:
            continue
        for task in phase.tasks:
            task.refresh_status()
            if filter_status and task.status.value not in filter_status:
                continue
            if filter_assignee and task.assignee not in filter_assignee:
                continue

            days = task.days_until_due()
            due_str = ""
            if task.due_date:
                if days is not None and days < 0:
                    due_str = f"{task.due_date} (超過{abs(days)}日)"
                else:
                    due_str = str(task.due_date)

            rows.append({
                "ID":       task.task_id,
                "フェーズ": phase.name,
                "タスク":   task.title,
                "担当者":   task.assignee,
                "期限":     due_str,
                "優先度":   task.priority.value,
                "ステータス": task.status.value,
                "ブロック": "⛔" if task.is_blocked(all_dict) else "",
            })

    if rows:
        import pandas as pd
        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.info("該当タスクなし")


# ─── 次にやるべきこと ─────────────────────────────────────────────────────────

def render_next_actions(proj: Project):
    st.subheader("次にやるべきこと")
    proj.refresh_all_statuses()
    actions = proj.next_actions()[:5]

    if not actions:
        st.info("実行可能なタスクはありません（全完了またはブロック中）")
        return

    for i, t in enumerate(actions, 1):
        with st.container(border=True):
            col1, col2, col3 = st.columns([3, 1, 2])
            col1.markdown(f"**#{i} {t.title}**")
            col2.markdown(f"👤 {t.assignee}")
            days = t.days_until_due()
            if days is not None:
                color = "red" if days < 0 else ("orange" if days <= 3 else "green")
                due_label = f"期限超過 {abs(days)}日" if days < 0 else f"あと {days}日"
                col3.markdown(f":{color}[{due_label}]")


# ─── サンプルプロジェクト ─────────────────────────────────────────────────────

def _create_sample_project() -> Project:
    from .templates import create_from_template
    proj = create_from_template(
        "産業廃棄物処理",
        project_name = "産業廃棄物処理手続き 2026年3月",
        assignee_map = {
            "環境管理担当": "田中健二",
            "法務担当":     "鈴木美咲",
            "購買担当":     "佐藤隆",
            "総務担当":     "山田花子",
            "経理担当":     "高橋誠",
        },
    )
    # 一部タスクを完了・進行中に設定
    all_tasks = proj.all_tasks()
    if len(all_tasks) >= 3:
        all_tasks[0].status = TaskStatus.DONE
        all_tasks[1].status = TaskStatus.DONE
        all_tasks[2].status = TaskStatus.IN_PROGRESS
    proj.refresh_all_statuses()
    return proj


# ─── メインレンダリング ────────────────────────────────────────────────────────

def main():
    proj = render_sidebar()

    if proj is None:
        st.title("プロジェクト進捗トラッカー")
        st.info("サイドバーからプロジェクトを開くかテンプレートから作成してください。")
        st.subheader("利用可能なテンプレート")
        for name, tmpl in TEMPLATES.items():
            st.markdown(f"- **{name}**: {tmpl['description']}")
        return

    proj.refresh_all_statuses()
    st.title(f"📊 {proj.name}")
    if proj.description:
        st.caption(proj.description)
    st.caption(f"最終更新: {datetime.now().strftime('%Y/%m/%d %H:%M')}")

    tabs = st.tabs(["サマリー", "カンバン", "タスク一覧", "次にやること", "アラート"])

    with tabs[0]:
        render_summary(proj)

    with tabs[1]:
        render_kanban(proj)

    with tabs[2]:
        render_task_table(proj)

    with tabs[3]:
        render_next_actions(proj)

    with tabs[4]:
        render_alerts(proj)


if __name__ == "__main__":
    main()
