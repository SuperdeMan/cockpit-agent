"""检索用哪句话 + 目录路由（2026-09-26，collector 真实问法驱动）。

两件事都在检索之前：
  · 原话是权威。collector 里规划器把「它有几档」补全成「座椅加热有几档」、把「告诉我空调有
    哪些模式，然后打开后备箱」拆出「空调有哪些模式」，而 Agent 一直拿原话去检索——必然零命中。
  · 词法零命中 / 没把握时让 LLM 在目录（封闭集合）里选章节；它只选编号，不作答。
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from agents._sdk.testing import run_handle
from agents.manual_rag.src.agent import ManualRagAgent, _merge_routed, _retrieval_question
from agents.manual_rag.src.providers.base import Chunk
from agents.manual_rag.src.toc_router import (
    SCOPE_OTHER_VEHICLE,
    SCOPE_THIS_VEHICLE,
    parse_verdict,
)
from agents.manual_rag.tests.test_colloquial_retrieval import _provider


# ── 检索用哪句话 ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,slot,expected", [
    ("座椅加热有几个档位", "座椅加热有几个档位", ("座椅加热有几个档位", "raw")),
    ("座椅有哪些加热档位", "座椅加热档位有哪些", ("座椅有哪些加热档位", "raw")),   # 单句改写：原话优先
    ("它有几档", "座椅加热有几档", ("座椅加热有几档", "anaphora")),
    ("那它的纯电续航呢", "理想L9的纯电续航是多少", ("理想L9的纯电续航是多少", "anaphora")),
    # 规划器拆成了多步，本步只占一个分句
    ("打开后备箱，再告诉我空调有哪些模式", "空调有哪些模式", ("空调有哪些模式", "clause")),
    ("告诉我空调有哪些模式，然后打开后备箱", "空调有哪些模式", ("空调有哪些模式", "clause")),
    # 规划器把一个多问句合并成一步：原话才带全部诉求
    ("胎压黄灯亮了，还能继续开吗？应该补到多少？", "胎压黄灯亮了应该补到多少",
     ("胎压黄灯亮了，还能继续开吗？应该补到多少？", "raw")),
    ("", "空调有哪些模式", ("空调有哪些模式", "slot")),
    ("空调有哪些模式", "", ("空调有哪些模式", "raw")),
])
def test_retrieval_question_prefers_the_users_words(raw, slot, expected):
    assert _retrieval_question(raw, slot) == expected


def _agent(tmp_path, *responses):
    provider = _provider(tmp_path)
    provider.provenance_mode = "real"
    provider.provenance_vendor = provider.document["document_id"]
    agent = ManualRagAgent(retriever=provider)
    agent.llm.complete = AsyncMock(side_effect=list(responses))
    return agent, provider


def _entry(provider, label: str) -> str:
    return next(entry.entry_id for entry in provider.table_of_contents()
                if entry.label == label)


def test_anaphoric_follow_up_retrieves_with_the_resolved_question(tmp_path):
    agent, _provider_ = _agent(tmp_path, "座椅加热可切换 3-2-1-off 三挡。")
    result = asyncio.run(run_handle(
        agent, "manual.query", slots={"question": "座椅加热有几档"}, raw_text="它有几档"))
    assert [chunk["page_start"] for chunk in result.ui_card["chunks"]][:1] == [37]
    assert result.data["retrieval_basis"] == "anaphora"
    prompt = agent.llm.complete.await_args[0][0][1]["content"]
    assert "【用户原话】它有几档" in prompt and "【问题】座椅加热有几档" in prompt


def test_slot_vetoed_by_words_the_user_never_said_falls_back_to_the_users_words(tmp_path):
    """规划器拆步时往槽里加了用户没说的词（真栈：「胎压应该补到多少kPa」，手册只写 bar），槽被
    专名闸否决、原话却没有：否决它的词不是用户的，回到原话检索。"""
    agent, _ = _agent(tmp_path, json.dumps({"scope": SCOPE_THIS_VEHICLE, "sections": []}),
                      "座椅加热可切换 3-2-1-off 三挡。")
    result = asyncio.run(run_handle(
        agent, "manual.query", slots={"question": "座椅加热有几个档位kPa"},
        raw_text="打开后备箱，再告诉我座椅加热有几个档位"))
    assert [chunk["page_start"] for chunk in result.ui_card["chunks"]][:1] == [37]
    assert result.data["retrieval_basis"] == "raw_slot_vetoed"


def test_vetoed_anaphora_resolution_is_not_undone(tmp_path):
    """指代补全被否决不回退：原话只有代词，补全出来的别家车型正该零命中。"""
    agent, _ = _agent(tmp_path)
    result = asyncio.run(run_handle(
        agent, "manual.query", slots={"question": "理想L9的纯电续航是多少"},
        raw_text="那它的纯电续航呢"))
    assert agent.llm.complete.await_count == 0
    assert not result.ui_card["sources"]


def test_safety_level_stays_on_the_users_words(tmp_path):
    """规划器的槽只参与检索：它写进去的告警词不能凭空把这轮升成安全告警。"""
    agent, _ = _agent(tmp_path, "座椅加热可切换 3-2-1-off 三挡。")
    result = asyncio.run(run_handle(
        agent, "manual.query", slots={"question": "座椅加热故障灯亮了有几档"},
        raw_text="它有几档"))
    assert not result.data.get("safety_signal")
    assert "_safety_alert" not in result.data


# ── 目录路由 ──────────────────────────────────────────────────────────────

def test_lexical_miss_is_rescued_by_toc_routing(tmp_path):
    agent, provider = _agent(tmp_path)
    sentinel = _entry(provider, "座椅和安全 > 车辆安全 > 哨兵模式")
    agent.llm.complete = AsyncMock(side_effect=[
        json.dumps({"scope": SCOPE_THIS_VEHICLE, "sections": [sentinel]}),
        "可以，开启哨兵模式后车辆停放期间会录制视频。"])
    result = asyncio.run(run_handle(
        agent, "manual.query", raw_text="停车时有人刮车能录下来吗"))
    assert result.ui_card["chunks"][0]["page_start"] == 53
    assert result.data["retrieval"] == "toc_router"
    assert result.data["toc_sections"] == [sentinel]
    route_prompt = agent.llm.complete.await_args_list[0][0][0]
    assert "【目录】" in route_prompt[1]["content"]
    # 路由只看目录，不看正文：它没有材料可以用来作答。
    assert "录制视频" not in route_prompt[1]["content"]


def test_zero_hit_after_routing_never_calls_the_generator(tmp_path):
    agent, _ = _agent(tmp_path, json.dumps({"scope": SCOPE_THIS_VEHICLE, "sections": []}))
    result = asyncio.run(run_handle(
        agent, "manual.query", raw_text="停车时有人刮车能录下来吗"))
    assert agent.llm.complete.await_count == 1        # 只有路由，没有生成
    assert "没有查到" in result.speech
    assert not result.ui_card["sources"]


def test_router_failure_falls_back_to_lexical_result(tmp_path):
    agent, _ = _agent(tmp_path, RuntimeError("LLM Gateway error: UNAVAILABLE"))
    result = asyncio.run(run_handle(
        agent, "manual.query", raw_text="停车时有人刮车能录下来吗"))
    assert "没有查到" in result.speech
    assert result.data["retrieval"] == "lexical"


def test_router_cannot_invent_sections(tmp_path):
    agent, _ = _agent(tmp_path, json.dumps({"scope": SCOPE_THIS_VEHICLE,
                                            "sections": ["T999", "p53", "哨兵模式"]}))
    result = asyncio.run(run_handle(
        agent, "manual.query", raw_text="停车时有人刮车能录下来吗"))
    assert agent.llm.complete.await_count == 1
    assert not result.ui_card["sources"]


def test_other_vehicle_verdict_voids_lexical_near_match(tmp_path):
    """奇骏不在品牌表里：词法会拿本车储电量页凑上，路由判「其他车型」才作废。"""
    agent, _ = _agent(tmp_path, json.dumps({"scope": SCOPE_OTHER_VEHICLE, "sections": []}))
    result = asyncio.run(run_handle(
        agent, "manual.query", raw_text="奇骏的电池容量是多少"))
    assert agent.llm.complete.await_count == 1
    assert result.data["retrieval"] == "toc_router_rejected"
    assert not result.ui_card["sources"]
    assert "73.6" not in result.speech


def test_other_vehicle_verdict_does_not_void_a_question_with_only_manual_words(tmp_path):
    """没有手册不认识的实词时不问路由，更谈不上被它否决。"""
    agent, _ = _agent(tmp_path, "座椅加热可切换 3-2-1-off 三挡。")
    result = asyncio.run(run_handle(agent, "manual.query", raw_text="座椅加热有几个档位"))
    assert agent.llm.complete.await_count == 1        # 只有生成
    assert result.data["retrieval"] == "lexical"


@pytest.mark.parametrize("question", [
    "比亚迪海豹的电池容量是多少",      # 外车型标记
    "油箱能加多少升油",                # 本车没有的对象
    "仪表上黄色感叹号是什么意思",      # 认图标只认受控视觉目录
    "这是什么",                        # 没有实词
])
def test_blocked_questions_never_reach_the_router(tmp_path, question):
    agent, _ = _agent(tmp_path, json.dumps({"scope": SCOPE_THIS_VEHICLE, "sections": ["T001"]}))
    result = asyncio.run(run_handle(agent, "manual.query", raw_text=question))
    assert agent.llm.complete.await_count == 0
    assert not result.ui_card["sources"]


def test_covering_hit_without_a_section_hit_still_routes(tmp_path):
    """用户的词全在词法首页的正文里、却不在它的章节名里（「吹风模式」只写在「空调控制」页
    正文里）：覆盖率满也不算有把握，再按目录路由。真实手册上「运动模式到底在哪切换」撞上
    讲特殊路况的页、「停车监控那个功能在哪打开」撞上智能领航页，覆盖率都在 0.79 以上。
    词法覆盖率够线，所以词法首页钉在第一、路由页跟在后面。"""
    agent, provider = _agent(tmp_path)
    seat = _entry(provider, "座椅和安全 > 座椅 > 座椅加热")
    agent.llm.complete = AsyncMock(side_effect=[
        json.dumps({"scope": SCOPE_THIS_VEHICLE, "sections": [seat]}),
        "吹风模式在空调控制界面里切换。"])
    result = asyncio.run(run_handle(agent, "manual.query", raw_text="吹风模式在哪调"))
    assert [chunk["page_start"] for chunk in result.ui_card["chunks"]][:2] == [197, 37]
    assert result.data["retrieval"] == "lexical+toc_router"


def test_mock_corpus_has_no_router(monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_VENDOR", "mock")
    monkeypatch.delenv("REQUIRE_REAL_PROVIDERS", raising=False)
    assert ManualRagAgent()._toc_router is None


def test_merge_puts_pages_both_routes_agree_on_first_then_alternates():
    """两路共选的页排最前（按路由顺序）；其余路由页与词法页交替。路由页一律排前曾把
    「三元锂电池平时充到多少」正确的词法首页（充电限值建议页）挤到第二。"""
    lexical = [Chunk(page_start=page, coverage=0.8) for page in (213, 247, 275)]
    routed = [Chunk(page_start=page) for page in (212, 213)]
    assert [chunk.page_start for chunk in _merge_routed(routed, lexical)] == [213, 212, 247, 275]
    assert _merge_routed(routed, lexical)[0].coverage == 0.8      # 共选页用词法那一块
    assert [chunk.page_start for chunk in _merge_routed(routed, [])] == [212, 213]
    many = [Chunk(page_start=page) for page in (1, 2, 3)]
    assert [chunk.page_start for chunk in _merge_routed(many, lexical)] == [1, 213, 2, 247]


def test_merge_pins_a_covering_lexical_top_page():
    """词法首页覆盖率够线、只是章节名对不上时，它钉在第一：真实手册「制动液多久换一次」的
    正确首页是保养计划页，按共选页优先会被路由选中的更换章节页挤到第二。其后仍是共选页，
    再路由页与其余词法页交替、路由页先（「后备箱都有哪几种打开方式」要的开闭页在路由里）。"""
    lexical = [Chunk(page_start=page, coverage=0.9) for page in (252, 264, 242)]
    routed = [Chunk(page_start=page) for page in (264, 265)]
    pinned = _merge_routed(routed, lexical, lexical_first=True)
    assert [chunk.page_start for chunk in pinned] == [252, 264, 265, 242]
    assert [chunk.page_start for chunk in _merge_routed(routed, lexical)] == [264, 265, 252, 242]


# ── 路由输出的解析 ────────────────────────────────────────────────────────

def test_parse_verdict_keeps_only_real_ids_in_order():
    valid = ["T001", "T002", "T003", "T004"]
    raw = '```json\n{"scope": "this_vehicle", "sections": ["T003", "t001", "T003", "T999", "T002", "T004"]}\n```'
    verdict = parse_verdict(raw, valid)
    assert verdict.sections == ("T003", "T001", "T002")
    assert verdict.scope == SCOPE_THIS_VEHICLE


@pytest.mark.parametrize("raw", ["", "没有相关章节", '{"sections": "T001"}', "[1, 2]",
                                 '{"scope": "maybe", "sections": []}'])
def test_parse_verdict_failures_are_silence(raw):
    verdict = parse_verdict(raw, ["T001"])
    assert verdict.sections == () and verdict.scope == ""


def test_other_vehicle_verdict_carries_no_sections():
    verdict = parse_verdict('{"scope": "other_vehicle", "sections": ["T001"]}', ["T001"])
    assert verdict.sections == () and verdict.scope == SCOPE_OTHER_VEHICLE
