"""车书知识库 Provider 工厂。"""
import os
from .base import KnowledgeRetriever
from .mock import MockKnowledgeRetriever


def build_knowledge_retriever() -> KnowledgeRetriever:
    vendor = os.getenv("KNOWLEDGE_VENDOR", "mock")
    if vendor == "pgvector" and os.getenv("PGVECTOR_DSN"):
        # TODO(Production): 接入 PgVectorRetriever。
        pass
    return MockKnowledgeRetriever()
