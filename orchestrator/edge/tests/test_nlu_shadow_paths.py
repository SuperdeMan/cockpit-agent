"""端侧 NLU 影子的挂载面与时序（M5 P3 收尾——补 P3a 记过账的那个盲区）。

P3a 的影子只挂在**上云**那一支，理由是快路径要毫秒级秒回。代价当时就写进了注释：
**规则误接发生在本地那一支，影子看不见——而误接恰恰是最危险的一类**
（漏接顶多多绕一圈云，误接是「用户说车窗、系统开天窗」）。

本组测试守两件事：
  ① 四条路径（local / multi / mixed / cloud）都要挂上，且 `path` 属性分得开
     ——`differ` 在 local 与在 cloud 是完全不同的两件事，混在一起这批数据就白攒了；
  ② **秒回不许等影子**：final 必须先出，影子后跑。这一条用一个「慢影子」反证，
     否则「fire-and-forget」只是注释里的一句话。
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from cockpit.orchestrator.v1 import orchestrator_pb2
from server import EdgeOrchestratorServicer


def _service(monkeypatch, shadow_attrs=None, delay=0.0):
    monkeypatch.setenv("NATS_URL", "")
    monkeypatch.setenv("EDGE_NLU_MODE", "shadow")
    service = EdgeOrchestratorServicer()
    spans: list[tuple[str, dict]] = []
    order: list[str] = []

    async def fake_span(trace_id, node, **kwargs):
        spans.append((node, kwargs.get("attrs") or {}))
        if node == "nlu.shadow":
            order.append("shadow")

    async def fake_memory(*args, **kwargs):
        return None

    default = {"nlu_domain": "setting", "nlu_object": "空调开闭控制",
               "nlu_conf": 0.9, "nlu_vs_rule": "agree"}

    seen_rule_objects: list[list[str] | None] = []

    async def fake_shadow(text, rule_objects=None):
        seen_rule_objects.append(rule_objects)
        if delay:
            await asyncio.sleep(delay)
        # 用 `is None` 而不是 `or`——`{}` 是「影子没结果」这个用例的输入，
        # `or` 会把它当成「没传」而回落默认值（本文件第一版就栽在这里）。
        return dict(default if shadow_attrs is None else shadow_attrs)

    service._seen_rule_objects = seen_rule_objects

    service.obs.emit_span = fake_span
    service.memory.append = fake_memory
    service._nlu_shadow = fake_shadow
    return service, spans, order


async def _drain(service, request, order=None):
    async for event in service.Handle(request, None):
        if event.HasField("final") and order is not None:
            order.append("final")
    # fire-and-forget：让已排队的影子任务跑完再断言
    pending = [t for t in service._bg if not t.done()]
    if pending:
        await asyncio.gather(*pending)


def _req(text, trace_id):
    return orchestrator_pb2.HandleRequest(
        text=text, session_id="shadow-path-test", request_id="r1",
        meta={"trace_id": trace_id})


def test_local_fast_path_now_has_a_shadow(monkeypatch):
    """P3a 的盲区：车已经被规则开走了，而影子当时一个字都没记。"""
    service, spans, _ = _service(monkeypatch)
    asyncio.run(_drain(service, _req("打开空调26度", "t-local")))

    nodes = [n for n, _ in spans]
    assert "route.local" in nodes, "前置条件变了：这句话不再走本地快路径"
    shadow = [a for n, a in spans if n == "nlu.shadow"]
    assert shadow, "本地快路径仍然看不见——P3a 的盲区没补上"
    assert shadow[0]["path"] == "local"
    assert shadow[0]["nlu_vs_rule"] == "agree"


def test_cloud_path_shadow_moved_to_its_own_span(monkeypatch):
    """影子从 route.cloud 的属性里搬到独立 span——四条路径的数据不该散在四个 node 里。"""
    service, spans, _ = _service(monkeypatch)

    async def fake_cloud_handle(request):
        yield orchestrator_pb2.HandleEvent(
            final=orchestrator_pb2.FinalResult(speech="好的"))

    service.cloud.handle = fake_cloud_handle
    asyncio.run(_drain(service, _req("给我讲个笑话", "t-cloud")))

    route_cloud = next(a for n, a in spans if n == "route.cloud")
    assert "nlu_vs_rule" not in route_cloud, "影子还寄生在 route.cloud 属性里"
    shadow = [a for n, a in spans if n == "nlu.shadow"]
    assert shadow and shadow[0]["path"] == "cloud"


def test_multi_intent_path_has_a_shadow(monkeypatch):
    service, spans, _ = _service(monkeypatch)
    asyncio.run(_drain(service, _req("打开空调，把车窗关上", "t-multi")))

    nodes = [n for n, _ in spans]
    assert "route.multi" in nodes, "前置条件变了：这句话不再走多意图快路径"
    shadow = [a for n, a in spans if n == "nlu.shadow"]
    assert shadow and shadow[0]["path"] == "multi"


def test_multi_intent_passes_all_executed_objects(monkeypatch):
    """多意图必须把**整组**已执行对象交给影子。

    模型是单标签分类器：「打开空调，把车窗关上」它只会给一个（真栈实测给「车窗」），
    而规则执行了 aircon + window 两条。只拿其中一条去比，结果恒为 `differ`——
    一条**结构性的假分歧**，与桥接表刚消灭的「命名差异被记成分歧」是同一类错。
    """
    service, _, _ = _service(monkeypatch)
    asyncio.run(_drain(service, _req("打开空调，把车窗关上", "t-multi-objs")))
    passed = [r for r in service._seen_rule_objects if r]
    assert passed, "多意图路径没把已执行对象传给影子"
    assert set(passed[0]) == {"aircon", "window"}


def test_agree_when_model_label_matches_any_executed_object(monkeypatch):
    """判据是「模型选中的那个在不在这一组里」，不是「等于第一个」。

    这里用真 `_nlu_shadow`（只替掉推理引擎），因为要验的正是判定分支本身。
    """
    import nlu as edge_nlu

    class _Engine:
        available = True

        def classify(self, text):
            return {"domain": "setting", "object": "车窗",
                    "conf": 0.75, "conf_domain": 0.9}

    monkeypatch.setenv("NATS_URL", "")
    monkeypatch.setenv("EDGE_NLU_MODE", "shadow")
    monkeypatch.setattr(edge_nlu, "default", lambda: _Engine())
    service = EdgeOrchestratorServicer()

    out = asyncio.run(service._nlu_shadow("打开空调，把车窗关上", ["aircon", "window"]))
    assert out["nlu_vs_rule"] == "agree", out       # 车窗 ∈ {aircon, window}
    assert out["rule_object"] == "aircon|window"

    out2 = asyncio.run(service._nlu_shadow("打开空调", ["aircon"]))
    assert out2["nlu_vs_rule"] == "differ", out2    # 车窗 ∉ {aircon}——这才是真分歧


def test_final_is_yielded_before_the_shadow_runs(monkeypatch):
    """秒回不许等影子。

    用一个「慢影子」反证：若影子还是被 await 在关键路径上，final 就会排在它后面。
    没有这一条，「fire-and-forget」只是注释里的一句话——而这正是 P3a 当初拒绝把影子
    挂上快路径的全部理由，理由不能只靠自觉守住。
    """
    service, _, order = _service(monkeypatch, delay=0.05)
    asyncio.run(_drain(service, _req("打开空调26度", "t-order"), order))
    assert order[0] == "final", f"影子跑在了秒回前面：{order}"
    assert "shadow" in order


def test_shadow_task_keeps_a_strong_reference(monkeypatch):
    """任务必须进 `_bg`——没人持有引用的 task 可能被 GC 半路收走，

    表现是「影子有时候有数据有时候没有」：不报错，只让数据悄悄变稀。
    """
    service, _, _ = _service(monkeypatch, delay=0.05)

    async def run():
        service._nlu_shadow_bg("t-ref", "打开空调", "local")
        assert any(not t.done() for t in service._bg)
        await asyncio.gather(*[t for t in service._bg if not t.done()])
        assert not [t for t in service._bg if not t.done()]

    asyncio.run(run())


def test_mode_off_schedules_nothing(monkeypatch):
    service, spans, _ = _service(monkeypatch)
    monkeypatch.setenv("EDGE_NLU_MODE", "off")

    async def run():
        service._nlu_shadow_bg("t-off", "打开空调", "local")
        assert not service._bg

    asyncio.run(run())


def test_empty_shadow_result_emits_no_span(monkeypatch):
    """模型不可用/推理失败时不落空 span——影子缺席应当是「没有这一行」，不是一行空的。"""
    service, spans, _ = _service(monkeypatch, shadow_attrs={})
    asyncio.run(_drain(service, _req("打开空调26度", "t-empty")))
    assert not [n for n, _ in spans if n == "nlu.shadow"]
