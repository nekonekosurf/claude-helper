"""
chunk_cleaner.py - Level 1: ノイズ除去

JERG文書チャンクから以下のノイズを除去する:
1. 目次ドット列（........）
2. ページヘッダー（JERG-X-XXXF\nNN\n パターン）
3. 過度な空白・改行
4. 無意味な記号列

情報量は一切落とさない（ノイズのみ除去）。
チャンクタイプ分類とメタデータ付与も行う（設計書 手法A に相当）。
"""

import re
import json
from pathlib import Path


# ========== ノイズ除去パターン ==========

# JERGヘッダー: ページ途中に挿入された「JERG-0-001F\n24\n」のようなパターン
# 実例: "配管 \nJERG-0-001F \n5 \n注記"
# バリエーション: JERG-X-XXXF、JERG-X-XXX-HB001B、JERG-X-XXX_N1 等
_JERG_HDR = re.compile(
    r'JERG-\d+-\d+[\w-]*\s*\n\s*(?:[ivxIVX]+|\d{1,4})\s*\n',
)

# ページ番号のみ行（数字のみの行、またはローマ数字のみ）
_PAGE_NUM_LINE = re.compile(
    r'(?m)^[ \t]*(?:[ivxIVX]+|\d{1,4})[ \t]*$\n?'
)

# 目次ドット列を含む行（ドットが連続5個以上）
_TOC_DOT_LINE = re.compile(
    r'[^\n]*\.{5,}[^\n]*\n?'
)

# 複数の空白行を1つにまとめる
_MULTI_BLANK = re.compile(r'\n{3,}')

# 行頭・行末の余分なスペース
_TRAILING_SPACE = re.compile(r'[ \t]+\n')
_LEADING_SPACE = re.compile(r'\n[ \t]+')

# 無意味な記号列（3文字以上の記号のみ行）
_SYMBOL_LINE = re.compile(
    r'(?m)^[ \t]*[─━─\-=_*#~]{5,}[ \t]*$\n?'
)


def _is_toc_chunk(text: str) -> bool:
    """目次チャンクかどうかを判定する。"""
    if len(text) == 0:
        return False
    dot_ratio = text.count('.') / len(text)
    # ドット比率が30%超 かつ 「目次」または目次特有の記号がある
    if dot_ratio > 0.3:
        return True
    # ドット列行が3行以上ある場合も目次とみなす
    dot_lines = _TOC_DOT_LINE.findall(text)
    if len(dot_lines) >= 3:
        return True
    return False


def _is_disclaimer_chunk(text: str) -> bool:
    """免責条項チャンクかどうかを判定する。"""
    return bool(re.search(r'免責条項|Disclaimer', text, re.IGNORECASE))


def _is_references_chunk(text: str) -> bool:
    """参照文書一覧チャンクかどうかを判定する。"""
    return bool(re.search(r'関連文書|参考文書|参照文書|適用文書|関係文書', text))


def _extract_cross_refs(text: str, self_doc_id: str) -> list[str]:
    """テキスト中の他文書への参照を抽出する。"""
    refs = re.findall(r'JERG-\d+-\d+(?:-\w+)?', text)
    # 自分自身のdoc_idと亜種を除外
    base_self = self_doc_id.split('-')[:-1] if re.search(r'[A-Z]$', self_doc_id) else None
    unique_refs = set()
    for r in refs:
        # 自分自身を除外（バージョン記号を無視して比較）
        r_base = re.sub(r'[A-Z]$', '', r)
        self_base = re.sub(r'[A-Z]$', '', self_doc_id)
        if r_base != self_base:
            unique_refs.add(r)
    return sorted(unique_refs)


