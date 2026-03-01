"""
improve_chunks.py - チャンク品質改善パイプライン

Level 1 (ノイズ除去) と Level 2 (コンテキスト注入) を連続実行する。

使い方:
  uv run scripts/improve_chunks.py                # 全チャンクを処理
  uv run scripts/improve_chunks.py --sample 20    # 最初の20チャンクでテスト
  uv run scripts/improve_chunks.py --doc JERG-0-001  # 特定文書のみ
  uv run scripts/improve_chunks.py --help

出力:
  data/index/chunks_cleaned.json   - Level 1 完了
  data/index/chunks_enriched.json  - Level 2 完了（最終成果物）
"""

import argparse
import json
import sys
import time
from pathlib import Path

# プロジェクトルートをパスに追加
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from src.chunk_cleaner import process_all_chunks, load_doc_titles
from src.context_injector import inject_context


def parse_args():
    parser = argparse.ArgumentParser(
        description='JERG チャンク品質改善パイプライン'
    )
    parser.add_argument(
        '--sample', type=int, default=0,
        help='処理するチャンク数（0=全件）'
    )
    parser.add_argument(
        '--doc', type=str, default='',
        help='処理対象の doc_id（例: JERG-0-001）。空=全文書'
    )
    parser.add_argument(
        '--input', type=str,
        default=str(ROOT / 'data' / 'index' / 'chunks.json'),
        help='入力 chunks.json のパス'
    )
    parser.add_argument(
        '--output-cleaned', type=str,
        default=str(ROOT / 'data' / 'index' / 'chunks_cleaned.json'),
        help='Level 1 出力パス'
    )
    parser.add_argument(
        '--output-enriched', type=str,
        default=str(ROOT / 'data' / 'index' / 'chunks_enriched.json'),
        help='Level 2 出力パス'
    )
    parser.add_argument(
        '--domain-map', type=str,
        default=str(ROOT / 'knowledge' / 'domain_map.yaml'),
        help='domain_map.yaml のパス'
    )
    parser.add_argument(
        '--level1-only', action='store_true',
        help='Level 1 のみ実行'
    )
    parser.add_argument(
        '--level2-only', action='store_true',
        help='Level 2 のみ実行（chunks_cleaned.json が必要）'
    )
    return parser.parse_args()


def print_separator(title: str):
    print()
    print('=' * 60)
    print(f'  {title}')
    print('=' * 60)


def print_sample_comparison(chunks_before: list, chunks_after: list, n: int = 3):
    """処理前後の比較を表示する。"""
    print()
    print('--- サンプル比較 ---')
    shown = 0
    for before, after in zip(chunks_before, chunks_after):
        if shown >= n:
            break
        print(f'\nchunk_id: {before["chunk_id"]}')
        print(f'chunk_type: {after.get("chunk_type", "?")}')
        print(f'is_toc: {after.get("is_toc", False)}')
        print(f'section: {after.get("section_number", "")} {after.get("section_title", "")}')
        print(f'cross_refs: {after.get("cross_refs", [])}')
        print(f'domain: {after.get("domain_name", "")}')

        orig = before['text'][:200].replace('\n', '\\n')
        cleaned = after.get('text_cleaned', before['text'])[:200].replace('\n', '\\n')
        if orig != cleaned:
            print(f'[元テキスト] {orig}')
            print(f'[クリーン済] {cleaned}')
        else:
            print(f'[テキスト]   {orig} (変更なし)')

        if 'context_header' in after:
            print(f'[コンテキスト]\n{after["context_header"]}')

        shown += 1


