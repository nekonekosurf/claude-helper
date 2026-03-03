#!/usr/bin/env python3
"""
宇宙データセット品質チェックスクリプト
- フォーマット検証（JSONL形式、必須フィールド確認）
- 重複チェック（input/output の類似度）
- 統計情報出力
"""

import json
import sys
import hashlib
import re
from pathlib import Path
from collections import Counter, defaultdict

# ===================== 設定 =====================
REQUIRED_FIELDS = {'instruction', 'input', 'output'}
MIN_OUTPUT_LEN = 50    # 出力の最小文字数
MIN_INPUT_LEN  = 2     # 入力の最小文字数
MAX_OUTPUT_LEN = 8000  # 出力の最大文字数（超えると要確認）
MIN_UNIQUE_RATIO = 0.90  # 重複除外後の残存率の最低値

# ===================== ユーティリティ =====================

def load_jsonl(filepath: Path):
    """JSONLファイルを読み込み、行番号付きで返す"""
    records = []
    errors = []
    with open(filepath, 'r', encoding='utf-8') as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                records.append((lineno, obj))
            except json.JSONDecodeError as e:
                errors.append(f'  行{lineno}: JSONパースエラー: {e}')
    return records, errors


def check_required_fields(records):
    """必須フィールドの存在チェック"""
    errors = []
    for lineno, obj in records:
        missing = REQUIRED_FIELDS - set(obj.keys())
        if missing:
            errors.append(f'  行{lineno}: フィールド不足: {missing}')
        for field in REQUIRED_FIELDS & set(obj.keys()):
            val = obj[field]
            if not isinstance(val, str):
                errors.append(f'  行{lineno}: {field} が文字列でない: {type(val)}')
            elif field == 'output' and len(val) < MIN_OUTPUT_LEN:
                errors.append(f'  行{lineno}: output が短すぎる: {len(val)} 文字 (最低 {MIN_OUTPUT_LEN} 文字)')
            elif field == 'input' and len(val) < MIN_INPUT_LEN:
                errors.append(f'  行{lineno}: input が短すぎる: {len(val)} 文字')
    return errors


def check_length_warnings(records):
    """長さに関する警告"""
    warnings = []
    for lineno, obj in records:
        out_len = len(obj.get('output', ''))
        if out_len > MAX_OUTPUT_LEN:
            warnings.append(f'  行{lineno}: output が長すぎる: {out_len} 文字 (最大 {MAX_OUTPUT_LEN} 文字推奨)')
    return warnings


def fingerprint(text: str) -> str:
    """テキストを正規化してフィンガープリントを生成"""
    # 空白・改行・句読点の正規化
    normalized = re.sub(r'\s+', ' ', text.strip().lower())
    return hashlib.md5(normalized.encode('utf-8')).hexdigest()


def check_duplicates(records):
    """inputとoutputの重複チェック"""
    input_fp_to_lines = defaultdict(list)
    output_fp_to_lines = defaultdict(list)

    for lineno, obj in records:
        inp = obj.get('input', '')
        out = obj.get('output', '')
        input_fp_to_lines[fingerprint(inp)].append(lineno)
        output_fp_to_lines[fingerprint(out)].append(lineno)

    dup_inputs  = {fp: lines for fp, lines in input_fp_to_lines.items()  if len(lines) > 1}
    dup_outputs = {fp: lines for fp, lines in output_fp_to_lines.items() if len(lines) > 1}

    issues = []
    for fp, lines in dup_inputs.items():
        issues.append(f'  重複input: 行 {lines} が同一内容')
    for fp, lines in dup_outputs.items():
        issues.append(f'  重複output: 行 {lines} が同一内容')

    n_unique_inputs  = len(input_fp_to_lines)
    n_unique_outputs = len(output_fp_to_lines)
    return issues, n_unique_inputs, n_unique_outputs


