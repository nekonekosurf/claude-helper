"""
Embeddingモデル選択ガイド（宇宙分野RAG向け）

Raspberry Pi (ARM64) + ローカル実行の制約での最適モデル選択。

選択肢の比較と選定ロジックを含む実用スクリプト。

実行方法:
    # モデル比較テスト
    uv run python -m space_rag.embedding_selector compare

    # 推奨モデルでベンチマーク
    uv run python -m space_rag.embedding_selector bench

    # 埋め込みの構築
    uv run python -m space_rag.embedding_selector build
"""

from __future__ import annotations

import sys
import time
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# ============================================================
# モデル比較表（設計判断の根拠）
# ============================================================

EMBEDDING_MODELS = {
    # ---- 軽量・高速（Raspberry Pi向け） ----

    "paraphrase-multilingual-MiniLM-L12-v2": {
        "provider": "sentence-transformers",
        "dim": 384,
        "size_mb": 470,
        "languages": ["ja", "en", "multilingual"],
        "speed_cpu": "fast",    # ~1000 sentences/sec (CPU)
        "quality": "good",      # 汎用精度は高い
        "space_quality": "moderate",  # 宇宙専門用語への適応は中程度
        "notes": "現在のプロジェクトで使用中。日本語+英語の両方に対応。",
        "recommended_for": "バランス重視",
        "fastembed_id": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
    },

    "all-MiniLM-L6-v2": {
        "provider": "sentence-transformers",
        "dim": 384,
        "size_mb": 91,
        "languages": ["en"],
        "speed_cpu": "very_fast",  # ~14000 sentences/sec
        "quality": "moderate",
        "space_quality": "moderate",
        "notes": "英語専用。最速・最軽量。英語文書中心なら十分。",
        "recommended_for": "英語文書・速度最優先",
        "fastembed_id": "sentence-transformers/all-MiniLM-L6-v2",
    },

    "BAAI/bge-m3": {
        "provider": "BAAI",
        "dim": 1024,
        "size_mb": 1170,
        "languages": ["ja", "en", "100+ languages"],
        "speed_cpu": "moderate",
        "quality": "excellent",
        "space_quality": "good",
        "notes": (
            "多言語対応の高精度モデル。Dense+Sparse+Colbert の3種類の埋め込みを"
            "同時生成できる（ColbertはRAG精度を大幅向上）。"
            "1.1GBと大きいがRaspberry Pi 4(4GB+)でも動作可能。"
        ),
        "recommended_for": "精度最優先（メモリ2GB以上）",
        "fastembed_id": "BAAI/bge-m3",
    },

    "intfloat/multilingual-e5-large": {
        "provider": "intfloat",
        "dim": 1024,
        "size_mb": 1120,
        "languages": ["ja", "en", "100+ languages"],
        "speed_cpu": "slow",
        "quality": "excellent",
        "space_quality": "good",
        "notes": (
            "E5シリーズの多言語大型モデル。クエリに 'query:' / パッセージに 'passage:' "
            "プレフィックスが必要。精度は高いが重い。"
        ),
        "recommended_for": "精度最優先・英日混在文書",
        "fastembed_id": "intfloat/multilingual-e5-large",
        "prefix": {"query": "query: ", "passage": "passage: "},
    },

    "BAAI/bge-small-en-v1.5": {
        "provider": "BAAI",
        "dim": 384,
        "size_mb": 134,
        "languages": ["en"],
        "speed_cpu": "fast",
        "quality": "good",
        "space_quality": "good",
        "notes": "英語専用の小型高精度モデル。英語文書RAGのコスパ最高。",
        "recommended_for": "英語文書・精度とのバランス",
        "fastembed_id": "BAAI/bge-small-en-v1.5",
    },

    "BAAI/bge-large-en-v1.5": {
        "provider": "BAAI",
        "dim": 1024,
        "size_mb": 1340,
        "languages": ["en"],
        "speed_cpu": "slow",
        "quality": "excellent",
        "space_quality": "excellent",
        "notes": (
            "英語専用の大型高精度モデル。宇宙工学の英語文書には最高精度。"
            "NASA/ESA文書（英語）を主に扱う場合の最有力候補。"
        ),
        "recommended_for": "英語文書・精度最優先",
        "fastembed_id": "BAAI/bge-large-en-v1.5",
    },
}

# ============================================================
# Raspberry Pi 向けの推奨設定
# ============================================================

