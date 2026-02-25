# チャンク品質改善設計書

## 概要

JERG文書（96件、11,462チャンク）の検索精度を向上させるための改善手法を設計する。
現状のPDF生テキスト抽出には多くの品質問題があり、検索精度の低下やLLM回答品質の劣化を引き起こしている。

本設計書では3つの改善手法（構造化JSON、相互参照グラフ、LLMリライト）を比較し、
ハルシネーションリスクと実装コストのバランスを考慮した段階的な導入計画を提案する。

---

## 1. 現状の問題点

### 1.1 PDFからの生テキスト抽出の問題

#### 目次チャンクの氾濫

全11,462チャンクのうち、約1,301チャンク（11.3%）が目次のドット列（`...`）で構成されており、検索ノイズとなっている。

**実例（JERG-0-001 chunk_id: JERG-0-001_1）:**
```
目    次
１. 総則 .......................................................... 1
１.１ 目的 ........................................................ 1
１.２ 適用範囲 .................................................... 1
```

このようなチャンクは検索に無意味なだけでなく、BM25スコアを歪める。「総則」「目的」「適用範囲」といったキーワードが目次チャンクでヒットしてしまい、本文のチャンクが埋もれる。

#### ページヘッダー・フッターの混入

約1,977チャンク（17.2%）にページ番号やヘッダー（`JERG-0-001F\n24\n`）がテキスト中に混入している。

**実例（JERG-0-001 chunk_id: JERG-0-001_50）:**
```
の作動回数を考慮すること。JERG-0-001F
24
３.２ 構造様式の設定
```

文の途中にヘッダーとページ番号が挿入されており、テキストの連続性が断たれている。

#### テーブル構造の崩壊

PDFの表がプレーンテキストに変換される際、列の対応関係が失われる。セルの内容が連結されたり、行が分断されたりする。

#### 改行位置のずれ

PDFのレイアウトに依存した改行がそのまま残り、文の途中で不自然に改行される。特に英文（Disclaimer等）で顕著。

### 1.2 相互参照の欠落

JERG文書は相互に参照し合う体系的な技術文書群であり、3,114チャンク（27.2%）に他文書への参照が含まれている。しかし現在のチャンクデータにはこの参照関係が構造化されていない。

**実例（JERG-0-017 chunk_id: JERG-0-017_146）:**
```
(2) JERG-0-050 海外部品品質確保ハンドブック
(3) JERG-0-051 海外コンポーネント品質確保ハンドブック
...
・JERG-0-036 「静電気対策ハンドブック（電子部品・装置）」
・JERG-0-049 「ソフトウェア開発標準」
・JERG-1-008 「ロケット搭載ソフトウェア開発標準」
```

この参照関係が活かされていないため、ユーザーが「品質保証の関連規格を全部教えて」と聞いても、個別の検索結果しか返せない。

### 1.3 文脈の欠落

チャンク単体では「どの文書の、どのセクションの、どういう文脈での記述か」が分からない。

**実例（JERG-0-001 chunk_id: JERG-0-001_50）:**
```
の作動回数を考慮すること。JERG-0-001F
24
３.２ 構造様式の設定
３.２.１ 金属圧力容器の構造様式
金属圧力容器は、推進薬、または加圧ガス等の流体を貯蔵し...
```

このチャンクだけ見ても、これが「宇宙用高圧ガス機器技術基準」の「3章 設計」の中の記述であるという文脈が失われている。LLMが回答生成する際、この文脈の欠落は回答品質を低下させる。

### 1.4 問題の定量的まとめ

| 問題 | 影響チャンク数 | 割合 |
|---|---|---|
| 目次ドット列チャンク | 1,301 | 11.3% |
| ページヘッダー混入 | 1,977 | 17.2% |
| 他文書への参照（未構造化） | 3,114 | 27.2% |
| 文脈情報の欠落 | 11,462 | 100% |

---

## 2. 改善手法 A: 構造化JSON（メタデータ付与）

### 概要

各チャンクに `doc_id`, `section_number`, `section_title`, `keywords`, `cross_refs` 等のメタデータを付与する。元テキストは一切変更しない。

### 現在のチャンク構造

```json
{
  "doc_id": "JERG-0-001",
  "filename": "JAXA-JERG-0-001F.pdf",
  "chunk_id": "JERG-0-001_50",
  "text": "の作動回数を考慮すること。JERG-0-001F\n24\n３.２ 構造様式の設定..."
}
```