def compute_statistics(records):
    """統計情報を計算して返す"""
    if not records:
        return {}

    instructions = [obj.get('instruction', '') for _, obj in records]
    inputs       = [obj.get('input',  '')      for _, obj in records]
    outputs      = [obj.get('output', '')      for _, obj in records]

    out_lens = [len(o) for o in outputs]
    inp_lens = [len(i) for i in inputs]

    # コードブロック含有率
    code_count = sum(1 for o in outputs if '```' in o or 'def ' in o or 'import ' in o)

    # 日本語含有率
    def has_japanese(text):
        return bool(re.search(r'[\u3040-\u30ff\u4e00-\u9fff]', text))
    jp_count = sum(1 for o in outputs if has_japanese(o))

    # instruction の重複
    instr_counter = Counter(instructions)
    duplicate_instrs = {k: v for k, v in instr_counter.items() if v > 1}

    return {
        'total_records': len(records),
        'output_len_min': min(out_lens),
        'output_len_max': max(out_lens),
        'output_len_avg': sum(out_lens) / len(out_lens),
        'output_len_median': sorted(out_lens)[len(out_lens)//2],
        'input_len_avg': sum(inp_lens) / len(inp_lens),
        'code_block_count': code_count,
        'code_block_ratio_pct': code_count / len(records) * 100,
        'japanese_output_count': jp_count,
        'japanese_output_ratio_pct': jp_count / len(records) * 100,
        'duplicate_instructions_count': len(duplicate_instrs),
        'duplicate_instructions': dict(list(duplicate_instrs.items())[:5]),  # 先頭5件
    }


def print_separator(char='-', width=60):
    print(char * width)


def check_file(filepath: Path) -> bool:
    """ファイル1本を検証し、結果を表示。エラーがあれば False を返す"""
    print_separator('=')
    print(f'ファイル: {filepath.name}')
    print_separator('=')

    if not filepath.exists():
        print(f'  [ERROR] ファイルが存在しません: {filepath}')
        return False

    # JSONLロード
    records, load_errors = load_jsonl(filepath)
    if load_errors:
        print('[FAIL] JSONパースエラー:')
        for e in load_errors:
            print(e)
        return False

    if not records:
        print('[FAIL] レコードが空です')
        return False

    all_ok = True

    # 必須フィールドチェック
    field_errors = check_required_fields(records)
    if field_errors:
        print('[FAIL] フィールドエラー:')
        for e in field_errors[:20]:
            print(e)
        if len(field_errors) > 20:
            print(f'  ... 他 {len(field_errors)-20} 件')
        all_ok = False
    else:
        print('[OK]  フィールド検証: 全レコード正常')

    # 長さ警告
    warnings = check_length_warnings(records)
    if warnings:
        print(f'[WARN] 長さ警告 ({len(warnings)} 件):')
        for w in warnings[:10]:
            print(w)

    # 重複チェック
    dup_issues, n_unique_inp, n_unique_out = check_duplicates(records)
    if dup_issues:
        print(f'[WARN] 重複検出 ({len(dup_issues)} 件):')
        for d in dup_issues[:10]:
            print(d)
    else:
        print('[OK]  重複チェック: 重複なし')

    # 統計
    stats = compute_statistics(records)
    print_separator()
    print('統計情報:')
    print(f'  総レコード数        : {stats["total_records"]}')
    print(f'  ユニーク入力数      : {n_unique_inp}  ({n_unique_inp/stats["total_records"]*100:.1f}%)')
    print(f'  ユニーク出力数      : {n_unique_out}  ({n_unique_out/stats["total_records"]*100:.1f}%)')
    print(f'  output長 min/avg/med/max: '
          f'{stats["output_len_min"]}/{stats["output_len_avg"]:.0f}/'
          f'{stats["output_len_median"]}/{stats["output_len_max"]}')
    print(f'  input長 平均        : {stats["input_len_avg"]:.1f} 文字')
    print(f'  コードブロック含有  : {stats["code_block_count"]} 件 ({stats["code_block_ratio_pct"]:.1f}%)')
    print(f'  日本語output含有    : {stats["japanese_output_count"]} 件 ({stats["japanese_output_ratio_pct"]:.1f}%)')
    if stats['duplicate_instructions_count'] > 0:
        print(f'  instruction重複数   : {stats["duplicate_instructions_count"]} 件')
        for k, v in stats['duplicate_instructions'].items():
            print(f'    "{k[:50]}..." x{v}')

    # 品質判定
    unique_ratio = n_unique_inp / stats['total_records']
    if unique_ratio < MIN_UNIQUE_RATIO:
        print(f'\n[FAIL] ユニーク入力率が低い: {unique_ratio*100:.1f}% (最低 {MIN_UNIQUE_RATIO*100:.0f}%)')
        all_ok = False

    if all_ok:
        print_separator()
        print('[PASS] このファイルは品質基準を満たしています')
    else:
        print_separator()
        print('[FAIL] 修正が必要な問題があります')

    return all_ok


def main():
    dataset_dir = Path(__file__).parent

    target_files = [
        dataset_dir / 'space_glossary.jsonl',
        dataset_dir / 'space_training_data.jsonl',
        dataset_dir / 'space_training_data_part2.jsonl',
        dataset_dir / 'space_coding_data.jsonl',
    ]

    # コマンドライン引数でファイル指定があれば上書き
    if len(sys.argv) > 1:
        target_files = [Path(p) for p in sys.argv[1:]]

    total_files = len(target_files)
    passed = 0
    total_records = 0

    for fp in target_files:
        ok = check_file(fp)
        if ok:
            passed += 1
        # レコード数集計
        if fp.exists():
            records, _ = load_jsonl(fp)
            total_records += len(records)
        print()

    print_separator('=')
    print(f'=== 総合結果 ===')
    print(f'ファイル数    : {total_files}')
    print(f'合格          : {passed}/{total_files}')
    print(f'総レコード数  : {total_records}')
    print_separator('=')

    if passed == total_files:
        print('全ファイル合格！データセットは使用可能な状態です。')
        sys.exit(0)
    else:
        print(f'{total_files - passed} ファイルに問題があります。修正してください。')
        sys.exit(1)


if __name__ == '__main__':
    main()
