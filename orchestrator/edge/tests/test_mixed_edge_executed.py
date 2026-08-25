"""混合路径的**同轮执行事实**上云（QA 卡 Q7 / OR2，2026-08-16）。

## 它修的是什么

「关闭空调然后打开，按顺序执行」端侧切成三段，前两段都**已经确定性解出**
（`aircon.close` / `aircon.open`，后者靠 Q7 的段内回填），第三段是整句修饰语。
可分组时修饰语粘到第二段后面 ⇒ **整组上云**，端侧那个已解出的结构化结果被丢弃，
云侧只拿到裸文本「打开，按顺序执行」——**对象在同一轮的另一个组里**。

真栈实测：云侧就此落兜底，答「这个我做不到哦，**我不能帮你执行操作**」，
而它 4 秒前刚关了空调。

## 为什么不在端侧改分组（实测否掉，留痕）

端侧**分不开**「按顺序执行」与「周杰伦的」——`_is_filler_segment` 对两者
**逐字同判 `True`**。丢掉前者无损，丢掉后者是事故（「…播一首歌，周杰伦的」
会变成随机放歌，§45.1 已经烧过一次）。所以补的是**上云时的上下文**，
不是分组判据：整段照旧上云，只是云侧多知道一件事——这一轮端侧已经做了什么。
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from cockpit.common.v1 import common_pb2
from cockpit.orchestrator.v1 import orchestrator_pb2

import server as server_module
from server import EdgeOrchestratorServicer


def _request(text: str = "关闭空调然后打开，按顺序执行") -> orchestrator_pb2.HandleRequest:
    return orchestrator_pb2.HandleRequest(
        text=text,
        session_id="mixed-executed-session",
        request_id="request-or2",
        context=common_pb2.ContextRef(user_id="u1", vehicle_id="vehicle-1"),
        meta={"trace_id": "trace-or2"},
    )


def _local(obj: str, operate: str, raw: str) -> dict:
    return {"confidence": 0.99, "data": {"object": obj, "operate": operate},
            "_raw_text": raw, "_needs_cloud": False, "_sep": "", "_cloud_domain": ""}


def _cloud(raw: str) -> dict:
    return {"confidence": 0.0, "data": {"object": "unknown", "operate": "unknown"},
            "_raw_text": raw, "_needs_cloud": True, "_sep": "，", "_cloud_domain": ""}


def _run(monkeypatch, mixed, request=None):
    """跑一趟混合路径，返回 (上云请求列表, 事件列表)。"""
    monkeypatch.setattr(server_module, "climate_feeling_intents", lambda _t: None)
    monkeypatch.setattr(server_module, "split_and_classify", lambda _t: None)
    monkeypatch.setattr(server_module, "split_and_classify_any", lambda _t: list(mixed))

    service = EdgeOrchestratorServicer()
    cloud_requests: list = []

    async def fake_cloud_handle(req):
        copied = orchestrator_pb2.HandleRequest()
        copied.CopyFrom(req)
        cloud_requests.append(copied)
        yield orchestrator_pb2.HandleEvent(
            final=orchestrator_pb2.FinalResult(speech="云端完成"))

    async def noop(*args, **kwargs):
        return None

    service.cloud.handle = fake_cloud_handle
    service.obs.emit_span = noop
    service.obs.emit_turn = noop

    async def go():
        return [ev async for ev in service.Handle(request or _request(), None)]

    return cloud_requests, asyncio.run(go())


def test_cloud_subrequest_carries_this_turns_executed_actions(monkeypatch):
    """OR2 的通道：上云请求必须带上本轮端侧**真的执行掉**的动作名。

    ⚠ 这里只有 `hvac.off` 一条，**而这正是 OR2 的病灶本身**：第二段「打开」端侧
    已经解出了 `aircon.open`（段内回填生效、`_needs_cloud=False`），可修饰语
    「按顺序执行」粘在它后面 ⇒ 那一组整组上云 ⇒ 已解出的结果被丢弃、动作没执行。
    云侧于是拿到一个无对象的碎片「按顺序执行」，而对象在**另一个组**里。
    上报第一组这条已执行动作，就是让云侧够得着那个对象的唯一途径。
    """
    mixed = [_local("aircon", "close", "关闭空调"),
             _local("aircon", "open", "打开"),
             _cloud("按顺序执行")]
    cloud_requests, _events = _run(monkeypatch, mixed)

    assert len(cloud_requests) == 1
    assert cloud_requests[0].meta["_edge_executed"] == "hvac.off"
    # 上云文本仍是**原话片段**，一个字都没被改写——补的是上下文不是话术。
    assert cloud_requests[0].text == "打开，按顺序执行"


def test_only_actually_executed_actions_are_reported(monkeypatch):
    """**只报真执行掉的**。VAL 被安全门控拒绝时端侧不下发 action，
    这里也一条都不许出现——否则云侧会据一件没发生的事做消解。

    （同 `_execute_val_observed` 那条既有纪律：门控拒绝只播报、不下发动作。）
    """
    async def refuse(*args, **kwargs):
        return False, "电量过低，暂不执行"

    monkeypatch.setattr(EdgeOrchestratorServicer, "_execute_val_observed", refuse)
    mixed = [_local("aircon", "close", "关闭空调"), _cloud("按顺序执行")]
    cloud_requests, _events = _run(monkeypatch, mixed)

    assert len(cloud_requests) == 1
    assert "_edge_executed" not in cloud_requests[0].meta


def test_no_local_action_means_no_key_at_all(monkeypatch):
    """整组都上云时**不写这个键**——空字符串和「没有这个键」在消费侧都判空，
    但少写一个键就少一次「它是不是空的」的判断。"""
    mixed = [_cloud("帮我查个天气"), _cloud("再看看路况")]
    cloud_requests, _events = _run(monkeypatch, mixed)

    assert len(cloud_requests) == 1
    assert "_edge_executed" not in cloud_requests[0].meta


def test_negated_segments_are_noops_while_the_real_directive_executes(monkeypatch):
    """NG4 服务层整链：负极性两段不重送云端，中间正指令不被一起拖走。"""
    mixed = server_module.split_and_classify_any(
        "车窗别开，空调关了，音乐别停")
    assert mixed is not None

    cloud_requests, events = _run(
        monkeypatch, mixed,
        request=_request("车窗别开，空调关了，音乐别停"))

    assert cloud_requests == []
    final = next(ev.final for ev in events
                 if ev.WhichOneof("event") == "final")
    commands = [a.payload.fields["command"].string_value for a in final.actions]
    assert commands == ["hvac.off"]
    assert "保持" in final.speech and "空调" in final.speech


def test_single_negated_directive_is_acknowledged_without_cloud_or_action(monkeypatch):
    """单句「别开」的反面不是「关」；也无需再让模型猜一次极性。"""
    cloud_requests, events = _run(
        monkeypatch, [], request=_request("车窗别开"))

    assert cloud_requests == []
    final = next(ev.final for ev in events
                 if ev.WhichOneof("event") == "final")
    assert not final.actions
    assert "保持" in final.speech


def test_negation_idiom_without_a_control_object_still_goes_to_cloud(monkeypatch):
    """「别开玩笑」的“开”不是车控动词，后面的股票问题不能被 no-op 吞掉。"""
    request = _request("别开玩笑认真回答沪深300现在怎么样")
    cloud_requests, _events = _run(monkeypatch, [], request=request)

    assert len(cloud_requests) == 1
    assert cloud_requests[0].text == request.text


def test_all_local_mixed_path_records_the_executed_action_ledger(monkeypatch):
    recorded = []

    def capture(_self, _request, user_text, speech, actions=None):
        recorded.append((user_text, speech, list(actions or [])))

    monkeypatch.setattr(EdgeOrchestratorServicer, "_record_local_turn", capture)
    mixed = server_module.split_and_classify_any(
        "车窗别开，空调关了，音乐别停")
    _cloud_requests, _events = _run(
        monkeypatch, mixed,
        request=_request("车窗别开，空调关了，音乐别停"))

    assert len(recorded) == 1
    assert EdgeOrchestratorServicer._executed_names(recorded[0][2]) == ["hvac.off"]


def test_client_cannot_spoof_the_internal_execution_fact(monkeypatch):
    """网关会透传客户端 meta；同名键必须在端侧入口剥掉，再由真实执行器盖章。

    否则网页/手机可自行声称「刚执行了 sunroof.open」，云侧会把未发生的事当成
    可信焦点。即使最终车控仍过 VAL，这也会污染指代消解与审计语义。
    """
    request = _request("帮我查个天气")
    request.meta["_edge_executed"] = "sunroof.open"
    cloud_requests, _events = _run(
        monkeypatch, [_cloud("帮我查个天气")], request=request)

    assert len(cloud_requests) == 1
    assert "_edge_executed" not in cloud_requests[0].meta


def test_reported_names_equal_the_actions_on_the_wire(monkeypatch):
    """名字口径与 Q6 的执行事实账本、obs、探针**共用 `_executed_names`**。

    审计问答答的、badcase 面板看的、云侧消解用的必须是同一个名字——
    三处各写一份提取逻辑，迟早有一份是错的（B4 判据）。这条把它钉成断言：
    上报给云侧的名字，与同一轮真的下发给 HMI 的那些 action **逐字相同**。
    """
    # `_sep` 用顺承连词，让云侧那段**自成一件事**（`_starts_new_act`）——
    # 否则它会粘到前一段上、把已解出的本地意图一起拖上云，就没有本地动作可比了。
    mixed = [_local("sunroof", "open", "打开天窗"),
             {**_cloud("查下今天天气"), "_sep": "，然后"}]
    cloud_requests, events = _run(monkeypatch, mixed)

    reported = cloud_requests[0].meta["_edge_executed"].split(",")
    local_final = next(ev.final for ev in events
                       if ev.WhichOneof("event") == "final" and ev.final.actions)
    on_the_wire = [a.payload.fields["command"].string_value
                   for a in local_final.actions]
    assert reported == on_the_wire == ["sunroof.open"]
