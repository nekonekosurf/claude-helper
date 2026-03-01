"""
context_injector.py - Level 2: コンテキスト注入

Level 1 処理済みチャンクに以下のコンテキストを付加する:
1. 文書名・ドメイン情報（domain_map.yaml から）
2. セクション階層（前後チャンクから推定）
3. 前後チャンクのサマリー（簡潔な要約）
4. 検索用コンテキストフィールド（context_header）

元テキスト（text フィールド）は変更しない。
context_header フィールドを新設し、LLM への提示時に使用する。
"""

import re
import json
from pathlib import Path
from collections import defaultdict


# ========== ドメインマップ読み込み ==========

def load_domain_map(domain_map_path: str | Path) -> dict:
    """
    domain_map.yaml を読み込み、doc_id -> ドメイン情報 の辞書を返す。

    返り値: {
        doc_id: {
            'domain_name': str,
            'domain_key': str,
            'expert_note': str,
            'keywords': list[str],
            'is_primary': bool,
        }
    }
    """
    import yaml

    with open(domain_map_path, encoding='utf-8') as f:
        data = yaml.safe_load(f)

    doc_to_domain = {}
    domains = data.get('domains', {})

    for domain_key, domain_info in domains.items():
        domain_name = domain_info.get('name', domain_key)
        expert_note = domain_info.get('expert_note', '').strip()
        keywords = domain_info.get('keywords', [])

        # primary_docs
        for doc_ref in domain_info.get('primary_docs', []):
            doc_id = _normalize_doc_id(str(doc_ref))
            if doc_id not in doc_to_domain:
                doc_to_domain[doc_id] = {
                    'domain_name': domain_name,
                    'domain_key': domain_key,
                    'expert_note': expert_note,
                    'keywords': keywords,
                    'is_primary': True,
                }

        # related_docs
        for doc_ref in domain_info.get('related_docs', []):
            doc_id = _normalize_doc_id(str(doc_ref))
            if doc_id not in doc_to_domain:
                doc_to_domain[doc_id] = {
                    'domain_name': domain_name,
                    'domain_key': domain_key,
                    'expert_note': expert_note,
                    'keywords': keywords,
                    'is_primary': False,
                }

    return doc_to_domain


def _normalize_doc_id(doc_ref: str) -> str:
    """
    YAMLのコメント付きdoc_id参照を正規化する。
    例: "JERG-0-001  # 宇宙用高圧ガス機器技術基準" → "JERG-0-001"
    """
    return doc_ref.split('#')[0].strip()


# ========== セクション階層の構築 ==========

def build_section_hierarchy(chunks: list[dict]) -> dict[str, list[str]]:
    """
    チャンクリストからdoc_idごとのセクション番号シーケンスを構築する。

    返り値: {doc_id: [section_number, ...]} (チャンク順)
    """
    doc_sections = defaultdict(list)
    for chunk in chunks:
        doc_id = chunk['doc_id']
        sec_num = chunk.get('section_number', '')
        doc_sections[doc_id].append(sec_num)
    return dict(doc_sections)


def _infer_parent_section(section_number: str) -> str:
    """
    セクション番号から親セクション番号を推定する。
    例: "3.2.1" → "3.2", "3.2" → "3", "3" → ""
    """
    if not section_number:
        return ''
    parts = section_number.split('.')
    if len(parts) <= 1:
        return ''
    return '.'.join(parts[:-1])


# ========== 前後チャンクのサマリー生成 ==========

def _brief_summary(text: str, max_chars: int = 80) -> str:
    """
    テキストの簡潔なサマリー（先頭の意味ある内容）を返す。
    目次や記号列はスキップする。
    """
    if not text:
        return ''

    # 目次ドット列を除外
    lines = [
        line.strip()
        for line in text.split('\n')
        if line.strip() and '......' not in line and len(line.strip()) > 5
    ]

    if not lines:
        return ''

    summary = ' '.join(lines[:3])
    if len(summary) > max_chars:
        summary = summary[:max_chars] + '...'
    return summary


# ========== コンテキストヘッダー生成 ==========

