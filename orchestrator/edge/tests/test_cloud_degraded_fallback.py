"""云端降级兜底（CLOUD-DEGRADED-LOCAL）的三道挡板（B1）。

这条分支曾是一条完整的执行旁路：云端出任何空结果故障（LLM 超时 / 解析失败 /
chitchat 空回复）时，它重新分类原话就直接下发 VAL，**不过 `_confirm_required`**——
「打开后备箱」于是可以无确认打开，不需要恶意输入。

三道挡板各自的覆盖：
① 云端已给过任何输出（含流式 speech_delta / action）→ 不兜底（防双执行）；
② 危险对象 → 不执行、不静默，播降级话术并留 CLOUD-DEGRADED-DANGER-BLOCKED；
③ 非危险车控 → 兜底行为不变（这条分支存在的意义，不许被本批误伤）。
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from cockpit.common.v1 import common_pb2
from cockpit.orchestrator.v1 import orchestrator_pb2

from server import EdgeOrchestratorServicer


def _drive(srv, text: str):
    req = orchestrator_pb2.HandleRequest(
        text=text, session_id="s-degraded", meta={"memory_enabled": "false"})

    async def run():
        return [ev async for ev in srv.Handle(req, None)]

    return asyncio.run(run())


def _servicer(cloud_events):
    """构造一个云端按 `cloud_events` 回放的 servicer。"""
    srv = EdgeOrchestratorServicer()

    async def fake_cloud_handle(req):
        for ev in cloud_events:
            yield ev

    srv.cloud.handle = fake_cloud_handle
    return srv


# QA 卡 Q13（2026-08-16）：本文件原来用「播放音乐」当「会上云的可执行原话」——
# 它当时确实上云（`classify()` 产 `music.play`，不在 `LOCAL_INTENTS`）。收敛后
# 媒体落 `media.play` **变成端侧快路径**，于是三条断言直接红，另外两条
# **变成假绿**：`state["media"] == "playing"` 照样成立，但成立的原因是端侧秒回，
# 云端兜底那条路一次都没走到。假绿比红更贵——它让一条 P0 回归探针悄悄退休。
#
# 兜底路径需要的原话要同时满足三条：端侧**认得出**（否则 local_structured 为空）、
# 名字**不在 LOCAL_INTENTS**（否则走快路径不上云）、VAL **执行得了**（否则断言无从判）。
# 场景模式正是这一形态：`scene_mode.set` 刻意不入 LOCAL_INTENTS（命名场景由云端
# scene-orchestrator 编排），而 VAL 能设状态位。
_CLOUD_ROUTED = "开启露营模式"


def _empty_final():
    return orchestrator_pb2.HandleEvent(final=orchestrator_pb2.FinalResult(speech=""))


def _speeches(events) -> str:
    return "".join(ev.final.speech for ev in events
                   if ev.WhichOneof("event") == "final")


# ── 挡板 ②：危险对象不兜底 ────────────────────────────────────────────────

def test_dangerous_object_not_executed_on_empty_cloud_final(caplog):
    """P0 回归探针：「打开后备箱」+ 云端空 final → 后备箱必须纹丝不动。"""
    srv = _servicer([_empty_final()])
    before = dict(srv.val.state)

    with caplog.at_level("WARNING"):
        events = _drive(srv, "打开后备箱")

    assert srv.val.state == before, f"危险动作被降级兜底执行了：{srv.val.state}"
    assert "CLOUD-DEGRADED-DANGER-BLOCKED" in caplog.text
    speech = _speeches(events)
    assert "确认" in speech, f"被挡后没有给用户任何交代：{speech!r}"
    # 不静默，也不能反手发一个「已执行」的动作卡
    assert not [a for ev in events if ev.WhichOneof("event") == "final"
                for a in ev.final.actions]


def test_dangerous_object_blocked_for_every_confirm_object():
    """逐个危险对象过一遍——挡的是「需要确认的对象」这一类，不是「后备箱」这一条。"""
    texts = {"trunk": "打开后备箱", "door_lock": "解锁车门"}
    for obj, text in texts.items():
        srv = _servicer([_empty_final()])
        before = dict(srv.val.state)
        _drive(srv, text)
        assert srv.val.state == before, f"{obj} 被降级兜底执行了"


def test_dangerous_object_still_blocked_when_cloud_yields_nothing_at_all():
    """云端一个事件都不发（连接建立但流为空）走的是 `not got` 早退分支，
    同样不许落到本地执行。"""
    srv = _servicer([])
    before = dict(srv.val.state)
    _drive(srv, "打开后备箱")
    assert srv.val.state == before


def test_dangerous_object_blocked_when_cloud_raises():
    """云端抛异常 → 既有 degrade 早退分支；确认状态零变化（不回归）。"""
    srv = EdgeOrchestratorServicer()

    async def boom(req):
        raise RuntimeError("cloud down")
        yield  # pragma: no cover - 使其成为 async generator

    srv.cloud.handle = boom
    before = dict(srv.val.state)
    events = _drive(srv, "打开后备箱")
    assert srv.val.state == before
    assert "网络" in _speeches(events)


# ── 挡板 ①：云端已有输出就不兜底 ──────────────────────────────────────────

def test_streamed_delta_then_empty_final_does_not_trigger_fallback():
    """本轮核实中发现的同族缺口（评审未提）：流式话术已经播出去、final.speech 恰为空。
    旧判定只看 final，于是认为「云端无输出」而本地补执行——双执行。"""
    srv = _servicer([
        orchestrator_pb2.HandleEvent(speech_delta="好的，"),
        orchestrator_pb2.HandleEvent(speech_delta="正在处理"),
        _empty_final(),
    ])
    before = dict(srv.val.state)
    _drive(srv, _CLOUD_ROUTED)
    assert srv.val.state == before, "云端已流出话术，端侧仍补了一次执行"


def test_streamed_action_then_empty_final_does_not_trigger_fallback():
    srv = _servicer([
        orchestrator_pb2.HandleEvent(
            action=common_pb2.AgentAction(type="info.card")),
        _empty_final(),
    ])
    before = dict(srv.val.state)
    _drive(srv, _CLOUD_ROUTED)
    assert srv.val.state == before


def test_empty_delta_does_not_count_as_output():
    """空 delta 不算输出——否则一个空串就能把兜底整条关掉。"""
    srv = _servicer([
        orchestrator_pb2.HandleEvent(speech_delta=""),
        _empty_final(),
    ])
    _drive(srv, _CLOUD_ROUTED)
    assert srv.val.state.get("scene_mode") == "camping"


# ── 挡板 ③：非危险车控兜底不变 ────────────────────────────────────────────

def test_non_dangerous_fallback_still_executes():
    """兜底存在的意义：LLM 规划失败但原意是明确车控——这条必须活着。"""
    srv = _servicer([_empty_final()])
    events = _drive(srv, _CLOUD_ROUTED)

    assert srv.val.state.get("scene_mode") == "camping"
    finals = [ev.final for ev in events if ev.WhichOneof("event") == "final"]
    assert any(f.actions for f in finals), "兜底执行成功却没回动作卡"


def test_cloud_with_speech_skips_fallback_entirely():
    """云端正常作答 → 兜底整段不进（既有行为，防本批把判定写反）。"""
    srv = _servicer([orchestrator_pb2.HandleEvent(
        final=orchestrator_pb2.FinalResult(speech="已经在放了"))])
    before = dict(srv.val.state)
    _drive(srv, _CLOUD_ROUTED)
    assert srv.val.state == before
