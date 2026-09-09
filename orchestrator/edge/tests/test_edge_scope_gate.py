"""端侧 T0 执行出口的会话授权闸（AR05 F07）。

## 修复前的反例（2026-09-09 离线复现，零网络）

网关按 token 注入 `meta["granted_scopes"]`；云侧 `context.build_context` 一直照它算
`granted_permissions`、`dispatch` 按 `check_permission` 硬拒。**端侧从来没读过这个键**：
token 只授 `location.read` 时，一句「打开车窗」走快路径 B 直接进 VAL——
`val.state["window"] == "open"`，还回了一条 `vehicle.control` 动作。

四个本地执行出口（多意图 A、混合 A2、单意图 B、云端降级兜底）以及云端回流分发
都是同一形态。本文件对每个出口各留一条负例 + 一条正例：**只验负例时，缺陷会躲在
没验的那半**（AR03/B2 同族纪律）。

## 判据归属

- 需要哪个 scope：`scope_gate.required_scopes` 按端侧 manifest 声明（edge-vehicle→
  `vehicle.control`、edge-media→`media.control`），不新建对象词表；
- 允不允许：`security.permission.check_permission`（运行时唯一权限决策）；
- 没有 scope 时怎么办：`security.session_scopes`（`PERMISSIONS_FAIL_OPEN` 同一开关）。
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from cockpit.common.v1 import common_pb2
from cockpit.orchestrator.v1 import orchestrator_pb2

import scope_gate
from server import EdgeOrchestratorServicer


def _drive(srv, text: str, *, scopes: str | None = "location.read"):
    meta = {"memory_enabled": "false"}
    if scopes is not None:
        meta["granted_scopes"] = scopes
    req = orchestrator_pb2.HandleRequest(text=text, session_id="s-scope", meta=meta)

    async def run():
        return [ev async for ev in srv.Handle(req, None)]

    return asyncio.run(run())


def _finals(events):
    return [e.final for e in events if e.WhichOneof("event") == "final"]


def _all_actions(events):
    return [a for f in _finals(events) for a in f.actions]


def _speech(events):
    return "".join(f.speech for f in _finals(events))


def _servicer(cloud_events=None):
    srv = EdgeOrchestratorServicer()
    if cloud_events is not None:
        async def fake_cloud_handle(req):
            for ev in cloud_events:
                yield ev
        srv.cloud.handle = fake_cloud_handle
    return srv


# ─── 快路径 B（单意图秒回）：反例 + 正例 ───

def test_single_local_command_without_vehicle_scope_does_not_execute():
    """反例本体：只有 location.read 的 token 说「打开车窗」——车窗不许动。"""
    srv = _servicer()
    before = dict(srv.val.state)

    events = _drive(srv, "打开车窗", scopes="location.read")

    assert srv.val.state == before, "缺 vehicle.control 却改了车态——闸没生效"
    assert _all_actions(events) == [], "被拒还下发动作，客户端会显示成『已执行』"
    assert "车辆控制" in _speech(events)


def test_single_local_command_with_vehicle_scope_still_executes():
    """正例防误伤：授了 vehicle.control 的照常秒回。"""
    srv = _servicer()

    events = _drive(srv, "打开车窗", scopes="vehicle.control")

    assert srv.val.state["window"] == "open"
    assert [a.type for a in _all_actions(events)] == ["vehicle.control"]


def test_media_command_needs_media_scope_not_vehicle_scope():
    """媒体对象按 edge-media 的声明要 media.control——两个域各判各的，不混授。"""
    srv = _servicer()
    events = _drive(srv, "播放音乐", scopes="vehicle.control")
    assert srv.val.state.get("media") != "playing"
    assert "媒体控制" in _speech(events)

    srv2 = _servicer()
    _drive(srv2, "播放音乐", scopes="media.control")
    assert srv2.val.state.get("media") == "playing"


# ─── 快路径 A（多意图并行）───

def test_multi_local_commands_denied_without_scope():
    srv = _servicer()
    before = dict(srv.val.state)

    events = _drive(srv, "打开车窗和空调", scopes="location.read")

    assert srv.val.state == before
    assert _all_actions(events) == []


def test_multi_local_commands_execute_with_scope():
    srv = _servicer()
    _drive(srv, "打开车窗和空调", scopes="vehicle.control")
    assert srv.val.state["window"] == "open"
    assert srv.val.state["hvac_on"] is True


# ─── 快路径 A2（混合：本地 + 上云）───

def test_mixed_local_part_denied_but_cloud_part_still_routed():
    """被拒的是车控那半，另一半仍要上云——不能因为权限不足把整轮吞掉。"""
    cloud_seen: list[str] = []
    srv = _servicer()

    async def fake_cloud_handle(req):
        cloud_seen.append(req.text)
        yield orchestrator_pb2.HandleEvent(
            final=orchestrator_pb2.FinalResult(speech="今天晴。"))

    srv.cloud.handle = fake_cloud_handle
    before = dict(srv.val.state)

    events = _drive(srv, "打开车窗，然后帮我查一下天气", scopes="location.read")

    assert srv.val.state == before
    assert cloud_seen, "云段被一起吞掉了"
    assert "车辆控制" in _speech(events)


def test_mixed_denied_local_is_not_reported_as_executed_to_cloud():
    cloud_reqs: list[orchestrator_pb2.HandleRequest] = []
    srv = _servicer()

    async def fake_cloud_handle(req):
        cloud_reqs.append(req)
        yield orchestrator_pb2.HandleEvent(
            final=orchestrator_pb2.FinalResult(speech="今天晴。"))

    srv.cloud.handle = fake_cloud_handle
    _drive(srv, "打开车窗，然后帮我查一下天气", scopes="location.read")

    assert cloud_reqs
    assert "_edge_executed" not in dict(cloud_reqs[0].meta)


# ─── 云端降级兜底 ───

def test_cloud_degraded_local_fallback_respects_scope():
    """云端零输出兜底那条路同样过闸——它是历史上出过完整执行旁路的分支（B1）。"""
    empty_final = orchestrator_pb2.HandleEvent(
        final=orchestrator_pb2.FinalResult(speech=""))
    srv = _servicer(cloud_events=[empty_final])
    before = dict(srv.val.state)

    _drive(srv, "开启露营模式", scopes="location.read")

    assert srv.val.state == before


def test_cloud_degraded_local_fallback_still_works_with_scope():
    empty_final = orchestrator_pb2.HandleEvent(
        final=orchestrator_pb2.FinalResult(speech=""))
    srv = _servicer(cloud_events=[empty_final])

    _drive(srv, "开启露营模式", scopes="vehicle.control")

    assert srv.val.state.get("scene_mode") == "camping"


# ─── 云端回流动作分发（纵深）───

def test_cloud_returned_action_without_scope_is_not_dispatched():
    """某个没声明 vehicle.control 的 Agent 吐出车控动作时，只有这一层拦得住。"""
    from google.protobuf import struct_pb2
    payload = struct_pb2.Struct()
    payload.update({"command": "hvac.on"})
    action = common_pb2.AgentAction(type="vehicle.control", payload=payload)
    final = orchestrator_pb2.FinalResult(speech="已为您打开空调")
    final.actions.append(action)
    srv = _servicer(cloud_events=[orchestrator_pb2.HandleEvent(final=final)])

    _drive(srv, "帮我查一下今天的新闻", scopes="location.read")

    assert srv.val.state.get("hvac_on") is not True


# ─── 无 granted_scopes 时的兜底：与云侧同一个开关 ───

def test_missing_scopes_fail_open_keeps_poc_behaviour(monkeypatch):
    """没有 granted_scopes + PERMISSIONS_FAIL_OPEN 默认 → PoC 默认集，逐字保持现状。"""
    monkeypatch.delenv("PERMISSIONS_FAIL_OPEN", raising=False)
    srv = _servicer()

    _drive(srv, "打开车窗", scopes=None)

    assert srv.val.state["window"] == "open"


def test_missing_scopes_fail_closed_blocks_vehicle_control(monkeypatch):
    """量产档 PERMISSIONS_FAIL_OPEN=false → 无 scope 一律不执行（与云侧同答）。"""
    monkeypatch.setenv("PERMISSIONS_FAIL_OPEN", "false")
    srv = _servicer()
    before = dict(srv.val.state)

    _drive(srv, "打开车窗", scopes=None)

    assert srv.val.state == before


# ─── 需求 scope 的来源是声明面，不是本文件里的表 ───

def test_required_scopes_come_from_edge_manifests():
    """新增端侧车控能力时不用回来改闸：它读的是注册给 Registry 的那份声明。"""
    from capabilities import build_edge_manifests

    manifests = {m.agent_id: m for m in build_edge_manifests()}
    vehicle_intent = sorted(manifests["edge-vehicle"].edge_intents)[0]
    media_intent = sorted(manifests["edge-media"].edge_intents)[0]

    assert scope_gate.required_scopes(intent_name=vehicle_intent) == ["vehicle.control"]
    assert scope_gate.required_scopes(intent_name=media_intent) == ["media.control"]


def test_unknown_command_requires_vehicle_control():
    """认不出来的本地写操作按最强要求判，不因为「没认出来」放行。"""
    assert scope_gate.required_scopes(intent_name="something.unheard_of") == [
        "vehicle.control"]


def test_denial_speech_does_not_point_at_device_permissions():
    """AR05 V07：业务 scope 不足不许把用户导去系统权限页。"""
    speech = scope_gate.denial_speech(["vehicle.control"])
    assert "车辆控制" in speech
    for wrong in ("系统设置", "麦克风", "定位", "摄像头"):
        assert wrong not in speech


# ─── 结构化问题（AR05 §11.3）：被拒不能只剩一句话术 ───

def _issues(events):
    return [i for f in _finals(events) for i in f.issues]


def test_scope_denial_emits_structured_issue_with_capability_recovery():
    srv = _servicer()

    events = _drive(srv, "打开车窗", scopes="location.read")

    issues = _issues(events)
    assert [i.code for i in issues] == ["permission.scope_missing"]
    issue = issues[0]
    assert issue.severity == "error"
    assert issue.scope == "capability"
    assert list(issue.affected_capabilities) == ["vehicle.control"]
    # 恢复出口指能力/连接配置，**不是**系统权限页
    assert [r.kind for r in issue.recovery] == ["open_capability_settings"]


def test_val_safety_rejection_emits_its_own_issue_code():
    """VAL 安全门控拒绝与权限不足是两种病，code 必须分得开。"""
    srv = _servicer()
    srv.val.state["speed_kmh"] = 120        # 高速行驶：开窗被安全门控挡下

    events = _drive(srv, "打开车窗", scopes="vehicle.control")

    codes = [i.code for i in _issues(events)]
    assert codes == ["safety.val_rejected"], codes
    assert srv.val.state["window"] != "open"


def test_successful_local_turn_carries_no_issues():
    """正例：没出问题就一条 issue 都不该有（恒空字段会让客户端天天判空）。"""
    srv = _servicer()
    events = _drive(srv, "打开车窗", scopes="vehicle.control")
    assert _issues(events) == []


def test_issue_is_stamped_once_across_mixed_local_and_cloud_finals():
    """混合路径两个 final：问题只盖第一个，不重复播报。"""
    srv = _servicer()

    async def fake_cloud_handle(req):
        yield orchestrator_pb2.HandleEvent(
            final=orchestrator_pb2.FinalResult(speech="今天晴。"))

    srv.cloud.handle = fake_cloud_handle
    events = _drive(srv, "打开车窗，然后帮我查一下天气", scopes="location.read")

    assert [i.code for i in _issues(events)] == ["permission.scope_missing"]


def test_issue_carries_request_id_for_attribution():
    """归属：客户端要能把问题挂到具体这一次请求上（§11.5-1）。"""
    srv = _servicer()
    req = orchestrator_pb2.HandleRequest(
        text="打开车窗", session_id="s-scope", request_id="req-42",
        meta={"memory_enabled": "false", "granted_scopes": "location.read"})

    async def run():
        return [ev async for ev in srv.Handle(req, None)]

    events = asyncio.run(run())
    assert [i.request_id for i in _issues(events)] == ["req-42"]
