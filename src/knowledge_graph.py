"""
knowledge_graph.py - ナレッジグラフ構築

公的文書からエンティティ・関係を抽出し、
NetworkXベースのグラフとして管理する。

Neo4j不要で動作し、オプションでNeo4jエクスポートも可能。

グラフの内容:
  ノード:
    - Document: 文書（doc_id, title）
    - Section: セクション（section_id, title, level）
    - Concept: 概念・用語（name, description）
    - Organization: 組織（name）
    - Regulation: 規格・規制（name）
  エッジ:
    - CONTAINS: Document → Section, Section → Section
    - REFERENCES: Document → Document, Section → Concept
    - DEFINES: Document → Concept
    - RELATED_TO: Concept → Concept
    - SUPERSEDES: Document → Document（改訂関係）
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

try:
    import networkx as nx
    NX_AVAILABLE = True
except ImportError:
    NX_AVAILABLE = False


# =========================================================
# データモデル
# =========================================================

@dataclass
class GraphNode:
    node_id: str
    node_type: str      # Document / Section / Concept / Organization / Regulation
    properties: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"node_id": self.node_id, "node_type": self.node_type, **self.properties}


@dataclass
class GraphEdge:
    from_id: str
    to_id: str
    edge_type: str      # CONTAINS / REFERENCES / DEFINES / RELATED_TO / SUPERSEDES
    properties: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"from": self.from_id, "to": self.to_id, "type": self.edge_type, **self.properties}


# =========================================================
# エンティティ抽出（パターンベース + LLM）
# =========================================================

# 宇宙/技術文書でよく出る組織・規格パターン
_ORG_PATTERNS = [
    re.compile(r'JAXA|宇宙航空研究開発機構'),
    re.compile(r'NASA|ESA|CNES|DLR'),
    re.compile(r'ISO\s*\d+|JIS\s*[A-Z]\s*\d+'),
]

_REG_PATTERNS = [
    re.compile(r'JERG-\d{1,2}-\d{3}(?:-[A-Z]+\d+)?'),
    re.compile(r'MIL-(?:STD|SPEC)-\d+[A-Z]?'),
    re.compile(r'ECSS-[A-Z]-[A-Z]{2}-\d+'),
    re.compile(r'ISO\s*\d+(?:-\d+)?'),
]

# 専門用語パターン（全大文字英字2文字以上 = 略語）
_ABBREV_PATTERN = re.compile(r'\b([A-Z]{2,})\b')

# 括弧で定義される用語
_DEFINITION_PATTERN = re.compile(
    r'「(.{2,20})」[はとも]?(?:、|　|\s)*(.{5,80}?)(?:という|と定義|をいう|である)',
)


def extract_entities_pattern(text: str) -> dict[str, list[str]]:
    """
    パターンマッチングでエンティティを抽出する（LLM不要）。

    Returns:
        {
          "organizations": [...],
          "regulations": [...],
          "abbreviations": [...],
          "defined_terms": [{"term": ..., "definition": ...}]
        }
    """
    organizations = []
    for pat in _ORG_PATTERNS:
        organizations.extend(pat.findall(text))

    regulations = []
    for pat in _REG_PATTERNS:
        regulations.extend(pat.findall(text))

    abbreviations = list(set(_ABBREV_PATTERN.findall(text)))

    defined_terms = []
    for m in _DEFINITION_PATTERN.finditer(text):
        defined_terms.append({
            "term": m.group(1).strip(),
            "definition": m.group(2).strip(),
        })

    return {
        "organizations": sorted(set(organizations)),
        "regulations": sorted(set(regulations)),
        "abbreviations": abbreviations,
        "defined_terms": defined_terms,
    }


ENTITY_EXTRACT_PROMPT = """\
以下の技術文書テキストから、重要なエンティティと関係を抽出してください。

抽出対象:
1. 概念・用語（技術用語、定義されている言葉）
2. 組織名（JAXA, NASAなど）
3. 規格・文書番号（JERG-X-XXX, ISO-XXXXなど）
4. 概念間の関係（A は B の一部、A は B を参照する、など）

出力形式（JSONのみ）:
{
  "concepts": [{"name": "用語", "description": "説明（30字以内）", "type": "concept/organization/regulation"}],
  "relations": [{"from": "概念A", "to": "概念B", "type": "PART_OF/REFERENCES/DEFINES/RELATED_TO", "label": "説明"}]
}

