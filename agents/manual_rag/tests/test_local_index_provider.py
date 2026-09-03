"""真实车型手册本地索引 Provider 契约。"""
from __future__ import annotations

import asyncio
import gzip
import json
from pathlib import Path

import pytest
import yaml

from agents._sdk.provenance import ProviderConfigError
from agents._sdk.testing import run_handle
from agents.manual_rag.src.agent import ManualRagAgent
from agents.manual_rag.src.index_format import (
    ExtractedPage,
    build_index_bundle,
    load_index_bundle,
    write_index_bundle,
)
from agents.manual_rag.src.providers.local_index import (
    ManualIndexError,
    ManualIndexRetriever,
)


def _bundle_path(tmp_path: Path) -> Path:
    pages = [
        ExtractedPage(
            page_number=245,
            section_path=("车辆规格", "规格与参数", "车轮与轮胎参数"),
            content="车轮与轮胎参数。轮胎压力（bar）：前后轮均为 2.9。",
        ),
        ExtractedPage(
            page_number=251,
            section_path=("保修和保养", "车辆保养", "保养信息"),
            content="定期保养：每 1 年或每行驶 20000 公里，以先达到者为准。",
        ),
        ExtractedPage(
            page_number=252,
            section_path=("保修和保养", "车辆保养", "保养信息"),
            content="时间与里程以先到者为准。制动液更换：2 年、4 年时更换。",
        ),
        ExtractedPage(
            page_number=164,
            section_path=("智能辅助驾驶", "智能泊车", "智能泊车辅助"),
            content="车速低于 15km/h 时，在中控屏点击智能泊车，选择车位后开始泊入。",
        ),
        ExtractedPage(
            page_number=186,
            section_path=("信息显示和娱乐", "中控显示屏", "连接"),
            content="手车互联支持小米互联互通，以及适用于部分安卓手机的 CarLink。",
        ),
    ]
    bundle = build_index_bundle(
        pages,
        document_id="xiaomi-su7-2024-user-manual",
        title="SU7用户手册",
        publisher="小米汽车",
        vehicle_model="xiaomi-su7-2024",
        vehicle_aliases=["SU7", "小米SU7", "Xiaomi SU7", "SU7 Pro", "SU7 Max"],
        revision="2024-04-15",
        source_file="manual.pdf",
        source_sha256="a" * 64,
        source_pages=278,
    )
    path = tmp_path / "manual.v1.json.gz"
    write_index_bundle(path, bundle)
    return path


def _catalog_path(tmp_path: Path, index_path: Path, **overrides) -> Path:
    document = load_index_bundle(index_path)["document"]
    keys = ("title", "publisher", "vehicle_model", "revision", "source_pages",
            "source_sha256", "content_sha256")
    trusted = {key: document[key] for key in keys}
    trusted.update(overrides)
    payload = {
        "schema_version": 1,
        "documents": {document["document_id"]: trusted},
    }
    path = tmp_path / "manual_catalog.yaml"
    path.write_text(yaml.safe_dump(
        payload, allow_unicode=True, sort_keys=True), encoding="utf-8")
    return path


def _provider(tmp_path: Path, *, vehicle_model: str = "") -> ManualIndexRetriever:
    index_path = _bundle_path(tmp_path)
    return ManualIndexRetriever(
        index_path, vehicle_model=vehicle_model,
        catalog_path=_catalog_path(tmp_path, index_path))


def _retrieve(provider: ManualIndexRetriever, query: str, **kwargs):
    return asyncio.run(provider.retrieve(query, **kwargs))


def test_real_index_retrieves_manual_chunk_with_structured_citation(tmp_path):
    provider = _provider(tmp_path)

    chunks = _retrieve(provider, "胎压应该打多少")

    assert chunks
    assert chunks[0].source_type == "manual"
    assert chunks[0].page_start == 245
    assert chunks[0].vehicle_model == "xiaomi-su7-2024"
    assert "PDF第245页" in chunks[0].source
    assert "2.9" in chunks[0].content
    assert 0 < chunks[0].score <= 1


def test_alias_query_retrieves_manual_wording(tmp_path):
    provider = _provider(tmp_path)

    chunks = _retrieve(provider, "自动泊车怎么开启")

    assert chunks and chunks[0].page_start == 164


def test_non_overlapping_aliases_combine_without_injecting_an_answer(tmp_path):
    provider = _provider(tmp_path)

    chunks = _retrieve(provider, "刹车油几年换")

    assert chunks and chunks[0].page_start == 252
    assert "制动液更换" in chunks[0].content


def test_unknown_latin_product_does_not_near_match_phone_connection(tmp_path):
    provider = _provider(tmp_path)

    assert _retrieve(provider, "SU7 支持 CarPlay 吗") == []
    assert _retrieve(provider, "支持 Android Auto 吗") == []


def test_known_multi_token_unit_and_vehicle_alias_are_not_overblocked(tmp_path):
    provider = _provider(tmp_path)

    parking = _retrieve(provider, "15 km/h 时能不能用自动泊车")
    pressure = _retrieve(provider, "Xiaomi SU7 胎压应该打多少")

    assert parking and parking[0].page_start == 164
    assert pressure and pressure[0].page_start == 245


def test_low_coverage_query_does_not_match_generic_warning_words(tmp_path):
    provider = _provider(tmp_path)

    assert _retrieve(provider, "机油灯亮了怎么办") == []


