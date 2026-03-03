"""
GraphRAG - 知識グラフ拡張RAGの簡易実装

Microsoft GraphRAGの主要コンセプトを軽量実装：
- エンティティ・関係の抽出
- コミュニティ検出（Leiden風の簡易版）
- グローバルサマリーの生成
- ローカル検索 vs グローバル検索
"""

from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass, field
from collections import defaultdict
from typing import Any
import numpy as np


@dataclass
class Entity:
    """抽出されたエンティティ"""
    id: str
    name: str
    entity_type: str          # SYSTEM, COMPONENT, STANDARD, CONCEPT 等
    description: str
    source_chunks: list[str] = field(default_factory=list)
    community_id: int = -1


@dataclass
class Relation:
    """エンティティ間の関係"""
    source: str               # entity id
    target: str               # entity id
    relation_type: str        # IS_PART_OF, REFERENCES, REQUIRES 等
    description: str
    weight: float = 1.0


@dataclass
class Community:
    """コミュニティ（密結合エンティティ群）"""
    id: int
    entity_ids: list[str]
    summary: str = ""
    keywords: list[str] = field(default_factory=list)
    level: int = 0            # 階層レベル (0=最上位)


class SimpleGraph:
    """
    隣接リストによる軽量グラフ実装
    networkx に依存しない設計
    """

    def __init__(self) -> None:
        self.nodes: dict[str, dict[str, Any]] = {}
        self.edges: list[tuple[str, str, dict[str, Any]]] = []
        self._adj: dict[str, set[str]] = defaultdict(set)

    def add_node(self, node_id: str, **attrs: Any) -> None:
        self.nodes[node_id] = attrs

    def add_edge(self, src: str, dst: str, **attrs: Any) -> None:
        self.edges.append((src, dst, attrs))
        self._adj[src].add(dst)
        self._adj[dst].add(src)

    def neighbors(self, node_id: str) -> set[str]:
        return self._adj.get(node_id, set())

    def degree(self, node_id: str) -> int:
        return len(self._adj.get(node_id, set()))

    def node_ids(self) -> list[str]:
        return list(self.nodes.keys())

    def modularity_communities(self) -> dict[str, int]:
        """
        Louvain風の貪欲コミュニティ検出（簡易版）
        実際の実装では python-louvain や graspologic を使用推奨
        """
        community_map: dict[str, int] = {}
        visited: set[str] = set()
        community_id = 0

        # BFSでコネクテッドコンポーネントを検出
        for node in self.node_ids():
            if node in visited:
                continue
            queue = [node]
            component = []
            while queue:
                current = queue.pop(0)
                if current in visited:
                    continue
                visited.add(current)
                component.append(current)
                for neighbor in self.neighbors(current):
                    if neighbor not in visited:
                        queue.append(neighbor)

            # コンポーネント内でエッジ密度によりサブコミュニティ化
            if len(component) <= 5:
                # 小コンポーネントはそのまま1コミュニティ
                for n in component:
                    community_map[n] = community_id
                community_id += 1
            else:
                # 大コンポーネントは次数中心性で2分割（簡易版）
                degrees = [(n, self.degree(n)) for n in component]
                degrees.sort(key=lambda x: x[1], reverse=True)
                pivot = len(component) // 2
                for n, _ in degrees[:pivot]:
                    community_map[n] = community_id
                community_id += 1
                for n, _ in degrees[pivot:]:
                    community_map[n] = community_id
                community_id += 1

        return community_map