### 改善後のチャンク構造

```json
{
  "doc_id": "JERG-0-001",
  "doc_title": "宇宙用高圧ガス機器技術基準",
  "filename": "JAXA-JERG-0-001F.pdf",
  "chunk_id": "JERG-0-001_50",
  "text": "の作動回数を考慮すること。JERG-0-001F\n24\n３.２ 構造様式の設定...",
  "section_number": "3.2",
  "section_title": "構造様式の設定",
  "cross_refs": ["JERG-0-003"],
  "chunk_type": "body",
  "is_toc": false,
  "page_number": 24
}
```

### 実装方法

```python
import re

def enrich_chunk(chunk: dict, doc_titles: dict) -> dict:
    """チャンクにメタデータを付与"""
    text = chunk["text"]

    # 1. 文書タイトル付与
    chunk["doc_title"] = doc_titles.get(chunk["doc_id"], "")

    # 2. セクション番号・タイトル抽出
    section_match = re.search(
        r'([０-９\d]+[\.\．][０-９\d]+(?:[\.\．][０-９\d]+)?)\s*(.+?)[\n\s]',
        text
    )
    if section_match:
        chunk["section_number"] = section_match.group(1)
        chunk["section_title"] = section_match.group(2).strip()

    # 3. 相互参照抽出
    refs = re.findall(r'JERG-\d+-\d+', text)
    chunk["cross_refs"] = list(set(r for r in refs if r != chunk["doc_id"]))

    # 4. 目次チャンク判定
    dot_ratio = text.count('.') / max(len(text), 1)
    chunk["is_toc"] = dot_ratio > 0.3

    # 5. チャンクタイプ分類
    if chunk["is_toc"]:
        chunk["chunk_type"] = "toc"
    elif re.search(r'免責条項|Disclaimer', text):
        chunk["chunk_type"] = "disclaimer"
    elif re.search(r'関連文書|参考文書|参照文書', text):
        chunk["chunk_type"] = "references"
    else:
        chunk["chunk_type"] = "body"

    # 6. ページ番号抽出
    page_match = re.search(r'JERG-\d+-\d+\w*\s*\n\s*(\d+)\s*\n', text)
    if page_match:
        chunk["page_number"] = int(page_match.group(1))

    return chunk
```

### メリット

- **LLM不要、高速処理**: 11,462チャンクを数分で処理可能
- **ハルシネーションリスクゼロ**: 元テキストを一切変更しない
- **検索時のフィルタリング**: `is_toc: true` のチャンクを検索対象から除外できる
- **セクション情報による検索精度向上**: 「3章の設計要件」のようなクエリに対応可能
- **回答生成時の文脈付与**: LLMに「この文書は JERG-0-001 宇宙用高圧ガス機器技術基準 の 3.2 構造様式の設定 のセクションです」と伝えられる

### デメリット

- テキスト品質自体は改善されない（ヘッダー混入、改行ずれはそのまま）
- 正規表現の精度に依存（全角・半角混在、フォーマットのばらつきへの対応が必要）
- セクション番号のないチャンクにはセクション情報を付与できない

### ハルシネーション対策

元テキストは一切変更しないため、ハルシネーション対策は不要。

### 実装コスト

**低（数時間）**
- 正規表現パターンの作成・テスト: 2-3時間
- `indexer.py` への統合: 1時間
- 検索時のフィルタリング追加: 1時間

---

## 3. 改善手法 B: 相互参照グラフ（文書間リンク）

### 概要

チャンク内の「JERG-X-XXXを参照」パターンを抽出し、文書間の関係グラフ（隣接リスト）を構築する。これにより2次・3次情報の芋づる式検索が可能になる。

### グラフ構造の例

```
JERG-0-017 (品質保証プログラム標準)
  ├─→ JERG-0-050 (海外部品品質確保ハンドブック)
  ├─→ JERG-0-051 (海外コンポーネント品質確保ハンドブック)
  ├─→ JERG-0-036 (静電気対策ハンドブック)
  ├─→ JERG-0-049 (ソフトウェア開発標準)
  │     ├─→ JERG-2-610 (宇宙機ソフトウェア開発標準)
  │     └─→ JERG-1-008 (ロケット搭載ソフトウェア開発標準)
  └─→ JERG-0-016 (...)
```

### 実装方法

