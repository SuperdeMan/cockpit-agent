"""llm-gateway Embed 单测（B）：provider embed + servicer Embed handler。"""
import asyncio
import importlib.util
import os
import sys

_DIR = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, _DIR)
from providers import MockProvider, OpenAICompatibleProvider, _EMBED_DIM  # noqa: E402
from cockpit.llm.v1 import llm_pb2  # noqa: E402


def test_mock_embed_dim_and_deterministic():
    p = MockProvider()
    v1 = asyncio.run(p.embed(["你好", "世界"]))
    assert len(v1) == 2 and all(len(v) == _EMBED_DIM for v in v1)
    v2 = asyncio.run(p.embed(["你好"]))
    assert v2[0] == v1[0]  # 确定性


def test_openai_embed_url_derivation():
    p = OpenAICompatibleProvider("k", base_url="https://x.test/v1/chat/completions")
    assert p.embed_url == "https://x.test/v1/embeddings"
    p2 = OpenAICompatibleProvider("k", base_url="https://x/v1/chat/completions",
                                  embed_url="https://y/custom/embed")
    assert p2.embed_url == "https://y/custom/embed"  # 显式覆盖


def test_embed_config_separate_key_and_auth():
    """embedding 用独立 key + bearer + 维度（百炼场景：chat=MiMo, embed=百炼）。"""
    p = OpenAICompatibleProvider(
        "mimo-key", base_url="https://mimo/v1/chat/completions",
        embed_url="https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings",
        embed_model="text-embedding-v4", embed_api_key="bailian-key",
        embed_auth_style="bearer", embed_dimensions=1024)
    assert p.embed_api_key == "bailian-key"      # 独立于 chat key
    assert p._embed_headers()["Authorization"] == "Bearer bailian-key"
    assert p.embed_dimensions == 1024
    # 缺省 embed key 回退 chat key
    p2 = OpenAICompatibleProvider("only-chat", base_url="https://x/v1/chat/completions")
    assert p2.embed_api_key == "only-chat"


def _servicer():
    spec = importlib.util.spec_from_file_location(
        "llm_server_under_test", os.path.join(_DIR, "server.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.LLMGatewayServicer()


def test_embed_servicer_returns_vectors():
    svc = _servicer()  # 无 key → MockProvider

    async def go():
        resp = await svc.Embed(llm_pb2.EmbedRequest(texts=["不吃辣", "摇滚"]), None)
        return resp

    resp = asyncio.run(go())
    assert len(resp.embeddings) == 2
    assert resp.dim == _EMBED_DIM
    assert len(resp.embeddings[0].values) == _EMBED_DIM


def test_embed_servicer_empty():
    svc = _servicer()
    resp = asyncio.run(svc.Embed(llm_pb2.EmbedRequest(texts=[]), None))
    assert len(resp.embeddings) == 0 and resp.dim == 0
