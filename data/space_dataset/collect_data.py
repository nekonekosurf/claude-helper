#!/usr/bin/env python3
"""
宇宙データ自動収集スクリプト
- NASA NTRS（論文リポジトリ）からのタイトル・要旨収集
- Wikipedia 宇宙関連記事からのデータ抽出
- JAXA公開プレスリリースの収集（HTMLパース）
- 収集データをJSONL形式で出力
"""

import json
import time
import re
import urllib.request
import urllib.parse
import urllib.error
from pathlib import Path
from datetime import datetime

OUTPUT_DIR = Path(__file__).parent / 'collected'
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

REQUEST_DELAY = 1.5  # API礼儀上の待機秒数（1秒以上）
USER_AGENT = 'SpaceDatasetCollector/1.0 (educational; contact: user@example.com)'

# ===================== ユーティリティ =====================

def http_get(url: str, timeout: int = 15) -> str:
    """シンプルなHTTP GETリクエスト（依存ライブラリなし）"""
    req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT,
                                               'Accept': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode('utf-8', errors='replace')
    except urllib.error.HTTPError as e:
        print(f'    [HTTP {e.code}] {url}')
        return ''
    except Exception as e:
        print(f'    [ERROR] {url}: {e}')
        return ''


def clean_text(text: str) -> str:
    """テキストのクリーニング"""
    text = re.sub(r'\s+', ' ', text).strip()
    text = re.sub(r'\[\d+\]', '', text)  # Wikipedia脚注番号除去
    text = re.sub(r'==+[^=]+=+', '', text)  # Wikiセクションヘッダ除去
    return text


def save_jsonl(records: list[dict], filepath: Path):
    with open(filepath, 'a', encoding='utf-8') as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')
    print(f'  -> {filepath.name} に {len(records)} 件追記')


# ===================== NASA NTRS 収集 =====================

def collect_nasa_ntrs(keywords: list[str], max_per_keyword: int = 10) -> list[dict]:
    """
    NASA NTRS（Technical Reports Server）API から論文を収集
    API: https://ntrs.nasa.gov/api/citations/search
    """
    records = []
    base_url = 'https://ntrs.nasa.gov/api/citations/search'

    for keyword in keywords:
        print(f'  NASA NTRS: "{keyword}" を検索中...')
        params = urllib.parse.urlencode({
            'q': keyword,
            'rows': max_per_keyword,
            'start': 0,
        })
        url = f'{base_url}?{params}'
        raw = http_get(url)
        if not raw:
            time.sleep(REQUEST_DELAY)
            continue

        try:
            data = json.loads(raw)
            results = data.get('results', [])
            for item in results:
                title    = item.get('title', '').strip()
                abstract = item.get('abstract', '').strip()
                authors  = ', '.join(
                    a.get('name', '') for a in item.get('authorAffiliations', [])
                    if isinstance(a, dict) and 'name' in a
                )[:200]
                pub_year = str(item.get('publicationDate', '')[:4])
                ntrs_id  = item.get('id', '')
                doc_url  = f'https://ntrs.nasa.gov/citations/{ntrs_id}' if ntrs_id else ''

                if not title or not abstract or len(abstract) < 50:
                    continue

                # 要旨の要約タスクとして構築
                records.append({
                    'instruction': '以下の宇宙関連論文の要旨（英語）を日本語で要約してください',
                    'input': f'タイトル: {title}\n著者: {authors}\n発行年: {pub_year}\n\n要旨（英語）:\n{abstract[:600]}',
                    'output': (
                        f'この論文「{title}」（{pub_year}年）は宇宙・航空宇宙分野の研究です。\n\n'
                        f'要旨の内容：{abstract[:300]}\n\n'
                        f'[出典: NASA NTRS {doc_url}]'
                    ),
                    '_source': 'nasa_ntrs',
                    '_keyword': keyword,
                })
        except (json.JSONDecodeError, KeyError) as e:
            print(f'    [PARSE ERROR] {e}')

        time.sleep(REQUEST_DELAY)

    print(f'  NASA NTRS 収集完了: {len(records)} 件')
    return records