RECOMMENDATIONS = {
    "raspi_4gb": {
        "model": "paraphrase-multilingual-MiniLM-L12-v2",
        "reason": "384次元・470MB。RAM 4GBでJERG日本語文書と英語文書を両方扱える最適解。",
        "chunk_size": 800,
        "overlap": 128,
    },
    "raspi_8gb_japanese": {
        "model": "BAAI/bge-m3",
        "reason": "日本語+英語を高精度で処理。1.1GBだが8GBメモリなら余裕。",
        "chunk_size": 512,
        "overlap": 64,
    },
    "raspi_english_only": {
        "model": "BAAI/bge-small-en-v1.5",
        "reason": "英語文書専用。134MBと軽量で精度も良い。NASA/ESA文書に最適。",
        "chunk_size": 800,
        "overlap": 128,
    },
}

# 現在プロジェクトで使用中のモデル
CURRENT_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"
CURRENT_FASTEMBED_ID = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


# ============================================================
# Vector DB 比較
# ============================================================

VECTOR_DBS = {
    "numpy_flat": {
        "description": "NumPy配列による全探索（現在の実装）",
        "pros": ["依存なし", "実装簡単", "メモリ効率良い"],
        "cons": ["大規模では遅い（O(n)）", "永続化は .npy ファイルのみ"],
        "suitable_for": "10万チャンク以下",
        "recommended": True,
        "notes": "JERG 11,462チャンクなら充分速い。宇宙分野RAGなら最初はこれで十分。",
    },
    "chromadb": {
        "description": "ChromaDB - ローカル完結型ベクトルDB",
        "pros": ["pip install chroma で即使える", "永続化・CRUD対応", "フィルタリング機能"],
        "cons": ["メタデータ検索が遅い場合あり", "50GB以上で不安定報告あり"],
        "suitable_for": "10万〜100万チャンク",
        "recommended": False,
        "install": "uv add chromadb",
    },
    "faiss": {
        "description": "Meta FAISS - 高速近似最近傍探索",
        "pros": ["GPU対応", "10億規模でも高速", "HNSW/IVFなど多様なインデックス"],
        "cons": ["メタデータ管理は別途必要", "ARM64ビルドが複雑"],
        "suitable_for": "100万チャンク以上",
        "recommended": False,
        "install": "uv add faiss-cpu  # ARM64では pip install faiss-cpu --no-binary :all: が必要",
    },
    "qdrant": {
        "description": "Qdrant - Rustベースの本格ベクトルDB",
        "pros": ["高速・スケーラブル", "フィルタリング高速", "REST APIあり"],
        "cons": ["別プロセスが必要", "設定が複雑"],
        "suitable_for": "本番環境・大規模",
        "recommended": False,
        "install": "Docker: docker run -p 6333:6333 qdrant/qdrant",
    },
}


# ============================================================
# 実装関数
# ============================================================

def compare_models():
    """モデル比較表を表示する"""
    print("=" * 80)
    print("Embeddingモデル比較 (宇宙分野RAG向け)")
    print("=" * 80)
    print()

    for name, info in EMBEDDING_MODELS.items():
        current = " [現在使用中]" if name == CURRENT_MODEL else ""
        print(f"【{name}】{current}")
        print(f"  次元数: {info['dim']}  サイズ: {info['size_mb']}MB")
        print(f"  言語: {', '.join(info['languages'])}")
        print(f"  CPU速度: {info['speed_cpu']}  精度: {info['quality']}")
        print(f"  宇宙分野適合: {info['space_quality']}")
        print(f"  推奨用途: {info['recommended_for']}")
        print(f"  備考: {info['notes']}")
        print()

    print("=" * 80)
    print("Raspberry Pi 向け推奨設定")
    print("=" * 80)
    for env, rec in RECOMMENDATIONS.items():
        print(f"\n【{env}】")
        print(f"  モデル: {rec['model']}")
        print(f"  理由: {rec['reason']}")
        print(f"  チャンクサイズ: {rec['chunk_size']}文字 / オーバーラップ: {rec['overlap']}文字")

    print()
    print("【結論】")
    print("  現在の構成（paraphrase-multilingual-MiniLM-L12-v2）はRaspberry Piに最適。")
    print("  宇宙英語文書が主体で精度を上げたい場合は BAAI/bge-m3 に移行を検討。")


def compare_vector_dbs():
    """ベクトルDB比較表を表示する"""
    print("=" * 80)
    print("Vector DB比較")
    print("=" * 80)
    print()
    for name, info in VECTOR_DBS.items():
        rec = " [推奨]" if info.get("recommended") else ""
        print(f"【{name}】{rec}")
        print(f"  {info['description']}")
        print(f"  用途: {info['suitable_for']}")
        print(f"  利点: {', '.join(info['pros'])}")
        print(f"  欠点: {', '.join(info['cons'])}")
        if "notes" in info:
            print(f"  備考: {info['notes']}")
        if "install" in info:
            print(f"  インストール: {info['install']}")
        print()

    print("【結論】")
    print("  宇宙分野RAG（〜数十万チャンク）ならNumPy flat searchが最適解。")
    print("  100万チャンクを超える場合は FAISS の HNSW インデックスへ移行。")


