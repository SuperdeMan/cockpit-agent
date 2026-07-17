"""车书知识库 Provider 工厂。

治理 P0：KNOWLEDGE_VENDOR 显式指到未接入的实现时 fail-fast 说清楚，
不再静默落回 mock 语料。
"""
import os

from agents._sdk.provenance import fail, log_resolution

from .base import KnowledgeRetriever
from .mock import MockKnowledgeRetriever


def build_knowledge_retriever() -> KnowledgeRetriever:
    vendor = (os.getenv("KNOWLEDGE_VENDOR", "mock") or "mock").strip().lower()
    if vendor == "pgvector":
        # TODO(Production): 接入 PgVectorRetriever。
        fail("knowledge", "KNOWLEDGE_VENDOR=pgvector 未接入（TODO），当前仅 mock 语料")
    elif vendor != "mock":
        fail("knowledge", f"未知 KNOWLEDGE_VENDOR={vendor}")
    m = MockKnowledgeRetriever()
    log_resolution("knowledge", "mock", False, m)
    return m