# ===================== Wikipedia 収集 =====================

def collect_wikipedia(article_titles: list[str], lang: str = 'ja') -> list[dict]:
    """
    Wikipedia APIから宇宙関連記事の冒頭・セクションを収集
    MediaWiki Action API を使用
    """
    records = []
    api_url = f'https://{lang}.wikipedia.org/w/api.php'

    for title in article_titles:
        print(f'  Wikipedia ({lang}): "{title}" を取得中...')
        params = urllib.parse.urlencode({
            'action': 'query',
            'prop': 'extracts',
            'exintro': '1',
            'explaintext': '1',
            'titles': title,
            'format': 'json',
            'redirects': '1',
        })
        url = f'{api_url}?{params}'
        raw = http_get(url)
        if not raw:
            time.sleep(REQUEST_DELAY)
            continue

        try:
            data = json.loads(raw)
            pages = data.get('query', {}).get('pages', {})
            for page_id, page in pages.items():
                if page_id == '-1':
                    print(f'    記事が見つかりません: {title}')
                    continue
                real_title = page.get('title', title)
                extract = clean_text(page.get('extract', ''))
                if len(extract) < 100:
                    continue

                # 冒頭300文字を説明文として使用
                short_desc = extract[:400]

                records.append({
                    'instruction': '以下の宇宙用語・概念についてわかりやすく説明してください',
                    'input': real_title,
                    'output': short_desc + ('...' if len(extract) > 400 else ''),
                    '_source': f'wikipedia_{lang}',
                    '_article': real_title,
                })
        except (json.JSONDecodeError, KeyError) as e:
            print(f'    [PARSE ERROR] {e}')

        time.sleep(REQUEST_DELAY)

    print(f'  Wikipedia 収集完了: {len(records)} 件')
    return records


# ===================== JAXA プレスリリース収集 =====================

def collect_jaxa_press(max_items: int = 20) -> list[dict]:
    """
    JAXAのプレスリリースフィードから最新情報を収集
    JAXA公式サイト: https://www.jaxa.jp/press/
    ここでは公開RSSを使用（存在する場合）
    """
    records = []
    # JAXA の公開プレスリリースRSS（英語）
    rss_url = 'https://global.jaxa.jp/rss/news.rss'
    print(f'  JAXA プレスリリース RSS 取得中: {rss_url}')

    raw = http_get(rss_url)
    if not raw:
        print('  [SKIP] JAXA RSS 取得失敗（ネットワーク制限の可能性）')
        return []

    # RSS XMLをシンプルな正規表現でパース
    items = re.findall(r'<item>(.*?)</item>', raw, re.DOTALL)
    count = 0
    for item_xml in items[:max_items]:
        title_m   = re.search(r'<title>(.*?)</title>', item_xml)
        desc_m    = re.search(r'<description>(.*?)</description>', item_xml, re.DOTALL)
        link_m    = re.search(r'<link>(.*?)</link>', item_xml)
        date_m    = re.search(r'<pubDate>(.*?)</pubDate>', item_xml)

        title = clean_text(title_m.group(1)) if title_m else ''
        desc  = clean_text(re.sub(r'<[^>]+>', '', desc_m.group(1))) if desc_m else ''
        link  = link_m.group(1).strip() if link_m else ''
        date  = date_m.group(1).strip() if date_m else ''

        if not title or not desc or len(desc) < 30:
            continue

        records.append({
            'instruction': '以下のJAXAプレスリリースを要約し、宇宙工学的な観点から重要なポイントを説明してください',
            'input': f'タイトル: {title}\n日付: {date}\n\n概要:\n{desc[:500]}',
            'output': (
                f'JAXA発表「{title}」（{date}）について説明します。\n\n'
                f'{desc[:300]}\n\n'
                f'この発表は日本の宇宙開発において重要なマイルストーンです。'
                f'[出典: {link}]'
            ),
            '_source': 'jaxa_press',
            '_date': date,
        })
        count += 1

    print(f'  JAXA プレスリリース 収集完了: {count} 件')
    return records