def _extract_section_info(text: str) -> tuple[str, str]:
    """
    テキストからセクション番号とタイトルを抽出する。
    全角・半角混在に対応。
    目次のドット列は除外する。
    返り値: (section_number, section_title)
    """
    # 全角数字を半角に変換してからマッチ
    normalized = text.translate(str.maketrans(
        '０１２３４５６７８９．',
        '0123456789.'
    ))

    # パターン: 「3.2.1」「3.2」形式（先頭または改行直後）
    # ただし後ろにドット列が続く目次行は除外する（「......」があれば除外）
    sec_pattern = re.compile(
        r'(?:^|\n)(\d+(?:\.\d+){1,3})\s+([^\n]{2,40})(?!\s*\.{5})',
    )
    for m in sec_pattern.finditer(normalized):
        sec_num = m.group(1)
        sec_title = m.group(2).strip()
        # タイトルに大量のドットが含まれていたらスキップ（目次行）
        if sec_title.count('.') >= 5:
            continue
        if len(sec_title) > 40:
            sec_title = sec_title[:40]
        return sec_num, sec_title

    # 全角数字パターン（元テキストで）
    zen_pattern = re.compile(
        r'(?:^|\n)([１-９０\d][．\.][１-９０\d０-９]+(?:[．\.][１-９０\d０-９]+)?)\s+([^\n]{2,40})'
    )
    for m in zen_pattern.finditer(text):
        sec_title = m.group(2).strip()
        if sec_title.count('.') >= 5:
            continue
        return m.group(1), sec_title

    return '', ''


def _extract_page_number(text: str) -> int | None:
    """テキストからページ番号を抽出する。"""
    # ヘッダーパターン: JERG-X-XXXF\n24\n
    m = re.search(
        r'JERG-\d+-\d+\w*\s*\n\s*(\d+)\s*\n',
        text
    )
    if m:
        return int(m.group(1))
    # 単独のページ番号行
    m = re.search(r'(?m)^(\d{1,4})$', text)
    if m:
        num = int(m.group(1))
        if 1 <= num <= 999:
            return num
    return None


def clean_text(text: str, doc_id: str = '') -> str:
    """
    チャンクテキストからノイズを除去する。
    情報量は保持する（本文・セクション見出し・数値は残す）。

    目次チャンクの場合はテキストをそのまま返す（目次情報も価値があるため）。
    ただし目次チャンクの is_toc フラグは classify_chunk() で設定される。
    """
    if not text:
        return text

    cleaned = text

    # 1. JERGヘッダー（ページ番号付き）を除去
    # 例: "JERG-0-001F\n24\n" → ""
    cleaned = _JERG_HDR.sub('\n', cleaned)

    # 2. 単独ページ番号行を除去（ローマ数字も含む）
    # ただし本文中の数値は残す（行全体が数字のみのケースのみ対象）
    cleaned = _PAGE_NUM_LINE.sub('', cleaned)

    # 3. 目次ドット列行を除去（ページ参照のドット列）
    # 例: "１. 総則 ............ 1" → 削除
    # ただし全文が目次のチャンクは is_toc で管理するので、混入分だけ除去
    cleaned = _TOC_DOT_LINE.sub('', cleaned)

    # 4. 無意味な区切り記号行
    cleaned = _SYMBOL_LINE.sub('', cleaned)

    # 5. 行末の余分なスペース
    cleaned = _TRAILING_SPACE.sub('\n', cleaned)

    # 6. 行頭の余分なスペース（インデントは保持する方針で、2スペース以上を除去）
    cleaned = re.sub(r'\n {2,}', '\n', cleaned)

    # 7. 複数の空白行を最大1行に
    cleaned = _MULTI_BLANK.sub('\n\n', cleaned)

    # 8. 先頭・末尾の空白を除去
    cleaned = cleaned.strip()

    return cleaned


def classify_chunk(text: str, cleaned_text: str, doc_id: str) -> dict:
    """
    チャンクを分類しメタデータを返す。

    返り値のキー:
      chunk_type: 'toc' | 'disclaimer' | 'references' | 'body'
      is_toc: bool
      cross_refs: list[str]
      section_number: str
      section_title: str
      page_number: int | None
    """
    # 元テキストで判定（目次はドット列で判定するため cleaned を使わない）
    is_toc = _is_toc_chunk(text)

    if is_toc:
        chunk_type = 'toc'
    elif _is_disclaimer_chunk(text):
        chunk_type = 'disclaimer'
    elif _is_references_chunk(text):
        chunk_type = 'references'
    else:
        chunk_type = 'body'

    cross_refs = _extract_cross_refs(text, doc_id)
    section_number, section_title = _extract_section_info(cleaned_text or text)
    page_number = _extract_page_number(text)

    return {
        'chunk_type': chunk_type,
        'is_toc': is_toc,
        'cross_refs': cross_refs,
        'section_number': section_number,
        'section_title': section_title,
        'page_number': page_number,
    }


