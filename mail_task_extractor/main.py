"""
main.py - メールタスク抽出システム CLI エントリポイント

使い方:
  # EMLファイルから処理
  uv run python -m mail_task_extractor.main --source eml --dir ./emails/

  # IMAPサーバーから取得
  uv run python -m mail_task_extractor.main --source imap \
    --host imap.example.com --user user@example.com --password xxxxxxxx

  # ダッシュボードのみ表示（既存タスクファイルを使用）
  uv run python -m mail_task_extractor.main --dashboard-only

  # CSVエクスポート
  uv run python -m mail_task_extractor.main --export-csv tasks.csv
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timedelta
from pathlib import Path

from .email_fetcher import create_fetcher
from .email_parser import EmailParser, ThreadGrouper
from .models import ParsedEmail
from .status_tracker import StatusTracker
from .task_extractor import BatchTaskExtractor, TaskExtractor
from .thread_analyzer import ProjectProgressTracker, ThreadAnalyzer


async def run(args: argparse.Namespace) -> None:
    # ── 設定 ──
    vllm_url = args.vllm_url
    model = args.model
    tasks_file = args.tasks_file

    # ── ステータストラッカー（タスクの永続化） ──
    tracker = StatusTracker(
        storage_path=tasks_file,
        base_url=vllm_url,
        model=model,
    )

    # ── ダッシュボードのみ表示モード ──
    if args.dashboard_only:
        analyzer = ThreadAnalyzer(base_url=vllm_url, model=model)
        progress_tracker = ProjectProgressTracker(analyzer)
        report = await progress_tracker.generate_dashboard_report(
            tracker.get_all_tasks(), []
        )
        print(report)
        if args.export_csv:
            tracker.export_csv(args.export_csv)
        return

    # ── CSVエクスポートのみ ──
    if args.export_csv and not args.source:
        tracker.export_csv(args.export_csv)
        return

    # ── メール取得 ──
    print(f"[1/5] メール取得中 (ソース: {args.source})")

    since = datetime.now() - timedelta(days=args.days)

    fetcher_kwargs: dict = {}
    if args.source == "imap":
        fetcher_kwargs = {
            "host": args.host,
            "username": args.user,
            "password": args.password,
            "port": args.port,
        }
    elif args.source in ("eml", "msg"):
        fetcher_kwargs = {"directory": args.dir}
    elif args.source == "gmail":
        fetcher_kwargs = {
            "credentials_file": args.credentials or "credentials.json",
            "token_file": args.token or "token.json",
        }
    elif args.source == "exchange":
        fetcher_kwargs = {
            "server": args.host,
            "username": args.user,
            "password": args.password,
        }

    fetcher = create_fetcher(args.source, **fetcher_kwargs)

    raw_emails: list[bytes] = []
    try:
        if args.source == "imap":
            with fetcher:
                for raw in fetcher.fetch_since(since, folder=args.folder):
                    raw_emails.append(raw)
        else:
            for raw in fetcher.fetch_since(since):
                raw_emails.append(raw)
    except Exception as e:
        print(f"[ERROR] メール取得失敗: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"  取得完了: {len(raw_emails)}通")

    # ── メール解析 ──
    print(f"[2/5] メール解析中...")
    parser = EmailParser(source=args.source)
    parsed_emails: list[ParsedEmail] = []
    for raw in raw_emails:
        em = parser.parse(raw)
        if em:
            parsed_emails.append(em)
    print(f"  解析完了: {len(parsed_emails)}通")

    # ── スレッドグループ化 ──
    print(f"[3/5] スレッドグループ化中...")
    grouper = ThreadGrouper()
    threads = grouper.group(parsed_emails)
    print(f"  {len(threads)}スレッドに整理しました")

    # ── タスク抽出 ──
    print(f"[4/5] LLMでタスク抽出中 (モデル: {model})")
    extractor = TaskExtractor(
        base_url=vllm_url,
        model=model,
        use_guided_decoding=not args.no_guided,
    )
    batch_extractor = BatchTaskExtractor(extractor)

    results = await batch_extractor.extract_all(
        parsed_emails,
        max_concurrent=args.concurrency,
    )

    all_tasks = batch_extractor.resolve_dependencies(results)
    tracker.add_tasks(all_tasks)
    print(f"  抽出完了: {len(all_tasks)}タスク")

    # ── ステータス更新（返信メールから） ──
    print(f"[5/5] ステータス自動更新中...")
    all_stored_tasks = tracker.get_all_tasks()
    update_count = 0
    for em in parsed_emails:
        # 返信メールのみ処理（In-Reply-To があるもの）
        if em.in_reply_to:
            updates = await tracker.process_reply(em, all_stored_tasks)
            update_count += len(updates)
            for task_id, new_status in updates:
                print(f"  [{task_id}] → {new_status.value}")
    print(f"  {update_count}件のステータスを更新しました")

    # ── レポート生成 ──
    analyzer = ThreadAnalyzer(base_url=vllm_url, model=model)
    progress_tracker = ProjectProgressTracker(analyzer)

    report = await progress_tracker.generate_dashboard_report(
        tracker.get_all_tasks(), threads
    )
    print("\n" + "=" * 60)
    print(report)
    print("=" * 60)

    # スレッド別分析
    if args.verbose:
        print("\n## スレッド別分析")
        for thread in threads[:5]:  # 最新5スレッドのみ
            analysis = analyzer.analyze_thread(thread)
            print(f"\n### {thread.subject}")
            print(f"  メール数: {analysis['email_count']}通")
            print(f"  未回答: {analysis['unanswered_count']}件")
            print(f"  ボールホルダー: {analysis['ball_holder']}")
            if analysis["is_stale"]:
                print(f"  [放置] {analysis['days_since_last_activity']}日間返信なし")

    # CSV エクスポート
    if args.export_csv:
        tracker.export_csv(args.export_csv)

    print(f"\nタスク保存先: {tasks_file}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="メールからタスクを自動抽出するシステム",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # ソース設定
    parser.add_argument(
        "--source", choices=["imap", "exchange", "gmail", "eml", "msg"],
        help="メールソースの種類"
    )
    parser.add_argument("--dir", default="./emails", help="EML/MSGファイルのディレクトリ")
    parser.add_argument("--host", help="IMAPサーバーホスト / Exchangeサーバー")
    parser.add_argument("--user", help="ユーザー名（メールアドレス）")
    parser.add_argument("--password", help="パスワード")
    parser.add_argument("--port", type=int, default=993, help="IMAPポート (デフォルト: 993)")
    parser.add_argument("--folder", default="INBOX", help="取得するフォルダ (デフォルト: INBOX)")
    parser.add_argument("--credentials", help="Gmail API credentials.json のパス")
    parser.add_argument("--token", help="Gmail API token.json のパス")

    # 期間
    parser.add_argument(
        "--days", type=int, default=30,
        help="何日前からのメールを処理するか (デフォルト: 30)"
    )

    # LLM設定
    parser.add_argument(
        "--vllm-url", default="http://localhost:8000/v1",
        help="vLLM OpenAI互換 API の URL"
    )
    parser.add_argument(
        "--model", default="meta-llama/Llama-3.1-8B-Instruct",
        help="使用するモデル名"
    )
    parser.add_argument(
        "--no-guided", action="store_true",
        help="vLLM Guided Decoding を無効化（古いモデル向け）"
    )
    parser.add_argument(
        "--concurrency", type=int, default=3,
        help="並列LLM呼び出し数 (デフォルト: 3)"
    )

    # 出力設定
    parser.add_argument(
        "--tasks-file", default="data/tasks.json",
        help="タスクの保存先JSONファイル"
    )
    parser.add_argument("--export-csv", help="CSVエクスポート先のパス")
    parser.add_argument("--dashboard-only", action="store_true",
                        help="メール取得・抽出をスキップして既存タスクのダッシュボードを表示")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="スレッド別詳細分析を表示")

    args = parser.parse_args()

    if not args.dashboard_only and not args.export_csv and not args.source:
        parser.error("--source を指定するか --dashboard-only / --export-csv を使用してください")

    asyncio.run(run(args))


if __name__ == "__main__":
    main()