# ===================== 宇宙用語ペア生成（静的データから拡張） =====================

ADDITIONAL_TERMS = [
    ('GOES衛星', 'GOES（Geostationary Operational Environmental Satellite）は、アメリカNOAAとNASAが運用する静止気象衛星。高度35,786kmのGEO軌道から北米・大西洋・太平洋域を連続監視。可視・赤外センサによる気象観測、雷放電マッピング（GLM）、宇宙天気観測（SUVI等）を実施。GOES-Rシリーズ（2016〜）は時間分解能1分（フルディスク5分）で、竜巻・ハリケーン・山火事の早期検出に貢献。'),
    ('相対航法（Relative Navigation）', '相対航法とは、宇宙機がターゲット（他の衛星、ISSなど）に対する相対位置・速度を推定する航法技術。センサ：LIDAR（レーザー測距）、可視カメラ（特徴点追跡）、GPS相対測位（RTK）。アルゴリズム：HCW方程式に基づくEKF。ランデブー・ドッキング、デブリ捕獲、フォーメーションフライトに必須。ISS補給機（HTV/Dragon）は自律相対航法でアプローチし、最終段階でロボットアームまたは直接ドッキング。'),
    ('RTG（放射性同位体熱電気発生器）', 'RTG（Radioisotope Thermoelectric Generator）は、放射性同位体（Pu-238等）の崩壊熱を熱電変換素子（SiGe合金等）で電力に変換する宇宙用電源。太陽光が届かない外惑星・深宇宙探査機に使用。変換効率6〜8%（低いが数十年間安定発電）。MMRTG（マルチミッションRTG）：キュリオシティ・パーサビアランスに搭載（110W出力）。打ち上げ安全性：フェイルセーフカプセル設計、打ち上げ事故時の拡散防止。'),
    ('GNSS干渉・スプーフィング', 'GNSS（GPS等）に対する妨害には干渉（Jamming）とスプーフィング（Spoofing）がある。Jamming：GNSS帯域（L1:1575.42MHz等）に強力な電波を送信して受信妨害。宇宙機への影響：衛星搭載GPS受信機の測位不能。対策：アンチジャミングアンテナ（CRPA）、信号多様性（マルチコンステレーション）。Spoofing：偽の測位信号を送信して誤った位置情報を与える。対策：搬送波位相検証、受信電力監視、慣性航法との統合。'),
    ('月面着陸精度（HDA）', 'HDA（Hazard Detection and Avoidance）は月面着陸船が安全な着陸地点をリアルタイムに選定・回避する自律技術。月面には5m以上の岩石や傾斜地が多く、事前のHRSC（高分解能ステレオカメラ）マッピングだけでは不十分。センサ：LIDAR（高度・地形マッピング）、高解像度カメラ。処理：リアルタイム地形マップ生成→安全領域選択→機動量計算。NASA ALHAT（Autonomous Landing and Hazard Avoidance Technology）がアルテミス用に開発。着陸精度：従来数kmから±100m以内へ向上。'),
    ('宇宙機の数値シミュレーション（STK）', 'STK（Systems Tool Kit、AGI/Ansys）は宇宙機の軌道・通信・カバレッジ・センサ性能を統合解析する業界標準ツール。主要機能：軌道伝播（SGP4・HPOP）、可視解析、リンクバジェット、カバレッジ統計、グラウンドトラック、センサ視野。Pythonインターフェース（STK Engine）やAPIで自動化も可能。NASA/ESA/JAXA含む世界中の宇宙機関・企業が使用。教育版はAGIウェブサイトで無料提供。'),
]

