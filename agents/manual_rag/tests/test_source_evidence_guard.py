"""Source-bound safety answers must not lose conditions in generation."""
import asyncio
from copy import deepcopy
import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml

from agents._sdk.testing import run_handle
from agents.manual_rag.src.agent import ManualRagAgent
from agents.manual_rag.src.providers.base import Chunk
from agents.manual_rag.src.source_evidence import load_evidence_guards
from agents.manual_rag.src.index_format import ExtractedPage, build_index_bundle, write_index_bundle
from agents.manual_rag.src.providers.local_index import ManualIndexError, ManualIndexRetriever


@pytest.fixture
def guarded_agent(monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_VENDOR", "mock")
    monkeypatch.delenv("REQUIRE_REAL_PROVIDERS", raising=False)
    agent = ManualRagAgent()
    # Synthetic source: deliberately different values from the deployed manual.
    source = ("此灯闪烁，可能表示压力低于2.1bar；此灯常亮表示监测故障。"
              "这两种情况都应停到安全位置并联系服务中心。")
    chunk = Chunk(content=source, source="测试手册第8页", source_type="manual")
    agent.kb.retrieve = AsyncMock(return_value=[chunk])
    agent.kb.guarded_evidence = AsyncMock(return_value=SimpleNamespace(
        guard_id="synthetic-pressure-alert", available=True,
        speech="手册原文：" + source, chunks=[chunk],
        evidence_ids=["alert-and-disposition"],
    ))
    agent.llm.complete = AsyncMock(return_value="灯亮说明低于2.1bar，补足就能继续开。")
    return agent, source


def test_alarm_answer_uses_complete_source_instead_of_condition_dropping_model(guarded_agent):
    agent, source = guarded_agent
    result = asyncio.run(run_handle(agent, "manual.query", raw_text="胎压黄灯亮了还能继续开吗？"))
    assert source in result.speech
    assert "补足就能继续开" not in result.speech
    agent.llm.complete.assert_not_awaited()
    agent.kb.retrieve.assert_not_awaited()
    assert result.data["_speech_verbatim"] is True
    assert result.data["source_evidence_ids"] == ["alert-and-disposition"]
    assert result.ui_card["chunks"][0]["content"] == source
    assert not result.actions


def test_unavailable_guard_never_falls_through_to_generated_safety_advice(guarded_agent):
    agent, _ = guarded_agent
    agent.kb.guarded_evidence.return_value = SimpleNamespace(
        guard_id="synthetic-pressure-alert", available=False,
        speech="", chunks=[], evidence_ids=[],
    )
    result = asyncio.run(run_handle(agent, "manual.query", raw_text="胎压报警应该怎么办？"))
    agent.llm.complete.assert_not_awaited()
    assert "无法核验" in result.speech
    assert result.data["grounding_rejected"] == "source_evidence"
    assert "2.1" not in result.speech


def test_ordinary_question_keeps_existing_generation_path(guarded_agent):
    agent, _ = guarded_agent
    agent.llm.complete.return_value = "请参看标签。"
    result = asyncio.run(run_handle(agent, "manual.query", raw_text="胎压多少正常？"))
    agent.kb.guarded_evidence.assert_not_awaited()
    agent.llm.complete.assert_awaited_once()
    assert result.speech == "请参看标签。"


def _profile(document, pages):
    parts = []
    for page, text in pages.items():
        part = {"id": f"p{page}", "label": "完整来源", "page": page,
                "start": 0, "end": len(text),
                "sha256": hashlib.sha256(text.encode()).hexdigest()}
        if page == 9:
            part["when_any"] = ["复位", "未更新"]
        parts.append(part)
    return {"schema_version": 1, "documents": {document["document_id"]: {
        "source_sha256": document["source_sha256"],
        "content_sha256": document["content_sha256"],
        "guards": [{"id": "pressure-test", "subjects": ["胎压"], "parts": parts}],
    }}}


@pytest.fixture
def evidence_source(tmp_path):
    document = {"document_id": "synthetic-conditions", "source_sha256": "a" * 64,
                "content_sha256": "b" * 64}
    pages = {8: "此灯闪烁可能低于2.1bar；常亮表示监测故障。两种情况均须停车检查。",
             9: "仅在压力恢复推荐值且显示未更新时，按维修说明复位；仍报警应停止并检修。"}
    path = tmp_path / "evidence.yaml"
    profile = _profile(document, pages)
    path.write_text(yaml.safe_dump(profile, allow_unicode=True), encoding="utf-8")
    return path, profile, document, pages


def test_complete_alternatives_are_indivisible_and_reset_is_only_opted_in(evidence_source):
    path, _, document, pages = evidence_source
    guard, = load_evidence_guards(path, document, pages)
    assert guard.available
    assert guard.matches("胎压灯常亮是什么情况？")
    assert not guard.matches("机油灯闪烁怎么办？")
    assert [p.text for p in guard.select("胎压灯亮还能开吗？")] == [pages[8]]
    assert [p.text for p in guard.select("胎压未更新，应该怎样复位？")] == list(pages.values())


@pytest.mark.parametrize("mutation", ["source", "content", "missing_page", "altered_text", "shortened_slice"])
def test_enrolled_but_unverifiable_evidence_stays_recognizable_and_unavailable(evidence_source, mutation):
    path, profile, document, pages = evidence_source
    document, pages, profile = deepcopy(document), deepcopy(pages), deepcopy(profile)
    if mutation in ("source", "content"):
        document[mutation + "_sha256"] = "c" * 64
    elif mutation == "missing_page":
        pages.pop(8)
    elif mutation == "altered_text":
        pages[8] = pages[8].replace("可能", "必然")
    else:
        profile["documents"][document["document_id"]]["guards"][0]["parts"][0]["end"] -= 1
        path.write_text(yaml.safe_dump(profile, allow_unicode=True), encoding="utf-8")
    guard, = load_evidence_guards(path, document, pages)
    assert guard.matches("胎压灯报警") and not guard.available
    assert guard.parts == ()


@pytest.mark.parametrize("field,value", [("page", True), ("start", -1), ("end", 0),
                                         ("sha256", "bad"), ("when_any", "复位"), ("when_ayn", ["复位"])])
def test_invalid_or_misspelled_source_coordinates_are_rejected(evidence_source, field, value):
    path, profile, document, pages = evidence_source
    profile["documents"][document["document_id"]]["guards"][0]["parts"][0][field] = value
    path.write_text(yaml.safe_dump(profile, allow_unicode=True), encoding="utf-8")
    with pytest.raises(ValueError):
        load_evidence_guards(path, document, pages)


def test_unenrolled_document_does_not_borrow_another_manuals_guard(evidence_source):
    path, _, document, pages = evidence_source
    document["document_id"] = "different-document"
    assert load_evidence_guards(path, document, pages) == ()


@pytest.fixture
def indexed_source(tmp_path):
    pages = {8: "胎压告警：此灯闪烁可能低于2.1bar；常亮表示监测故障。两种情况均须停车检查。",
             9: "胎压仅在压力恢复推荐值且显示未更新时，按维修说明复位；仍报警应停止并检修。",
             10: "雨刮器通过拨杆开关调节。"}
    bundle = build_index_bundle(
        [ExtractedPage(page, ("车辆使用", "胎压" if page != 10 else "雨刮器"), text)
         for page, text in pages.items()], document_id="synthetic-conditions", title="测试手册",
        publisher="Test", vehicle_model="test-car", vehicle_aliases=["test-car"],
        revision="2026-09-28", source_file="synthetic.pdf", source_sha256="a" * 64, source_pages=10,
    )
    index = tmp_path / "manual.json.gz"
    write_index_bundle(index, bundle)
    document = bundle["document"]
    catalog = tmp_path / "catalog.yaml"
    catalog.write_text(yaml.safe_dump({"schema_version": 1, "documents": {
        document["document_id"]: {key: document[key] for key in (
            "title", "publisher", "vehicle_model", "revision", "source_pages", "source_sha256", "content_sha256")}}}), encoding="utf-8")
    profile = tmp_path / "evidence.yaml"
    profile.write_text(yaml.safe_dump(_profile(document, {8: pages[8], 9: pages[9]}), allow_unicode=True), encoding="utf-8")
    return ManualIndexRetriever(index, catalog_path=catalog, source_evidence_path=profile), pages


def test_real_provider_materializes_the_exact_source_pages(indexed_source):
    kb, pages = indexed_source
    answer = asyncio.run(kb.guarded_evidence("胎压灯亮后如何复位？"))
    assert answer.available
    assert [chunk.page_start for chunk in answer.chunks] == [8, 9]
    assert all(pages[page] in answer.speech for page in (8, 9))
    assert all(chunk.document_id == "synthetic-conditions" for chunk in answer.chunks)


def test_broken_guard_resource_does_not_silently_disable_it(indexed_source):
    kb, _ = indexed_source
    folder = kb.index_path.parent
    resource = folder / "evidence.yaml"
    resource.write_text("schema_version: true\ndocuments: {}\n", encoding="utf-8")
    with pytest.raises(ManualIndexError, match="原文证据配置不可用"):
        ManualIndexRetriever(kb.index_path, catalog_path=folder / "catalog.yaml",
                             source_evidence_path=resource)


@pytest.mark.parametrize("query,model", [("胎压灯报警怎么办？", "other-car"),
                                        ("比亚迪汉胎压灯报警怎么办？", ""),
                                        ("雨刮器如何使用？", "")])
def test_provider_preserves_vehicle_and_subject_boundaries(indexed_source, query, model):
    kb, _ = indexed_source
    assert asyncio.run(kb.guarded_evidence(query, vehicle_model=model)) is None


def test_sibling_question_does_not_get_replaced_by_an_alert_from_the_other_clause(indexed_source):
    kb, _ = indexed_source
    agent = ManualRagAgent(retriever=kb)
    agent.llm.complete = AsyncMock(return_value="雨刮器通过拨杆开关调节。")
    agent._toc_router = None
    result = asyncio.run(run_handle(agent, "manual.query",
                                   raw_text="胎压黄灯亮了，再告诉我雨刮器如何使用？",
                                   slots={"question": "雨刮器如何使用"}))
    assert "雨刮器通过拨杆" in result.speech
    assert "source_evidence_guard" not in result.data
    agent.llm.complete.assert_awaited_once()


def test_guard_keeps_a_controlled_icon_outside_the_text_evidence_pages(tmp_path):
    from agents.manual_rag.tests.test_local_index_provider import _visual_package_path, _catalog_path
    from agents.manual_rag.src.index_format import load_manual_package
    index = _visual_package_path(tmp_path)
    loaded = load_manual_package(index)
    pages = {c["page_start"]: c["content"] for c in loaded.index["chunks"] if c["page_start"] in (89, 95)}
    profile = _profile(loaded.index["document"], pages)
    profile["documents"][loaded.index["document"]["document_id"]]["guards"][0]["subjects"] = ["后雾灯"]
    resource = tmp_path / "guarded-visual.yaml"
    resource.write_text(yaml.safe_dump(profile, allow_unicode=True), encoding="utf-8")
    kb = ManualIndexRetriever(index, catalog_path=_catalog_path(tmp_path, index), source_evidence_path=resource)
    result = asyncio.run(kb.guarded_evidence("仪表上的后雾灯图标是什么意思？"))
    images = [image for chunk in result.chunks for image in chunk.images]
    assert any(image.asset_id.endswith(":p0192:i04") and image.match_kind == "visual_caption" for image in images)
    assert {89, 95, 192} <= {chunk.page_start for chunk in result.chunks}
    assert all(text in result.speech for text in pages.values())


def test_visual_page_budget_never_silently_discards_required_conditions(indexed_source, monkeypatch):
    kb, _ = indexed_source
    monkeypatch.setattr(kb, "_visual_evidence", lambda *_: {
        page: [({"asset_id": f"synthetic:{page}"}, "visual_caption")] for page in (97, 98, 99)})
    result = asyncio.run(kb.guarded_evidence("胎压灯未更新如何复位？"))
    assert not result.available
    assert not result.chunks and not result.speech
    assert len(kb._evidence_guards[0].parts) == 2
