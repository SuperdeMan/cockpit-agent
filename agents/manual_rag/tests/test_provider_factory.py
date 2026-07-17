"""车书知识库工厂契约：默认 mock；显式指到未接入实现时 fail-fast（治理 P0）。"""
import pytest

from agents._sdk.provenance import ProviderConfigError
from agents.manual_rag.src.providers import build_knowledge_retriever
from agents.manual_rag.src.providers.mock import MockKnowledgeRetriever


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("KNOWLEDGE_VENDOR", raising=False)
    monkeypatch.delenv("PGVECTOR_DSN", raising=False)


def test_default_env_resolves_mock():
    assert isinstance(build_knowledge_retriever(), MockKnowledgeRetriever)


def test_explicit_unimplemented_vendor_fails_fast(monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_VENDOR", "pgvector")
    with pytest.raises(ProviderConfigError, match="未接入"):
        build_knowledge_retriever()
