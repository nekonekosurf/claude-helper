"""相互参照グラフ構築 - JERG文書間の参照関係を抽出・管理

data/index/chunks.json から「JERG-X-YYY」パターンの参照を抽出し、
文書間の有向グラフを構築する。
"""

import json
import re
from collections import defaultdict
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
INDEX_DIR = DATA_DIR / "index"
GRAPH_PATH = DATA_DIR / "cross_references.json"

# chunks.json の候補パス（index優先、なければ data/ 直下）
_CHUNKS_CANDIDATES = [
    INDEX_DIR / "chunks.json",
    DATA_DIR / "chunks.json",
]

# JERG文書番号のパターン（JERG-0-039-TM001 等の拡張形式も含む）
_JERG_PATTERN = re.compile(r'JERG-\d{1,2}-\d{3}(?:-[A-Z]+\d+[A-Z]?)?')

_graph: dict | None = None


def _find_chunks_path() -> Path:
    """chunks.json のパスを解決する（index優先、なければ data/ 直下）"""
    for p in _CHUNKS_CANDIDATES:
        if p.exists():
            return p
    raise FileNotFoundError(
        "chunks.json が見つかりません。候補: "
        + ", ".join(str(p) for p in _CHUNKS_CANDIDATES)
    )


def _load_doc_ids() -> list[str]:
    """chunks.json から全文書IDを取得（長い順でソート＝最長一致用）"""
    chunks_path = _find_chunks_path()
    with open(chunks_path, encoding="utf-8") as f:
        chunks = json.load(f)
    doc_ids = sorted(set(c["doc_id"] for c in chunks), key=len, reverse=True)
    return doc_ids


def _resolve_ref(ref: str, all_doc_ids: list[str]) -> str | None:
    """
    テキスト中の参照文字列を既知の doc_id に解決する。
    最長一致を使い、JERG-0-039 が JERG-0-039-TM001 にも対応するようにする。
    """
    for doc_id in all_doc_ids:
        if ref.startswith(doc_id) or doc_id.startswith(ref):
            return doc_id
    return None


def build_graph() -> dict:
    """
    全チャンクを走査して文書間の相互参照グラフを構築する。

    Returns:
        {
            "nodes": {doc_id: {"doc_id": str, "out_refs": [...], "in_refs": [...]}},
            "edges": [{"from": doc_id, "to": doc_id, "count": int, "chunks": [...]}],
            "total_edges": int,
        }
    """
    chunks_path = _find_chunks_path()
    with open(chunks_path, encoding="utf-8") as f:
        chunks = json.load(f)

    all_doc_ids = _load_doc_ids()
    all_doc_id_set = set(all_doc_ids)

    # エッジ: (from_doc, to_doc) → {count, chunks}
    edge_map: dict[tuple[str, str], dict] = defaultdict(lambda: {"count": 0, "chunks": []})

    for chunk in chunks:
        source_doc = chunk["doc_id"]
        chunk_id = chunk["chunk_id"]
        text = chunk["text"]

        # テキストから全JERG参照を抽出
        found_refs = _JERG_PATTERN.findall(text)
        seen_targets = set()

        for ref in found_refs:
            target_doc = _resolve_ref(ref, all_doc_ids)
            if target_doc is None:
                continue
            if target_doc == source_doc:
                continue  # 自己参照はスキップ
            if target_doc not in all_doc_id_set:
                continue
            if target_doc in seen_targets:
                # 同一チャンク内の重複参照はカウントしない（チャンクは1回だけ記録）
                continue

            seen_targets.add(target_doc)
            key = (source_doc, target_doc)
            edge_map[key]["count"] += 1
            edge_map[key]["chunks"].append(chunk_id)

    # ノード情報を構築
    nodes: dict[str, dict] = {}
    out_refs: dict[str, list] = defaultdict(list)
    in_refs: dict[str, list] = defaultdict(list)

    edges = []
    for (from_doc, to_doc), info in sorted(edge_map.items()):
        edges.append({
            "from": from_doc,
            "to": to_doc,
            "count": info["count"],
            "chunks": info["chunks"],
        })
        if from_doc not in out_refs:
            out_refs[from_doc] = []
        if to_doc not in out_refs[from_doc]:
            out_refs[from_doc].append(to_doc)

        if to_doc not in in_refs:
            in_refs[to_doc] = []
        if from_doc not in in_refs[to_doc]:
            in_refs[to_doc].append(from_doc)

    for doc_id in all_doc_id_set:
        nodes[doc_id] = {
            "doc_id": doc_id,
            "out_refs": out_refs.get(doc_id, []),
            "in_refs": in_refs.get(doc_id, []),
            "out_degree": len(out_refs.get(doc_id, [])),
            "in_degree": len(in_refs.get(doc_id, [])),
        }

    graph = {
        "nodes": nodes,
        "edges": edges,
        "total_edges": len(edges),
        "total_nodes": len(nodes),
    }

    return graph


def save_graph(graph: dict | None = None):
    """グラフを data/cross_references.json に保存する"""
    if graph is None:
        graph = build_graph()

    GRAPH_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(GRAPH_PATH, "w", encoding="utf-8") as f:
        json.dump(graph, f, ensure_ascii=False, indent=2)

    print(f"Cross-reference graph saved: {GRAPH_PATH}")
    print(f"  Nodes: {graph['total_nodes']}, Edges: {graph['total_edges']}")
    return graph


def load_graph() -> dict:
    """保存済みグラフをロード（未保存なら構築して保存）"""
    global _graph
    if _graph is not None:
        return _graph

    if GRAPH_PATH.exists():
        with open(GRAPH_PATH, encoding="utf-8") as f:
            _graph = json.load(f)
    else:
        _graph = save_graph()

    return _graph


def get_related_docs(doc_id: str, direction: str = "both", depth: int = 1) -> list[str]:
    """
    指定した文書に関連する文書IDリストを返す。

    Args:
        doc_id: 起点文書ID（例: "JERG-2-100"）
        direction: "out"（参照先）/ "in"（被参照）/ "both"
        depth: 何ホップまで辿るか（1=直接参照のみ）

    Returns:
        関連文書IDのリスト（doc_id自身は除く）
    """
    graph = load_graph()
    nodes = graph["nodes"]

    if doc_id not in nodes:
        return []

    visited = {doc_id}
    frontier = {doc_id}

    for _ in range(depth):
        next_frontier = set()
        for current in frontier:
            node = nodes.get(current, {})
            if direction in ("out", "both"):
                for ref in node.get("out_refs", []):
                    if ref not in visited:
                        next_frontier.add(ref)
                        visited.add(ref)
            if direction in ("in", "both"):
                for ref in node.get("in_refs", []):
                    if ref not in visited:
                        next_frontier.add(ref)
                        visited.add(ref)
        frontier = next_frontier

    visited.discard(doc_id)
    return sorted(visited)


def get_hub_docs(top_n: int = 10) -> list[dict]:
    """
    最も多く参照される文書（ハブ文書）を返す。
    クエリの文書フィルタなしでも重要文書を優先するために使用。
    """
    graph = load_graph()
    nodes = list(graph["nodes"].values())
    nodes.sort(key=lambda n: n["in_degree"], reverse=True)
    return nodes[:top_n]


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "build":
        graph = save_graph()
        print("\nTop 10 most referenced documents:")
        hubs = get_hub_docs(10)
        for node in hubs:
            print(f"  {node['doc_id']}: in={node['in_degree']}, out={node['out_degree']}")
    else:
        print("Usage: python -m src.cross_reference build")