テキスト:
{text}
"""


def extract_entities_llm(
    client: Any,
    model: str,
    text: str,
    max_chars: int = 1500,
) -> dict:
    """
    LLMでエンティティと関係を抽出する。

    Returns:
        {"concepts": [...], "relations": [...]}
    """
    from src.llm_client import chat

    text_for_llm = text[:max_chars]
    prompt = ENTITY_EXTRACT_PROMPT.format(text=text_for_llm)
    messages = [{"role": "user", "content": prompt}]

    try:
        response = chat(client, model, messages, tools=None)
        content = response.content or ""

        if "```" in content:
            start = content.index("```") + 3
            if content[start:start+4] == "json":
                start += 4
            end = content.index("```", start)
            content = content[start:end].strip()

        return json.loads(content)

    except Exception as e:
        print(f"  エンティティ抽出LLM失敗: {e}")
        return {"concepts": [], "relations": []}


# =========================================================
# グラフ構築
# =========================================================

class DocumentKnowledgeGraph:
    """
    複数文書から構築するナレッジグラフ。

    使用例:
        kg = DocumentKnowledgeGraph()
        kg.add_document("JERG-2-100", "宇宙機一般要求")
        kg.add_section("JERG-2-100", "3.1", "熱設計要件", level=2)
        kg.add_concept("熱制御", "宇宙機の温度管理システム", "concept")
        kg.add_relation("JERG-2-100", "熱制御", "DEFINES")
        kg.add_relation("JERG-2-100", "JERG-0-051", "REFERENCES")
        subgraph = kg.find_related("熱制御", depth=2)
    """

    def __init__(self):
        self.nodes: dict[str, GraphNode] = {}
        self.edges: list[GraphEdge] = []
        # adjacency: node_id → [(edge_type, to_id, props)]
        self._adj_out: dict[str, list] = defaultdict(list)
        self._adj_in: dict[str, list] = defaultdict(list)

    # ---------- 追加 ----------

    def add_node(self, node_id: str, node_type: str, **props) -> GraphNode:
        """ノードを追加（重複時は上書き）"""
        node = GraphNode(node_id=node_id, node_type=node_type, properties=props)
        self.nodes[node_id] = node
        return node

    def add_document(self, doc_id: str, title: str, **props) -> GraphNode:
        return self.add_node(doc_id, "Document", title=title, **props)

    def add_section(
        self,
        doc_id: str,
        section_id: str,
        title: str,
        level: int = 1,
        summary: str = "",
        **props,
    ) -> GraphNode:
        full_id = f"{doc_id}#{section_id}"
        node = self.add_node(
            full_id, "Section",
            doc_id=doc_id,
            section_id=section_id,
            title=title,
            level=level,
            summary=summary,
            **props,
        )
        # Document CONTAINS Section
        self.add_edge(doc_id, full_id, "CONTAINS")
        return node

    def add_concept(self, name: str, description: str = "", ctype: str = "concept") -> GraphNode:
        return self.add_node(name, "Concept", description=description, ctype=ctype)

    def add_edge(self, from_id: str, to_id: str, edge_type: str, **props):
        """エッジを追加"""
        edge = GraphEdge(from_id=from_id, to_id=to_id, edge_type=edge_type, properties=props)
        self.edges.append(edge)
        self._adj_out[from_id].append((edge_type, to_id, props))
        self._adj_in[to_id].append((edge_type, from_id, props))

    def add_relation(self, from_id: str, to_id: str, edge_type: str, **props):
        """エッジの別名（使いやすいように）"""
        self.add_edge(from_id, to_id, edge_type, **props)

    # ---------- 検索 ----------

    def find_related(
        self,
        node_id: str,
        depth: int = 2,
        edge_types: list[str] | None = None,
        direction: str = "both",
    ) -> list[dict]:
        """
        指定ノードから幅優先探索で関連ノードを返す。

        Args:
            node_id: 起点ノードID
            depth: 探索の深さ
            edge_types: フィルタするエッジタイプ（Noneで全て）
            direction: "out" / "in" / "both"

        Returns:
            [{"node": GraphNode, "distance": int, "path": [edge_type, ...]}]
        """
        visited = {node_id}
        frontier = [(node_id, 0, [])]
        results = []

        while frontier:
            current_id, dist, path = frontier.pop(0)
            if dist >= depth:
                continue

            # 隣接ノードを収集
            neighbors = []
            if direction in ("out", "both"):
                for etype, nid, props in self._adj_out.get(current_id, []):
                    if edge_types is None or etype in edge_types:
                        neighbors.append((etype, nid))
            if direction in ("in", "both"):
                for etype, nid, props in self._adj_in.get(current_id, []):
                    if edge_types is None or etype in edge_types:
                        neighbors.append((f"<-{etype}", nid))

            for etype, nid in neighbors:
                if nid not in visited and nid in self.nodes:
                    visited.add(nid)
                    new_path = path + [etype]
                    results.append({
                        "node": self.nodes[nid],
                        "distance": dist + 1,
                        "path": new_path,
                    })
                    frontier.append((nid, dist + 1, new_path))

        return results

    def search_by_type(self, node_type: str) -> list[GraphNode]:
        """ノードタイプで絞り込み"""
        return [n for n in self.nodes.values() if n.node_type == node_type]

    def get_hub_nodes(self, top_n: int = 10) -> list[tuple[str, int]]:
        """最も多く参照されるノード（被参照数の多い順）"""
        in_degree = {nid: len(edges) for nid, edges in self._adj_in.items()}
        sorted_hubs = sorted(in_degree.items(), key=lambda x: x[1], reverse=True)
        return sorted_hubs[:top_n]

    def get_concept_network(self) -> list[dict]:
        """Concept ノード間の関係のみ抽出（概念マップ用）"""
        concept_edges = []
        for edge in self.edges:
            from_node = self.nodes.get(edge.from_id)
            to_node = self.nodes.get(edge.to_id)
            if from_node and to_node:
                if from_node.node_type == "Concept" or to_node.node_type == "Concept":
                    concept_edges.append({
                        "from": edge.from_id,
                        "to": edge.to_id,
                        "type": edge.edge_type,
                    })
        return concept_edges

    # ---------- NetworkX連携 ----------

    def to_networkx(self):
        """NetworkXのDiGraphに変換（可視化・分析用）"""
        if not NX_AVAILABLE:
            raise ImportError("pip install networkx が必要です")

        G = nx.DiGraph()
        for node_id, node in self.nodes.items():
            G.add_node(node_id, node_type=node.node_type, **node.properties)
        for edge in self.edges:
            G.add_edge(edge.from_id, edge.to_id, edge_type=edge.edge_type, **edge.properties)
        return G

    def pagerank(self, top_n: int = 10) -> list[tuple[str, float]]:
        """PageRankで重要ノードを計算（networkx必要）"""
        G = self.to_networkx()
        scores = nx.pagerank(G)
        return sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_n]

    # ---------- 保存・読み込み ----------

    def save(self, path: str | Path):
        """グラフをJSONで保存"""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "nodes": [n.to_dict() for n in self.nodes.values()],
            "edges": [e.to_dict() for e in self.edges],
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"Knowledge graph saved: {path}")
        print(f"  Nodes: {len(self.nodes)}, Edges: {len(self.edges)}")

    @classmethod
    def load(cls, path: str | Path) -> "DocumentKnowledgeGraph":
        """保存済みグラフを読み込む"""
        path = Path(path)
        with open(path, encoding="utf-8") as f:
            data = json.load(f)

        kg = cls()
        for nd in data["nodes"]:
            node_id = nd.pop("node_id")
            node_type = nd.pop("node_type")
            kg.add_node(node_id, node_type, **nd)

        for ed in data["edges"]:
            from_id = ed.pop("from")
            to_id = ed.pop("to")
            edge_type = ed.pop("type")
            kg.add_edge(from_id, to_id, edge_type, **ed)

        return kg

    # ---------- Neo4j エクスポート ----------

    def to_neo4j_cypher(self) -> list[str]:
        """
        Neo4jにインポート用のCypherクエリを生成する。

        使用例:
            queries = kg.to_neo4j_cypher()
            with neo4j_driver.session() as session:
                for q in queries:
                    session.run(q)
        """
        queries = []

        # ノード作成
        for node_id, node in self.nodes.items():
            props = {**node.properties, "node_id": node_id}
            props_str = ", ".join(f'{k}: {json.dumps(v, ensure_ascii=False)}' for k, v in props.items())
            q = f"MERGE (n:{node.node_type} {{{props_str}}})"
            queries.append(q)

        # エッジ作成
        for edge in self.edges:
            props_str = ""
            if edge.properties:
                ps = ", ".join(f'{k}: {json.dumps(v, ensure_ascii=False)}' for k, v in edge.properties.items())
                props_str = f" {{{ps}}}"
            q = (
                f"MATCH (a {{node_id: {json.dumps(edge.from_id)}}}), "
                f"(b {{node_id: {json.dumps(edge.to_id)}}}) "
                f"MERGE (a)-[:{edge.edge_type}{props_str}]->(b)"
            )
            queries.append(q)

        return queries


# =========================================================
# 文書チャンクからグラフ構築するユーティリティ
# =========================================================

def build_graph_from_chunks(
    chunks: list[dict],
    client: Any = None,
    model: str = "",
    use_llm: bool = False,
    llm_limit: int = 50,
    verbose: bool = True,
) -> DocumentKnowledgeGraph:
    """
    chunks.json 形式のデータからナレッジグラフを構築する。

    chunks: [{"doc_id", "chunk_id", "text", "section_number", "section_title", ...}]
    """
    kg = DocumentKnowledgeGraph()
    doc_titles: dict[str, str] = {}

    # 文書ノードを収集
    for chunk in chunks:
        doc_id = chunk["doc_id"]
        if doc_id not in doc_titles:
            doc_titles[doc_id] = chunk.get("doc_title", doc_id)
            kg.add_document(doc_id, doc_titles[doc_id])

    if verbose:
        print(f"[knowledge_graph] {len(doc_titles)} 文書を検出")

    # セクションノードと関係を追加
    processed_sections: set[str] = set()
    llm_count = 0

    for chunk in chunks:
        doc_id = chunk["doc_id"]
        sec_num = chunk.get("section_number", "")
        sec_title = chunk.get("section_title", "")

        if sec_num and sec_num not in processed_sections:
            processed_sections.add(sec_num)
            kg.add_section(
                doc_id=doc_id,
                section_id=sec_num,
                title=sec_title,
                level=sec_num.count('.') + 1,
                summary=chunk.get("summary", ""),
            )

        # パターンベースのエンティティ抽出
        entities = extract_entities_pattern(chunk["text"])

        # 規格参照エッジ
        for ref in entities["regulations"]:
            if ref != doc_id:
                kg.add_concept(ref, ctype="regulation")
                kg.add_edge(doc_id, ref, "REFERENCES")

        # 組織エッジ
        for org in entities["organizations"]:
            kg.add_concept(org, ctype="organization")
            kg.add_edge(doc_id, org, "MENTIONS")

        # 定義された用語をConceptノードに
        for dt in entities["defined_terms"]:
            kg.add_concept(dt["term"], description=dt["definition"])
            kg.add_edge(doc_id, dt["term"], "DEFINES")

        # LLMエンティティ抽出（上限あり）
        if use_llm and client and model and llm_count < llm_limit:
            llm_data = extract_entities_llm(client, model, chunk["text"])
            llm_count += 1

            for concept in llm_data.get("concepts", []):
                kg.add_concept(
                    concept["name"],
                    description=concept.get("description", ""),
                    ctype=concept.get("type", "concept"),
                )

            for rel in llm_data.get("relations", []):
                if rel["from"] in kg.nodes and rel["to"] in kg.nodes:
                    kg.add_edge(rel["from"], rel["to"], rel["type"],
                                label=rel.get("label", ""))

    # 文書間の相互参照エッジ（cross_refsがあれば）
    for chunk in chunks:
        doc_id = chunk["doc_id"]
        for ref in chunk.get("cross_refs", []):
            if ref in kg.nodes and ref != doc_id:
                kg.add_edge(doc_id, ref, "REFERENCES")

    if verbose:
        print(f"  Nodes: {len(kg.nodes)}, Edges: {len(kg.edges)}")
        hubs = kg.get_hub_nodes(5)
        print("  Top 5 参照ノード:")
        for nid, cnt in hubs:
            print(f"    {nid}: {cnt}参照")

    return kg


# =========================================================
# スタンドアロン実行
# =========================================================

if __name__ == "__main__":
    import sys

    # --- 簡単なテスト ---
    kg = DocumentKnowledgeGraph()
    kg.add_document("JERG-2-100", "宇宙機一般要求仕様")
    kg.add_document("JERG-0-051", "熱設計標準")
    kg.add_section("JERG-2-100", "3.1", "熱設計要件", level=2,
                   summary="熱制御システムの基本要件を規定")
    kg.add_section("JERG-2-100", "3.1.1", "温度許容範囲", level=3,
                   summary="DTRと温度マージンの定義")

    kg.add_concept("熱制御", "宇宙機の温度管理システム")
    kg.add_concept("DTR", "設計温度範囲 (Design Temperature Range)")
    kg.add_concept("TMM", "熱数学モデル (Thermal Mathematical Model)")

    kg.add_edge("JERG-2-100", "JERG-0-051", "REFERENCES")
    kg.add_edge("JERG-2-100", "熱制御", "DEFINES")
    kg.add_edge("熱制御", "DTR", "RELATED_TO", label="温度範囲を管理")
    kg.add_edge("熱制御", "TMM", "RELATED_TO", label="解析ツール")

    # 関連ノード検索
    print("=== 「熱制御」から深さ2の関連ノード ===")
    related = kg.find_related("熱制御", depth=2)
    for r in related:
        print(f"  {'  '*r['distance']}[{r['node'].node_type}] {r['node'].node_id}")
        print(f"  {'  '*r['distance']}  パス: {' → '.join(r['path'])}")

    # ハブノード
    print("\n=== ハブノード（最多参照） ===")
    hubs = kg.get_hub_nodes(5)
    for nid, cnt in hubs:
        print(f"  {nid}: {cnt}参照")

    # 保存
    kg.save("/tmp/test_knowledge_graph.json")

    # Cypherクエリ出力（Neo4j用）
    print("\n=== Neo4j Cypherクエリ（先頭5件） ===")
    queries = kg.to_neo4j_cypher()
    for q in queries[:5]:
        print(f"  {q}")