class GraphRAG:
    """
    GraphRAG: グラフ構造を活用した高度なRAG

    使い方:
        rag = GraphRAG(llm_client=my_llm)
        rag.index(chunks)            # インデックス構築
        results = rag.local_search("熱制御システムの冗長化方針")
        results = rag.global_search("このドキュメント群の主要テーマは？")
    """

    def __init__(self, llm_client: Any = None) -> None:
        self.llm = llm_client
        self.graph = SimpleGraph()
        self.entities: dict[str, Entity] = {}
        self.relations: list[Relation] = []
        self.communities: dict[int, Community] = {}
        self._chunk_entities: dict[str, list[str]] = defaultdict(list)  # chunk_id -> entity_ids

    # --------------------
    # インデックス構築
    # --------------------

    def index(self, chunks: list[dict[str, Any]]) -> None:
        """
        チャンクからグラフを構築

        Args:
            chunks: [{"chunk_id": ..., "text": ..., "metadata": {...}}, ...]
        """
        print(f"[GraphRAG] {len(chunks)} チャンクを処理中...")

        for chunk in chunks:
            chunk_id = chunk.get("chunk_id", "")
            text = chunk.get("text", "")
            if not text.strip():
                continue

            # エンティティ抽出
            entities, relations = self._extract_entities_relations(chunk_id, text)
            for entity in entities:
                self._add_entity(entity)
            self.relations.extend(relations)

            # チャンクとエンティティの対応付け
            for e in entities:
                self._chunk_entities[chunk_id].append(e.id)

        # グラフ構築
        self._build_graph()

        # コミュニティ検出
        self._detect_communities()

        # コミュニティサマリー生成
        self._generate_community_summaries()

        print(f"[GraphRAG] 完了: {len(self.entities)} エンティティ, "
              f"{len(self.relations)} 関係, {len(self.communities)} コミュニティ")

    def _extract_entities_relations(
        self,
        chunk_id: str,
        text: str
    ) -> tuple[list[Entity], list[Relation]]:
        """
        LLMを使ってエンティティと関係を抽出

        LLMなしの場合はルールベースのフォールバック
        """
        if self.llm:
            return self._llm_extract(chunk_id, text)
        else:
            return self._rule_based_extract(chunk_id, text)

    def _llm_extract(
        self,
        chunk_id: str,
        text: str
    ) -> tuple[list[Entity], list[Relation]]:
        """LLMによるエンティティ・関係抽出"""
        prompt = f"""以下のテキストからエンティティと関係を抽出してください。

テキスト:
{text}

JSON形式で出力してください:
{{
  "entities": [
    {{"name": "エンティティ名", "type": "SYSTEM|COMPONENT|STANDARD|CONCEPT|PERSON|ORG", "description": "説明"}}
  ],
  "relations": [
    {{"source": "エンティティ名", "target": "エンティティ名", "type": "IS_PART_OF|REFERENCES|REQUIRES|IMPLEMENTS|USES", "description": "関係の説明"}}
  ]
}}
"""
        try:
            response = self.llm.complete(prompt)
            data = json.loads(response)
            entities = []
            for e in data.get("entities", []):
                eid = self._make_entity_id(e["name"])
                entities.append(Entity(
                    id=eid,
                    name=e["name"],
                    entity_type=e.get("type", "CONCEPT"),
                    description=e.get("description", ""),
                    source_chunks=[chunk_id]
                ))
            relations = []
            for r in data.get("relations", []):
                relations.append(Relation(
                    source=self._make_entity_id(r["source"]),
                    target=self._make_entity_id(r["target"]),
                    relation_type=r.get("type", "RELATED_TO"),
                    description=r.get("description", "")
                ))
            return entities, relations
        except Exception:
            return self._rule_based_extract(chunk_id, text)

    def _rule_based_extract(
        self,
        chunk_id: str,
        text: str
    ) -> tuple[list[Entity], list[Relation]]:
        """
        ルールベースのフォールバック抽出
        宇宙分野の典型的なパターンに対応
        """
        import re
        entities: list[Entity] = []
        relations: list[Relation] = []

        # セクション番号パターン (例: 3.2.4、第3章)
        section_pattern = re.compile(r'(\d+(?:\.\d+)+)\s+([^\n。]{5,40})')
        for m in section_pattern.finditer(text[:500]):  # 先頭のみ
            name = f"{m.group(1)} {m.group(2).strip()}"
            eid = self._make_entity_id(name)
            entities.append(Entity(
                id=eid,
                name=name,
                entity_type="CONCEPT",
                description=f"セクション: {name}",
                source_chunks=[chunk_id]
            ))

        # JIS/ISO/JERG規格番号パターン
        std_pattern = re.compile(r'(JERG|JAXA|JIS|ISO|MIL|IEEE)[- ]?[\w\-\.]+')
        for m in std_pattern.finditer(text):
            name = m.group(0)
            eid = self._make_entity_id(name)
            entities.append(Entity(
                id=eid,
                name=name,
                entity_type="STANDARD",
                description=f"規格文書: {name}",
                source_chunks=[chunk_id]
            ))

        return entities, relations

    def _make_entity_id(self, name: str) -> str:
        return hashlib.md5(name.lower().encode()).hexdigest()[:12]

    def _add_entity(self, entity: Entity) -> None:
        if entity.id in self.entities:
            self.entities[entity.id].source_chunks.extend(entity.source_chunks)
        else:
            self.entities[entity.id] = entity

    def _build_graph(self) -> None:
        """エンティティと関係からグラフを構築"""
        for eid, entity in self.entities.items():
            self.graph.add_node(eid, name=entity.name, type=entity.entity_type)

        for relation in self.relations:
            if relation.source in self.entities and relation.target in self.entities:
                self.graph.add_edge(
                    relation.source,
                    relation.target,
                    type=relation.relation_type,
                    description=relation.description,
                    weight=relation.weight
                )

    def _detect_communities(self) -> None:
        """コミュニティ検出（Leiden風の簡易版）"""
        community_map = self.graph.modularity_communities()

        # コミュニティ構造を構築
        community_entities: dict[int, list[str]] = defaultdict(list)
        for entity_id, comm_id in community_map.items():
            community_entities[comm_id].append(entity_id)
            if entity_id in self.entities:
                self.entities[entity_id].community_id = comm_id

        for comm_id, entity_ids in community_entities.items():
            self.communities[comm_id] = Community(
                id=comm_id,
                entity_ids=entity_ids
            )

    def _generate_community_summaries(self) -> None:
        """各コミュニティのサマリーを生成"""
        for comm_id, community in self.communities.items():
            entity_names = [
                self.entities[eid].name
                for eid in community.entity_ids
                if eid in self.entities
            ]

            if self.llm and entity_names:
                entity_info = "\n".join([
                    f"- {self.entities[eid].name}: {self.entities[eid].description}"
                    for eid in community.entity_ids
                    if eid in self.entities
                ])
                prompt = f"""以下のエンティティ群のコミュニティサマリーを2-3文で生成してください:
{entity_info}

サマリー:"""
                try:
                    community.summary = self.llm.complete(prompt)
                except Exception:
                    community.summary = f"エンティティ群: {', '.join(entity_names[:5])}"
            else:
                community.summary = f"エンティティ群: {', '.join(entity_names[:5])}"

            community.keywords = entity_names[:10]

    # --------------------
    # 検索
    # --------------------

    def local_search(
        self,
        query: str,
        top_k: int = 5
    ) -> dict[str, Any]:
        """
        ローカル検索: クエリに関連するエンティティとその周辺を検索
        特定の質問（事実確認、詳細情報）に適する
        """
        # クエリと関連するエンティティを名前マッチで検索
        query_lower = query.lower()
        matched_entities = []

        for eid, entity in self.entities.items():
            score = 0.0
            if query_lower in entity.name.lower():
                score += 2.0
            if query_lower in entity.description.lower():
                score += 1.0
            # キーワード部分マッチ
            for kw in query_lower.split():
                if kw in entity.name.lower():
                    score += 0.5
            if score > 0:
                matched_entities.append((score, entity))

        matched_entities.sort(key=lambda x: x[0], reverse=True)
        top_entities = [e for _, e in matched_entities[:top_k]]

        # 関連チャンクを収集
        related_chunks = []
        for entity in top_entities:
            related_chunks.extend(entity.source_chunks)
        related_chunks = list(set(related_chunks))

        # 関連エンティティ（近傍）
        neighbor_ids = set()
        for entity in top_entities:
            for nid in self.graph.neighbors(entity.id):
                neighbor_ids.add(nid)

        neighbors = [self.entities[nid] for nid in neighbor_ids if nid in self.entities]

        return {
            "type": "local",
            "matched_entities": [
                {
                    "name": e.name,
                    "type": e.entity_type,
                    "description": e.description,
                    "community_id": e.community_id
                }
                for e in top_entities
            ],
            "neighbor_entities": [
                {"name": e.name, "type": e.entity_type}
                for e in neighbors[:10]
            ],
            "related_chunk_ids": related_chunks
        }

    def global_search(
        self,
        query: str,
        top_k_communities: int = 5
    ) -> dict[str, Any]:
        """
        グローバル検索: コミュニティサマリーを使ったデータセット全体を横断する検索
        抽象的な質問（テーマ、概要、全体像）に適する
        """
        query_lower = query.lower()

        # コミュニティサマリーとのマッチング
        community_scores = []
        for comm_id, community in self.communities.items():
            score = 0.0
            summary_lower = community.summary.lower()
            for kw in query_lower.split():
                if kw in summary_lower:
                    score += 1.0
            for keyword in community.keywords:
                if query_lower in keyword.lower():
                    score += 0.5
            community_scores.append((score, community))

        community_scores.sort(key=lambda x: x[0], reverse=True)
        top_communities = [c for _, c in community_scores[:top_k_communities]]

        # Map: 各コミュニティから回答候補を生成
        partial_answers = []
        for community in top_communities:
            partial_answers.append({
                "community_id": community.id,
                "summary": community.summary,
                "keywords": community.keywords,
                "entity_count": len(community.entity_ids)
            })

        return {
            "type": "global",
            "query": query,
            "community_results": partial_answers,
            "total_communities": len(self.communities),
            "total_entities": len(self.entities)
        }

    def save(self, path: str) -> None:
        """グラフ構造をJSONに保存"""
        data = {
            "entities": {
                eid: {
                    "name": e.name,
                    "entity_type": e.entity_type,
                    "description": e.description,
                    "source_chunks": e.source_chunks,
                    "community_id": e.community_id
                }
                for eid, e in self.entities.items()
            },
            "relations": [
                {
                    "source": r.source,
                    "target": r.target,
                    "relation_type": r.relation_type,
                    "description": r.description,
                    "weight": r.weight
                }
                for r in self.relations
            ],
            "communities": {
                str(cid): {
                    "entity_ids": c.entity_ids,
                    "summary": c.summary,
                    "keywords": c.keywords,
                    "level": c.level
                }
                for cid, c in self.communities.items()
            }
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"[GraphRAG] 保存完了: {path}")

    def load(self, path: str) -> None:
        """保存したグラフ構造を読み込む"""
        with open(path, encoding="utf-8") as f:
            data = json.load(f)

        self.entities = {
            eid: Entity(
                id=eid,
                name=e["name"],
                entity_type=e["entity_type"],
                description=e["description"],
                source_chunks=e["source_chunks"],
                community_id=e["community_id"]
            )
            for eid, e in data["entities"].items()
        }
        self.relations = [
            Relation(
                source=r["source"],
                target=r["target"],
                relation_type=r["relation_type"],
                description=r["description"],
                weight=r["weight"]
            )
            for r in data["relations"]
        ]
        self.communities = {
            int(cid): Community(
                id=int(cid),
                entity_ids=c["entity_ids"],
                summary=c["summary"],
                keywords=c["keywords"],
                level=c["level"]
            )
            for cid, c in data["communities"].items()
        }
        self._build_graph()
        print(f"[GraphRAG] 読み込み完了: {len(self.entities)} エンティティ, "
              f"{len(self.communities)} コミュニティ")