def benchmark_current_model(n_queries: int = 10):
    """現在のモデルでベンチマークを実行する"""
    try:
        from fastembed import TextEmbedding
        import numpy as np
    except ImportError:
        print("fastembed がインストールされていません: uv add fastembed")
        return

    print(f"ベンチマーク: {CURRENT_FASTEMBED_ID}")
    print(f"テストクエリ数: {n_queries}")

    # テストクエリ（宇宙分野）
    test_queries = [
        "LEO衛星の熱制御設計",
        "thermal control system spacecraft",
        "リアクションホイールの制御アルゴリズム",
        "FMEA analysis attitude control",
        "MLI多層断熱材の設計基準",
        "比推力とデルタVの関係",
        "CubeSat power budget solar array",
        "JERG構造設計標準の要求事項",
        "spacecraft structural vibration testing",
        "宇宙機の電力系設計",
    ][:n_queries]

    print("\nモデルをロード中...")
    load_start = time.time()
    model = TextEmbedding(CURRENT_FASTEMBED_ID)
    load_time = time.time() - load_start
    print(f"ロード時間: {load_time:.2f}秒")

    # 埋め込み計算
    print("\n埋め込み計算中...")
    embed_start = time.time()
    embeddings = list(model.embed(test_queries))
    embed_time = time.time() - embed_start

    embeddings_np = np.array(embeddings)
    print(f"計算時間: {embed_time:.2f}秒 ({embed_time/len(test_queries)*1000:.0f}ms/query)")
    print(f"埋め込み次元: {embeddings_np.shape}")
    print(f"クエリ/秒: {len(test_queries)/embed_time:.1f}")

    # コサイン類似度チェック
    print("\n類似度サンプル:")
    e0 = embeddings_np[0] / np.linalg.norm(embeddings_np[0])
    for i, (q, emb) in enumerate(zip(test_queries, embeddings)):
        emb_norm = emb / np.linalg.norm(emb)
        sim = float(e0 @ emb_norm)
        print(f"  [{i}] sim={sim:.3f} : {q}")


def build_space_embeddings():
    """
    宇宙知識ベースの埋め込みを構築する。

    既存のJERGインデックスの再構築は src.vector_search.build_embeddings() を使用し、
    space_kb/ の独立インデックスをここで構築する。
    """
    try:
        from fastembed import TextEmbedding
        import numpy as np
        import json
    except ImportError:
        print("fastembed がインストールされていません: uv add fastembed")
        return

    base_dir = Path(__file__).parent.parent
    kb_dir = base_dir / "data" / "space_kb"

    if not kb_dir.exists() or not any(kb_dir.glob("*.jsonl")):
        print(f"space_kb が空です: {kb_dir}")
        print("先に knowledge_builder を実行してください:")
        print("  uv run python -m space_rag.knowledge_builder glossary")
        return

    # 全チャンクを読み込む
    chunks = []
    for jsonl_file in sorted(kb_dir.glob("*.jsonl")):
        with open(jsonl_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    chunks.append(json.loads(line))

    if not chunks:
        print("チャンクが見つかりません")
        return

    print(f"チャンク数: {len(chunks)}")
    texts = [c["text"] for c in chunks]
    chunk_ids = [c["chunk_id"] for c in chunks]

    print(f"モデルをロード中: {CURRENT_FASTEMBED_ID}")
    model = TextEmbedding(CURRENT_FASTEMBED_ID)

    print("埋め込み計算中...")
    start = time.time()
    embeddings = list(model.embed(texts, batch_size=32))
    elapsed = time.time() - start
    print(f"完了: {elapsed:.1f}秒 ({len(texts)/elapsed:.0f} chunks/sec)")

    emb_matrix = np.array(embeddings, dtype=np.float32)

    # 保存
    emb_dir = base_dir / "data" / "embeddings_space_kb"
    emb_dir.mkdir(parents=True, exist_ok=True)

    np.save(str(emb_dir / "vectors.npy"), emb_matrix)
    with open(emb_dir / "chunk_ids.json", "w", encoding="utf-8") as f:
        json.dump(chunk_ids, f, ensure_ascii=False)

    print(f"保存: {emb_dir}/vectors.npy  shape={emb_matrix.shape}")


# ============================================================
# CLI
# ============================================================

def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    command = sys.argv[1] if len(sys.argv) > 1 else "compare"

    if command == "compare":
        compare_models()
        print()
        compare_vector_dbs()
    elif command == "bench":
        benchmark_current_model()
    elif command == "build":
        build_space_embeddings()
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