def build_additional_term_records() -> list[dict]:
    records = []
    for term, explanation in ADDITIONAL_TERMS:
        records.append({
            'instruction': '以下の宇宙用語を説明してください',
            'input': term,
            'output': explanation,
            '_source': 'static_additional',
        })
    return records


# ===================== メイン =====================

def main():
    print('=== 宇宙データセット自動収集スクリプト ===')
    print(f'開始時刻: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
    print(f'出力先: {OUTPUT_DIR}')
    print()

    all_records = []

    # 1. 追加用語データ（静的）
    print('--- 追加用語データ (静的) ---')
    extra = build_additional_term_records()
    all_records.extend(extra)
    print(f'  追加用語: {len(extra)} 件')

    # 2. Wikipedia 日本語記事
    print('\n--- Wikipedia 日本語記事 ---')
    wiki_jp_titles = [
        '人工衛星', '静止軌道', 'ホーマン遷移軌道', 'ラグランジュ点',
        '宇宙ゴミ', 'スペースデブリ', '電気推進', 'イオンエンジン',
        '合成開口レーダー', '地球観測衛星', '宇宙条約', 'ヴァン・アレン帯',
        '太陽同期軌道', '国際宇宙ステーション', 'ロケット', '宇宙天気',
        'GPS衛星', '宇宙服', '重力アシスト', 'キュービックサット',
    ]
    wiki_records = collect_wikipedia(wiki_jp_titles, lang='ja')
    all_records.extend(wiki_records)

    # 3. Wikipedia 英語記事（英→日説明タスク）
    print('\n--- Wikipedia 英語記事 ---')
    wiki_en_titles = [
        'Attitude control system', 'Orbital mechanics', 'Satellite bus',
        'Telemetry', 'Debris removal', 'Hall effect thruster',
        'Star tracker', 'Reaction wheel', 'Ground station',
        'Launch window',
    ]
    wiki_en_records = collect_wikipedia(wiki_en_titles, lang='en')
    # 英語記事は instruction を変える
    for r in wiki_en_records:
        r['instruction'] = '以下の宇宙関連概念（英語記事）を日本語で説明してください'
    all_records.extend(wiki_en_records)

    # 4. NASA NTRS 論文要旨
    print('\n--- NASA NTRS 論文収集 ---')
    ntrs_keywords = [
        'attitude control satellite', 'orbital mechanics', 'debris removal',
        'telemetry spacecraft', 'electric propulsion', 'star tracker',
        'thermal control satellite', 'GPS orbit determination',
        'cubesat design', 'launch vehicle performance',
    ]
    ntrs_records = collect_nasa_ntrs(ntrs_keywords, max_per_keyword=5)
    all_records.extend(ntrs_records)

    # 5. JAXA プレスリリース
    print('\n--- JAXA プレスリリース ---')
    jaxa_records = collect_jaxa_press(max_items=15)
    all_records.extend(jaxa_records)

    # 保存
    print(f'\n--- 収集データ保存 ---')
    print(f'総収集件数: {len(all_records)} 件')

    # ソース別に分類して保存
    source_groups: dict[str, list] = {}
    for r in all_records:
        src = r.pop('_source', 'unknown')
        r.pop('_keyword', None)
        r.pop('_article', None)
        r.pop('_date', None)
        source_groups.setdefault(src, []).append(r)

    for src, recs in source_groups.items():
        fp = OUTPUT_DIR / f'collected_{src}.jsonl'
        save_jsonl(recs, fp)

    # 全件をひとつのファイルにも保存
    all_fp = OUTPUT_DIR / 'collected_all.jsonl'
    save_jsonl(all_records, all_fp)

    print(f'\n完了: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
    print(f'収集データは {OUTPUT_DIR} に保存されました')
    print('\n使い方:')
    print('  python collect_data.py  → データ収集実行')
    print('  収集後に check_data.py で品質確認')
    print('  prepare_data.py でファインチューニング形式に変換')


if __name__ == '__main__':
    main()