```python
import json
import re
from collections import defaultdict

def build_cross_reference_graph(chunks: list[dict]) -> dict:
    """チャンクデータから相互参照グラフを構築"""

    # 隣接リスト: doc_id -> {referenced_doc_id: [chunk_ids]}
    graph = defaultdict(lambda: defaultdict(list))
    # 逆参照: doc_id -> {referencing_doc_id: [chunk_ids]}
    reverse_graph = defaultdict(lambda: defaultdict(list))

    ref_pattern = re.compile(r'JERG-\d+-\d+')

    for chunk in chunks:
        doc_id = chunk["doc_id"]
        refs = ref_pattern.findall(chunk["text"])
        other_refs = set(r for r in refs if r != doc_id)

        for ref in other_refs:
            graph[doc_id][ref].append(chunk["chunk_id"])
            reverse_graph[ref][doc_id].append(chunk["chunk_id"])

    return {
        "forward": {k: dict(v) for k, v in graph.items()},
        "reverse": {k: dict(v) for k, v in reverse_graph.items()},
    }


def get_related_docs(graph: dict, doc_id: str, depth: int = 2) -> list[str]:
    """指定文書から depth 階層までの関連文書を取得"""
    visited = set()
    queue = [(doc_id, 0)]
    result = []

    while queue:
        current, d = queue.pop(0)
        if current in visited or d > depth:
            continue
        visited.add(current)
        if current != doc_id:
            result.append({"doc_id": current, "depth": d})

        # 順参照（この文書が参照している文書）
        for ref in graph.get("forward", {}).get(current, {}):
            if ref not in visited:
                queue.append((ref, d + 1))

        # 逆参照（この文書を参照している文書）
        for ref in graph.get("reverse", {}).get(current, {}):
            if ref not in visited:
                queue.append((ref, d + 1))

    return result
```

### 検索への統合

```python
def search_with_graph(query: str, bm25_results: list, graph: dict, max_related: int = 3):
    """BM25検索結果に相互参照グラフを組み合わせる"""

    # 1. BM25で直接ヒットした文書
    hit_docs = set(r["doc_id"] for r in bm25_results[:5])

    # 2. ヒット文書から1-2ホップの関連文書を取得
    related_docs = set()
    for doc_id in hit_docs:
        for related in get_related_docs(graph, doc_id, depth=2):
            related_docs.add(related["doc_id"])

    # 3. 関連文書のチャンクからクエリに合うものを追加検索
    additional_results = search_in_docs(query, list(related_docs), max_related)

    return bm25_results + additional_results
```

### 利用シナリオ

**ユーザー質問:** 「熱設計の試験条件を教えて」

1. BM25検索 → JERG-2-200（宇宙機熱制御設計標準）がヒット
2. 相互参照グラフでJERG-2-200の参照先を確認
3. JERG-2-211（宇宙機熱設計ハンドブック）、JERG-2-320（宇宙機環境試験標準）等を発見
4. これら関連文書からも試験条件に関するチャンクを取得
5. 複数文書の情報を統合した包括的な回答を生成

### メリット

- **2次・3次情報の芋づる式検索が可能**: 直接検索では見つからない関連情報にアクセスできる
- **文書体系の構造理解**: どの文書が基幹的で、どの文書が派生的かが分かる
- **LLM不要**: 正規表現による機械的な抽出のみ
- **routing_rules.yaml との統合**: 適応型プロンプト設計（adaptive-prompt-design.md）のルーティングルールと組み合わせ、カテゴリ横断の検索が可能

### デメリット

- **明示的な参照のみ**: `JERG-X-XXX` と明記されていない暗黙的な関連は拾えない
- **グラフが深くなるとコンテキスト消費増**: 2-3ホップ先まで辿ると関連チャンクが膨大になり、LLMのコンテキストウィンドウを圧迫する
- **参照関係の質にばらつき**: 「関連文書一覧」に列挙されている参照と、本文中で言及されている参照では重要度が異なるが、現時点では区別できない

### ハルシネーション対策

元テキストを変更しないため、ハルシネーション対策は不要。参照関係は文書から機械的に抽出するため、人間のバイアスも入らない。

### 実装コスト

**低〜中（1日）**
- 参照パターンの抽出・グラフ構築: 3-4時間
- 検索エンジンへの統合: 3-4時間
- テスト・調整: 2時間

---

## 4. 改善手法 C: LLMリライト（Qiita記事化）

### 概要

