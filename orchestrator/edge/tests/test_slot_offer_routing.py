"""An answer to the cloud's choice question goes back to the cloud; the edge runs nothing for it.

The borrowed-name real-stack probe (2026-10-04) offered 「上海东方明珠广播电视塔 / 东方·明珠城」. Tapping the first
one sends the name as the next utterance; the edge fast path read 「广播」 as "turn the radio on", played the radio
locally, and the cloud's pending question never received its answer. The cloud already tells the edge what it is
waiting for (`FinalResult.slot_request`, suggestions taken from the real result); the edge remembers it per session.
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from cockpit.common.v1 import common_pb2
from cockpit.orchestrator.v1 import orchestrator_pb2

import server
from server import EdgeOrchestratorServicer

TOWER, LOCAL = "上海东方明珠广播电视塔", "东方·明珠城"


def _offer(*names, state="active", ttl_ms=300_000):
    final = orchestrator_pb2.FinalResult(speech="「东方明珠」我找到两处，您要去哪一个？")
    final.slot_request.operation_id = "op-1"
    final.slot_request.slot = "destination"
    final.slot_request.suggestions.extend(names)
    final.slot_request.state = state
    final.slot_request.server_now_ms = 1_000
    final.slot_request.expires_at_ms = 1_000 + ttl_ms
    return orchestrator_pb2.HandleEvent(final=final)


def _plain(speech="云端完成"):
    return orchestrator_pb2.HandleEvent(final=orchestrator_pb2.FinalResult(speech=speech))


def _conversation(turns, cloud_reply, clock=None):
    """turns: [(text, session)]；cloud_reply(text) → 云端这一轮的事件列表；clock: 每轮开始时候选到期时钟的读数。
    返回 (上云的原话, 每轮车态变化)。"""
    service = EdgeOrchestratorServicer()
    now = [0.0]
    saved_clock = server._offer_clock
    if clock is not None:
        server._offer_clock = lambda: now[0]
    cloud_texts: list[str] = []

    async def fake_cloud_handle(req):
        cloud_texts.append(req.text)
        for event in cloud_reply(req.text):
            yield event

    async def noop(*args, **kwargs):
        return None

    service.cloud.handle = fake_cloud_handle
    service.obs.emit_span = noop
    service.obs.emit_turn = noop
    changes: list[set[str]] = []

    async def go():
        for index, (text, session) in enumerate(turns):
            if clock is not None:
                now[0] = clock[index]
            before = dict(service.val.state)
            request = orchestrator_pb2.HandleRequest(
                text=text, session_id=session, request_id=f"req-{index}",
                context=common_pb2.ContextRef(user_id="u1", vehicle_id="v1"), meta={"trace_id": f"trace-{index}"})
            _ = [ev async for ev in service.Handle(request, None)]
            changes.append({k for k in before if before[k] != service.val.state.get(k)})

    try:
        asyncio.run(go())
    finally:
        server._offer_clock = saved_clock
    return cloud_texts, changes


def _reply_with_offer(**offer_kw):
    return lambda text: [_offer(TOWER, LOCAL, **offer_kw)] if "东方明珠要开多久" in text else [_plain()]


def test_without_an_offer_the_name_is_a_local_radio_command():
    """反向对照：这条缺陷的前提——没有候选在等时，端侧规则确实把这个名字当成「打开广播」。"""
    cloud_texts, changes = _conversation([(TOWER, "s1")], lambda text: [_plain()])
    assert cloud_texts == [] and "media" in changes[0]


def test_the_offered_name_goes_back_to_the_cloud():
    cloud_texts, changes = _conversation(
        [("去东方明珠要开多久", "s1"), (TOWER, "s1"), ("东方明珠广播电视塔", "s1")], _reply_with_offer())
    assert cloud_texts == ["去东方明珠要开多久", TOWER]     # 第二句回到云端；云端这次没再问，候选清掉
    assert changes[1] == set()                              # 收音机没开


def test_a_command_during_the_question_still_runs_locally_and_keeps_the_offer():
    cloud_texts, changes = _conversation(
        [("去东方明珠要开多久", "s1"), ("打开空调", "s1"), (TOWER, "s1")], _reply_with_offer())
    assert "hvac_on" in changes[1]
    assert cloud_texts == ["去东方明珠要开多久", TOWER] and changes[2] == set()


def test_expired_held_or_other_session_offers_do_not_capture_the_turn():
    for offer_kw, session in (({"ttl_ms": 0}, "s1"), ({"state": "held"}, "s1"), ({}, "s2")):
        cloud_texts, changes = _conversation(
            [("去东方明珠要开多久", "s1"), (TOWER, session)], _reply_with_offer(**offer_kw))
        assert cloud_texts == ["去东方明珠要开多久"] and "media" in changes[1], (offer_kw, session)


def test_an_offer_expires_when_the_server_said_it_would():
    """云端给的有效期 60 秒：59 秒时说候选名仍回云端，61 秒后就是一句普通的话（这里本地规则会开收音机）。"""
    for second_turn_at, back_to_cloud in ((59.0, True), (61.0, False)):
        cloud_texts, changes = _conversation(
            [("去东方明珠要开多久", "s1"), (TOWER, "s1")], _reply_with_offer(ttl_ms=60_000),
            clock=[0.0, second_turn_at])
        assert (cloud_texts[-1] == TOWER) is back_to_cloud and ("media" in changes[1]) is not back_to_cloud


def test_a_later_cloud_answer_without_a_question_clears_the_offer():
    cloud_texts, changes = _conversation(
        [("去东方明珠要开多久", "s1"), ("今天深圳天气怎么样", "s1"), (TOWER, "s1")], _reply_with_offer())
    assert cloud_texts == ["去东方明珠要开多久", "今天深圳天气怎么样"] and "media" in changes[2]


def test_no_local_fallback_when_the_cloud_says_nothing_to_the_answer():
    """云端对这句回答给了个空结果（零输出）：零输出兜底会对原话重新分类并本地执行——回答不是指令，不许。"""
    def reply(text):
        return [_offer(TOWER, LOCAL)] if "东方明珠要开多久" in text else [_plain("")]
    cloud_texts, changes = _conversation([("去东方明珠要开多久", "s1"), (TOWER, "s1")], reply)
    assert cloud_texts == ["去东方明珠要开多久", TOWER] and changes[1] == set()


def test_an_offer_from_the_cloud_half_of_a_mixed_turn_is_remembered_too():
    cloud_texts, changes = _conversation(
        [("打开空调，再算一下去东方明珠要开多久", "s1"), (TOWER, "s1")], _reply_with_offer())
    assert "hvac_on" in changes[0] and cloud_texts[-1] == TOWER and changes[1] == set()
