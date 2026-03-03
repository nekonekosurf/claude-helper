"""
宇宙・航空宇宙分野 RAGシステム

ローカルLLMエージェントに宇宙専門知識を組み込むための
Retrieval-Augmented Generation エンジン。

主要コンポーネント:
    SpaceRAG        : RAGエンジン本体 (rag_engine.py)
    SpaceGlossary   : 宇宙専門用語辞書 (space_glossary.py)
    KnowledgeBuilder: ナレッジベース構築 (knowledge_builder.py)
    DataCollector   : データ収集 (data_collector.py)

クイックスタート:
    from space_rag import SpaceRAG

    rag = SpaceRAG()
    result = rag.retrieve("LEO衛星のMLI設計について教えて")
    context = rag.build_prompt_context(result)
    print(context)
"""

from space_rag.rag_engine import SpaceRAG, RAGResult, RetrievedChunk

__all__ = ["SpaceRAG", "RAGResult", "RetrievedChunk"]
__version__ = "0.1.0"