各チャンクをLLMで「文脈付き・読みやすい文章」に書き換える。検索用には書き換え版を使い、ユーザーへの回答生成には原文を使う「二重保持方式」を採用する。

### 実装方法

```python
REWRITE_PROMPT = """以下は「{doc_title}」（{doc_id}）のセクション {section_info} からの抜粋です。

この内容を以下の規則に従って書き換えてください:

【厳守事項】
1. 数値（温度、圧力、寸法、係数等）を変更しない
2. 新しい情報を追加しない（元テキストにない内容は書かない）
3. 「〜と考えられる」「〜であろう」等の推測を入れない
4. 専門用語を別の用語に置き換えない（「MLI」を「断熱材」に変えない等）
5. 規格番号（JERG-X-XXX等）を変更・省略しない

【書き換え指示】
- 文書名・セクション名を冒頭に明記する
- 不自然な改行やヘッダー混入を除去する
- 箇条書きや表が崩れている場合は整形する
- 文脈が分かるように、必要に応じて前後関係を補足する（ただし元テキストの情報の範囲内で）

---
元テキスト:
{original_text}
---

書き換え版:"""


def rewrite_chunk(chunk: dict, llm_client, doc_titles: dict) -> dict:
    """チャンクをLLMでリライトする"""

    doc_title = doc_titles.get(chunk["doc_id"], chunk["doc_id"])
    section_info = chunk.get("section_title", "不明")

    prompt = REWRITE_PROMPT.format(
        doc_title=doc_title,
        doc_id=chunk["doc_id"],
        section_info=section_info,
        original_text=chunk["text"],
    )

    response = llm_client.complete(prompt)

    # 原文を保持し、リライト版を別フィールドに格納
    chunk["text_original"] = chunk["text"]
    chunk["text_rewritten"] = response

    return chunk
```

### リライト前後の例（想定）

**Before（元テキスト）:**
```
の作動回数を考慮すること。JERG-0-001F
24
３.２ 構造様式の設定
３.２.１ 金属圧力容器の構造様式
金属圧力容器は、推進薬、または加圧ガス等の流体を貯蔵し、圧力荷重を受けるシ
ェル部分、ロケット、衛星等への結合のための取付部、および加圧ガス、推進薬の充塡
排出の配管部分で構成される。
```

**After（リライト版）:**
```
【JERG-0-001 宇宙用高圧ガス機器技術基準 / 3.2 構造様式の設定】

3.2.1 金属圧力容器の構造様式

金属圧力容器は以下の3つの部分で構成される:
- シェル部分: 推進薬または加圧ガス等の流体を貯蔵し、圧力荷重を受ける
- 取付部: ロケット・衛星等への結合のための部分
- 配管部分: 加圧ガス・推進薬の充填・排出のための部分
```

### メリット

- **検索精度の大幅向上**: 多様な表現でヒットするようになる（例: 「金属タンクの構造」でもヒット）
- **人間にも読みやすい**: ヘッダー混入や改行ずれが除去される
- **文脈の自動補完**: 冒頭に文書名・セクション名が明記されるため、チャンク単体で理解可能
- **要約との統合も可能**: 長いチャンクの要約版も同時に生成できる

### デメリット

- **ハルシネーションリスクが最も高い**: LLMが内容を「改善」する過程で情報が変質する危険
- **処理時間・コスト**: 11,462チャンク x API呼び出し = 数時間〜数日（モデル・速度による）
- **品質のばらつき**: チャンクの内容によってリライト品質が安定しない
- **再現性の問題**: 同じチャンクでもLLMの応答が毎回異なる可能性

### ハルシネーション対策（5層防御）

#### 防御層 1: 原文絶対保持（二重保持方式）

```json
{
  "chunk_id": "JERG-0-001_50",
  "text": "（元テキスト - 絶対に変更しない）",
  "text_rewritten": "（リライト版 - 検索用）",
  "text_original": "（元テキストのバックアップ）"
}
```

- 検索はリライト版（`text_rewritten`）で行う
- ユーザーに返す回答の根拠は必ず原文（`text`）を使う
- これにより「検索しやすいが原文と異なる表現」がユーザーに直接伝わることを防ぐ

#### 防御層 2: 差分チェック（意味的類似度検証）