def test_vehicle_model_mismatch_fails_closed(tmp_path):
    provider = _provider(tmp_path)

    assert _retrieve(provider, "胎压多少", vehicle_model="xiaomi-yu7") == []


def test_configured_vehicle_model_mismatch_rejects_startup(tmp_path):
    index_path = _bundle_path(tmp_path)
    with pytest.raises(ManualIndexError, match="车型不一致"):
        ManualIndexRetriever(
            index_path, vehicle_model="xiaomi-yu7",
            catalog_path=_catalog_path(tmp_path, index_path))


def test_tampered_content_hash_rejects_startup(tmp_path):
    path = _bundle_path(tmp_path)
    catalog = _catalog_path(tmp_path, path)
    payload = json.loads(gzip.decompress(path.read_bytes()))
    payload["chunks"][0]["content"] = "被替换的内容"
    path.write_bytes(gzip.compress(
        json.dumps(payload, ensure_ascii=False, sort_keys=True,
                   separators=(",", ":")).encode("utf-8"),
        mtime=0,
    ))

    with pytest.raises(ManualIndexError, match="sha256"):
        ManualIndexRetriever(path, catalog_path=catalog)


def test_self_consistent_but_untrusted_fingerprint_rejects_startup(tmp_path):
    path = _bundle_path(tmp_path)
    catalog = _catalog_path(tmp_path, path, source_sha256="f" * 64)

    with pytest.raises(ManualIndexError, match="catalog 指纹不一致.*source_sha256"):
        ManualIndexRetriever(path, catalog_path=catalog)


def test_factory_local_is_real_and_missing_index_is_fail_fast(tmp_path, monkeypatch):
    from agents.manual_rag.src.providers import build_knowledge_retriever

    monkeypatch.setenv("KNOWLEDGE_VENDOR", "local")
    index_path = _bundle_path(tmp_path)
    monkeypatch.setenv("MANUAL_INDEX_PATH", str(index_path))
    monkeypatch.setattr(
        "agents.manual_rag.src.providers._DEFAULT_CATALOG",
        _catalog_path(tmp_path, index_path),
    )
    provider = build_knowledge_retriever()
    assert isinstance(provider, ManualIndexRetriever)
    assert provider.provenance_mode == "real"
    assert provider.provenance_vendor == "xiaomi-su7-2024-user-manual"

    monkeypatch.setenv("MANUAL_INDEX_PATH", str(tmp_path / "missing.json.gz"))
    with pytest.raises(ProviderConfigError, match="索引"):
        build_knowledge_retriever()


def test_agent_card_and_prompt_carry_real_manual_citations(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock
    from agents.manual_rag.src.providers import build_knowledge_retriever

    monkeypatch.setenv("KNOWLEDGE_VENDOR", "local")
    index_path = _bundle_path(tmp_path)
    monkeypatch.setenv("MANUAL_INDEX_PATH", str(index_path))
    monkeypatch.setattr(
        "agents.manual_rag.src.providers._DEFAULT_CATALOG",
        _catalog_path(tmp_path, index_path),
    )
    provider = build_knowledge_retriever()
    agent = ManualRagAgent(retriever=provider)
    agent.llm.complete = AsyncMock(return_value="手册标注的前后轮压力为 2.9 bar。")

    result = asyncio.run(run_handle(
        agent, "manual.query", raw_text="胎压应该打多少"))

    card = result.ui_card or {}
    assert card["_prov"]["mode"] == "real"
    assert card["_prov"]["vendor"] == "xiaomi-su7-2024-user-manual"
    assert card["_prov"]["data_time"] == "2024-04-15"
    assert card["_prov"]["data_time_label"] == "手册版本"
    assert card["document"]["source_sha256"] == "a" * 64
    assert card["chunks"][0]["page_start"] == 245
    messages = agent.llm.complete.await_args[0][0]
    assert "PDF第245页" in messages[1]["content"]
    assert "2.9" in messages[1]["content"]


def test_agent_rejects_number_not_grounded_in_real_manual(tmp_path):
    from unittest.mock import AsyncMock

    provider = _provider(tmp_path)
    provider.provenance_mode = "real"
    provider.provenance_vendor = provider.document["document_id"]
    agent = ManualRagAgent(retriever=provider)
    agent.llm.complete = AsyncMock(return_value="建议前后轮都充到 2.5 bar。")

    result = asyncio.run(run_handle(
        agent, "manual.query", raw_text="胎压应该打多少"))

    assert "无法从引用片段核对" in result.speech
    assert (result.data or {}).get("grounding_rejected") == "numeric"
    assert (result.ui_card or {})["_prov"]["mode"] == "real"


def test_agent_keeps_number_grounded_in_real_manual(tmp_path):
    from unittest.mock import AsyncMock

    provider = _provider(tmp_path)
    provider.provenance_mode = "real"
    provider.provenance_vendor = provider.document["document_id"]
    agent = ManualRagAgent(retriever=provider)
    agent.llm.complete = AsyncMock(return_value="手册标注的前后轮压力为 2.9 bar。")

    result = asyncio.run(run_handle(
        agent, "manual.query", raw_text="胎压应该打多少"))

    assert result.speech == "手册标注的前后轮压力为 2.9 bar。"
    assert not (result.data or {}).get("grounding_rejected")