def print_stats(chunks: list):
    """チャンクの統計情報を表示する。"""
    from collections import Counter

    total = len(chunks)
    type_counts = Counter(c.get('chunk_type', 'unknown') for c in chunks)
    domain_counts = Counter(c.get('domain', '') for c in chunks if c.get('domain'))
    has_section = sum(1 for c in chunks if c.get('section_number'))
    has_crossrefs = sum(1 for c in chunks if c.get('cross_refs'))
    changed = sum(
        1 for c in chunks
        if c.get('text_cleaned') and c['text_cleaned'] != c['text']
    )

    print()
    print('--- 統計情報 ---')
    print(f'総チャンク数: {total}')
    print(f'チャンクタイプ:')
    for t, cnt in type_counts.most_common():
        pct = cnt / total * 100
        print(f'  {t}: {cnt} ({pct:.1f}%)')
    print(f'セクション情報あり: {has_section} ({has_section/total*100:.1f}%)')
    print(f'相互参照あり: {has_crossrefs} ({has_crossrefs/total*100:.1f}%)')
    print(f'ノイズ除去済み: {changed} ({changed/total*100:.1f}%)')
    print(f'ドメイン分類 (上位5):')
    for d, cnt in domain_counts.most_common(5):
        print(f'  {d}: {cnt}')


def main():
    args = parse_args()

    # ========== 入力の読み込み ==========
    print_separator('入力データ読み込み')

    chunks_path = Path(args.input)
    if not chunks_path.exists():
        print(f'ERROR: {chunks_path} が見つかりません')
        sys.exit(1)

    with open(chunks_path, encoding='utf-8') as f:
        all_chunks = json.load(f)

    print(f'読み込み完了: {len(all_chunks)} チャンク')

    # フィルタリング
    if args.doc:
        all_chunks = [c for c in all_chunks if c['doc_id'] == args.doc]
        print(f'文書フィルタ ({args.doc}): {len(all_chunks)} チャンク')

    if args.sample > 0:
        all_chunks = all_chunks[:args.sample]
        print(f'サンプルモード: {len(all_chunks)} チャンク')

    chunks_before = [dict(c) for c in all_chunks]  # 比較用にコピー

    # ========== Level 1: ノイズ除去 ==========
    if not args.level2_only:
        print_separator('Level 1: ノイズ除去')

        domain_map_path = Path(args.domain_map)
        if not domain_map_path.exists():
            print(f'ERROR: {domain_map_path} が見つかりません')
            sys.exit(1)

        doc_titles = load_doc_titles(domain_map_path)
        print(f'文書タイトル: {len(doc_titles)} 件')

        t0 = time.time()
        cleaned_chunks = process_all_chunks(all_chunks, doc_titles, verbose=True)
        elapsed = time.time() - t0
        print(f'処理時間: {elapsed:.1f}秒')

        # 出力
        out_cleaned = Path(args.output_cleaned)
        with open(out_cleaned, 'w', encoding='utf-8') as f:
            json.dump(cleaned_chunks, f, ensure_ascii=False, indent=1)
        print(f'保存: {out_cleaned}')

    else:
        # Level 2 のみの場合は Level 1 済みファイルを読み込む
        cleaned_path = Path(args.output_cleaned)
        if not cleaned_path.exists():
            print(f'ERROR: {cleaned_path} が見つかりません。先に Level 1 を実行してください。')
            sys.exit(1)
        with open(cleaned_path, encoding='utf-8') as f:
            cleaned_chunks = json.load(f)
        print(f'Level 1 済みデータ読み込み: {len(cleaned_chunks)} チャンク')

    # ========== Level 2: コンテキスト注入 ==========
    if not args.level1_only:
        print_separator('Level 2: コンテキスト注入')

        domain_map_path = Path(args.domain_map)
        t0 = time.time()
        enriched_chunks = inject_context(cleaned_chunks, domain_map_path, verbose=True)
        elapsed = time.time() - t0
        print(f'処理時間: {elapsed:.1f}秒')

        # 出力
        out_enriched = Path(args.output_enriched)
        with open(out_enriched, 'w', encoding='utf-8') as f:
            json.dump(enriched_chunks, f, ensure_ascii=False, indent=1)
        print(f'保存: {out_enriched}')

        final_chunks = enriched_chunks
    else:
        final_chunks = cleaned_chunks

    # ========== 結果表示 ==========
    print_separator('結果サマリー')
    print_stats(final_chunks)
    print_sample_comparison(chunks_before, final_chunks, n=3)

    print()
    print('完了。')


if __name__ == '__main__':
    main()