```python
from difflib import SequenceMatcher

def validate_rewrite(original: str, rewritten: str) -> bool:
    """リライト版が原文から大きく逸脱していないか検証"""

    # 1. 数値の保全チェック
    original_numbers = set(re.findall(r'\d+\.?\d*', original))
    rewritten_numbers = set(re.findall(r'\d+\.?\d*', rewritten))
    if not original_numbers.issubset(rewritten_numbers):
        return False  # 原文の数値が欠落している

    # 2. 規格番号の保全チェック
    original_refs = set(re.findall(r'JERG-\d+-\d+', original))
    rewritten_refs = set(re.findall(r'JERG-\d+-\d+', rewritten))
    if original_refs != rewritten_refs:
        return False  # 規格番号が変わっている

    # 3. テキスト類似度チェック（閾値: 0.4以上）
    # リライトなので完全一致は求めないが、大きく変わっていたら棄却
    ratio = SequenceMatcher(None, original, rewritten).ratio()
    if ratio < 0.3:
        return False  # 大きく書き換わりすぎ

    # 4. 長さチェック（原文の0.5〜2.0倍の範囲）
    len_ratio = len(rewritten) / max(len(original), 1)
    if len_ratio < 0.5 or len_ratio > 2.0:
        return False

    return True
```

#### 防御層 3: 制約付きプロンプト

プロンプトに以下の制約を明記（前述の `REWRITE_PROMPT` 参照）:
1. 数値を変更しない
2. 新しい情報を追加しない
3. 推測を入れない
4. 専門用語を置き換えない
5. 規格番号を変更・省略しない

#### 防御層 4: サンプル検証（段階的展開）

```
Phase C-1: 最初の100チャンクをリライト
  ↓ 人間がスポットチェック（20-30件を目視確認）
  ↓ 問題なければ
Phase C-2: 1,000チャンクに拡大
  ↓ 自動検証 + ランダムサンプリング（50件）
  ↓ 問題なければ
Phase C-3: 全11,462チャンクに展開
```

#### 防御層 5: 回答生成時の原文参照強制

```python
def generate_answer(query: str, search_results: list, llm_client) -> str:
    """回答生成時は必ず原文を参照する"""

    # 検索は text_rewritten でヒットしたが、
    # 回答生成には text（原文）を使う
    context_chunks = []
    for result in search_results:
        context_chunks.append({
            "doc_id": result["doc_id"],
            "section": result.get("section_title", ""),
            "content": result["text"],  # ← 原文を使用（text_rewritten ではない）
        })

    prompt = f"""以下の文書情報に基づいて質問に回答してください。
文書に書かれていない情報は「記載なし」と回答してください。

{json.dumps(context_chunks, ensure_ascii=False)}

質問: {query}
"""
    return llm_client.complete(prompt)
```

### 実装コスト

**高（数日〜）**
- リライトプロンプトの設計・チューニング: 1日
- バッチ処理パイプラインの実装: 1日
- 検証パイプラインの実装: 半日
- Phase C-1（100件テスト）: 半日
- Phase C-2〜C-3（段階展開）: 1-2日
- 検索エンジンへの統合: 半日

---

## 5. 比較表

| 項目 | A: 構造化JSON | B: 相互参照グラフ | C: LLMリライト |
|---|---|---|---|
| **実装コスト** | 低（数時間） | 低〜中（1日） | 高（数日〜） |
| **処理時間** | 数分 | 数分 | 数時間〜数日 |
| **LLM必要** | 不要 | 不要 | 必要 |
| **ハルシネーションリスク** | なし | なし | 中〜高 |
| **検索精度改善** | 中 | 高（間接検索） | 最高 |
| **2次/3次情報対応** | x | 対応（芋づる式検索可能） | 限定的（チャンク内参照のみ） |
| **元テキスト変更** | なし | なし | あり（別フィールドに保持） |
| **目次ノイズ除去** | 可能（フィルタ） | 対象外 | 可能（リライト対象外にする） |
| **ヘッダー混入除去** | 不可 | 対象外 | 可能 |
| **テーブル整形** | 不可 | 対象外 | 可能（品質にばらつき） |
| **文脈補完** | 部分的（メタデータ） | 部分的（関連文書） | 完全（文書名・セクション明記） |
| **保守コスト** | 低 | 低 | 中（モデル変更で品質変動） |

---

## 6. 推奨実装順序

### Phase 1: A（構造化JSON）+ B（相互参照グラフ）を同時実装

**期間:** 1-2日
**リスク:** なし