def process_chunk(chunk: dict, doc_titles: dict) -> dict:
    """
    1チャンクにLevel 1処理（ノイズ除去 + メタデータ付与）を適用する。

    chunk: 元チャンク辞書 {'doc_id', 'filename', 'chunk_id', 'text'}
    doc_titles: {doc_id: タイトル文字列} の辞書
    返り値: 改善済みチャンク辞書
    """
    result = dict(chunk)
    original_text = chunk['text']
    doc_id = chunk['doc_id']

    # 文書タイトルを付与
    result['doc_title'] = doc_titles.get(doc_id, '')

    # ノイズ除去
    cleaned = clean_text(original_text, doc_id)
    result['text_cleaned'] = cleaned

    # 分類・メタデータ
    meta = classify_chunk(original_text, cleaned, doc_id)
    result.update(meta)

    return result


def process_all_chunks(
    chunks: list[dict],
    doc_titles: dict,
    verbose: bool = False,
) -> list[dict]:
    """
    全チャンクにLevel 1処理を適用する。

    chunks: chunks.json のリスト
    doc_titles: {doc_id: タイトル} の辞書
    verbose: 進捗表示
    """
    results = []
    toc_count = 0
    cleaned_count = 0

    for i, chunk in enumerate(chunks):
        processed = process_chunk(chunk, doc_titles)
        results.append(processed)

        if processed['is_toc']:
            toc_count += 1
        if processed['text_cleaned'] != chunk['text']:
            cleaned_count += 1

        if verbose and (i + 1) % 1000 == 0:
            print(f'  processed {i + 1}/{len(chunks)} chunks...')

    if verbose:
        print(f'  Total: {len(chunks)} chunks')
        print(f'  TOC chunks: {toc_count} ({toc_count/len(chunks)*100:.1f}%)')
        print(f'  Cleaned (noise removed): {cleaned_count} ({cleaned_count/len(chunks)*100:.1f}%)')

    return results


# ========== 文書タイトルの取得 ==========

def load_doc_titles(domain_map_path: str | Path) -> dict[str, str]:
    """
    domain_map.yaml から {doc_id: タイトル} を構築する。

    タイトルはコメント（# 以降）から抽出する。
    例: "- JERG-0-001  # 宇宙用高圧ガス機器技術基準"
    """
    import yaml

    with open(domain_map_path, encoding='utf-8') as f:
        data = yaml.safe_load(f)

    # YAML をそのままパースしても doc_id -> title のマッピングはコメントに入っているので
    # テキストとしても読んでコメントを解析する
    titles = {}
    with open(domain_map_path, encoding='utf-8') as f:
        for line in f:
            # "- JERG-X-XXX  # タイトル" パターン
            m = re.match(r'\s*-\s+(JERG-[\d]+-[\d\w-]+)\s+#\s+(.+)', line)
            if m:
                doc_id = m.group(1).strip()
                title = m.group(2).strip()
                titles[doc_id] = title

    return titles


# ========== スタンドアロン実行 ==========

if __name__ == '__main__':
    import sys

    base = Path(__file__).parent.parent
    chunks_path = base / 'data' / 'index' / 'chunks.json'
    domain_map_path = base / 'knowledge' / 'domain_map.yaml'
    output_path = base / 'data' / 'index' / 'chunks_cleaned.json'

    print('=== Level 1: ノイズ除去 ===')
    print(f'Input:  {chunks_path}')
    print(f'Output: {output_path}')
    print()

    with open(chunks_path, encoding='utf-8') as f:
        chunks = json.load(f)
    print(f'Loaded {len(chunks)} chunks')

    doc_titles = load_doc_titles(domain_map_path)
    print(f'Loaded {len(doc_titles)} doc titles from domain_map.yaml')
    print()

    results = process_all_chunks(chunks, doc_titles, verbose=True)

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    print(f'\nSaved to {output_path}')
