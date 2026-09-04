"""B5 缺陷 C：Edge 在 final 帧上也标 driving（裁决点仍只有 server.py::_is_driving 一处）。

process 帧只有复杂轮才有；简单轮从不带标 ⇒ 客户端「Edge 标 false 起 30s 退出」那条 false
可能永远不到（B4 真机实测行车档 3h12m 退不出）。final 每轮都有，在 Handle 出口统一盖。
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from cockpit.orchestrator.v1 import orchestrator_pb2

from server import EdgeOrchestratorServicer

# 端侧快路径（hvac.on 在 LOCAL_INTENTS，无 process 帧）——正是缺陷 C 的形态。
# 跑红前先核一眼它真走本地：`_finals` 为空就换 LOCAL_INTENTS 表里任一句。
_LOCAL = "打开空调"
# 端侧认得出、不在 LOCAL_INTENTS、VAL 执行得了（test_cloud_degraded_fallback 同一句，理由见那边）
_CLOUD_ROUTED = "开启露营模式"


def _drive(srv, text: str):
    req = orchestrator_pb2.HandleRequest(
        text=text, session_id="s-driving-stamp", meta={"memory_enabled": "false"})

    async def run():
        return [ev async for ev in srv.Handle(req, None)]

    return asyncio.run(run())


def _servicer(cloud_events):
    srv = EdgeOrchestratorServicer()

    async def fake_cloud_handle(req):
        for ev in cloud_events:
            yield ev

    srv.cloud.handle = fake_cloud_handle
    return srv


def _finals(events):
    return [ev.final for ev in events if ev.WhichOneof("event") == "final"]


def _progress(events):
    return [ev.progress for ev in events if ev.WhichOneof("event") == "progress"]


def _cloud_events():
    return [
        orchestrator_pb2.HandleEvent(progress=orchestrator_pb2.ProcessUpdate(
            phase="analyze", label="规划", status="done")),
        orchestrator_pb2.HandleEvent(final=orchestrator_pb2.FinalResult(speech="好的")),
    ]


def _moving(srv):
    srv.val.state["speed_kmh"] = 30
    srv.val.state["gear"] = "D"


def _parked(srv):
    srv.val.state["speed_kmh"] = 0
    srv.val.state["gear"] = "P"


def test_local_fast_path_final_is_stamped_true_when_moving():
    srv = _servicer([])
    _moving(srv)
    finals = _finals(_drive(srv, _LOCAL))
    assert finals, "这句没走本地快路径——换 LOCAL_INTENTS 里的一句"
    assert all(f.driving is True for f in finals)


def test_local_fast_path_final_is_stamped_false_when_parked():
    srv = _servicer([])
    _parked(srv)
    finals = _finals(_drive(srv, _LOCAL))
    assert finals
    assert all(f.driving is False for f in finals)


def test_cloud_path_stamps_both_progress_and_final():
    srv = _servicer(_cloud_events())
    _moving(srv)
    events = _drive(srv, _CLOUD_ROUTED)
    assert [p.driving for p in _progress(events)] == [True]
    assert [f.driving for f in _finals(events)] == [True]


def test_gear_d_at_zero_speed_counts_as_driving():
    """与 _is_driving 同口径：红灯停车挡位 D 仍算行车（退出只在 P/N 且零速）。"""
    srv = _servicer([])
    srv.val.state["speed_kmh"] = 0
    srv.val.state["gear"] = "D"
    assert all(f.driving for f in _finals(_drive(srv, _LOCAL)))


def test_edge_overrides_cloud_supplied_driving():
    """Edge 是车辆状态真相源：云端 final 自带 driving=True 也按 VAL 盖成 False。"""
    srv = _servicer([orchestrator_pb2.HandleEvent(
        final=orchestrator_pb2.FinalResult(speech="x", driving=True))])
    _parked(srv)
    assert [f.driving for f in _finals(_drive(srv, _CLOUD_ROUTED))] == [False]