1. `indexer.py` にメタデータ付与処理を追加
2. 相互参照グラフを構築し `data/index/cross_refs.json` に保存
3. `searcher.py` に以下を追加:
   - `is_toc: true` チャンクの検索除外フィルタ
   - 相互参照グラフによる関連文書の追加検索
4. テスト: 既知の質問パターンで検索精度を比較

**期待効果:**
- 目次チャンクの除外だけで、検索精度が10-15%改善（推定）
- 相互参照グラフにより、単一文書では回答できなかった質問に対応可能

### Phase 2: C（LLMリライト）の小規模テスト

**期間:** 2-3日
**リスク:** 低（100件限定のため）

1. 品質が悪いチャンク（ヘッダー混入、テーブル崩壊）を100件選定
2. リライトプロンプトを設計・チューニング
3. リライト実行 + 自動検証パイプライン
4. 人間によるスポットチェック（20-30件）
5. 検索精度の比較テスト

### Phase 3: C の段階的展開

**期間:** 1週間
**リスク:** 中（品質管理が重要）

1. 検証結果が良好なら1,000件に拡大
2. 自動検証で棄却されたチャンクの分析・プロンプト改善
3. 全チャンクに展開
4. A + B + C の組み合わせで検索精度を最終評価

### 最終アーキテクチャ（A + B + C 統合）

```
ユーザー質問
    │
    ▼
┌─────────────────────────────────┐
│  1. BM25検索（text_rewritten）   │  ← C のリライト版で検索
│  2. メタデータフィルタ           │  ← A の構造化で目次除外
│  3. 相互参照グラフ展開           │  ← B でヒット文書の関連文書も検索
│  4. リランキング                 │
└───────────────┬─────────────────┘
                │
                ▼
┌─────────────────────────────────┐
│  回答生成（text = 原文を使用）    │  ← ハルシネーション防止
│  + メタデータで文脈付与           │  ← A のセクション情報
│  + 関連文書リストを提示           │  ← B の相互参照
└─────────────────────────────────┘
```

---

## 7. ハルシネーション防止の設計原則

本プロジェクトでは以下の5つの原則を全ての改善手法に適用する。

### 原則 1: 原文は絶対に上書きしない

```
chunks.json の text フィールドは不可侵
リライト版は text_rewritten という別フィールドに格納
バックアップとして text_original も保持
```

これにより、万が一リライト品質に問題があっても、原文に影響しない。

### 原則 2: 検索はリライト版、回答生成は原文

```
検索パイプライン: query → BM25(text_rewritten) → ヒットチャンク
回答パイプライン: ヒットチャンク.text（原文） → LLM → 回答
```

検索精度はリライト版の恩恵を受けつつ、ユーザーに返す情報は原文に基づく。

### 原則 3: 数値・固有名詞・規格番号は変更不可

リライトプロンプトに以下を明記:
- 温度・圧力・寸法・係数等の数値 → 変更禁止
- JERG番号・JIS番号・NASA規格番号 → 変更禁止
- 専門用語（MLI、OSR等） → 別の表現に置き換え禁止

自動検証でこれらの保全をチェックする。

### 原則 4: 自動検証パイプライン

```python
def validation_pipeline(original: str, rewritten: str) -> dict:
    """リライト品質の自動検証"""
    return {
        "numbers_preserved": check_numbers(original, rewritten),
        "refs_preserved": check_references(original, rewritten),
        "similarity_score": compute_similarity(original, rewritten),
        "length_ratio": len(rewritten) / max(len(original), 1),
        "pass": all([...]),  # 全チェック合格で True
    }
```

検証に失敗したチャンクはリライト版を使用せず、原文のまま運用する。

### 原則 5: 人間スポットチェックの仕組み

- Phase C-1（100件）: 20-30件を目視確認
- Phase C-2（1,000件）: ランダム50件を目視確認
- Phase C-3（全件）: 自動検証で棄却されたチャンクを全件目視確認
- 運用後: 月次でランダム20件を抜き取りチェック

---

## 8. 次のアクション

1. **即座に着手**: 手法 A（構造化JSON）の実装を開始
   - `src/indexer.py` に `enrich_chunk()` 関数を追加
   - `data/index/chunks.json` を再構築
2. **同時並行**: 手法 B（相互参照グラフ）の実装
   - `src/graph.py` を新規作成
   - `data/index/cross_refs.json` を生成
3. **計画策定**: 手法 C（LLMリライト）のテスト計画を具体化
   - リライト対象100件の選定基準を決定
   - 使用するLLMモデルとコスト見積もり
