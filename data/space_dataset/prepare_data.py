#!/usr/bin/env python3
"""
宇宙データセット準備スクリプト
- 複数のJSONLファイルを結合してファインチューニング用データセットを生成
- GPT-OSS / Gemma 向けフォーマット変換（Alpaca, ChatML, Gemma Instruction形式）
- 訓練/検証/テスト分割
- シャッフル・重複排除
"""

import json
import random
import hashlib
import re
from pathlib import Path
from typing import Optional
import argparse

# ===================== 設定 =====================
DEFAULT_SEED = 42
TRAIN_RATIO  = 0.85
VAL_RATIO    = 0.10
TEST_RATIO   = 0.05

# ===================== データロード =====================

def load_jsonl(filepath: Path) -> list[dict]:
    records = []
    if not filepath.exists():
        print(f'  [SKIP] {filepath.name} が見つかりません')
        return records
    with open(filepath, 'r', encoding='utf-8') as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                records.append(obj)
            except json.JSONDecodeError as e:
                print(f'  [WARN] {filepath.name}:{lineno}: JSONエラー: {e}')
    print(f'  ロード: {filepath.name} → {len(records)} 件')
    return records


def deduplicate(records: list[dict]) -> list[dict]:
    """inputのMD5フィンガープリントで重複排除"""
    seen = set()
    unique = []
    for r in records:
        fp = hashlib.md5(r.get('input', '').strip().lower().encode()).hexdigest()
        if fp not in seen:
            seen.add(fp)
            unique.append(r)
    removed = len(records) - len(unique)
    if removed > 0:
        print(f'  重複排除: {removed} 件除去 → {len(unique)} 件')
    return unique


# ===================== フォーマット変換 =====================

def to_alpaca_format(record: dict) -> dict:
    """
    Alpaca形式（GPT-OSSやLLaMA系でよく使われる）
    {"instruction": ..., "input": ..., "output": ...}
    ※ 元データがそのままAlpaca形式なので恒等変換
    """
    return {
        'instruction': record.get('instruction', ''),
        'input':       record.get('input', ''),
        'output':      record.get('output', ''),
    }


def to_chatml_format(record: dict) -> dict:
    """
    ChatML形式（GPT系・Gemmaのchat templateに合わせる）
    {"messages": [{"role": "system/user/assistant", "content": ...}]}
    """
    instruction = record.get('instruction', '')
    inp         = record.get('input', '')
    output      = record.get('output', '')

    user_content = instruction
    if inp:
        user_content = f'{instruction}\n\n{inp}'

    return {
        'messages': [
            {'role': 'system',    'content': '宇宙・航空宇宙分野の専門アシスタントです。正確で実用的な情報を日本語で提供します。'},
            {'role': 'user',      'content': user_content},
            {'role': 'assistant', 'content': output},
        ]
    }


def to_gemma_format(record: dict) -> dict:
    """
    Gemma Instruction Tuning形式
    {"text": "<start_of_turn>user\n...<end_of_turn>\n<start_of_turn>model\n...<end_of_turn>"}
    """
    instruction = record.get('instruction', '')
    inp         = record.get('input', '')
    output      = record.get('output', '')

    user_content = instruction
    if inp:
        user_content = f'{instruction}\n\n{inp}'

    text = (
        f'<start_of_turn>user\n{user_content}<end_of_turn>\n'
        f'<start_of_turn>model\n{output}<end_of_turn>'
    )
    return {'text': text}


def to_gpt_oss_format(record: dict) -> dict:
    """
    GPT-OSS (gpt-oss-120b) 向け：Cerebras APIのOpenAI互換形式
    {"messages": [...]} 形式 (ChatML互換)
    """
    return to_chatml_format(record)


# ===================== データ分割・保存 =====================

def split_dataset(records: list[dict], seed: int = DEFAULT_SEED):
    """訓練/検証/テストに分割"""
    rng = random.Random(seed)
    records = records.copy()
    rng.shuffle(records)

    n = len(records)
    n_train = int(n * TRAIN_RATIO)
    n_val   = int(n * VAL_RATIO)

    train = records[:n_train]
    val   = records[n_train:n_train + n_val]
    test  = records[n_train + n_val:]

    return train, val, test


def save_jsonl(records: list[dict], filepath: Path):
    with open(filepath, 'w', encoding='utf-8') as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')
    print(f'  保存: {filepath.name} ({len(records)} 件)')


# ===================== メイン =====================

def main():
    parser = argparse.ArgumentParser(description='宇宙データセット準備スクリプト')
    parser.add_argument('--format', choices=['alpaca', 'chatml', 'gemma', 'gpt_oss'],
                        default='alpaca', help='出力フォーマット (default: alpaca)')
    parser.add_argument('--seed', type=int, default=DEFAULT_SEED, help='乱数シード')
    parser.add_argument('--output-dir', type=str, default=None,
                        help='出力ディレクトリ（デフォルト: スクリプトと同じディレクトリ）')
    parser.add_argument('--no-split', action='store_true',
                        help='訓練/検証/テスト分割をしない（全件を1ファイルに出力）')
    args = parser.parse_args()

    script_dir = Path(__file__).parent
    output_dir = Path(args.output_dir) if args.output_dir else script_dir / 'prepared'
    output_dir.mkdir(parents=True, exist_ok=True)

    print('=== 宇宙データセット準備開始 ===')
    print(f'フォーマット: {args.format}')
    print(f'出力先: {output_dir}')

    # ソースファイルのロード
    source_files = [
        script_dir / 'space_glossary.jsonl',
        script_dir / 'space_training_data.jsonl',
        script_dir / 'space_training_data_part2.jsonl',
        script_dir / 'space_coding_data.jsonl',
    ]

    all_records = []
    print('\n--- データロード ---')
    for fp in source_files:
        records = load_jsonl(fp)
        all_records.extend(records)

    print(f'\n合計: {len(all_records)} 件')

    # 重複排除
    print('\n--- 重複排除 ---')
    all_records = deduplicate(all_records)

    # フォーマット変換
    print(f'\n--- フォーマット変換 ({args.format}) ---')
    format_fn = {
        'alpaca':   to_alpaca_format,
        'chatml':   to_chatml_format,
        'gemma':    to_gemma_format,
        'gpt_oss':  to_gpt_oss_format,
    }[args.format]

    converted = [format_fn(r) for r in all_records]
    print(f'変換完了: {len(converted)} 件')

    # 保存
    print('\n--- データ保存 ---')
    if args.no_split:
        save_jsonl(converted, output_dir / f'space_dataset_{args.format}.jsonl')
    else:
        train, val, test = split_dataset(converted, seed=args.seed)
        save_jsonl(train, output_dir / f'train_{args.format}.jsonl')
        save_jsonl(val,   output_dir / f'val_{args.format}.jsonl')
        save_jsonl(test,  output_dir / f'test_{args.format}.jsonl')
        print(f'\n分割比: train={len(train)}, val={len(val)}, test={len(test)}')

    # サンプル表示
    print('\n--- サンプル（先頭1件） ---')
    sample = converted[0]
    print(json.dumps(sample, ensure_ascii=False, indent=2)[:500] + '...')

    print('\n=== 準備完了 ===')


if __name__ == '__main__':
    main()
