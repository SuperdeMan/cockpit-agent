"""车书知识库 Provider 工厂。

治理 P0：KNOWLEDGE_VENDOR 显式指到未接入的实现时 fail-fast 说清楚，
不再静默落回 mock 语料。
"""
import os
from pathlib import Path

from agents._sdk.provenance import fail, log_resolution

from .base import KnowledgeRetriever
from .mock import MockKnowledgeRetriever


_DEFAULT_INDEX = (Path(__file__).resolve().parents[4] / "models" / "manual_rag"
                  / "xiaomi-su7-2024.v2.mrag")
_DEFAULT_CATALOG = (Path(__file__).resolve().parents[2] / "resources"
                    / "manual_catalog.yaml")


def build_knowledge_retriever() -> KnowledgeRetriever:
    vendor = (os.getenv("KNOWLEDGE_VENDOR", "mock") or "mock").strip().lower()
    if vendor in ("local", "manual", "file"):
        index_path = os.getenv("MANUAL_INDEX_PATH", "").strip() or str(_DEFAULT_INDEX)
        vehicle_model = os.getenv("KNOWLEDGE_VEHICLE_MODEL", "").strip()
        try:
            from .local_index import ManualIndexRetriever
            provider = ManualIndexRetriever(
                index_path, vehicle_model=vehicle_model,
                catalog_path=_DEFAULT_CATALOG)
        except Exception as exc:
            fail("knowledge", f"车型手册索引构造失败：{exc}", exc)
        log_resolution(
            "knowledge", provider.document["document_id"], True, provider)
        return provider
    if vendor == "pgvector":
        # TODO(Production): 接入 PgVectorRetriever。
        fail("knowledge", "KNOWLEDGE_VENDOR=pgvector 未接入；当前真实实现为 local 索引")
    elif vendor != "mock":
        fail("knowledge", f"未知 KNOWLEDGE_VENDOR={vendor}")
    m = MockKnowledgeRetriever()
    log_resolution("knowledge", "mock", False, m)
    return m