def build_context_header(
    chunk: dict,
    prev_chunk: dict | None,
    next_chunk: dict | None,
    domain_info: dict | None,
) -> str:
    """
    チャンクに付加するコンテキストヘッダー文字列を生成する。

    形式:
    [文書: JERG-X-XXX タイトル] [分野: ドメイン名] [セクション: X.Y タイトル]
    前文脈: ...
    後文脈: ...
    """
    parts = []

    # 文書情報
    doc_id = chunk.get('doc_id', '')
    doc_title = chunk.get('doc_title', '')
    if doc_title:
        parts.append(f'[文書: {doc_id} {doc_title}]')
    else:
        parts.append(f'[文書: {doc_id}]')

    # ドメイン情報
    if domain_info:
        parts.append(f'[分野: {domain_info["domain_name"]}]')

    # セクション情報
    sec_num = chunk.get('section_number', '')
    sec_title = chunk.get('section_title', '')
    if sec_num and sec_title:
        parts.append(f'[セクション: {sec_num} {sec_title}]')
    elif sec_num:
        parts.append(f'[セクション: {sec_num}]')

    # チャンクタイプ
    chunk_type = chunk.get('chunk_type', 'body')
    if chunk_type != 'body':
        type_labels = {
            'toc': '[目次]',
            'disclaimer': '[免責条項]',
            'references': '[参照文書一覧]',
        }
        parts.append(type_labels.get(chunk_type, f'[{chunk_type}]'))

    header = ' '.join(parts)

    # 前後文脈
    context_lines = [header]

    if prev_chunk and prev_chunk.get('doc_id') == chunk.get('doc_id'):
        prev_text = prev_chunk.get('text_cleaned') or prev_chunk.get('text', '')
        prev_summary = _brief_summary(prev_text)
        if prev_summary:
            context_lines.append(f'前文脈: {prev_summary}')

    if next_chunk and next_chunk.get('doc_id') == chunk.get('doc_id'):
        next_text = next_chunk.get('text_cleaned') or next_chunk.get('text', '')
        next_summary = _brief_summary(next_text)
        if next_summary:
            context_lines.append(f'後文脈: {next_summary}')

    return '\n'.join(context_lines)


# ========== メインの注入処理 ==========

def inject_context(
    chunks: list[dict],
    domain_map_path: str | Path,
    verbose: bool = False,
) -> list[dict]:
    """
    全チャンクにコンテキスト情報を注入する。

    chunks: Level 1 処理済みチャンクのリスト
    domain_map_path: knowledge/domain_map.yaml のパス
    verbose: 進捗表示
    返り値: コンテキスト注入済みチャンクのリスト
    """
    # ドメイン情報を読み込む
    domain_map = load_domain_map(domain_map_path)
    if verbose:
        print(f'  Loaded domain map: {len(domain_map)} doc entries')

    results = []
    injected_count = 0

    for i, chunk in enumerate(chunks):
        result = dict(chunk)
        doc_id = chunk.get('doc_id', '')

        # 前後チャンクを取得（同一文書内のみ）
        prev_chunk = None
        next_chunk = None

        if i > 0 and chunks[i - 1]['doc_id'] == doc_id:
            prev_chunk = chunks[i - 1]
        if i < len(chunks) - 1 and chunks[i + 1]['doc_id'] == doc_id:
            next_chunk = chunks[i + 1]

        # ドメイン情報を取得
        # doc_id が完全一致しない場合、基本IDで検索
        domain_info = domain_map.get(doc_id)
        if not domain_info:
            # バージョン記号を除いたIDで再試行
            # 例: "JERG-0-001" で "JERG-0-001F" に対応
            base_id = re.sub(r'[A-Z]\d*$', '', doc_id)
            domain_info = domain_map.get(base_id)

        # ドメイン情報をチャンクに付与
        if domain_info:
            result['domain'] = domain_info['domain_key']
            result['domain_name'] = domain_info['domain_name']
            result['is_primary_doc'] = domain_info.get('is_primary', False)
        else:
            result['domain'] = ''
            result['domain_name'] = ''
            result['is_primary_doc'] = False

        # 親セクション番号を付与
        sec_num = chunk.get('section_number', '')
        result['parent_section'] = _infer_parent_section(sec_num)

        # コンテキストヘッダーを生成・付与
        context_header = build_context_header(
            chunk=result,
            prev_chunk=prev_chunk,
            next_chunk=next_chunk,
            domain_info=domain_info,
        )
        result['context_header'] = context_header
        injected_count += 1

        results.append(result)

        if verbose and (i + 1) % 1000 == 0:
            print(f'  processed {i + 1}/{len(chunks)} chunks...')

    if verbose:
        print(f'  Context injected: {injected_count} chunks')

    return results


# ========== スタンドアロン実行 ==========

if __name__ == '__main__':
    base = Path(__file__).parent.parent
    input_path = base / 'data' / 'index' / 'chunks_cleaned.json'
    domain_map_path = base / 'knowledge' / 'domain_map.yaml'
    output_path = base / 'data' / 'index' / 'chunks_enriched.json'

    print('=== Level 2: コンテキスト注入 ===')
    print(f'Input:  {input_path}')
    print(f'Output: {output_path}')
    print()

    if not input_path.exists():
        print(f'ERROR: {input_path} not found. Run chunk_cleaner.py first.')
        import sys
        sys.exit(1)

    with open(input_path, encoding='utf-8') as f:
        chunks = json.load(f)
    print(f'Loaded {len(chunks)} chunks')

    results = inject_context(chunks, domain_map_path, verbose=True)

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    print(f'\nSaved to {output_path}')
